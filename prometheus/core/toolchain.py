"""Detection of the tools PROMETHEUS depends on, with a repair id for each fixable gap."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .secrets import SecretStore

OK, WARN, MISSING = "ok", "warn", "missing"
MIN_NODE = 22


@dataclass
class Check:
    key: str
    title: str
    status: str
    detail: str
    path: str = ""
    repair: str | None = None      # id understood by the wizard's repair runner
    repair_label: str = ""
    hint: str = ""                 # what the system-package alternative is


def _run(argv: list[str], timeout: float = 8.0) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout + proc.stderr).strip()


def _version_tuple(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    return tuple(int(g) for g in m.groups() if g is not None) if m else ()


# ---- resolvers (also used to launch things) ----------------------------------

def find_node() -> str | None:
    local = paths.runtimes_dir() / "node" / "bin" / "node"
    for cand in (shutil.which("node"), str(local) if local.exists() else None):
        if cand and (_version_tuple(_run([cand, "--version"])) or (0,))[0] >= MIN_NODE:
            return cand
    return None


def find_npm() -> list[str] | None:
    """argv prefix that runs npm, preferring the system one."""
    system = shutil.which("npm")
    if system:
        return [system]
    local_node_npm = paths.runtimes_dir() / "node" / "bin" / "npm"
    if local_node_npm.exists():
        return [str(local_node_npm)]
    cli = paths.runtimes_dir() / "npm" / "bin" / "npm-cli.js"
    node = find_node()
    if cli.exists() and node:
        return [node, str(cli)]
    return None


def java_major(java: str) -> int | None:
    out = _run([java, "-version"])
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def find_java17() -> str | None:
    cands: list[str] = []
    if os.environ.get("JAVA_HOME"):
        cands.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / "java"))
    cands.append(str(paths.runtimes_dir() / "java17" / "bin" / "java"))
    jvm = Path("/usr/lib/jvm")
    if jvm.is_dir():
        cands += [str(p / "bin" / "java") for p in sorted(jvm.iterdir())]
    if shutil.which("java"):
        cands.append(shutil.which("java"))
    for cand in cands:
        if Path(cand).exists() and java_major(cand) == 17:
            return cand
    return None


def bridge_ready() -> bool:
    b = paths.bridge_dir()
    return (b / "dist" / "main.js").exists() and (b / "node_modules" / "mineflayer").is_dir()


# ---- checks ------------------------------------------------------------------

def check_python() -> Check:
    v = sys.version_info
    ok = v >= (3, 11)
    return Check("python", "Python", OK if ok else MISSING,
                 f"{v.major}.{v.minor}.{v.micro}" + ("" if ok else " (3.11 or newer required)"),
                 sys.executable, hint="" if ok else "Install the python package from the Arch repositories.")


def check_qt() -> Check:
    import PySide6
    from PySide6.QtGui import QGuiApplication
    platform = QGuiApplication.platformName() if QGuiApplication.instance() else "not started"
    return Check("qt", "Qt 6 (PySide6)", OK, f"{PySide6.__version__}, platform: {platform}")


def check_node() -> Check:
    node = find_node()
    if node:
        return Check("node", "Node.js", OK, _run([node, "--version"]), node)
    found = shutil.which("node")
    detail = (f"{_run([found, '--version'])} is too old (need {MIN_NODE}+)" if found else "Not found")
    return Check("node", "Node.js", MISSING, detail, repair="node",
                 repair_label="Install Node.js LTS for this user",
                 hint="System alternative: the nodejs package from the Arch repositories.")


def check_npm() -> Check:
    npm = find_npm()
    if npm:
        return Check("npm", "npm", OK, _run(npm + ["--version"]) or "found", npm[-1])
    if not find_node():
        return Check("npm", "npm", MISSING, "Needs Node.js first")
    return Check("npm", "npm", MISSING, "Not found (Arch ships it separately from nodejs)", repair="npm",
                 repair_label="Install npm for this user",
                 hint="System alternative: the npm package from the Arch repositories.")


def check_java() -> Check:
    java = find_java17()
    if java:
        line = _run([java, "-version"]).splitlines()[0] if _run([java, "-version"]) else "Java 17"
        return Check("java", "Java 17", OK, line, java)
    other = shutil.which("java")
    detail = f"Found Java {java_major(other)}, but Minecraft 1.20.1 needs 17" if other else "Not found"
    return Check("java", "Java 17", WARN, detail, repair="java17",
                 repair_label="Install Temurin 17 for this user",
                 hint="Needed to run Minecraft 1.20.1 (launchers often bundle it) and to build the "
                      "companion mod. System alternative: jre17-openjdk.")


def check_keyring(store: SecretStore | None = None) -> Check:
    ok, desc = (store or SecretStore()).availability()
    if ok:
        return Check("keyring", "Secret Service keyring", OK, desc)
    return Check("keyring", "Secret Service keyring", WARN, desc,
                 hint="Without it the API key is kept for the current session only and never written to disk.")


def check_bridge() -> Check:
    if bridge_ready():
        return Check("bridge", "Minecraft bridge (Mineflayer)", OK, "Dependencies installed and built",
                     str(paths.bridge_dir()))
    if not (paths.bridge_dir() / "package.json").exists():
        return Check("bridge", "Minecraft bridge (Mineflayer)", MISSING,
                     f"Bridge sources not found at {paths.bridge_dir()}")
    if not (find_node() and find_npm()):
        return Check("bridge", "Minecraft bridge (Mineflayer)", MISSING, "Needs Node.js and npm first")
    return Check("bridge", "Minecraft bridge (Mineflayer)", MISSING, "Dependencies not installed yet",
                 repair="bridge", repair_label="Install bridge dependencies")


def run_all(store: SecretStore | None = None) -> list[Check]:
    return [check_python(), check_qt(), check_node(), check_npm(), check_java(),
            check_keyring(store), check_bridge()]


def bridge_install_commands() -> list[list[str]]:
    """Commands that install and build the bridge. Fixed argv; never built from user input."""
    npm = find_npm()
    if not npm:
        raise RuntimeError("npm is not available.")
    return [npm + ["ci", "--no-audit", "--no-fund"], npm + ["run", "build"]]


def tool_env() -> dict[str, str]:
    """Environment with user-local runtimes on PATH so npm scripts can find node."""
    env = dict(os.environ)
    node = find_node()
    extra = [str(Path(node).parent)] if node else []
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env
