"""Objects shared by every page: settings, secret store and the bridge connection."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from .core import config as cfg
from .core.bridge import BridgeClient
from .core.secrets import SecretStore

log = logging.getLogger("prometheus.app")


class AppContext(QObject):
    configChanged = Signal()

    def __init__(self, secrets: SecretStore | None = None):
        super().__init__()
        self.load_error = ""
        try:
            self.config = cfg.load()
        except cfg.ConfigError as exc:
            # Never silently overwrite a file we could not read; start from defaults and say so.
            self.load_error = str(exc)
            self.config = cfg.Config()
            log.error("settings could not be loaded: %s", exc)
        self.secrets = secrets or SecretStore()
        self.bridge = BridgeClient(self)

    def save(self) -> None:
        cfg.save(self.config)
        self.load_error = ""
        log.info("settings saved")
        self.configChanged.emit()
