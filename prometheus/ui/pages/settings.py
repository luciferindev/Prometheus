"""Settings for what exists today: server, account, Claude credentials, files."""
from __future__ import annotations

import copy

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...context import AppContext
from ...core import claude_api, config as cfg, paths
from ...core.secrets import ANTHROPIC_KEY, SecretStoreError
from .. import theme
from ..widgets import Card, button, label
from ..workers import run_async


class SettingsPage(QWidget):
    setupRequested = Signal()

    def __init__(self, ctx: AppContext, store=None):
        super().__init__()
        self.ctx = ctx
        self.store = store
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        host = QWidget()
        scroll.setWidget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(label("Settings", "PageTitle"))

        # Server + account
        srv = Card("Minecraft connection")
        form = QFormLayout()
        form.setSpacing(10)
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.public = QCheckBox("Allow servers outside this machine and private networks")
        self.public.clicked.connect(self._confirm_public)
        self.mode = QComboBox()
        self.mode.addItem("Microsoft account", "microsoft")
        self.mode.addItem("Offline player", "offline")
        self.username = QLineEdit()
        form.addRow("Server address", self.host)
        form.addRow("Port", self.port)
        form.addRow("", self.public)
        form.addRow("Sign-in", self.mode)
        form.addRow("Account label / player name", self.username)
        srv.body.addLayout(form)
        row = QHBoxLayout()
        save = button("Save", "Primary")
        save.clicked.connect(self._save_connection)
        signout = button("Sign out of Microsoft", tip="Delete the cached Microsoft tokens for the agent")
        signout.clicked.connect(self._sign_out)
        self.conn_status = label("", wrap=True)
        row.addWidget(save)
        row.addWidget(signout)
        row.addWidget(self.conn_status, 1)
        srv.body.addLayout(row)
        root.addWidget(srv)

        # Claude
        claude = Card("Claude API")
        self.key_state = label("", "Muted", wrap=True)
        claude.body.addWidget(self.key_state)
        cform = QFormLayout()
        cform.setSpacing(10)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Paste a new key to replace the stored one")
        self.model = QComboBox()
        cform.addRow("API key", self.key)
        cform.addRow("Model", self.model)
        claude.body.addLayout(cform)
        crow = QHBoxLayout()
        self.validate = button("Validate and save", "Primary")
        self.validate.clicked.connect(self._validate_key)
        remove = button("Remove stored key")
        remove.clicked.connect(self._remove_key)
        self.claude_status = label("", wrap=True)
        crow.addWidget(self.validate)
        crow.addWidget(remove)
        crow.addWidget(self.claude_status, 1)
        claude.body.addLayout(crow)
        root.addWidget(claude)

        # Agent limits
        agent = Card("Agent behaviour and limits")
        aform = QFormLayout()
        aform.setSpacing(10)
        self.effort = QComboBox()
        for value, text in (("low", "Low: fastest and cheapest"), ("medium", "Medium"), ("high", "High: most thorough")):
            self.effort.addItem(text, value)
        self.max_cost = QDoubleSpinBox()
        self.max_cost.setRange(0.01, 1000)
        self.max_cost.setDecimals(2)
        self.max_cost.setPrefix("$ ")
        self.max_cost.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.max_steps = QSpinBox()
        self.max_steps.setRange(1, 1000)
        self.max_steps.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.confirm_break = QCheckBox("Ask me before the agent mines blocks")
        self.movement = QComboBox()
        self.movement.addItem("Free: may dig and pillar with dirt or cobblestone to get around", "free")
        self.movement.addItem("Careful: only clears leaves and vines, never places blocks", "careful")
        self.chat_control = QCheckBox("Take goals and instructions from this player's in-game chat")
        self.chat_operator = QLineEdit()
        self.chat_operator.setPlaceholderText("Your exact in-game name")
        aform.addRow("Reasoning effort", self.effort)
        aform.addRow("Cost limit per run", self.max_cost)
        aform.addRow("Step limit per run", self.max_steps)
        aform.addRow("", self.confirm_break)
        aform.addRow("Movement", self.movement)
        aform.addRow("", self.chat_control)
        aform.addRow("Chat operator", self.chat_operator)
        agent.body.addLayout(aform)
        arow = QHBoxLayout()
        asave = button("Save", "Primary")
        asave.clicked.connect(self._save_agent)
        self.agent_status = label("", wrap=True)
        arow.addWidget(asave)
        arow.addWidget(self.agent_status, 1)
        agent.body.addLayout(arow)
        agent.body.addWidget(label("With chat control on, every chat line from that player is a goal (when the agent "
                                   "is idle) or an instruction (while it works); \"stop\" stops it, and the agent "
                                   "answers in chat. On offline-mode servers anyone can join under any name, so only "
                                   "use this on servers you control.", "Muted", wrap=True))
        mrow2 = QHBoxLayout()
        clear_mem = button("Clear the agent's saved notes")
        clear_mem.clicked.connect(lambda: self._clear("notes"))
        clear_hist = button("Clear run history")
        clear_hist.clicked.connect(lambda: self._clear("history"))
        mrow2.addWidget(clear_mem)
        mrow2.addWidget(clear_hist)
        mrow2.addStretch(1)
        agent.body.addLayout(mrow2)
        agent.body.addWidget(label("Costs are estimates from token counts and list prices. A run stops when either "
                                   "limit is reached. Limits apply from the agent's next step.", "Muted", wrap=True))
        root.addWidget(agent)

        # Files and setup
        misc = Card("Setup and files")
        self.instance = label("", "Muted", wrap=True, selectable=True)
        misc.body.addWidget(self.instance)
        mrow = QHBoxLayout()
        wizard = button("Run setup again", icon="wand")
        wizard.clicked.connect(self.setupRequested.emit)
        logs = button("Open log folder")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir()))))
        conf = button("Open settings folder")
        conf.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.config_dir()))))
        for b in (wizard, logs, conf):
            mrow.addWidget(b)
        mrow.addStretch(1)
        misc.body.addLayout(mrow)
        misc.body.addWidget(label("Logs redact API keys and tokens before anything is written.", "Muted"))
        root.addWidget(misc)
        root.addStretch(1)

        ctx.configChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        c = self.ctx.config
        self.host.setText(c.server.host)
        self.port.setValue(c.server.port)
        self.public.setChecked(c.server.allow_public)
        self.mode.setCurrentIndex(self.mode.findData(c.auth.mode))
        self.username.setText(c.auth.username)
        self.effort.setCurrentIndex(self.effort.findData(c.agent.effort))
        self.max_cost.setValue(c.agent.max_cost_usd)
        self.max_steps.setValue(c.agent.max_steps)
        self.confirm_break.setChecked(c.agent.confirm_block_breaking)
        self.movement.setCurrentIndex(self.movement.findData(c.agent.movement))
        self.chat_control.setChecked(c.agent.chat_control)
        self.chat_operator.setText(c.agent.chat_operator)
        self.instance.setText(f"Minecraft instance: {c.minecraft.instance_dir or 'none selected'}")
        if self.model.findData(c.anthropic.model) < 0:
            self.model.clear()
            if c.anthropic.model:
                self.model.addItem(c.anthropic.model, c.anthropic.model)
        self.model.setCurrentIndex(self.model.findData(c.anthropic.model))
        run_async(self.ctx.secrets.availability, on_done=lambda r: self.key_state.setText(
            f"Keys are stored in: {r[1]}" if r[0] else r[1]))

    def _say(self, target, text: str, color: str) -> None:
        target.setStyleSheet(f"color: {color};")
        target.setText(text)

    def _confirm_public(self, checked: bool) -> None:
        if checked and QMessageBox.question(
                self, "Allow public servers?",
                "An autonomous agent on a public server can affect other players and may break that server's "
                "rules. Only enable this for servers you run or are allowed to automate on.\n\nAllow it?"
        ) != QMessageBox.StandardButton.Yes:
            self.public.setChecked(False)

    def _save_connection(self) -> None:
        new = copy.deepcopy(self.ctx.config)
        new.server.host, new.server.port = self.host.text().strip(), self.port.value()
        new.server.allow_public = self.public.isChecked()
        new.auth.mode, new.auth.username = self.mode.currentData(), self.username.text().strip()
        try:
            new.validate()
            self.ctx.config = new
            self.ctx.save()
        except (cfg.ConfigError, OSError) as exc:
            return self._say(self.conn_status, str(exc), theme.BAD)
        self._say(self.conn_status, "Saved. Applies to the next connection.", theme.GOOD)

    def _save_agent(self) -> None:
        new = copy.deepcopy(self.ctx.config)
        new.agent.effort = self.effort.currentData()
        new.agent.max_cost_usd = round(self.max_cost.value(), 2)
        new.agent.max_steps = self.max_steps.value()
        new.agent.confirm_block_breaking = self.confirm_break.isChecked()
        new.agent.movement = self.movement.currentData()
        new.agent.chat_control = self.chat_control.isChecked()
        new.agent.chat_operator = self.chat_operator.text().strip()
        try:
            new.validate()
            self.ctx.config = new
            self.ctx.save()
        except (cfg.ConfigError, OSError) as exc:
            return self._say(self.agent_status, str(exc), theme.BAD)
        self._say(self.agent_status, "Saved.", theme.GOOD)

    def _clear(self, what: str) -> None:
        if self.store is None:
            return
        text = ("Delete every note the agent saved about your servers?" if what == "notes"
                else "Delete the stored history of all agent runs?")
        if QMessageBox.question(self, "Delete?", text + " This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.clear_memory() if what == "notes" else self.store.delete_all()
        except Exception as exc:
            return self._say(self.agent_status, str(exc), theme.BAD)
        self._say(self.agent_status, "Deleted.", theme.GOOD)

    def _sign_out(self) -> None:
        self.ctx.bridge.call(
            "auth.signOut", {},
            lambda r: self._say(self.conn_status, f"Removed {r.get('removed', 0)} cached token files.", theme.GOOD),
            lambda m: self._say(self.conn_status, m, theme.BAD))

    def _validate_key(self) -> None:
        typed = self.key.text().strip()
        self.validate.setEnabled(False)
        self._say(self.claude_status, "Asking Anthropic…", theme.MUTED)

        def work() -> tuple[list[claude_api.ModelInfo], bool]:
            key = typed or self.ctx.secrets.get(ANTHROPIC_KEY) or ""
            models = claude_api.list_models(key)
            persisted = self.ctx.secrets.set(ANTHROPIC_KEY, typed) if typed else True
            return models, persisted

        run_async(work, on_done=self._validated, on_error=self._key_failed)

    def _validated(self, result) -> None:
        models, persisted = result
        self.validate.setEnabled(True)
        chosen = claude_api.default_model(models, self.model.currentData() or self.ctx.config.anthropic.model)
        self.model.clear()
        for m in models:
            self.model.addItem(f"{m.display_name}  ({m.id})", m.id)
        self.model.setCurrentIndex(self.model.findData(chosen))
        note = "" if persisted else " No keyring is available, so the key is kept for this session only."
        try:
            self.ctx.config.anthropic.model = chosen
            self.ctx.save()
        except (SecretStoreError, cfg.ConfigError, OSError) as exc:
            return self._say(self.claude_status, str(exc), theme.BAD)
        self.key.clear()
        self._say(self.claude_status, f"Key accepted; using {chosen}. Pick another model and press the button "
                                      f"again to change it.{note}", theme.GOOD)

    def _key_failed(self, message: str) -> None:
        self.validate.setEnabled(True)
        self._say(self.claude_status, message, theme.BAD)

    def _remove_key(self) -> None:
        if QMessageBox.question(self, "Remove the API key?",
                                "This deletes the Anthropic API key from your keyring.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ctx.secrets.delete(ANTHROPIC_KEY)
            self.ctx.config.anthropic.model = ""
            self.ctx.save()
        except (SecretStoreError, OSError) as exc:
            return self._say(self.claude_status, str(exc), theme.BAD)
        self._say(self.claude_status, "The stored key was removed.", theme.GOOD)
