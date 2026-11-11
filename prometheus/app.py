"""Application entry point."""
from __future__ import annotations

import logging
import os
import signal
import sys

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .context import AppContext
from .core import logsetup, paths
from .ui import theme
from .ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    log = logsetup.setup()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("PROMETHEUS")
    app.setDesktopFileName("prometheus")       # matches prometheus.desktop for Wayland window icons
    app.setWindowIcon(QIcon(str(paths.resources_dir() / "prometheus.svg")))
    theme.apply(app)
    ctx = AppContext()
    window = MainWindow(ctx)
    window.show()
    # Close cleanly on Ctrl+C or a plain kill, so the bridge and the arena server are shut down too.
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: window.close())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)      # lets Python run its signal handlers while Qt is idle
    heartbeat.start(250)
    log.info("started on platform %s", app.platformName())
    if not ctx.config.setup_complete and not ctx.load_error:
        QTimer.singleShot(0, window.run_setup)
    code = app.exec()
    ctx.bridge.stop()
    if not QThreadPool.globalInstance().waitForDone(2000):
        log.warning("a background task was still running at exit; exiting anyway")
        logging.shutdown()
        os._exit(code)
    return code
