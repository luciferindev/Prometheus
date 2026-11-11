"""Find Minecraft installations and read what is actually installed in an instance.

Everything reported here is read from files on disk (launcher metadata and the
fabric.mod.json inside each mod jar); nothing is assumed.
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

VANILLA, PRISM, UNKNOWN = "vanilla-launcher", "prism-multimc", "unknown"


@dataclass
class ModInfo:
    id: str
    version: str
    name: str
    file: str
    depends: dict[str, object] = field(default_factory=dict)
    nested: list["ModInfo"] = field(default_factory=list)


@dataclass
class Instance:
    root: Path                 # what the user picked
    game_dir: Path             # where mods/ lives
    kind: str
    name: str
    mc_versions: list[str] = field(default_factory=list)       # MC versions this instance can launch
    fabric_loaders: dict[str, list[str]] = field(default_factory=dict)  # mc version -> loader versions
    mods: list[ModInfo] = field(default_factory=list)
    unreadable_jars: list[str] = field(default_factory=list)

    def mod(self, mod_id: str) -> ModInfo | None:
        for m in self.mods:
            if m.id == mod_id:
                return m
            for n in m.nested:
                if n.id == mod_id:
                    return n
        return None


def launcher_roots(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    return [p for p in (
        home / ".minecraft",
        home / ".local/share/PrismLauncher",
        home / ".local/share/multimc",
        home / ".local/share/PolyMC",
        home / ".var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher",
        home / ".var/app/com.mojang.Minecraft/.minecraft",
    ) if p.is_dir()]


def discover(home: Path | None = None) -> list[Instance]:
    """All instances found in the usual launcher locations."""
    found: list[Instance] = []
    for root in launcher_roots(home):
        if (root / "instances").is_dir():
            for inst in sorted((root / "instances").iterdir()):
                if (inst / "mmc-pack.json").exists():
                    found.append(inspect(inst))
        elif (root / "versions").is_dir() or (root / "launcher_profiles.json").exists():
            found.append(inspect(root))
    return found


def inspect(root: Path) -> Instance:
    """Read an instance folder (a .minecraft, or a Prism/MultiMC instance folder)."""
    root = Path(root)
    if (root / "mmc-pack.json").exists():
        game = next((root / d for d in (".minecraft", "minecraft") if (root / d).is_dir()), root / ".minecraft")
        inst = Instance(root, game, PRISM, root.name)
        _read_mmc_pack(inst)
    elif (root / "versions").is_dir() or (root / "launcher_profiles.json").exists():
        inst = Instance(root, root, VANILLA, "Minecraft Launcher")
        _read_vanilla_versions(inst)
    else:
        inst = Instance(root, root, UNKNOWN, root.name)
    _read_mods(inst)
    return inst


def _read_mmc_pack(inst: Instance) -> None:
    try:
        comps = json.loads((inst.root / "mmc-pack.json").read_text(encoding="utf-8")).get("components", [])
    except (OSError, json.JSONDecodeError):
        return
    versions = {c.get("uid"): c.get("version") for c in comps if isinstance(c, dict)}
    mc = versions.get("net.minecraft")
    if mc:
        inst.mc_versions = [mc]
        if versions.get("net.fabricmc.fabric-loader"):
            inst.fabric_loaders[mc] = [versions["net.fabricmc.fabric-loader"]]


_FABRIC_DIR = re.compile(r"^fabric-loader-(?P<loader>[\w.+\-]+?)-(?P<mc>\d+\.\d+(?:\.\d+)?)$")


def _read_vanilla_versions(inst: Instance) -> None:
    vdir = inst.root / "versions"
    if not vdir.is_dir():
        return
    for d in sorted(vdir.iterdir()):
        if not (d / f"{d.name}.json").exists():
            continue
        m = _FABRIC_DIR.match(d.name)
        if m:
            inst.fabric_loaders.setdefault(m["mc"], []).append(m["loader"])
        elif re.fullmatch(r"\d+\.\d+(\.\d+)?", d.name):
            inst.mc_versions.append(d.name)


def read_mod_jar(path: Path) -> ModInfo | None:
    """Parse fabric.mod.json from a jar, including jar-in-jar mods. None if not a Fabric mod."""
    try:
        with zipfile.ZipFile(path) as zf:
            return _read_mod_zip(zf, path.name, depth=0)
    except (zipfile.BadZipFile, OSError, KeyError, json.JSONDecodeError, ValueError):
        return None


def _read_mod_zip(zf: zipfile.ZipFile, label: str, depth: int) -> ModInfo | None:
    if "fabric.mod.json" not in zf.namelist():
        return None
    meta = json.loads(zf.read("fabric.mod.json").decode("utf-8"), strict=False)
    info = ModInfo(str(meta["id"]), str(meta.get("version", "")), str(meta.get("name", meta["id"])),
                   label, meta.get("depends", {}) or {})
    if depth < 1:
        for jar in meta.get("jars", []) or []:
            inner = jar.get("file") if isinstance(jar, dict) else None
            if inner and inner in zf.namelist() and zf.getinfo(inner).file_size < 64 << 20:
                try:
                    import io
                    with zipfile.ZipFile(io.BytesIO(zf.read(inner))) as nested:
                        child = _read_mod_zip(nested, f"{label}!{inner}", depth + 1)
                    if child:
                        info.nested.append(child)
                except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, ValueError):
                    continue
    return info


def _read_mods(inst: Instance) -> None:
    mods_dir = inst.game_dir / "mods"
    if not mods_dir.is_dir():
        return
    for jar in sorted(mods_dir.glob("*.jar")):
        info = read_mod_jar(jar)
        if info:
            inst.mods.append(info)
        else:
            inst.unreadable_jars.append(jar.name)
