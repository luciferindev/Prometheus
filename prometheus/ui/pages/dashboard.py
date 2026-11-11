"""Main dashboard: connection control and live state read from the bot."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView, QListWidget,
                               QProgressBar, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...context import AppContext
from ...core import netpolicy
from .. import theme
from ..widgets import Card, DeviceCodeDialog, StatusPill, button, label
from ..workers import run_async

log = logging.getLogger("prometheus.dashboard")
DASH = "—"
PHASE_VIEW = {
    "idle": ("Not connected", theme.MUTED),
    "connecting": ("Connecting", theme.WARN),
    "logged_in": ("Logged in, waiting to spawn", theme.WARN),
    "spawned": ("In world", theme.GOOD),
    "disconnected": ("Disconnected", theme.MUTED),
}


class DashboardPage(QWidget):
    setupRequested = Signal()

    def __init__(self, ctx: AppContext, runner=None):
        super().__init__()
        self.ctx = ctx
        self.runner = runner
        self.phase = "idle"
        self._dialog: DeviceCodeDialog | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(label("Dashboard", "PageTitle"))

        self.banner = label("", "Banner", wrap=True)
        self.banner.setVisible(False)
        root.addWidget(self.banner)
        self.error = label("", "BannerBad", wrap=True, selectable=True)
        self.error.setVisible(False)
        root.addWidget(self.error)

        # Connection
        conn = Card("Connection")
        top = QHBoxLayout()
        self.pill = StatusPill()
        self.target = label("", "Mono", selectable=True)
        top.addWidget(self.pill)
        top.addWidget(self.target)
        top.addStretch(1)
        self.connect_btn = button("Connect", "Primary", "plug", "Join the configured server (Ctrl+Return)")
        self.disconnect_btn = button("Disconnect", tip="Leave the server (Ctrl+D)")
        self.estop_btn = button("Emergency stop", "Danger", "stop",
                                "Kill the bridge process immediately (Ctrl+Shift+E)")
        self.connect_btn.clicked.connect(self.connect_bot)
        self.disconnect_btn.clicked.connect(self.disconnect_bot)
        self.estop_btn.clicked.connect(self.emergency_stop)
        for b in (self.connect_btn, self.disconnect_btn, self.estop_btn):
            top.addWidget(b)
        conn.body.addLayout(top)
        self.server_info = label("Server status has not been queried yet.", "Muted", wrap=True, selectable=True)
        conn.body.addWidget(self.server_info)
        root.addWidget(conn)

        # Agent task
        task = Card("Agent task")
        trow = QHBoxLayout()
        self.task_pill = StatusPill()
        self.task_text = label("No run yet. Give the agent a goal on the Agent page (Ctrl+G).", wrap=True)
        trow.addWidget(self.task_pill)
        trow.addWidget(self.task_text, 1)
        task.body.addLayout(trow)
        self.task_usage = label("", "Muted")
        task.body.addWidget(self.task_usage)
        root.addWidget(task)
        if runner is not None:
            runner.stateChanged.connect(lambda _s: self._refresh_task())
            runner.usageChanged.connect(self._refresh_task)
            runner.event.connect(self._task_event)
            runner.planChanged.connect(self._refresh_task)

        # Vitals + location
        mid = QHBoxLayout()
        mid.setSpacing(14)
        vit = Card("Agent")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        self.health = QProgressBar(objectName="Health")
        self.food = QProgressBar(objectName="Food")
        self.health_txt, self.food_txt = label(DASH), label(DASH)
        for r, (name, bar, txt) in enumerate((("Health", self.health, self.health_txt),
                                              ("Hunger", self.food, self.food_txt))):
            bar.setRange(0, 20)
            bar.setAccessibleName(name)
            grid.addWidget(label(name, "Muted"), r, 0)
            grid.addWidget(bar, r, 1)
            grid.addWidget(txt, r, 2)
        self.armor, self.level, self.player = label(DASH), label(DASH), label(DASH)
        self.armor.setToolTip("Armor points calculated from the vanilla armor pieces the agent is wearing")
        for r, (name, w) in enumerate((("Armor", self.armor), ("Level", self.level), ("Player", self.player)), 2):
            grid.addWidget(label(name, "Muted"), r, 0)
            grid.addWidget(w, r, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        vit.body.addLayout(grid)
        vit.body.addStretch(1)
        mid.addWidget(vit, 1)

        loc = Card("Location")
        self.coords = label(DASH, "Value", selectable=True)
        loc.body.addWidget(self.coords)
        lgrid = QGridLayout()
        lgrid.setVerticalSpacing(10)
        self.dimension, self.biome, self.mode, self.held = label(DASH), label(DASH), label(DASH), label(DASH)
        for r, (name, w) in enumerate((("Dimension", self.dimension), ("Biome", self.biome),
                                       ("Game mode", self.mode), ("Holding", self.held))):
            lgrid.addWidget(label(name, "Muted"), r, 0)
            lgrid.addWidget(w, r, 1)
        lgrid.setColumnStretch(1, 1)
        loc.body.addLayout(lgrid)
        loc.body.addStretch(1)
        mid.addWidget(loc, 1)
        root.addLayout(mid)

        # Inventory + events
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        inv = Card("Inventory")
        self.inv_summary = label("No inventory data.", "Muted")
        inv.body.addWidget(self.inv_summary)
        self.inventory = QTableWidget(0, 3)
        self.inventory.setHorizontalHeaderLabels(["Item", "Count", "Slot"])
        self.inventory.verticalHeader().setVisible(False)
        self.inventory.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.inventory.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.inventory.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.inventory.setShowGrid(False)
        inv.body.addWidget(self.inventory, 1)
        ev = Card("Connection events")
        self.events = QListWidget(objectName="Events")
        self.events.setWordWrap(True)
        self.events.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ev.body.addWidget(self.events, 1)
        split.addWidget(inv)
        split.addWidget(ev)
        split.setSizes([500, 500])
        root.addWidget(split, 1)

        ctx.bridge.notification.connect(self._on_notification)
        ctx.bridge.exited.connect(self._on_bridge_exit)
        ctx.bridge.runningChanged.connect(lambda _r: self._refresh_buttons())
        ctx.configChanged.connect(self._refresh_static)
        self._clear_state()
        self._refresh_static()

    # ---- agent task ----
    def _refresh_task(self) -> None:
        r = self.runner
        names = {"idle": ("Idle", theme.MUTED), "starting": ("Starting", theme.WARN),
                 "running": ("Running", theme.GOOD), "paused": ("Paused", theme.WARN)}
        self.task_pill.set(*names[r.state])
        if r.state == "idle":
            if r.last_outcome:
                self.task_text.setText(f"Last run: {r.goal}\n{r.last_outcome.splitlines()[0][:240]}")
        else:
            active = next((s["title"] for s in r.plan if s["status"] == "in_progress"), "")
            done = sum(s["status"] == "done" for s in r.plan)
            self.task_text.setText(f"Goal: {r.goal}" + (f"\nSubtask: {active} ({done} of {len(r.plan)} steps done)"
                                                       if active else ""))
        cost = r.cost
        if r.usage.requests:
            self.task_usage.setText(
                f"{r.usage.input_tokens:,} input / {r.usage.output_tokens:,} output tokens · "
                + (f"about ${cost:.4f}" if cost is not None else "cost unknown for this model")
                + f" · step {r.steps}")

    def _task_event(self, e: dict) -> None:
        if self.runner.state != "idle" and e["kind"] in ("tool_call", "note"):
            first = (e["body"] or e["title"]).splitlines()[0][:200]
            active = next((s["title"] for s in self.runner.plan if s["status"] == "in_progress"), "")
            self.task_text.setText(f"Goal: {self.runner.goal}" + (f"\nSubtask: {active}" if active else "")
                                   + f"\nNow: {e['title'] if e['kind'] == 'tool_call' else first}")

    # ---- static bits ----
    def _refresh_static(self) -> None:
        c = self.ctx.config
        self.target.setText(f"{c.server.host}:{c.server.port}")
        notes = []
        if self.ctx.load_error:
            notes.append(f"Settings could not be loaded and defaults are in use: {self.ctx.load_error}")
        if not c.setup_complete:
            notes.append("Setup has not been completed. Open Setup from the sidebar to configure the "
                         "server and account.")
        elif not c.anthropic.model:
            notes.append("The Claude API is not configured yet. The Minecraft connection works without it.")
        self.banner.setText("\n".join(notes))
        self.banner.setVisible(bool(notes))
        self._set_phase(self.phase)

    def _refresh_buttons(self) -> None:
        busy = self.phase in ("connecting", "logged_in", "spawned")
        self.connect_btn.setEnabled(not busy)
        self.disconnect_btn.setEnabled(busy)
        self.estop_btn.setEnabled(self.ctx.bridge.is_running())

    def _set_phase(self, phase: str, reason: str = "") -> None:
        self.phase = phase
        text, color = PHASE_VIEW[phase]
        self.pill.set(text, color)
        self._refresh_buttons()
        if phase == "disconnected":
            self._clear_state()

    def _event(self, text: str, level: str = "info") -> None:
        self.events.insertItem(0, f"{time.strftime('%H:%M:%S')}   {text}")
        if level != "info":
            self.events.item(0).setForeground(QColor(theme.BAD if level == "error" else theme.WARN))
        while self.events.count() > 200:
            self.events.takeItem(self.events.count() - 1)
        getattr(log, "error" if level == "error" else "info")(text)

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))

    # ---- actions ----
    def connect_bot(self) -> None:
        if self.phase in ("connecting", "logged_in", "spawned"):
            return
        c = self.ctx.config
        if not c.auth.username:
            self._show_error("No agent account is configured. Open Setup and choose a Microsoft account "
                             "or an offline player name.")
            return
        self._show_error("")
        self._set_phase("connecting")
        self._event(f"Connecting to {c.server.host}:{c.server.port} as {c.auth.username} ({c.auth.mode})")
        run_async(netpolicy.check_host, c.server.host, c.server.allow_public,
                  on_done=lambda _: self._ping_then_join(), on_error=self._connect_failed)

    def _ping_then_join(self) -> None:
        c = self.ctx.config
        self.ctx.bridge.call("server.ping", {"host": c.server.host, "port": c.server.port},
                             self._pinged, self._ping_failed)

    def _pinged(self, res: dict) -> None:
        self.server_info.setText(
            f"{res.get('versionName')} (protocol {res.get('protocol')}) · "
            f"{res.get('playersOnline')}/{res.get('playersMax')} players · {res.get('latencyMs')} ms · "
            f"{res.get('motd', '')}")
        if res.get("protocol") != 763:
            self._connect_failed(f"The server is {res.get('versionName')} (protocol {res.get('protocol')}), "
                                 "but PROMETHEUS targets Minecraft 1.20.1 (protocol 763).")
            return
        self._join()

    def _ping_failed(self, message: str) -> None:
        if "Could not reach" in message:
            self._connect_failed(message + " Is the world running and opened to LAN, or the server started?")
        else:   # e.g. status disabled on the server: still try to join
            self._event(f"Status query failed ({message}); trying to join anyway", "warn")
            self._join()

    def _join(self) -> None:
        c = self.ctx.config
        self.ctx.bridge.call("bot.connect", {"host": c.server.host, "port": c.server.port, "auth": c.auth.mode,
                                             "username": c.auth.username, "version": c.minecraft.mc_version},
                             None, self._connect_failed)

    def _connect_failed(self, message: str) -> None:
        self._event(message, "error")
        self._show_error(message)
        self._set_phase("disconnected")

    def disconnect_bot(self) -> None:
        self.ctx.bridge.call("bot.disconnect", {}, None, lambda m: self._event(m, "error"))

    def emergency_stop(self) -> None:
        if not self.ctx.bridge.is_running():
            return
        self.ctx.bridge.stop(kill=True)
        self._close_dialog()
        self._event("Emergency stop: the bridge process was killed, the bot is off the server and any agent "
                    "run was ended.", "warn")
        self._set_phase("disconnected")

    # ---- bridge events ----
    def _on_notification(self, method: str, params: dict) -> None:
        if method == "bot.status":
            phase, reason = params["phase"], params.get("reason", "")
            if phase in ("logged_in", "spawned"):
                self._close_dialog()
            if phase != self.phase or reason:
                self._event(PHASE_VIEW[phase][0] + (f": {reason}" if reason else ""))
            self._set_phase(phase, reason)
        elif method == "bot.state":
            self._show_state(params)
        elif method == "bot.event":
            kind = params["kind"]
            self._event(params["text"], "error" if kind in ("error", "kicked") else "warn")
            if kind in ("error", "kicked"):
                self._show_error(params["text"])
        elif method == "auth.deviceCode" and self.phase == "connecting" and self.isVisible():
            self._close_dialog()
            self._dialog = DeviceCodeDialog(self, params["userCode"], params["verificationUri"],
                                            params["expiresIn"])
            self._dialog.show()
            self._event("Microsoft sign-in required: waiting for the device code to be entered")

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None

    def _on_bridge_exit(self, reason: str) -> None:
        self._event(reason, "error")
        self._show_error(reason + " Connect again to restart it; details are in Logs.")
        self._set_phase("disconnected")

    # ---- state rendering ----
    def _clear_state(self) -> None:
        for bar in (self.health, self.food):
            bar.setValue(0)
        for w in (self.health_txt, self.food_txt, self.armor, self.level, self.player, self.coords,
                  self.dimension, self.biome, self.mode, self.held):
            w.setText(DASH)
        self.inventory.setRowCount(0)
        self.inv_summary.setText("No inventory data.")

    def _show_state(self, s: dict) -> None:
        def fmt(v, suffix=""):
            if v is None:
                return DASH
            return f"{round(v, 1):g}{suffix}" if isinstance(v, (int, float)) else str(v)

        self.health.setValue(int(round(s.get("health") or 0)))
        self.food.setValue(int(round(s.get("food") or 0)))
        self.health_txt.setText(fmt(s.get("health"), " / 20"))
        self.food_txt.setText(fmt(s.get("food"), " / 20"))
        self.armor.setText(fmt(s.get("armor"), " / 20") if s.get("armor") is not None else "unknown (non-vanilla armor)")
        self.level.setText(fmt(s.get("level")))
        self.player.setText(fmt(s.get("username")))
        pos = s.get("position")
        self.coords.setText(f"{pos['x']:.1f}   {pos['y']:.1f}   {pos['z']:.1f}" if pos else DASH)
        self.dimension.setText(fmt(s.get("dimension")))
        self.biome.setText(fmt(s.get("biome")) if s.get("biome") else "chunk not loaded")
        self.mode.setText(fmt(s.get("gameMode")))
        self.held.setText(fmt(s.get("heldItem")) if s.get("heldItem") else "nothing")
        items = [i for i in s.get("inventory", []) if isinstance(i, dict)]
        self.inventory.setRowCount(len(items))
        for r, item in enumerate(items):
            cells = (str(item.get("displayName") or item.get("name")), str(item.get("count")), str(item.get("slot")))
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                if col:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.inventory.setItem(r, col, cell)
        total = sum(int(i.get("count") or 0) for i in items)
        self.inv_summary.setText(f"{len(items)} stacks, {total} items" if items else "The inventory is empty.")
