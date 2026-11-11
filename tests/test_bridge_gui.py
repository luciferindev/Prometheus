"""End-to-end: Qt application <-> Node bridge <-> protocol fixture."""
import pytest
from PySide6.QtWidgets import QWizard

from prometheus.context import AppContext
from prometheus.core import claude_api, config as cfg, toolchain
from prometheus.core.bridge import BridgeClient, validate_notification
from prometheus.core.secrets import ANTHROPIC_KEY, SecretStore
from prometheus.ui import theme
from prometheus.ui.main_window import MainWindow
from prometheus.ui.wizard import SetupWizard

pytestmark = pytest.mark.skipif(not toolchain.bridge_ready(), reason="bridge is not built")


def test_notification_validation():
    assert validate_notification("bot.status", {"phase": "spawned"})
    assert validate_notification("bot.status", {"phase": "hacked"}) is None
    assert validate_notification("shell.exec", {"cmd": "id"}) is None
    assert validate_notification("bot.state", {"inventory": [], "position": {"x": 1, "y": 2, "z": 3}})
    assert validate_notification("bot.state", {"inventory": "nope"}) is None
    assert validate_notification("auth.deviceCode", {"userCode": "ABC", "verificationUri": "http://evil", "expiresIn": 9}) is None


@pytest.fixture
def bridge(qtbot):
    client = BridgeClient()
    yield client
    client.stop()


def call(qtbot, client, method, params):
    box = {}
    client.call(method, params, lambda r: box.update(result=r), lambda e: box.update(error=e))
    qtbot.waitUntil(lambda: bool(box), timeout=15000)
    return box


def test_bridge_hello_ping_and_validation(qtbot, bridge, fake_server):
    port, _ = fake_server
    hello = call(qtbot, bridge, "bridge.hello", {})["result"]
    assert hello["minecraftVersion"] == "1.20.1" and hello["mineflayerVersion"]
    status = call(qtbot, bridge, "server.ping", {"host": "127.0.0.1", "port": port})["result"]
    assert status["protocol"] == 763 and status["motd"] == "PROMETHEUS test fixture"
    assert "Could not reach" in call(qtbot, bridge, "server.ping", {"host": "127.0.0.1", "port": 1})["error"]
    assert "invalid params" in call(qtbot, bridge, "server.ping", {"host": "$(reboot)", "port": port})["error"]
    assert "unknown method" in call(qtbot, bridge, "shell.exec", {"cmd": "id"})["error"]


def test_pending_calls_fail_when_bridge_is_killed(qtbot, bridge):
    call(qtbot, bridge, "bridge.hello", {})
    box = {}
    bridge.call("server.ping", {"host": "10.255.255.1", "port": 25565}, None, lambda e: box.update(error=e))
    bridge.stop(kill=True)
    qtbot.waitUntil(lambda: bool(box), timeout=5000)
    assert "stopped" in box["error"] and not bridge.is_running()


@pytest.fixture
def window(qtbot, qapp, memory_keyring, fake_server):
    theme.apply(qapp)
    ctx = AppContext(SecretStore(memory_keyring))
    ctx.config.setup_complete = True
    ctx.config.server.port = fake_server[0]
    ctx.config.auth.mode, ctx.config.auth.username = "offline", "GuiBot"
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    yield win
    ctx.bridge.stop()


def test_dashboard_connects_shows_live_state_and_emergency_stops(qtbot, window):
    dash = window.dashboard
    assert dash.coords.text() == "—" and not dash.estop_btn.isEnabled()
    dash.connect_btn.click()
    qtbot.waitUntil(lambda: dash.phase == "spawned" and dash.player.text() == "GuiBot"
                    and dash.health_txt.text() != "—", timeout=20000)
    assert dash.pill.text().endswith("In world")
    assert dash.health_txt.text() == "17 / 20" and dash.health.value() == 17
    assert dash.food_txt.text() == "14 / 20"
    assert dash.coords.text().split() == ["8.5", "64.0", "-3.5"]
    assert dash.dimension.text() == "overworld"
    assert dash.armor.text() == "0 / 20"
    assert "763" in dash.server_info.text()
    assert not dash.connect_btn.isEnabled() and dash.disconnect_btn.isEnabled()

    dash.estop_btn.click()
    assert dash.phase == "disconnected" and not window.ctx.bridge.is_running()
    assert dash.coords.text() == "—" and dash.health_txt.text() == "—"    # no stale readings
    assert dash.connect_btn.isEnabled()


def test_dashboard_reports_server_kick_then_reconnects(qtbot, window, fake_server):
    dash = window.dashboard
    dash.connect_btn.click()
    qtbot.waitUntil(lambda: dash.phase == "spawned", timeout=20000)
    fake_server[1].stdin.write("kick\n")
    fake_server[1].stdin.flush()
    qtbot.waitUntil(lambda: dash.phase == "disconnected", timeout=10000)
    assert dash.player.text() == "—"
    dash.connect_btn.click()
    qtbot.waitUntil(lambda: dash.phase == "spawned", timeout=20000)
    dash.disconnect_btn.click()
    qtbot.waitUntil(lambda: dash.phase == "disconnected", timeout=10000)


def test_dashboard_explains_unreachable_server_and_public_host_block(qtbot, window):
    dash = window.dashboard
    window.ctx.config.server.port = 1
    dash.connect_btn.click()
    qtbot.waitUntil(lambda: dash.phase == "disconnected" and dash.error.isVisible(), timeout=15000)
    assert "Could not reach 127.0.0.1:1" in dash.error.text()
    window.ctx.config.server.host = "8.8.8.8"
    dash.connect_btn.click()
    qtbot.waitUntil(lambda: "public address" in dash.error.text(), timeout=10000)
    assert dash.phase == "disconnected"


def test_settings_save_validates(qtbot, window):
    s = window.settings
    s.username.setText("bad name!")
    s.mode.setCurrentIndex(s.mode.findData("offline"))
    s._save_connection()
    assert "3-16 characters" in s.conn_status.text() and window.ctx.config.auth.username == "GuiBot"
    s.username.setText("Other_Bot")
    s.port.setValue(25570)
    s._save_connection()
    assert cfg.load().auth.username == "Other_Bot" and cfg.load().server.port == 25570
    assert window.dashboard.target.text() == "127.0.0.1:25570"


def test_wizard_end_to_end_saves_config_and_stores_key(qtbot, qapp, memory_keyring, fake_server, monkeypatch):
    theme.apply(qapp)
    models = [claude_api.ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5", 1_000_000),
              claude_api.ModelInfo("claude-opus-5-5", "Claude Opus 5.5", 1_000_000)]
    seen_keys = []
    monkeypatch.setattr(claude_api, "list_models", lambda key: (seen_keys.append(key), models)[1])
    ctx = AppContext(SecretStore(memory_keyring))
    wiz = SetupWizard(ctx)
    qtbot.addWidget(wiz)
    wiz.show()

    wiz.next()                                           # system check
    system = wiz.currentPage()
    qtbot.waitUntil(system.isComplete, timeout=15000)
    assert system.status.text() == "Everything required is ready."
    wiz.next()                                           # instance (none on this machine: allowed)
    wiz.next()                                           # server
    server = wiz.currentPage()
    server.port.setValue(fake_server[0])
    server.test.click()
    qtbot.waitUntil(lambda: "Server reachable" in server.result.text(), timeout=15000)
    wiz.next()                                           # account
    account = wiz.currentPage()
    account.offline.setChecked(True)
    account.name.setText("x")
    wiz.next()
    assert wiz.currentPage() is account and "3-16" in account.status.text()   # invalid name blocks
    account.name.setText("WizardBot")
    wiz.next()                                           # claude
    claude = wiz.currentPage()
    assert not claude.isComplete()
    claude.key.setText("sk-ant-test-0000000000")
    claude.validate.click()
    qtbot.waitUntil(claude.isComplete, timeout=5000)
    assert claude.model.currentData() == "claude-opus-5-5"
    wiz.next()                                           # summary
    assert "WizardBot" in wiz.currentPage().body.text()
    wiz.button(QWizard.WizardButton.FinishButton).click()
    qtbot.waitUntil(lambda: ctx.config.setup_complete, timeout=5000)

    saved = cfg.load()
    assert saved.setup_complete and saved.auth.username == "WizardBot" and saved.server.port == fake_server[0]
    assert saved.anthropic.model == "claude-opus-5-5"
    assert memory_keyring.get_password("prometheus", ANTHROPIC_KEY) == "sk-ant-test-0000000000"
    assert seen_keys == ["sk-ant-test-0000000000"]
    assert "sk-ant" not in cfg.config_path().read_text()
    ctx.bridge.stop()
