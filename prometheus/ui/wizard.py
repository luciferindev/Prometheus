"""First-run setup wizard. Every status shown here comes from a real check."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLineEdit, QMessageBox, QRadioButton, QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget, QWizard, QWizardPage)

from ..context import AppContext
from ..core import claude_api, compat, config as cfg, installer, minecraft, netpolicy, paths, toolchain
from ..core.secrets import ANTHROPIC_KEY
from . import theme
from .widgets import CheckRow, DeviceCodeDialog, button, label
from .workers import run_async

SIGN_IN_TIMEOUT_MS = 16 * 60 * 1000


class Page(QWizardPage):
    def __init__(self, wizard: "SetupWizard", title: str, intro: str):
        super().__init__()
        self.wiz = wizard
        self.ctx = wizard.ctx
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(8, 4, 8, 4)
        self.lay.setSpacing(12)
        self.lay.addWidget(label(title, "PageTitle"))
        self.lay.addWidget(label(intro, "Muted", wrap=True))

    def rows_area(self) -> QVBoxLayout:
        """A scrollable column for check rows."""
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(0)
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        self.lay.addWidget(scroll, 1)
        return col

    @staticmethod
    def clear(col: QVBoxLayout) -> None:
        while col.count() > 1:
            item = col.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


class WelcomePage(Page):
    def __init__(self, wizard):
        super().__init__(wizard, "Welcome to PROMETHEUS",
                         "This guide connects PROMETHEUS to your vanilla Minecraft Java 1.20.1 world and to the "
                         "Claude API. Nothing here needs a terminal, and nothing is installed system-wide.")
        for text in ("Check Java, Node.js and the Minecraft bridge, and repair what is missing",
                     "Find your Minecraft installation and check it has version 1.20.1",
                     "Choose and test the server the agent will join",
                     "Set up the agent's Minecraft account",
                     "Add your Anthropic API key and pick a model"):
            self.lay.addWidget(label(f"  •  {text}"))
        self.lay.addStretch(1)
        self.lay.addWidget(label("Your API key is stored in the system keyring. Microsoft sign-in happens on "
                                 "microsoft.com; PROMETHEUS never asks for that password.", "Muted", wrap=True))


class SystemPage(Page):
    REQUIRED = {"python", "node", "npm", "bridge"}

    def __init__(self, wizard):
        super().__init__(wizard, "System check",
                         "Missing tools can be installed into your home folder "
                         f"({paths.runtimes_dir()}) with verified downloads. No administrator rights are used.")
        self.col = self.rows_area()
        self.status = label("", "Muted")
        recheck = button("Check again")
        recheck.clicked.connect(self.recheck)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(recheck)
        self.lay.addLayout(row)
        self._ready = False
        self._proc: QProcess | None = None

    def initializePage(self) -> None:
        self.recheck()

    def isComplete(self) -> bool:
        return self._ready

    def recheck(self) -> None:
        self.status.setText("Checking…")
        run_async(toolchain.run_all, self.ctx.secrets, on_done=self._show,
                  on_error=lambda e: self.status.setText(f"Check failed: {e}"))

    def _show(self, checks: list[toolchain.Check]) -> None:
        self.clear(self.col)
        for i, chk in enumerate(checks):
            row = CheckRow(chk.title, chk.status, chk.detail, chk.hint, chk.repair_label if chk.repair else "")
            if row.action is not None:
                row.action.clicked.connect(lambda _=False, c=chk, r=row: self._repair(c, r))
            self.col.insertWidget(i, row)
        missing = [c.title for c in checks if c.key in self.REQUIRED and c.status == toolchain.MISSING]
        self._ready = not missing
        self.status.setText("Everything required is ready." if self._ready
                            else "Still needed: " + ", ".join(missing))
        self.completeChanged.emit()

    def _repair(self, chk: toolchain.Check, row: CheckRow) -> None:
        row.action.setEnabled(False)
        if chk.repair == "bridge":
            try:
                self._run_commands(toolchain.bridge_install_commands(), row)
            except RuntimeError as exc:
                row.set_failed(str(exc))
            return
        row.set_progress(0, 0, "Looking up the current release…")
        run_async(installer.install_runtime, chk.repair, on_progress=row.set_progress,
                  on_done=lambda _: self.recheck(), on_error=row.set_failed)

    def _run_commands(self, commands: list[list[str]], row: CheckRow) -> None:
        if not commands:
            self.recheck()
            return
        argv, rest = commands[0], commands[1:]
        proc = QProcess(self)
        env = QProcessEnvironment()
        for key, value in toolchain.tool_env().items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(str(paths.bridge_dir()))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        step = "Downloading packages from the npm registry…" if "ci" in argv else "Building the bridge…"
        row.set_progress(0, 0, step)

        def finished(code: int, _status) -> None:
            output = bytes(proc.readAll().data()).decode(errors="replace").strip()
            proc.deleteLater()
            self._proc = None
            if code != 0:
                tail = "\n".join(output.splitlines()[-6:])
                row.set_failed(f"{step.rstrip('…')} failed (exit code {code}).\n{tail}")
            else:
                self._run_commands(rest, row)

        proc.finished.connect(finished)
        proc.errorOccurred.connect(
            lambda err: row.set_failed(f"Could not run npm: {proc.errorString()}")
            if err == QProcess.ProcessError.FailedToStart else None)
        self._proc = proc
        proc.start(argv[0], argv[1:])


class InstancePage(Page):
    def __init__(self, wizard):
        super().__init__(wizard, "Minecraft instance",
                         "Pick the installation you play on: a .minecraft folder or a Prism/MultiMC "
                         "instance folder. PROMETHEUS checks that it has vanilla Minecraft 1.20.1.")
        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setAccessibleName("Minecraft instance")
        self.combo.currentIndexChanged.connect(self._selected)
        browse = button("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.combo, 1)
        row.addWidget(browse)
        self.lay.addLayout(row)
        self.col = self.rows_area()
        self.status = label("", "Muted", wrap=True)
        self.lay.addWidget(self.status)
        self._inst: minecraft.Instance | None = None
        self._token = 0

    def initializePage(self) -> None:
        self.status.setText("Looking for Minecraft installations…")
        run_async(minecraft.discover, on_done=self._discovered,
                  on_error=lambda e: self.status.setText(f"Search failed: {e}"))

    def _discovered(self, found: list[minecraft.Instance]) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        for inst in found:
            self.combo.addItem(f"{inst.name}  —  {inst.root}", str(inst.root))
        saved = self.wiz.draft.minecraft.instance_dir
        if saved and self.combo.findData(saved) < 0 and Path(saved).is_dir():
            self.combo.addItem(saved, saved)
        if saved:
            self.combo.setCurrentIndex(max(self.combo.findData(saved), 0))
        self.combo.blockSignals(False)
        if self.combo.count():
            self._selected()
        else:
            self.status.setText("No Minecraft installation was found in the usual places. Browse to your "
                                ".minecraft folder or launcher instance. You can also continue without one "
                                "if the game runs on another machine; only the server address is required.")

    def _browse(self) -> None:
        start = str(Path.home() / ".minecraft") if (Path.home() / ".minecraft").is_dir() else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select a Minecraft instance folder", start)
        if not chosen:
            return
        if self.combo.findData(chosen) < 0:
            self.combo.addItem(chosen, chosen)
        self.combo.setCurrentIndex(self.combo.findData(chosen))
        self._selected()

    def _selected(self) -> None:
        path = self.combo.currentData()
        if not path:
            return
        self._token += 1
        token = self._token
        self.status.setText("Reading the instance…")

        def analyse(p: str):
            inst = minecraft.inspect(Path(p))
            return inst, compat.evaluate(inst)

        run_async(analyse, path, on_done=lambda res: self._show(token, *res),
                  on_error=lambda e: self.status.setText(f"Could not read the instance: {e}"))

    def _show(self, token: int, inst, findings: list[compat.Finding]) -> None:
        if token != self._token:
            return
        self._inst = inst
        self.clear(self.col)
        for i, f in enumerate(findings):
            self.col.insertWidget(i, CheckRow(f.title, f.level, f.detail))
        self.status.setText("This folder cannot be used as it is. You can still continue: only the server "
                            "address is required." if compat.blocking(findings) else "")

    def validatePage(self) -> bool:
        self.wiz.draft.minecraft.instance_dir = self.combo.currentData() or ""
        return True


class ServerPage(Page):
    def __init__(self, wizard):
        super().__init__(wizard, "Minecraft server",
                         "The agent joins a server as its own player. For single-player, start your world and "
                         "use \"Open to LAN\", or run a local 1.20.1 server, then enter its address here.")
        form = QFormLayout()
        form.setSpacing(10)
        self.host = QLineEdit(wizard.draft.server.host)
        self.host.setAccessibleName("Server address")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.port.setValue(wizard.draft.server.port)
        self.port.setAccessibleName("Server port")
        self.public = QCheckBox("Allow servers outside this machine and private networks")
        self.public.setChecked(wizard.draft.server.allow_public)
        self.public.clicked.connect(self._confirm_public)
        form.addRow("Address", self.host)
        form.addRow("Port", self.port)
        form.addRow("", self.public)
        self.lay.addLayout(form)
        row = QHBoxLayout()
        self.test = button("Test connection", "Primary")
        self.test.clicked.connect(self._test)
        row.addWidget(self.test)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.result = label("", wrap=True, selectable=True)
        self.lay.addWidget(self.result)
        self.lay.addStretch(1)

    def _confirm_public(self, checked: bool) -> None:
        if checked and QMessageBox.question(
                self, "Allow public servers?",
                "An autonomous agent on a public server can affect other players and may break that server's "
                "rules. Only enable this for servers you run or are allowed to automate on.\n\nAllow it?"
        ) != QMessageBox.StandardButton.Yes:
            self.public.setChecked(False)

    def _say(self, text: str, color: str) -> None:
        self.result.setStyleSheet(f"color: {color};")
        self.result.setText(text)
        self.test.setEnabled(True)

    def _test(self) -> None:
        host, port = self.host.text().strip(), self.port.value()
        if not host:
            return self._say("Enter the server address first.", theme.BAD)
        self.test.setEnabled(False)
        self._say("Testing…", theme.MUTED)
        self.test.setEnabled(False)
        run_async(netpolicy.check_host, host, self.public.isChecked(),
                  on_done=lambda _: self.ctx.bridge.call("server.ping", {"host": host, "port": port},
                                                         self._ok, self._fail),
                  on_error=self._fail)

    def _ok(self, res: dict) -> None:
        text = (f"Server reachable: {res.get('versionName')} · {res.get('playersOnline')}/{res.get('playersMax')} "
                f"players · {res.get('latencyMs')} ms\n{res.get('motd', '')}")
        if res.get("protocol") != 763:
            self._say(text + f"\nThis server speaks protocol {res.get('protocol')}, not 1.20.1 (763). "
                             "The agent will not be able to join it.", theme.WARN)
        else:
            self._say(text, theme.GOOD)

    def _fail(self, message: str) -> None:
        self._say(f"{message}\nCheck that the world is running and opened to LAN (or the server is started), "
                  "and that the port matches the one shown in the game chat or server.properties.", theme.BAD)

    def validatePage(self) -> bool:
        d = self.wiz.draft.server
        d.host, d.port, d.allow_public = self.host.text().strip(), self.port.value(), self.public.isChecked()
        if not d.host:
            self._say("Enter the server address first.", theme.BAD)
            return False
        return True


class AccountPage(Page):
    def __init__(self, wizard):
        super().__init__(wizard, "The agent's Minecraft account",
                         "The agent needs its own player. Use a Microsoft account that owns Minecraft: Java "
                         "Edition, or an offline name for servers running with online-mode=false.")
        self.ms = QRadioButton("Microsoft account (required for online-mode servers and LAN worlds)")
        self.offline = QRadioButton("Offline player (only works when the server has online-mode=false)")
        group = QButtonGroup(self)
        group.addButton(self.ms)
        group.addButton(self.offline)
        (self.ms if wizard.draft.auth.mode == "microsoft" else self.offline).setChecked(True)
        self.lay.addWidget(self.ms)
        self.lay.addWidget(self.offline)
        form = QFormLayout()
        self.name_label = label("")
        self.name = QLineEdit(wizard.draft.auth.username)
        form.addRow(self.name_label, self.name)
        self.lay.addLayout(form)
        row = QHBoxLayout()
        self.sign_in = button("Sign in with Microsoft", "Primary")
        self.sign_in.clicked.connect(self._sign_in)
        row.addWidget(self.sign_in)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.status = label("", wrap=True)
        self.lay.addWidget(self.status)
        self.lay.addStretch(1)
        self.ms.toggled.connect(self._mode_changed)
        self._dialog: DeviceCodeDialog | None = None
        self.ctx.bridge.notification.connect(self._on_notification)
        self._mode_changed()

    def _mode_changed(self) -> None:
        ms = self.ms.isChecked()
        self.name_label.setText("Account label" if ms else "Player name")
        self.name.setPlaceholderText("Any name for this sign-in, e.g. the account's gamertag" if ms
                                     else "3-16 letters, digits or underscores")
        self.sign_in.setVisible(ms)
        self.status.setText("You can sign in now, or when you first connect." if ms else "")
        self.status.setStyleSheet(f"color: {theme.MUTED};")

    def _sign_in(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.status.setStyleSheet(f"color: {theme.BAD};")
            return self.status.setText("Enter an account label first.")
        self.sign_in.setEnabled(False)
        self.status.setStyleSheet(f"color: {theme.MUTED};")
        self.status.setText("Contacting Microsoft…")
        self.ctx.bridge.call("auth.signIn", {"username": name}, self._signed_in, self._sign_in_failed,
                             timeout_ms=SIGN_IN_TIMEOUT_MS)

    def _on_notification(self, method: str, params: dict) -> None:
        if method != "auth.deviceCode" or self.wiz.currentPage() is not self:
            return
        self._close_dialog()
        self._dialog = DeviceCodeDialog(self, params["userCode"], params["verificationUri"], params["expiresIn"])
        self._dialog.show()

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None

    def _signed_in(self, res: dict) -> None:
        self._close_dialog()
        self.sign_in.setEnabled(True)
        self.status.setStyleSheet(f"color: {theme.GOOD};")
        self.status.setText(f"Signed in. The agent will play as {res.get('profileName')}.")

    def _sign_in_failed(self, message: str) -> None:
        self._close_dialog()
        self.sign_in.setEnabled(True)
        self.status.setStyleSheet(f"color: {theme.BAD};")
        self.status.setText(message)

    def validatePage(self) -> bool:
        d = self.wiz.draft.auth
        d.mode = "microsoft" if self.ms.isChecked() else "offline"
        d.username = self.name.text().strip()
        try:
            if not d.username:
                raise cfg.ConfigError("Enter a name first.")
            if d.mode == "offline":
                cfg.validate_offline_name(d.username)
        except cfg.ConfigError as exc:
            self.status.setStyleSheet(f"color: {theme.BAD};")
            self.status.setText(str(exc))
            return False
        return True


class ClaudePage(Page):
    def __init__(self, wizard):
        super().__init__(wizard, "Claude API",
                         "Paste an Anthropic API key from the Claude Console (platform.claude.com). It is checked with a free "
                         "model-list request and then stored in your system keyring, never in a file.")
        form = QFormLayout()
        form.setSpacing(10)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-ant-…")
        self.key.setAccessibleName("Anthropic API key")
        self.key.textEdited.connect(self._invalidate)
        self.model = QComboBox()
        self.model.setEnabled(False)
        self.model.setAccessibleName("Claude model")
        form.addRow("API key", self.key)
        form.addRow("Model", self.model)
        self.lay.addLayout(form)
        row = QHBoxLayout()
        self.validate = button("Validate key", "Primary")
        self.validate.clicked.connect(self._validate)
        row.addWidget(self.validate)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.status = label("", wrap=True)
        self.lay.addWidget(self.status)
        self.lay.addStretch(1)
        self.later = QCheckBox("Set this up later (the Minecraft connection works without it)")
        self.later.toggled.connect(lambda _: self.completeChanged.emit())
        self.lay.addWidget(self.later)
        self._validated = False
        self._stored: str | None = None

    def initializePage(self) -> None:
        run_async(self.ctx.secrets.get, ANTHROPIC_KEY, on_done=self._stored_key, on_error=lambda _e: None)

    def _stored_key(self, key: str | None) -> None:
        self._stored = key
        if key:
            self.key.setPlaceholderText("A key is already stored. Leave blank to keep it.")

    def _invalidate(self) -> None:
        self._validated = False
        self.model.setEnabled(False)
        self.completeChanged.emit()

    def _say(self, text: str, color: str) -> None:
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(text)

    def _validate(self) -> None:
        key = self.key.text().strip() or self._stored or ""
        self.validate.setEnabled(False)
        self._say("Asking Anthropic…", theme.MUTED)
        run_async(claude_api.list_models, key, on_done=self._models, on_error=self._failed)

    def _models(self, models: list[claude_api.ModelInfo]) -> None:
        self.validate.setEnabled(True)
        self.model.clear()
        for m in models:
            self.model.addItem(f"{m.display_name}  ({m.id})", m.id)
        self.model.setCurrentIndex(self.model.findData(
            claude_api.default_model(models, self.wiz.draft.anthropic.model)))
        self.model.setEnabled(True)
        self._validated = True
        self._say(f"Key accepted. {len(models)} models are available to it.", theme.GOOD)
        self.completeChanged.emit()

    def _failed(self, message: str) -> None:
        self.validate.setEnabled(True)
        self._invalidate()
        self._say(message, theme.BAD)

    def isComplete(self) -> bool:
        return self.later.isChecked() or self._validated

    def validatePage(self) -> bool:
        if self._validated:
            self.wiz.new_api_key = self.key.text().strip() or None
            self.wiz.draft.anthropic.model = self.model.currentData()
        return True


class SummaryPage(Page):
    def __init__(self, wizard):
        super().__init__(wizard, "Ready", "Review your choices. Finish saves them; you can change any of "
                                          "them later in Settings.")
        self.body = label("", wrap=True, selectable=True)
        self.lay.addWidget(self.body)
        self.lay.addStretch(1)

    def initializePage(self) -> None:
        d = self.wiz.draft
        account = (f"Microsoft account \"{d.auth.username}\"" if d.auth.mode == "microsoft"
                   else f"offline player {d.auth.username}")
        claude = d.anthropic.model or "not configured yet"
        self.body.setText(
            f"<p><b>Instance</b><br>{d.minecraft.instance_dir or 'none selected'}</p>"
            f"<p><b>Server</b><br>{d.server.host}:{d.server.port}"
            f"{' (public servers allowed)' if d.server.allow_public else ''}</p>"
            f"<p><b>Agent account</b><br>{account}</p>"
            f"<p><b>Claude model</b><br>{claude}</p>")


class SetupWizard(QWizard):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.draft = copy.deepcopy(ctx.config)
        self.new_api_key: str | None = None
        self.setWindowTitle("PROMETHEUS setup")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setMinimumSize(760, 600)
        for page in (WelcomePage, SystemPage, InstancePage, ServerPage, AccountPage, ClaudePage, SummaryPage):
            self.addPage(page(self))
        for which in (QWizard.WizardButton.NextButton, QWizard.WizardButton.FinishButton):
            btn = self.button(which)
            btn.setObjectName("Primary")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def accept(self) -> None:
        if self.new_api_key:
            finish = self.button(QWizard.WizardButton.FinishButton)
            finish.setEnabled(False)
            key, self.new_api_key = self.new_api_key, None

            def stored(persisted: bool) -> None:
                if not persisted:
                    QMessageBox.warning(self, "API key kept for this session only",
                                        "No Secret Service keyring is available, so the key was not saved. "
                                        "You will be asked for it again next time.")
                self.accept()

            def failed(message: str) -> None:
                self.new_api_key = key
                finish.setEnabled(True)
                QMessageBox.critical(self, "Could not store the API key", message)

            run_async(self.ctx.secrets.set, ANTHROPIC_KEY, key, on_done=stored, on_error=failed)
            return
        self.draft.setup_complete = True
        try:
            self.ctx.config = self.draft
            self.ctx.save()
        except (cfg.ConfigError, OSError) as exc:
            QMessageBox.critical(self, "Could not save settings", str(exc))
            return
        super().accept()
