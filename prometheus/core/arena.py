"""The training arena: a private vanilla 1.20.1 server that PROMETHEUS runs itself.

It listens on 127.0.0.1 only, in offline mode, in a flat world. Because the application owns
the server process, it can give kits and reset the arena through the server console. Those
console lines are fixed templates written by the trainer; nothing Claude writes ever reaches
the console.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from . import installer, paths, toolchain
from .installer import Artifact, InstallError, Progress

log = logging.getLogger("prometheus.arena")

PORT = 25610
MC = "1.20.1"
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
EULA_URL = "https://aka.ms/MinecraftEULA"
_PLAYER_EVENT = re.compile(r"\]: ([A-Za-z0-9_]{3,16}) (joined|left) the game\s*$")
_SAFE_LINE = re.compile(r"^[A-Za-z0-9_ .:~@\[\]{}=,\-]{1,200}$")      # braces: item enchantments

PROPERTIES = f"""online-mode=false
server-ip=127.0.0.1
server-port={PORT}
level-type=minecraft\\:flat
generate-structures=false
spawn-monsters=false
spawn-animals=false
spawn-npcs=false
difficulty=easy
pvp=true
spawn-protection=0
max-players=4
view-distance=4
simulation-distance=4
enable-rcon=false
motd=PROMETHEUS training arena
"""


def arena_dir() -> Path:
    return paths.data_dir() / "arena"


def stop_stale_server() -> bool:
    """Stop an arena server left over from an earlier session (for example after the application
    was killed). It is only touched if its pid file, working directory and command line all say it
    is this arena's server. Returns True if one was stopped."""
    pid_file = arena_dir() / "server.pid"
    try:
        pid = int(pid_file.read_text().strip())
        cwd = os.readlink(f"/proc/{pid}/cwd")
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except (OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        return False
    if Path(cwd) != arena_dir().resolve() or "server.jar" not in cmdline:
        pid_file.unlink(missing_ok=True)
        return False
    log.warning("stopping an arena server left over from an earlier session (pid %s)", pid)
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):                       # it saves the world before exiting
            time.sleep(0.1)
            os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        log.error("could not stop the leftover arena server: %s", exc)
    pid_file.unlink(missing_ok=True)
    return True


def install(progress: Progress | None = None, cancel: threading.Event | None = None) -> Path:
    """Worker thread: fetch Java 17 (if missing) and Mojang's server jar, both checksum-verified."""
    if toolchain.find_java17() is None:
        installer.install_runtime("java17", progress, cancel)
    directory = arena_dir()
    directory.mkdir(parents=True, exist_ok=True)
    jar = directory / "server.jar"
    if not jar.exists():
        manifest = installer.http_json(MANIFEST)
        try:
            url = next(v["url"] for v in manifest["versions"] if v["id"] == MC)
            server = installer.http_json(url)["downloads"]["server"]
            art = Artifact(server["url"], "server.jar", "sha1", server["sha1"], int(server.get("size", 0)))
        except (StopIteration, LookupError, TypeError) as exc:
            raise InstallError("Mojang's version list has no server download for 1.20.1.") from exc
        installer.download(art, directory, progress, cancel)
    (directory / "server.properties").write_text(PROPERTIES, encoding="utf-8")
    return directory


class ArenaServer(QObject):
    stateChanged = Signal(str)     # missing | stopped | starting | running
    failed = Signal(str)
    playerJoined = Signal(str)     # read from the server's own log lines
    playerLeft = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buf = ""
        self._stopping = False
        self._restart = False
        self.state = "stopped" if self.installed() else "missing"

    def installed(self) -> bool:
        return (arena_dir() / "server.jar").exists() and toolchain.find_java17() is not None

    def eula_accepted(self) -> bool:
        path = arena_dir() / "eula.txt"
        return path.exists() and "eula=true" in path.read_text(encoding="utf-8", errors="replace")

    def accept_eula(self) -> None:
        """Only called after the user ticked the EULA box in the Training page."""
        arena_dir().mkdir(parents=True, exist_ok=True)
        (arena_dir() / "eula.txt").write_text("eula=true\n", encoding="utf-8")

    def refresh(self) -> None:
        if self._proc is None:
            self._set("stopped" if self.installed() else "missing")

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.stateChanged.emit(state)

    def start(self) -> None:
        if self._proc is not None:
            if self._stopping:
                self._restart = True     # still shutting down: start again as soon as it has exited
            return
        java = toolchain.find_java17()
        if not self.installed() or java is None:
            return self.failed.emit("The training arena is not installed yet.")
        if not self.eula_accepted():
            return self.failed.emit("The Minecraft EULA has not been accepted for the training arena.")
        stop_stale_server()
        proc = QProcess(self)
        proc.setWorkingDirectory(str(arena_dir()))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        self._proc, self._buf, self._stopping, self._restart = proc, "", False, False
        self._set("starting")
        argv = [java, "-Xmx1G", "-jar", "server.jar", "nogui"]
        setpriv = shutil.which("setpriv")
        if setpriv:      # ask the kernel to stop the server if this application dies for any reason
            argv = [setpriv, "--pdeathsig", "TERM", "--"] + argv
        proc.start(argv[0], argv[1:])
        if proc.waitForStarted(5000):
            (arena_dir() / "server.pid").write_text(str(self._java_pid(proc.processId())))
        log.info("arena server starting")

    @staticmethod
    def _java_pid(pid: int) -> int:
        return pid       # setpriv execs java in place, so the pid is the server's own

    def _on_output(self) -> None:
        assert self._proc is not None
        self._buf += bytes(self._proc.readAllStandardOutput().data()).decode(errors="replace")
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            if "Done (" in line and self.state == "starting":
                log.info("arena server ready")
                self._set("running")
            elif (event := _PLAYER_EVENT.search(line)) and "<" not in line:     # "<name> text" is chat, not an event
                (self.playerJoined if event.group(2) == "joined" else self.playerLeft).emit(event.group(1))
            elif "FAILED TO BIND TO PORT" in line:
                self._stopping = True        # reported here; the exit that follows is not a second error
                self.failed.emit(f"Port {PORT} is already in use by another program, so the arena could not start.")

    def _on_finished(self, code: int, _status) -> None:
        was = self.state
        self._proc = None
        (arena_dir() / "server.pid").unlink(missing_ok=True)
        if self._restart:
            self._restart = False
            return self.start()
        self._set("stopped" if self.installed() else "missing")
        if was in ("starting", "running") and code != 0 and not self._stopping:
            self.failed.emit(f"The arena server stopped unexpectedly (exit code {code}).")

    def command(self, line: str) -> None:
        """Send one console line. Rejects anything outside a conservative character set."""
        if self._proc is None or self.state != "running":
            raise RuntimeError("The arena server is not running.")
        if not _SAFE_LINE.match(line):
            raise ValueError(f"Refusing console line: {line!r}")
        self._proc.write(line.encode() + b"\n")

    def stop(self, wait: bool = False) -> None:
        """Ask the server to save and stop. Returns at once unless wait=True (used when quitting)."""
        proc = self._proc
        if proc is None:
            return
        self._stopping = True
        try:
            proc.write(b"stop\n")
        except RuntimeError:
            pass
        if wait:
            if not proc.waitForFinished(10_000):
                proc.kill()
                proc.waitForFinished(2000)
        else:
            QTimer.singleShot(12_000, lambda: proc.kill() if self._proc is proc else None)
