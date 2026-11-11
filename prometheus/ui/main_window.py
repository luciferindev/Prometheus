from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..context import AppContext
from . import theme
from ..agent.runner import AgentRunner
from .pages.agent import AgentPage
from .pages.dashboard import DashboardPage
from .pages.logs import LogsPage
from ..agent.pvp import Trainer
from .pages.settings import SettingsPage
from .pages.training import TrainingPage
from .pages.vision import VisionPage
from .widgets import button
from .wizard import SetupWizard


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, runner: AgentRunner | None = None, trainer: Trainer | None = None):
        super().__init__()
        self.ctx = ctx
        self.runner = runner or AgentRunner(ctx)
        self.setWindowTitle("PROMETHEUS")
        self.setMinimumSize(980, 660)

        self.logs = LogsPage()            # first, so it captures everything logged from here on
        self.dashboard = DashboardPage(ctx, self.runner)
        self.agent = AgentPage(ctx, self.runner)
        self.vision = VisionPage(ctx, self.runner)
        self.trainer = trainer or Trainer(ctx, self.runner.store)
        self.training = TrainingPage(ctx, self.trainer)
        self.settings = SettingsPage(ctx, self.runner.store)
        self.dashboard.setupRequested.connect(self.run_setup)
        self.settings.setupRequested.connect(self.run_setup)

        side = QWidget(objectName="SidebarWrap")
        side.setFixedWidth(210)
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 0, 0, 12)
        col.setSpacing(0)
        col.addWidget(QLabel("PROMETHEUS", objectName="Brand"))
        self.nav = QListWidget(objectName="Sidebar")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setAccessibleName("Pages")
        self.stack = QStackedWidget()
        for name, icon, page in (("Dashboard", "dashboard", self.dashboard), ("Agent", "wand", self.agent),
                                 ("Vision", "eye", self.vision), ("Training", "sword", self.training),
                                 ("Settings", "settings", self.settings), ("Logs", "logs", self.logs)):
            self.nav.addItem(QListWidgetItem(theme.icon(icon, theme.MUTED), name))
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        col.addWidget(self.nav, 1)
        setup = button("Setup…", icon="wand", tip="Open the setup wizard")
        setup.clicked.connect(self.run_setup)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(12, 0, 12, 0)
        wrap.addWidget(setup)
        col.addLayout(wrap)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(side)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav.setCurrentRow(0)

        self.bridge_label = QLabel()
        self.statusBar().addPermanentWidget(self.bridge_label)
        self.statusBar().showMessage(f"PROMETHEUS {__version__} · Minecraft 1.20.1")
        ctx.bridge.runningChanged.connect(self._bridge_state)
        ctx.configChanged.connect(self.runner.push_movement_mode)
        self._bridge_state(ctx.bridge.is_running())

        for keys, fn in (("Ctrl+1", lambda: self.nav.setCurrentRow(0)),
                         ("Ctrl+2", lambda: self.nav.setCurrentRow(1)),
                         ("Ctrl+3", lambda: self.nav.setCurrentRow(2)),
                         ("Ctrl+4", lambda: self.nav.setCurrentRow(3)),
                         ("Ctrl+5", lambda: self.nav.setCurrentRow(4)),
                         ("Ctrl+6", lambda: self.nav.setCurrentRow(5)),
                         ("Ctrl+G", self._focus_goal),
                         ("Ctrl+.", lambda: self.runner.stop()),
                         ("Ctrl+Return", self.dashboard.connect_bot),
                         ("Ctrl+D", self.dashboard.disconnect_bot),
                         ("Ctrl+Shift+E", self.dashboard.emergency_stop),
                         ("Ctrl+F", self._focus_search),
                         ("Ctrl+Q", self.close)):
            QShortcut(QKeySequence(keys), self, activated=fn, context=Qt.ShortcutContext.ApplicationShortcut)

        geometry = QSettings("prometheus", "prometheus").value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1180, 760)

    def _focus_goal(self) -> None:
        self.nav.setCurrentRow(1)
        self.agent.goal.setFocus()

    def _focus_search(self) -> None:
        self.nav.setCurrentRow(5)
        self.logs.search.setFocus()

    def _bridge_state(self, running: bool) -> None:
        self.bridge_label.setText("Bridge running" if running else "Bridge stopped")
        self.bridge_label.setStyleSheet(f"color: {theme.GOOD if running else theme.MUTED}; padding-right: 8px;")

    def run_setup(self) -> None:
        self.runner.stop("Stopped because Setup was opened.")
        if self.dashboard.phase in ("connecting", "logged_in", "spawned"):
            self.dashboard.disconnect_bot()
        wizard = SetupWizard(self.ctx, self)
        wizard.exec()
        wizard.deleteLater()

    def closeEvent(self, event) -> None:
        QSettings("prometheus", "prometheus").setValue("geometry", self.saveGeometry())
        self.runner.stop("Stopped because the application was closed.")
        self.trainer.stop("Training stopped because the application was closed.", wait=True)
        self.trainer.arena.stop(wait=True)
        self.ctx.bridge.stop()
        super().closeEvent(event)
