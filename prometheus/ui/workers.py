"""Run blocking work off the GUI thread and deliver results back through Qt signals."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

log = logging.getLogger("prometheus.worker")
_live: set["Task"] = set()


class _Signals(QObject):
    """Lives in the GUI thread. Its signals are emitted from the worker and delivered to its
    own slots, so Qt queues them onto the GUI thread before any callback touches a widget."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, on_done, on_error, on_progress):
        super().__init__()
        self._on_done, self._on_error, self._on_progress = on_done, on_error, on_progress
        self.task: "Task | None" = None
        self.done.connect(self._done)
        self.failed.connect(self._failed)
        self.progress.connect(self._progress)

    def _call(self, cb, *args) -> None:
        if cb is None:
            return
        try:
            cb(*args)
        except RuntimeError as exc:   # the widget that asked was closed in the meantime
            if "already deleted" not in str(exc):
                raise

    def _release(self) -> None:
        # Drop the last reference from a later event-loop turn, on the GUI thread, never from
        # inside this object's own slot.
        task = self.task
        QTimer.singleShot(0, lambda: _live.discard(task))

    @Slot(object)
    def _done(self, result) -> None:
        self._release()
        self._call(self._on_done, result)

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._release()
        self._call(self._on_error, message)

    @Slot(int, int, str)
    def _progress(self, done: int, total: int, text: str) -> None:
        self._call(self._on_progress, done, total, text)


class Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], args: tuple, signals: _Signals, with_progress: bool):
        super().__init__()
        self.setAutoDelete(False)
        self.fn, self.args, self.with_progress = fn, args, with_progress
        self.signals = signals
        signals.task = self
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            if self.with_progress:
                result = self.fn(*self.args, progress=self.signals.progress.emit, cancel=self.cancel)
            else:
                result = self.fn(*self.args)
        except Exception as exc:  # reported to the user by the caller's on_error
            log.info("task %s failed: %s", getattr(self.fn, "__name__", "?"), exc)
            self.signals.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.signals.done.emit(result)


def run_async(fn: Callable[..., Any], *args, on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None,
              on_progress: Callable[[int, int, str], None] | None = None) -> Task:
    """Run fn(*args) in the pool. If on_progress is given, fn also receives progress= and cancel=."""
    task = Task(fn, args, _Signals(on_done, on_error, on_progress), on_progress is not None)
    _live.add(task)
    QThreadPool.globalInstance().start(task)
    return task
