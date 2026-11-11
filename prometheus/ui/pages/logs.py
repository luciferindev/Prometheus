"""Live, searchable view of the application's structured log."""
from __future__ import annotations

import logging
import time
from collections import deque

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from ...core.logsetup import redact
from ..widgets import label

LEVELS = {"All levels": logging.DEBUG, "Warnings and errors": logging.WARNING, "Errors only": logging.ERROR}


class _Emitter(QObject):
    record = Signal(int, str)


class QtLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.emitter = _Emitter()

    def emit(self, record: logging.LogRecord) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        line = f"{stamp}  {record.levelname:<7}  {record.name.removeprefix('prometheus.')}: {redact(record.getMessage())}"
        self.emitter.record.emit(record.levelno, line)


class LogsPage(QWidget):
    def __init__(self):
        super().__init__()
        self._records: deque[tuple[int, str]] = deque(maxlen=3000)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(label("Logs", "PageTitle"))
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)
        self.level = QComboBox()
        self.level.addItems(list(LEVELS))
        self.level.currentIndexChanged.connect(self._refill)
        row.addWidget(self.search, 1)
        row.addWidget(self.level)
        root.addLayout(row)
        self.view = QPlainTextEdit(objectName="Mono")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(3000)
        self.view.setPlaceholderText("Nothing logged yet.")
        root.addWidget(self.view, 1)
        self.handler = QtLogHandler()
        self.handler.emitter.record.connect(self._add)
        logging.getLogger("prometheus").addHandler(self.handler)

    def _matches(self, level: int, line: str) -> bool:
        needle = self.search.text().lower()
        return level >= LEVELS[self.level.currentText()] and (not needle or needle in line.lower())

    def _add(self, level: int, line: str) -> None:
        self._records.append((level, line))
        if self._matches(level, line):
            self.view.appendPlainText(line)

    def _refill(self) -> None:
        self.view.setPlainText("\n".join(line for lvl, line in self._records if self._matches(lvl, line)))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
