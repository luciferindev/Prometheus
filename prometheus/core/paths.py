"""Filesystem locations. Everything lives in user-owned XDG directories.

Setting PROMETHEUS_HOME redirects all of them under one folder (used by tests).
"""
from __future__ import annotations

import os
from pathlib import Path

APP = "prometheus"


def _xdg(env: str, default: str) -> Path:
    home = os.environ.get("PROMETHEUS_HOME")
    if home:
        return Path(home) / default.split("/")[-1]
    base = os.environ.get(env)
    return (Path(base) if base else Path.home() / default) / APP


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def log_dir() -> Path:
    return state_dir() / "logs"


def runtimes_dir() -> Path:
    """User-local toolchains installed by the repair actions (Java, Node, npm)."""
    return data_dir() / "runtimes"


def auth_cache_dir() -> Path:
    """Microsoft token cache written by the bridge (prismarine-auth)."""
    return data_dir() / "auth"


def bridge_dir() -> Path:
    override = os.environ.get("PROMETHEUS_BRIDGE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "bridge"


def resources_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "resources"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path
