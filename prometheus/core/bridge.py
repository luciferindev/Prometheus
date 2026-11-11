"""Client for the Node bridge: JSON-RPC 2.0 over the child process's stdio.

The pipe is private to this process pair, so there is no listening socket for
anything else on the machine to talk to. Everything arriving from the bridge is
still validated before it reaches the UI.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from . import paths, toolchain

log = logging.getLogger("prometheus.bridge")

NOTIFICATIONS = {"bot.status", "bot.state", "bot.event", "bot.chat", "path.update", "pvp.result", "auth.deviceCode"}
PHASES = {"idle", "connecting", "logged_in", "spawned", "disconnected"}
MAX_LINE = 1 << 20

OnResult = Callable[[dict], None]
OnError = Callable[[str], None]


class BridgeError(RuntimeError):
    pass


def validate_notification(method: str, params: Any) -> dict | None:
    """Return params if the notification is well-formed, else None."""
    if method not in NOTIFICATIONS or not isinstance(params, dict):
        return None
    if method == "bot.status":
        ok = params.get("phase") in PHASES and isinstance(params.get("reason", ""), str)
    elif method == "path.update":
        ok = (isinstance(params.get("status"), str) and isinstance(params.get("path"), list)
              and isinstance(params.get("explored"), list) and len(params["path"]) <= 600
              and len(params["explored"]) <= 3000
              and all(isinstance(p, list) and len(p) == 3 and all(isinstance(v, (int, float)) for v in p)
                      for p in params["path"] + params["explored"]))
    elif method == "pvp.result":
        ok = (isinstance(params.get("died"), bool) and isinstance(params.get("opponentDied"), bool)
              and all(isinstance(params.get(k), (int, float)) for k in
                      ("healthEnd", "damageTaken", "totemsUsed", "crystalsPlaced", "crystalsDetonated", "seconds"))
              and isinstance(params.get("enemyOffsets", []), list) and len(params.get("enemyOffsets", [])) <= 60)
    elif method == "bot.chat":
        ok = isinstance(params.get("username"), str) and isinstance(params.get("message"), str)
    elif method == "bot.event":
        ok = isinstance(params.get("kind"), str) and isinstance(params.get("text"), str)
    elif method == "auth.deviceCode":
        ok = (isinstance(params.get("userCode"), str) and isinstance(params.get("expiresIn"), (int, float))
              and str(params.get("verificationUri", "")).startswith("https://"))
    else:  # bot.state
        pos = params.get("position")
        ok = isinstance(params.get("inventory"), list) and (
            pos is None or (isinstance(pos, dict) and all(isinstance(pos.get(k), (int, float)) for k in "xyz")))
    return params if ok else None


class BridgeClient(QObject):
    notification = Signal(str, dict)
    runningChanged = Signal(bool)
    exited = Signal(str)          # human-readable reason, emitted on unexpected exit

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buf = b""
        self._next_id = 1
        # id -> (on_result, on_error, monotonic deadline, method)
        self._pending: dict[int, tuple[OnResult | None, OnError | None, float, str]] = {}
        self._stopping = False
        # One long-lived timer reaps overdue calls. (Per-call QTimers plus closures made
        # reference cycles of QObjects, which Python's GC could then free on a worker thread.)
        self._reaper = QTimer(self)
        self._reaper.setInterval(500)
        self._reaper.timeout.connect(self._reap)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self) -> None:
        if self.is_running():
            return
        node = toolchain.find_node()
        if not node:
            raise BridgeError("Node.js 22 or newer was not found. Open Setup to install it.")
        if not toolchain.bridge_ready():
            raise BridgeError("The Minecraft bridge is not installed yet. Open Setup to install it.")
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PROMETHEUS_AUTH_CACHE", str(paths.ensure_private_dir(paths.auth_cache_dir())))
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(str(paths.bridge_dir()))
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_proc_error)
        self._proc, self._buf, self._stopping = proc, b"", False
        proc.start(node, [str(paths.bridge_dir() / "dist" / "main.js")])
        if not proc.waitForStarted(5000):
            self._proc = None
            raise BridgeError(f"Could not start the bridge: {proc.errorString()}")
        log.info("bridge started (pid %s)", proc.processId())
        self.runningChanged.emit(True)

    def stop(self, kill: bool = False) -> None:
        """Stop the bridge. kill=True is the emergency path: SIGKILL, no goodbye."""
        proc = self._proc
        if proc is None:
            return
        self._stopping = True
        if kill:
            proc.kill()
        else:
            proc.closeWriteChannel()      # bridge exits on stdin EOF after leaving the server
            if not proc.waitForFinished(1500):
                proc.kill()
        proc.waitForFinished(1500)

    def call(self, method: str, params: dict | None = None, on_result: OnResult | None = None,
             on_error: OnError | None = None, timeout_ms: int = 15000) -> None:
        try:
            self.start()
        except BridgeError as exc:
            if on_error:
                on_error(str(exc))
            return
        rid = self._next_id
        self._next_id += 1
        self._pending[rid] = (on_result, on_error, time.monotonic() + timeout_ms / 1000, method)
        if not self._reaper.isActive():
            self._reaper.start()
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        assert self._proc is not None
        self._proc.write(payload.encode() + b"\n")

    # ---- internals ----
    def _settle(self, rid: int, result: dict | None = None, error: str | None = None) -> None:
        entry = self._pending.pop(rid, None)
        if entry is None:
            return
        on_result, on_error, _deadline, _method = entry
        if not self._pending:
            self._reaper.stop()
        try:
            if error is not None:
                if on_error:
                    on_error(error)
            elif on_result:
                on_result(result or {})
        except RuntimeError as exc:   # receiver widget was destroyed while the call was in flight
            if "already deleted" not in str(exc):
                raise

    def _reap(self) -> None:
        now = time.monotonic()
        for rid, (_r, _e, deadline, method) in list(self._pending.items()):
            if now >= deadline:
                self._settle(rid, error=f"The bridge did not answer {method} in time.")

    def _on_stdout(self) -> None:
        assert self._proc is not None
        self._buf += bytes(self._proc.readAllStandardOutput().data())
        if len(self._buf) > MAX_LINE and b"\n" not in self._buf:
            log.warning("bridge sent an oversized message; dropping buffer")
            self._buf = b""
            return
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            if line.strip():
                self._handle(line)

    def _handle(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("bridge sent invalid JSON; ignored")
            return
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            log.warning("bridge sent a non JSON-RPC message; ignored")
            return
        if "method" in msg:
            params = validate_notification(str(msg["method"]), msg.get("params"))
            if params is None:
                log.warning("bridge sent a malformed %s notification; ignored", msg.get("method"))
                return
            self.notification.emit(msg["method"], params)
            return
        rid = msg.get("id")
        if not isinstance(rid, int) or rid not in self._pending:
            return
        err = msg.get("error")
        if isinstance(err, dict):
            self._settle(rid, error=str(err.get("message", "Unknown bridge error")))
        elif isinstance(msg.get("result"), dict):
            self._settle(rid, result=msg["result"])
        else:
            self._settle(rid, error="The bridge returned a malformed response.")

    def _on_stderr(self) -> None:
        assert self._proc is not None
        for line in bytes(self._proc.readAllStandardError().data()).decode(errors="replace").splitlines():
            if line.strip():
                log.info("bridge: %s", line.strip()[:500])

    def _on_proc_error(self, _err) -> None:
        if self._proc is not None and not self._stopping:
            log.error("bridge process error: %s", self._proc.errorString())

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        expected = self._stopping
        self._proc = None
        for rid in list(self._pending):
            self._settle(rid, error="The bridge stopped before answering.")
        self.runningChanged.emit(False)
        if not expected:
            reason = ("The bridge crashed." if status == QProcess.ExitStatus.CrashExit
                      else f"The bridge exited unexpectedly (code {code}).")
            log.error(reason)
            self.exited.emit(reason)
        else:
            log.info("bridge stopped")
