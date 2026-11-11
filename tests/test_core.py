import hashlib
import io
import json
import tarfile

import keyring.backends.fail
import pytest

from prometheus.core import config as cfg
from prometheus.core import installer, netpolicy
from prometheus.core.logsetup import redact
from prometheus.core.secrets import ANTHROPIC_KEY, SecretStore


# ---- config ----
def test_config_defaults_when_missing():
    c = cfg.load()
    assert c.setup_complete is False and c.server.host == "127.0.0.1" and c.server.port == 25565
    assert c.server.allow_public is False and c.minecraft.mc_version == "1.20.1"


def test_config_roundtrip_and_permissions():
    c = cfg.Config()
    c.server.host, c.server.port, c.auth.mode, c.auth.username = "192.168.1.20", 25570, "offline", "Bot_1"
    c.setup_complete = True
    path = cfg.save(c)
    assert path.stat().st_mode & 0o777 == 0o600
    assert cfg.load() == c
    assert "key" not in path.read_text().lower()


def test_config_rejects_bad_values():
    c = cfg.Config()
    c.server.port = 70000
    with pytest.raises(cfg.ConfigError):
        cfg.save(c)
    c = cfg.Config()
    c.auth.mode, c.auth.username = "offline", "has space"
    with pytest.raises(cfg.ConfigError):
        c.validate()


def test_config_corrupt_and_mistyped_files_raise_not_reset():
    path = cfg.config_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(cfg.ConfigError):
        cfg.load()
    path.write_text(json.dumps({"server": {"port": "25565"}}))
    with pytest.raises(cfg.ConfigError):
        cfg.load()
    path.write_text(json.dumps({"server": {"port": 1234}, "future_field": 1}))
    assert cfg.load().server.port == 1234


# ---- secrets ----
def test_secret_store_roundtrip(memory_keyring):
    store = SecretStore(memory_keyring)
    assert store.availability()[0] is True
    assert store.get(ANTHROPIC_KEY) is None
    assert store.set(ANTHROPIC_KEY, "sk-ant-test-123456") is True
    assert store.get(ANTHROPIC_KEY) == "sk-ant-test-123456"
    store.delete(ANTHROPIC_KEY)
    store.delete(ANTHROPIC_KEY)  # deleting twice is fine
    assert store.get(ANTHROPIC_KEY) is None


def test_secret_store_without_backend_is_session_only():
    store = SecretStore(keyring.backends.fail.Keyring())
    ok, reason = store.availability()
    assert ok is False and "Secret Service" in reason
    assert store.set(ANTHROPIC_KEY, "sk-ant-test-123456") is False
    assert store.get(ANTHROPIC_KEY) == "sk-ant-test-123456"          # this session
    assert SecretStore(keyring.backends.fail.Keyring()).get(ANTHROPIC_KEY) is None  # not persisted


def test_logs_redact_credentials():
    line = redact("key sk-ant-api03-abcdefghijk and Bearer abcdefghijklmnop.qrst and eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.sig")
    assert "sk-ant" not in line and "abcdefghijklmnop" not in line and "eyJhbGci" not in line
    assert line.count("[redacted]") == 3


# ---- installer ----
def _artifact(tmp_path, data: bytes, digest: str | None = None):
    src = tmp_path / "src.bin"
    src.write_bytes(data)
    return installer.Artifact(src.as_uri(), "thing.bin", "sha256", digest or hashlib.sha256(data).hexdigest())


def test_download_verifies_checksum_and_reports_progress(tmp_path):
    seen = []
    out = installer.download(_artifact(tmp_path, b"x" * 200_000), tmp_path / "out", lambda d, t, n: seen.append((d, t)))
    assert out.read_bytes() == b"x" * 200_000
    assert seen[-1][0] == 200_000


def test_download_discards_file_on_checksum_mismatch(tmp_path):
    with pytest.raises(installer.InstallError, match="checksum mismatch"):
        installer.download(_artifact(tmp_path, b"payload", "00" * 32), tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_extract_refuses_path_traversal(tmp_path):
    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as tf:
        info = tarfile.TarInfo("../escaped.txt")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"oops"))
    with pytest.raises(installer.InstallError):
        installer.extract_tar(evil, tmp_path / "dest")
    assert not (tmp_path / "escaped.txt").exists()


def test_extract_flattens_single_top_level_dir(tmp_path):
    good = tmp_path / "good.tar"
    with tarfile.open(good, "w") as tf:
        info = tarfile.TarInfo("package/bin/tool")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"hi"))
    dest = installer.extract_tar(good, tmp_path / "dest")
    assert (dest / "bin" / "tool").read_bytes() == b"hi"


def test_sri_conversion():
    import base64
    digest = hashlib.sha512(b"abc").digest()
    assert installer.sri_to_hex("sha512-" + base64.b64encode(digest).decode()) == ("sha512", digest.hex())


# ---- network policy ----
def test_private_hosts_allowed_public_blocked_by_default():
    netpolicy.check_host("127.0.0.1", allow_public=False)
    netpolicy.check_host("localhost", allow_public=False)
    netpolicy.check_host("192.168.1.50", allow_public=False)
    with pytest.raises(netpolicy.HostPolicyError, match="public address"):
        netpolicy.check_host("8.8.8.8", allow_public=False)
    netpolicy.check_host("8.8.8.8", allow_public=True)
