"""Typed, validated, atomically persisted application settings.

Secrets never go in this file; see secrets.py.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import paths

SCHEMA_VERSION = 1
MC_VERSION = "1.20.1"
AUTH_MODES = ("microsoft", "offline")


class ConfigError(ValueError):
    pass


@dataclass
class MinecraftConfig:
    instance_dir: str = ""
    mc_version: str = MC_VERSION


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 25565
    allow_public: bool = False


@dataclass
class AuthConfig:
    mode: str = "microsoft"
    # Offline: the in-game name. Microsoft: a label that keys the token cache.
    username: str = ""


@dataclass
class AnthropicConfig:
    model: str = ""


EFFORTS = ("low", "medium", "high")


@dataclass
class AgentConfig:
    effort: str = "medium"
    max_cost_usd: float = 1.0          # per run; the run stops when the estimate reaches it
    max_steps: int = 60                # Claude requests per run
    confirm_block_breaking: bool = False
    movement: str = "free"             # free: may dig and pillar to get around; careful: only clears leaves
    chat_control: bool = False         # take goals and instructions from one player's in-game chat
    chat_operator: str = ""            # that player's exact name


@dataclass
class Config:
    schema_version: int = SCHEMA_VERSION
    setup_complete: bool = False
    minecraft: MinecraftConfig = field(default_factory=MinecraftConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    def validate(self) -> None:
        if not (1 <= self.server.port <= 65535):
            raise ConfigError(f"Server port {self.server.port} is outside 1-65535.")
        if not self.server.host.strip():
            raise ConfigError("Server address is empty.")
        if self.auth.mode not in AUTH_MODES:
            raise ConfigError(f"Unknown authentication mode {self.auth.mode!r}.")
        if self.agent.effort not in EFFORTS:
            raise ConfigError(f"Unknown effort level {self.agent.effort!r}.")
        if self.agent.movement not in ("free", "careful"):
            raise ConfigError(f"Unknown movement mode {self.agent.movement!r}.")
        if self.agent.chat_control and not self.agent.chat_operator.strip():
            raise ConfigError("Enter the player name that may command the agent through chat.")
        if not (0.01 <= self.agent.max_cost_usd <= 1000):
            raise ConfigError("The cost limit must be between $0.01 and $1000.")
        if not (1 <= self.agent.max_steps <= 1000):
            raise ConfigError("The step limit must be between 1 and 1000.")
        if self.auth.mode == "offline" and self.auth.username:
            validate_offline_name(self.auth.username)


def validate_offline_name(name: str) -> None:
    if not (3 <= len(name) <= 16) or not all(c.isascii() and (c.isalnum() or c == "_") for c in name):
        raise ConfigError("Offline player names are 3-16 characters: letters, digits, underscore.")


def _from_dict(cls: type, data: Any) -> Any:
    """Build a dataclass from untrusted JSON: unknown keys dropped, wrong types rejected."""
    if not isinstance(data, dict):
        raise ConfigError(f"Expected an object for {cls.__name__}.")
    obj = cls()
    for f in fields(cls):
        if f.name not in data:
            continue
        value, current = data[f.name], getattr(obj, f.name)
        if is_dataclass(current):
            value = _from_dict(type(current), value)
        elif isinstance(current, float) and type(value) is int:
            value = float(value)
        elif type(value) is not type(current):
            raise ConfigError(f"{cls.__name__}.{f.name} should be {type(current).__name__}.")
        setattr(obj, f.name, value)
    return obj


def config_path() -> Path:
    return paths.config_dir() / "config.json"


def load(path: Path | None = None) -> Config:
    """Load settings. A missing file yields defaults; a corrupt one raises ConfigError."""
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    cfg = _from_dict(Config, raw)
    cfg.validate()
    return cfg


def save(cfg: Config, path: Path | None = None) -> Path:
    cfg.validate()
    path = path or config_path()
    paths.ensure_private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(cfg), fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
