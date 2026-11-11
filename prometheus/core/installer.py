"""Verified downloads and user-local installs. Nothing here needs root.

Every download is checked against a digest published by the upstream's own
metadata API before it is used.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths

USER_AGENT = "prometheus-desktop/0.1"
Progress = Callable[[int, int, str], None]  # bytes done, bytes total (0 = unknown), label


class InstallError(RuntimeError):
    pass


class Cancelled(InstallError):
    pass


@dataclass(frozen=True)
class Artifact:
    url: str
    filename: str
    algo: str          # sha1 | sha256 | sha512
    digest_hex: str
    size: int = 0


def http_json(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise InstallError(f"Could not fetch {url}: {getattr(exc, 'reason', exc)}") from exc


def sri_to_hex(integrity: str) -> tuple[str, str]:
    """Convert an npm 'sha512-<base64>' integrity string to (algo, hex)."""
    algo, _, b64 = integrity.partition("-")
    return algo, base64.b64decode(b64).hex()


def download(art: Artifact, dest_dir: Path, progress: Progress | None = None,
             cancel: threading.Event | None = None) -> Path:
    """Download to dest_dir/filename, verifying the digest. Partial files never survive."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / art.filename
    hasher = hashlib.new(art.algo)
    req = urllib.request.Request(art.url, headers={"User-Agent": USER_AGENT})
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".dl-")
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or art.size or 0)
            done = 0
            while chunk := resp.read(1 << 16):
                if cancel is not None and cancel.is_set():
                    raise Cancelled("Cancelled.")
                out.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total, art.filename)
        if hasher.hexdigest().lower() != art.digest_hex.lower():
            raise InstallError(f"{art.filename}: {art.algo} checksum mismatch; the download was discarded.")
        os.replace(tmp, final)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InstallError(f"Download of {art.filename} failed: {getattr(exc, 'reason', exc)}") from exc
    finally:
        Path(tmp).unlink(missing_ok=True)
    return final


def extract_tar(archive: Path, dest: Path) -> Path:
    """Extract into a fresh `dest`, refusing path traversal. Returns the single top-level dir if any."""
    staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".extract-"))
    try:
        with tarfile.open(archive) as tf:
            tf.extractall(staging, filter="data")  # rejects absolute paths, '..', devices, bad links
        if dest.exists():
            shutil.rmtree(dest)
        entries = list(staging.iterdir())
        source = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
        os.replace(source, dest)
    except (tarfile.TarError, OSError) as exc:
        raise InstallError(f"Could not unpack {archive.name}: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return dest


# ---- toolchain recipes -------------------------------------------------------

def java17_artifact() -> Artifact:
    data = http_json("https://api.adoptium.net/v3/assets/latest/17/hotspot"
                     "?architecture=x64&image_type=jre&os=linux&vendor=eclipse")
    try:
        pkg = data[0]["binary"]["package"]
        return Artifact(pkg["link"], pkg["name"], "sha256", pkg["checksum"], int(pkg.get("size", 0)))
    except (LookupError, TypeError) as exc:
        raise InstallError("Adoptium returned no Java 17 build for linux/x64.") from exc


def npm_artifact() -> Artifact:
    data = http_json("https://registry.npmjs.org/npm/latest")
    algo, digest = sri_to_hex(data["dist"]["integrity"])
    return Artifact(data["dist"]["tarball"], f"npm-{data['version']}.tgz", algo, digest)


def node_artifact() -> Artifact:
    index = http_json("https://nodejs.org/dist/index.json")
    lts = next((e for e in index if e.get("lts") and "linux-x64" in e.get("files", [])), None)
    if lts is None:
        raise InstallError("nodejs.org lists no LTS build for linux-x64.")
    version = lts["version"]
    name = f"node-{version}-linux-x64.tar.xz"
    base = f"https://nodejs.org/dist/{version}"
    req = urllib.request.Request(f"{base}/SHASUMS256.txt", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            sums = resp.read().decode()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise InstallError(f"Could not fetch Node.js checksums: {exc}") from exc
    for line in sums.splitlines():
        digest, _, fname = line.partition("  ")
        if fname.strip() == name:
            return Artifact(f"{base}/{name}", name, "sha256", digest.strip())
    raise InstallError(f"No published checksum for {name}.")


RUNTIME_RECIPES: dict[str, Callable[[], Artifact]] = {
    "java17": java17_artifact,
    "node": node_artifact,
    "npm": npm_artifact,
}


def install_runtime(name: str, progress: Progress | None = None,
                    cancel: threading.Event | None = None) -> Path:
    """Install a toolchain under ~/.local/share/prometheus/runtimes/<name>."""
    art = RUNTIME_RECIPES[name]()
    root = paths.runtimes_dir()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix=".tmp-") as tmp:
        archive = download(art, Path(tmp), progress, cancel)
        if progress:
            progress(0, 0, f"Unpacking {art.filename}")
        return extract_tar(archive, root / name)
