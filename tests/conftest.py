"""Test isolation: no test touches the real keyring, real settings, or the network
(the bridge tests talk to a local protocol fixture only)."""
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parents[1]


class MemoryKeyring(KeyringBackend):
    priority = 5

    def __init__(self):
        super().__init__()
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.data:
            raise PasswordDeleteError("not found")
        del self.data[(service, username)]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    # Keep toolchains installed for development visible, but put settings/logs/auth in tmp.
    real_runtimes = os.environ.get("PROMETHEUS_TEST_RUNTIMES")
    monkeypatch.setenv("PROMETHEUS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    if real_runtimes:
        (tmp_path / "home" / "share").mkdir(parents=True)
        (tmp_path / "home" / "share" / "runtimes").symlink_to(real_runtimes)
    return tmp_path


@pytest.fixture(autouse=True)
def collect_on_gui_thread():
    """Free each test's Qt objects here, on the GUI thread, instead of leaving them for a
    garbage-collection pass that might run on a worker thread during a later test."""
    yield
    import gc
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    app = QCoreApplication.instance()
    if app is not None:
        QThreadPool.globalInstance().waitForDone(5000)
        app.processEvents()
        gc.collect()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


@pytest.fixture
def memory_keyring():
    return MemoryKeyring()


def make_mod_jar(path: Path, mod_id: str, version: str, depends: dict | None = None, name: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(
            {"schemaVersion": 1, "id": mod_id, "version": version, "name": name or mod_id,
             "depends": depends or {}}))
    return path


@pytest.fixture
def vanilla_instance(tmp_path):
    """A .minecraft folder with Fabric 0.16.10 for 1.20.1, Fabric API and Create."""
    root = tmp_path / ".minecraft"
    for vid in ("1.20.1", "fabric-loader-0.16.10-1.20.1"):
        (root / "versions" / vid).mkdir(parents=True)
        (root / "versions" / vid / f"{vid}.json").write_text("{}")
    (root / "launcher_profiles.json").write_text('{"profiles": {}}')
    make_mod_jar(root / "mods" / "fabric-api-0.92.2+1.20.1.jar", "fabric-api", "0.92.2+1.20.1", name="Fabric API")
    make_mod_jar(root / "mods" / "create-fabric-6.0.8.1.jar", "create", "6.0.8.1+build.1744-mc1.20.1",
                 {"minecraft": "1.20.1", "fabricloader": ">=0.16.0", "fabric-api": ">=0.92.2", "java": ">=17"},
                 name="Create")
    return root


@pytest.fixture(scope="session")
def fake_server():
    """Protocol-level 1.20.1 fixture from bridge/test/fake-server.ts; yields (port, process)."""
    from prometheus.core import toolchain
    node = toolchain.find_node()
    if not node or not (REPO / "bridge" / "node_modules" / "mineflayer").is_dir():
        pytest.skip("bridge dependencies are not installed")
    proc = subprocess.Popen([node, str(REPO / "bridge" / "test" / "fake-server.ts")], cwd=REPO / "bridge",
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    port = int(proc.stdout.readline().strip())
    yield port, proc
    proc.stdin.close()
    proc.wait(timeout=5)
