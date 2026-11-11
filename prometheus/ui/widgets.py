"""Small reusable widgets."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import theme


def label(text: str = "", name: str = "", wrap: bool = False, selectable: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def button(text: str, kind: str = "", icon: str = "", tip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if kind:
        btn.setObjectName(kind)
    if icon:
        btn.setIcon(theme.icon(icon, "#1a1208" if kind == "Primary" else theme.TEXT, 16))
    if tip:
        btn.setToolTip(tip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class Card(QFrame):
    def __init__(self, title: str = ""):
        super().__init__()
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)
        if title:
            self.body.addWidget(label(title.upper(), "CardTitle"))


class StatusPill(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set("Idle", theme.MUTED)

    def set(self, text: str, color: str) -> None:
        self.setText(f"●  {text}")
        self.setStyleSheet(f"color: {color}; background: {theme.SURFACE_HI}; border: 1px solid {color};"
                           "border-radius: 12px; padding: 4px 12px; font-weight: 600;")


class CheckRow(QWidget):
    """One dependency/compatibility line: status icon, title, detail, optional action and progress."""

    def __init__(self, title: str, level: str, detail: str, hint: str = "", action_label: str = ""):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(12)
        ico = QLabel()
        ico.setPixmap(theme.pixmap(level, theme.LEVEL_COLOR.get(level, theme.MUTED), 20))
        ico.setAccessibleName({"ok": "OK", "warn": "Warning", "missing": "Missing", "error": "Problem",
                               "info": "Note"}.get(level, level))
        row.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title))
        self.detail = label(detail, "Muted", wrap=True, selectable=True)
        col.addWidget(self.detail)
        if hint:
            col.addWidget(label(hint, "Muted", wrap=True))
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        col.addWidget(self.progress)
        row.addLayout(col, 1)
        self.action: QPushButton | None = None
        if action_label:
            self.action = button(action_label)
            row.addWidget(self.action, 0, Qt.AlignmentFlag.AlignTop)

    def set_progress(self, done: int, total: int, text: str) -> None:
        self.progress.setVisible(True)
        self.progress.setRange(0, total if total > 0 else 0)
        if total > 0:
            self.progress.setValue(done)
            self.detail.setText(f"{text}: {done / 1e6:.1f} / {total / 1e6:.1f} MB")
        else:
            self.detail.setText(text)

    def set_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.detail.setText(message)
        self.detail.setStyleSheet(f"color: {theme.BAD};")
        if self.action:
            self.action.setEnabled(True)


class DeviceCodeDialog(QDialog):
    """Microsoft device-code sign-in. The user authenticates on microsoft.com in their own
    browser; PROMETHEUS only ever sees the resulting token cache, never a password."""

    def __init__(self, parent: QWidget, code: str, uri: str, expires_in: float):
        super().__init__(parent)
        self.setWindowTitle("Sign in with Microsoft")
        self.setModal(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(14)
        lay.addWidget(label("Enter this code on Microsoft's sign-in page", "PageTitle"))
        lay.addWidget(label(f"Open {uri} in your browser, enter the code below and approve the sign-in. "
                            f"The code expires in about {int(expires_in // 60)} minutes. "
                            "PROMETHEUS never sees your Microsoft password.", "Muted", wrap=True))
        code_label = label(code, "Code", selectable=True)
        code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(code_label)
        row = QHBoxLayout()
        copy = button("Copy code")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(code))
        open_btn = button("Open sign-in page", "Primary")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(uri)))
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(open_btn)
        lay.addLayout(row)
        self.status = label("Waiting for you to finish in the browser…", "Muted")
        lay.addWidget(self.status)
        self.setMinimumWidth(460)
