"""Checks that a Minecraft instance is usable for vanilla 1.20.1.

Everything reported is read from the launcher's own files on disk.
"""
from __future__ import annotations

from dataclasses import dataclass

from .minecraft import PRISM, VANILLA, Instance

MC = "1.20.1"
OK, WARN, ERROR, INFO = "ok", "warn", "error", "info"


@dataclass
class Finding:
    key: str
    title: str
    level: str
    detail: str


def evaluate(inst: Instance) -> list[Finding]:
    if inst.kind not in (VANILLA, PRISM):
        return [Finding("instance", "Instance folder", ERROR,
                        "This does not look like a .minecraft folder or a Prism/MultiMC instance.")]
    out: list[Finding] = []
    if MC in inst.mc_versions:
        out.append(Finding("minecraft", f"Minecraft {MC}", OK, "Installed in this instance"))
    elif inst.kind == VANILLA:
        have = ", ".join(inst.mc_versions) or "none"
        out.append(Finding("minecraft", f"Minecraft {MC}", WARN,
                           f"Not downloaded yet (found: {have}). In the Minecraft Launcher, create an "
                           f"installation for release {MC} and start it once."))
    else:
        have = inst.mc_versions[0] if inst.mc_versions else "unknown"
        out.append(Finding("minecraft", f"Minecraft {MC}", ERROR,
                           f"This instance is Minecraft {have}. Create a {MC} instance in your launcher "
                           "and select it here."))
    if inst.mods:
        names = ", ".join(m.name for m in inst.mods[:4]) + ("…" if len(inst.mods) > 4 else "")
        out.append(Finding("mods", "Mods in this instance", INFO,
                           f"{len(inst.mods)} Fabric mods found ({names}). PROMETHEUS targets vanilla "
                           "Minecraft: blocks and items added by mods are not recognised by the agent."))
    return out


def blocking(findings: list[Finding]) -> bool:
    return any(f.level == ERROR for f in findings)
