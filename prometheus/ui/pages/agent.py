"""Agent page: give a goal, control the run, and inspect everything it did and why."""
from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...agent.runner import AgentRunner
from ...context import AppContext
from .. import theme
from ..widgets import Card, StatusPill, button, label

# kind -> (short label, colour)
KINDS = {
    "goal": ("GOAL", theme.ACCENT_HI), "user": ("YOU", theme.ACCENT_HI),
    "observation": ("OBSERVE", theme.INFO), "reasoning": ("REASONING", "#b79cf5"), "note": ("PLAN", theme.TEXT),
    "tool_call": ("ACTION", theme.WARN), "tool_result": ("RESULT", theme.GOOD), "tool_error": ("FAILED", theme.BAD),
    "api": ("API", theme.MUTED), "status": ("STATUS", theme.MUTED), "approval": ("APPROVAL", theme.WARN),
    "chat": ("CHAT", theme.INFO), "error": ("ERROR", theme.BAD),
}
FILTERS = {
    "Everything": None,
    "Plans and reasoning": {"goal", "user", "note", "reasoning", "status", "error"},
    "Actions and results": {"tool_call", "tool_result", "tool_error", "approval"},
    "Observations": {"observation", "chat"},
    "Failures": {"tool_error", "error"},
    "API usage": {"api"},
}
STATE_VIEW = {"idle": ("Idle", theme.MUTED), "starting": ("Starting", theme.WARN),
              "running": ("Running", theme.GOOD), "paused": ("Paused", theme.WARN)}


def fill_tree(parent, value) -> None:
    """Show JSON as a collapsible tree."""
    items = value.items() if isinstance(value, dict) else enumerate(value)
    for key, child in items:
        node = QTreeWidgetItem(parent, [str(key), ""])
        if isinstance(child, (dict, list)):
            node.setText(1, f"{len(child)} {'fields' if isinstance(child, dict) else 'items'}")
            node.setForeground(1, QColor(theme.MUTED))
            fill_tree(node, child)
        else:
            node.setText(1, json.dumps(child, ensure_ascii=False) if not isinstance(child, str) else child)


class AgentPage(QWidget):
    def __init__(self, ctx: AppContext, runner: AgentRunner):
        super().__init__()
        self.ctx, self.runner = ctx, runner
        self._events: list[dict] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(label("Agent", "PageTitle"))

        ctl = Card("Goal")
        row = QHBoxLayout()
        self.goal = QLineEdit()
        self.goal.setAccessibleName("Goal for the agent")
        self.goal.returnPressed.connect(self._submit)
        self.start_btn = button("Start", "Primary", tip="Start working on this goal (Enter)")
        self.pause_btn = button("Pause", tip="Hold before the next request to Claude")
        self.stop_btn = button("Stop", "Danger", "stop", "Stop the run and halt the bot's movement")
        self.start_btn.clicked.connect(self._submit)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.stop_btn.clicked.connect(lambda: self.runner.stop())
        row.addWidget(self.goal, 1)
        for b in (self.start_btn, self.pause_btn, self.stop_btn):
            row.addWidget(b)
        ctl.body.addLayout(row)
        info = QHBoxLayout()
        self.pill = StatusPill()
        self.current = label("", wrap=True)
        info.addWidget(self.pill)
        info.addWidget(self.current, 1)
        ctl.body.addLayout(info)
        self.usage = label("", "Muted", selectable=True)
        ctl.body.addWidget(self.usage)
        self.plan = label("", wrap=True)
        self.plan.setTextFormat(Qt.TextFormat.RichText)
        self.plan.setVisible(False)
        ctl.body.addWidget(self.plan)
        root.addWidget(ctl)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the activity log")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)
        self.filter = QComboBox()
        self.filter.addItems(list(FILTERS))
        self.filter.currentIndexChanged.connect(self._refill)
        self.filter.setAccessibleName("Activity filter")
        self.history = QComboBox()
        self.history.setAccessibleName("Run to display")
        self.history.setMinimumWidth(260)
        self.history.activated.connect(self._pick_run)
        self.new_btn = button("New conversation", tip="Start the next goal without the earlier conversation")
        self.new_btn.clicked.connect(self._new_conversation)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.filter)
        bar.addWidget(self.history)
        bar.addWidget(self.new_btn)
        root.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        left = Card("Activity timeline")
        self.timeline = QListWidget(objectName="Events")
        self.timeline.setWordWrap(True)
        self.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline.currentItemChanged.connect(self._show_detail)
        left.body.addWidget(self.timeline, 1)
        right = Card("Details")
        self.detail_title = label("Select an entry to inspect it.", wrap=True)
        self.detail_body = QPlainTextEdit()
        self.detail_body.setReadOnly(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Field", "Value"])
        self.tree.setColumnWidth(0, 190)
        self.tree.setAlternatingRowColors(False)
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(self.detail_body)
        vsplit.addWidget(self.tree)
        vsplit.setSizes([160, 260])
        right.body.addWidget(self.detail_title)
        right.body.addWidget(vsplit, 1)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([560, 460])
        root.addWidget(split, 1)
        root.addWidget(label("Reasoning entries are the summaries the Claude API returns; Claude's private reasoning "
                             "is not available to any application. Plans are what Claude wrote for you before acting.",
                             "Muted", wrap=True))

        runner.event.connect(self._add)
        runner.stateChanged.connect(self._state)
        runner.usageChanged.connect(self._usage)
        runner.approvalRequested.connect(self._approve)
        runner.planChanged.connect(self._plan)
        self._load_history()
        ctx.configChanged.connect(self._usage)
        self._state(runner.state)
        self._usage()

    # ---- controls ----
    def _submit(self) -> None:
        text = self.goal.text().strip()
        if not text:
            return
        if self.runner.state == "idle":
            self.history.setCurrentIndex(0)
            self._events.clear()
            self.timeline.clear()
            self.runner.start(text)
        else:
            self.runner.add_instruction(text)
        self.goal.clear()

    def _plan(self) -> None:
        marks = {"pending": ("○", theme.MUTED), "in_progress": ("▶", theme.ACCENT_HI),
                 "done": ("✓", theme.GOOD), "failed": ("✗", theme.BAD)}
        rows = []
        for step in self.runner.plan:
            mark, colour = marks[step["status"]]
            title = step["title"].replace("&", "&amp;").replace("<", "&lt;")
            rows.append(f'<span style="color:{colour}">{mark}&nbsp; {title}</span>')
        self.plan.setText("<b>Claude's plan</b><br>" + "<br>".join(rows))
        self.plan.setVisible(bool(rows))

    def _load_history(self) -> None:
        self.history.clear()
        self.history.addItem("Current run", None)
        try:
            runs = self.runner.store.runs(40)
        except Exception:
            runs = []
        for run in runs:
            when = time.strftime("%d %b %H:%M", time.localtime(run["started"]))
            self.history.addItem(f"{when} · {run['status']} · {run['goal'][:40]}", run["id"])

    def _pick_run(self, index: int) -> None:
        run_id = self.history.itemData(index)
        if run_id is None or self.runner.state != "idle":
            self.history.setCurrentIndex(0)
            return
        try:
            self._events = self.runner.store.events(run_id)
        except Exception:
            self._events = []
        self._refill()

    def _new_conversation(self) -> None:
        self.runner.reset_conversation()
        self._events.clear()
        self.timeline.clear()
        self.history.setCurrentIndex(0)
        self.current.setText("New conversation. The next goal starts without the earlier context.")

    def _toggle_pause(self) -> None:
        self.runner.resume() if self.runner.state == "paused" else self.runner.pause()

    def _state(self, state: str) -> None:
        text, color = STATE_VIEW[state]
        self.pill.set(text, color)
        idle = state == "idle"
        self.start_btn.setText("Start" if idle else "Send instruction")
        self.goal.setPlaceholderText('What should the agent do? For example: "Collect 8 logs"' if idle
                                     else "Add an instruction for the running agent")
        self.pause_btn.setEnabled(state in ("running", "paused"))
        self.pause_btn.setText("Resume" if state == "paused" else "Pause")
        self.stop_btn.setEnabled(not idle)
        self.new_btn.setEnabled(idle)
        self.history.setEnabled(idle)
        if idle:
            self._load_history()
        if idle:
            self.current.setText(self.runner.last_outcome.split("\n")[0][:300] if self.runner.last_outcome
                                 else "No run yet.")
        else:
            self.current.setText(f"Goal: {self.runner.goal}")

    def _usage(self) -> None:
        r, u, cfg = self.runner, self.runner.usage, self.ctx.config.agent
        if not r.model:
            return self.usage.setText("No Claude model configured. Add your API key in Settings.")
        cost = r.cost
        money = (f"about ${cost:.4f} of ${cfg.max_cost_usd:.2f} limit" if cost is not None
                 else "no price is known for this model, so only the step limit applies")
        self.usage.setText(
            f"{r.model} · step {r.steps} of {cfg.max_steps} · {u.input_tokens:,} input / {u.output_tokens:,} output "
            f"tokens · cache read {u.cache_read_tokens:,} · {money}"
            + (f" · last response {u.last_latency_s:.1f} s" if u.requests else ""))

    def _approve(self, description: str) -> None:
        answer = QMessageBox.question(self, "Allow this action?", f"{description}\n\nAllow it?")
        self.runner.resolve_approval(answer == QMessageBox.StandardButton.Yes)

    # ---- timeline ----
    def _matches(self, e: dict) -> bool:
        kinds = FILTERS[self.filter.currentText()]
        if kinds is not None and e["kind"] not in kinds:
            return False
        needle = self.search.text().lower()
        return not needle or needle in (e["title"] + " " + e["body"] + " " + json.dumps(e["data"], default=str)).lower()

    def _item(self, index: int, e: dict) -> QListWidgetItem:
        tag, color = KINDS.get(e["kind"], (e["kind"].upper(), theme.TEXT))
        first = e["body"].strip().split("\n")[0]
        text = f"{time.strftime('%H:%M:%S', time.localtime(e['ts']))}  {tag}  {e['title']}"
        if first and e["kind"] in ("note", "reasoning", "goal", "user", "tool_call", "tool_result", "tool_error",
                                   "status", "error", "chat"):
            text += f"\n{first[:220]}"
        item = QListWidgetItem(text)
        item.setForeground(QColor(color))
        item.setData(Qt.ItemDataRole.UserRole, index)
        return item

    def _add(self, e: dict) -> None:
        if e["kind"] == "goal" and self.history.currentIndex() != 0:      # a run started from in-game chat
            self.history.setCurrentIndex(0)
            self._events.clear()
            self.timeline.clear()
        self._events.append(e)
        if self._matches(e):
            at_end = self.timeline.currentRow() in (-1, self.timeline.count() - 1)
            self.timeline.addItem(self._item(len(self._events) - 1, e))
            if at_end:
                self.timeline.scrollToBottom()

    def _refill(self) -> None:
        self.timeline.clear()
        for i, e in enumerate(self._events):
            if self._matches(e):
                self.timeline.addItem(self._item(i, e))
        self.timeline.scrollToBottom()

    def _show_detail(self, item: QListWidgetItem | None) -> None:
        self.tree.clear()
        if item is None:
            self.detail_title.setText("Select an entry to inspect it.")
            return self.detail_body.clear()
        e = self._events[item.data(Qt.ItemDataRole.UserRole)]
        self.detail_title.setText(f"{KINDS.get(e['kind'], (e['kind'],))[0]} · {e['title']}")
        self.detail_body.setPlainText(e["body"])
        if e["data"]:
            fill_tree(self.tree.invisibleRootItem(), e["data"])
            for i in range(self.tree.topLevelItemCount()):   # first level open, deeper levels collapsed
                self.tree.topLevelItem(i).setExpanded(True)
