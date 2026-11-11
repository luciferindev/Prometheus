"""Training page: crystal PvP practice in the private arena, with measured progress."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QListWidget, QMessageBox, QProgressBar, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QHeaderView)

from ...agent import pvp
from ...agent.pvp import Trainer
from ...context import AppContext
from ...core import arena
from .. import theme
from ..widgets import Card, StatusPill, button, label
from ..workers import run_async

STATES = {"idle": ("Idle", theme.MUTED), "preparing": ("Preparing", theme.WARN), "fighting": ("Fighting", theme.GOOD),
          "coaching": ("Claude is reviewing", theme.INFO), "stopping": ("Stopping", theme.WARN)}


class EvaluationChart(QWidget):
    """Share of points the best settings win against the starting settings, per evaluation."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(170)
        self.points: list[dict] = []
        self.setAccessibleName("Chart of evaluation results against the starting settings")

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        area = QRectF(44, 10, self.width() - 58, self.height() - 38)
        p.setPen(QColor(theme.MUTED))
        for share in (0, 0.5, 1):
            y = area.bottom() - share * area.height()
            p.setPen(QPen(QColor(theme.BORDER), 1, Qt.PenStyle.DashLine if share == 0.5 else Qt.PenStyle.SolidLine))
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y - 9, 38, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{share:.0%}")
        if not self.points:
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "No evaluation yet. The first one comes after 3 generations.")
            return p.end()
        n = len(self.points)
        xy = [QPointF(area.left() + (area.width() * (i + 0.5) / n), area.bottom() - pt["share"] * area.height())
              for i, pt in enumerate(self.points)]
        p.setPen(QPen(QColor(theme.ACCENT), 2.4))
        p.drawPolyline(QPolygonF(xy))
        p.setBrush(QColor(theme.ACCENT))
        for point, pt in zip(xy, self.points):
            p.drawEllipse(point, 4, 4)
            if n <= 14:
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(point.x() - 30, area.bottom() + 4, 60, 18), Qt.AlignmentFlag.AlignCenter,
                           f"gen {pt['generation']}")
                p.setPen(QPen(QColor(theme.ACCENT), 2.4))
        p.end()


class TrainingPage(QWidget):
    def __init__(self, ctx: AppContext, trainer: Trainer):
        super().__init__()
        self.ctx, self.trainer = ctx, trainer
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(label("Training", "PageTitle"))
        root.addWidget(label(
            "Two bots duel with end crystals on an island hanging over a lake of lava, in a private arena on this computer. The fighting is automatic and "
            "runs on a set of numeric settings. Each fighter predicts where the opponent's next "
            "crystal will stand and can block that spot; the prediction is updated from every crystal it sees. Training searches for better settings by having a challenger fight the best so far; "
            "Claude can coach between evaluations by reading the fight statistics, keeping lessons, and proposing "
            "the next challenger. Claude's own model is not retrained, and there is no guarantee of mastery: the "
            "chart shows what was actually measured.", "Muted", wrap=True))

        ctl = Card("Arena")
        row = QHBoxLayout()
        self.pill = StatusPill()
        self.arena_state = label("", wrap=True)
        row.addWidget(self.pill)
        row.addWidget(self.arena_state, 1)
        self.install_btn = button("Install arena")
        self.start_btn = button("Start training", "Primary")
        self.stop_btn = button("Stop", "Danger", "stop")
        self.reset_btn = button("Reset progress")
        self.install_btn.clicked.connect(self._install)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(lambda: self.trainer.stop())
        self.reset_btn.clicked.connect(self._reset)
        for b in (self.install_btn, self.start_btn, self.stop_btn, self.reset_btn):
            row.addWidget(b)
        ctl.body.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        ctl.body.addWidget(self.progress)
        net = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("Train the settings (hand-written tactics)", "settings")
        self.mode.addItem("Train the neural network", "network")
        self.mode.setAccessibleName("What to train")
        self.mode.currentIndexChanged.connect(lambda _i: self._refresh())
        self.pretrain_btn = button("Pre-train in simulator", tip="About four minutes for 40 generations; can be repeated")
        self.pretrain_btn.clicked.connect(self._pretrain)
        net.addWidget(self.mode)
        net.addWidget(self.pretrain_btn)
        net.addStretch(1)
        ctl.body.addLayout(net)
        self.network_info = label("", "Muted", wrap=True, selectable=True)
        ctl.body.addWidget(self.network_info)
        self.eula = QCheckBox(f"I accept the Minecraft EULA ({arena.EULA_URL}) for this local arena server")
        self.coach = QCheckBox("Let Claude coach after each evaluation (uses API tokens, within the cost limit in Settings)")
        ctl.body.addWidget(self.eula)
        ctl.body.addWidget(self.coach)
        self.summary = label("", "Muted", wrap=True, selectable=True)
        ctl.body.addWidget(self.summary)
        self.watch = label("", wrap=True, selectable=True)
        ctl.body.addWidget(self.watch)
        root.addWidget(ctl)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        chart = Card("Measured progress: best settings against the starting settings")
        self.chart = EvaluationChart()
        chart.body.addWidget(self.chart, 1)
        mid.addWidget(chart, 3)
        best = Card("Settings")
        self.table = QTableWidget(len(pvp.SPACE), 3)
        self.table.setHorizontalHeaderLabels(["Setting", "Start", "Best"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setShowGrid(False)
        best.body.addWidget(self.table, 1)
        mid.addWidget(best, 2)
        root.addLayout(mid, 1)

        low = QHBoxLayout()
        low.setSpacing(14)
        log = Card("Training log")
        self.log = QListWidget(objectName="Events")
        self.log.setWordWrap(True)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        log.body.addWidget(self.log, 1)
        lessons = Card("Lessons Claude wrote down")
        self.lessons = QListWidget(objectName="Events")
        self.lessons.setWordWrap(True)
        self.lessons.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lessons.body.addWidget(self.lessons, 1)
        low.addWidget(log, 3)
        low.addWidget(lessons, 2)
        root.addLayout(low, 1)

        trainer.stateChanged.connect(lambda _s: self._refresh())
        trainer.message.connect(self._message)
        trainer.roundFinished.connect(self._round)
        trainer.progressChanged.connect(self._refresh)
        trainer.arena.stateChanged.connect(lambda _s: self._refresh())
        trainer.spectatorsChanged.connect(self._refresh)
        trainer.simProgress.connect(self._sim_progress)
        self.mode.setCurrentIndex(self.mode.findData(trainer.mode))
        self._refresh()

    # ---- actions ----
    def _install(self) -> None:
        self.install_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self._message("Downloading Java 17 (if needed) and Mojang's 1.20.1 server, with checksum verification…")
        run_async(arena.install, on_progress=self._install_progress, on_done=self._installed, on_error=self._install_failed)

    def _install_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setRange(0, total if total > 0 else 0)
        if total > 0:
            self.progress.setValue(done)
        self.arena_state.setText(f"{name}: {done / 1e6:.0f} MB" + (f" of {total / 1e6:.0f} MB" if total > 0 else ""))

    def _installed(self, _path) -> None:
        self.progress.setVisible(False)
        self._message("The arena is installed.")
        self.trainer.arena.refresh()
        self._refresh()

    def _install_failed(self, error: str) -> None:
        self.progress.setVisible(False)
        self._message(f"Installing the arena failed: {error}")
        self._refresh()

    def _start(self) -> None:
        if not self.trainer.arena.eula_accepted():
            if not self.eula.isChecked():
                self._message("Tick the EULA box first: the arena is a Minecraft server and needs your agreement.")
                return
            self.trainer.arena.accept_eula()
        if self.coach.isChecked() and not self.ctx.config.anthropic.model:
            self._message("Coaching needs a Claude model and API key (Settings). Starting without it.")
            self.coach.setChecked(False)
        self.trainer.start(self.coach.isChecked(), self.mode.currentData())

    def _pretrain(self) -> None:
        if self.trainer.pretraining():
            return self.trainer.cancel_pretraining()
        self.progress.setVisible(True)
        self.progress.setRange(0, 40)
        self.progress.setValue(0)
        self.trainer.pretrain(40)
        self._refresh()

    def _sim_progress(self, generation: int, total: int, text: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(generation)
        self.network_info.setText(text.capitalize() + ".")

    def _reset(self) -> None:
        if QMessageBox.question(self, "Reset training progress?",
                                "This deletes the best settings found so far, the trained network, the learned "
                                "opponent habits, the evaluation history and Claude's lessons. It cannot be undone.") == QMessageBox.StandardButton.Yes:
            self.trainer.reset_progress()
            self.log.clear()

    # ---- display ----
    def _message(self, text: str) -> None:
        self.log.insertItem(0, text)
        while self.log.count() > 300:
            self.log.takeItem(self.log.count() - 1)

    def _round(self, r: dict) -> None:
        a, b = r["first"], r["second"]
        who = "challenger" if r["kind"] == "challenge" else "best"
        picks = r.get("choices") or {}
        total = sum(picks.values())
        tactics = (" The network chose: " + ", ".join(f"{k.replace('_', ' ')} {v / total:.0%}" for k, v in
                                                       sorted(picks.items(), key=lambda kv: -kv[1])) + ".") if total else ""
        other = "best" if r["kind"] == "challenge" else "start"
        result = {1.0: f"{who} won", 0.0: f"{other} won", 0.5: "draw"}[r["points"]]
        falls = "".join(f" The {name} fell into the lava." for name, side in ((who, a), (other, b)) if side.get("fell"))
        self._message(f"Round {r['round']} ({r['kind']}): {result} in {a.get('seconds')} s. {who}: {a.get('totemsUsed')} "
                      f"totems used, {a.get('crystalsDetonated')} crystals set off, {a.get('damageTaken', 0):.0f} damage "
                      f"taken. {other}: {b.get('totemsUsed')} totems, {b.get('crystalsDetonated')} crystals, "
                      f"{b.get('damageTaken', 0):.0f} damage. Spots blocked: {a.get('blocksPlaced', 0)} and {b.get('blocksPlaced', 0)}; obsidian placed: "
                      f"{a.get('obsidianPlaced', 0)} and {b.get('obsidianPlaced', 0)}.{falls}{tactics}")

    def _refresh(self) -> None:
        t = self.trainer
        self.pill.set(*STATES[t.state])
        installed, idle = t.arena.installed(), t.state == "idle"
        names = {"missing": "The arena is not installed (about 100 MB: Java 17 and the Minecraft server).",
                 "stopped": "The arena is installed and stopped.", "starting": "The arena server is starting…",
                 "running": f"The arena server is running on 127.0.0.1:{t.port}."}
        if not self.progress.isVisible():
            self.arena_state.setText(names.get(t.arena.state, t.arena.state))
        self.install_btn.setVisible(not installed)
        self.install_btn.setEnabled(not self.progress.isVisible())
        busy_sim = t.pretraining()
        if not busy_sim and self.progress.maximum() == 40 and self.install_btn.isEnabled():
            self.progress.setVisible(False)
        self.start_btn.setEnabled(installed and idle and not busy_sim)
        self.mode.setEnabled(idle and not busy_sim)
        self.pretrain_btn.setEnabled(idle)
        self.pretrain_btn.setText("Cancel pre-training" if busy_sim else
                                  "Pre-train more in simulator" if t.policy else "Pre-train in simulator")
        if not busy_sim:
            if t.policy:
                info = t.sim_info
                self.network_info.setText(
                    f"Network: 12 inputs, 16 hidden units, 5 tactics (293 weights). Pre-trained for "
                    f"{info.get('generations', 0)} generations in the simulator, where it takes "
                    f"{info.get('win_rate', 0):.0%} of the points from the hand-written tactics. The simulator is a "
                    "simplification; the chart below shows how it does in real fights.")
            else:
                self.network_info.setText("No network yet. Pre-training teaches one in a fast simulator of the duel; "
                                          "after that, training tests and fine-tunes it in real fights.")
        self.stop_btn.setEnabled(not idle)
        self.reset_btn.setEnabled(idle and not t.pretraining())
        self.eula.setVisible(installed and not t.arena.eula_accepted())
        self.coach.setEnabled(idle)
        evaluations = t.evaluations()
        self.chart.points = evaluations
        self.chart.update()
        latest = f"latest evaluation {evaluations[-1]['share']:.0%}" if evaluations else "not evaluated yet"
        cost = t.usage.cost(self.ctx.config.anthropic.model) if self.ctx.config.anthropic.model else None
        coach = (f" · coaching used {t.usage.input_tokens + t.usage.output_tokens:,} tokens"
                 + (f" (about ${cost:.4f})" if cost is not None else "")) if t.usage.requests else ""
        hits, seen = t.predicted
        predict = (f" · prediction: {hits / seen:.0%} of {seen:,} opponent crystals stood on one of the three predicted spots"
                   if seen else " · prediction: no opponent crystals seen yet")
        self.summary.setText(f"Generation {t.generation} · {t.rounds_played} rounds fought · {latest}{predict}{coach}")
        if t.arena.state == "running":
            watching = (f" Watching now: {', '.join(t.spectators)}." if t.spectators else "")
            self.watch.setText(
                f"Watch the fights in Minecraft Java 1.20.1: Multiplayer → Direct Connection → 127.0.0.1:{t.port}. "
                f"You are given operator rights and put in spectator mode above the island; rounds are "
                f"announced in chat.{watching}")
        else:
            self.watch.setText("To watch in Minecraft, start training first; the address to join appears here.")
        for row, (key, (_low, _high, start, doc)) in enumerate(pvp.SPACE.items()):
            best = t.champion[key]
            cells = (key, f"{start:g}", f"{best:g}")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(doc)
                if col == 2 and best != start:
                    item.setForeground(QColor(theme.ACCENT_HI))
                self.table.setItem(row, col, item)
        lessons = t.lessons()
        if self.lessons.count() != len(lessons):
            self.lessons.clear()
            self.lessons.addItems(list(reversed(lessons)))
