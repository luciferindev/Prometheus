import json

from prometheus.core import compat, minecraft
from tests.conftest import make_mod_jar


def test_inspect_vanilla_instance(vanilla_instance):
    inst = minecraft.inspect(vanilla_instance)
    assert inst.kind == minecraft.VANILLA and "1.20.1" in inst.mc_versions


def test_vanilla_1_20_1_instance_is_ok(tmp_path):
    root = tmp_path / ".minecraft"
    (root / "versions" / "1.20.1").mkdir(parents=True)
    (root / "versions" / "1.20.1" / "1.20.1.json").write_text("{}")
    findings = compat.evaluate(minecraft.inspect(root))
    assert [(f.key, f.level) for f in findings] == [("minecraft", compat.OK)]


def test_missing_1_20_1_is_a_warning_with_instructions(tmp_path):
    root = tmp_path / ".minecraft"
    (root / "versions" / "1.21.4").mkdir(parents=True)
    (root / "versions" / "1.21.4" / "1.21.4.json").write_text("{}")
    finding = compat.evaluate(minecraft.inspect(root))[0]
    assert finding.level == compat.WARN and "1.21.4" in finding.detail and "Launcher" in finding.detail


def test_mods_are_reported_as_unrecognised_not_checked(vanilla_instance):
    findings = {f.key: f for f in compat.evaluate(minecraft.inspect(vanilla_instance))}
    assert findings["minecraft"].level == compat.OK
    assert findings["mods"].level == compat.INFO and "2 Fabric mods" in findings["mods"].detail
    assert not compat.blocking(list(findings.values()))


def test_prism_instance_with_wrong_version_blocks(tmp_path):
    inst_dir = tmp_path / "instances" / "Pack"
    (inst_dir / ".minecraft" / "mods").mkdir(parents=True)
    (inst_dir / "mmc-pack.json").write_text(json.dumps({"components": [{"uid": "net.minecraft", "version": "1.19.2"}]}))
    (inst_dir / ".minecraft" / "mods" / "broken.jar").write_bytes(b"not a zip")
    make_mod_jar(inst_dir / ".minecraft" / "mods" / "x.jar", "x", "1.0")
    inst = minecraft.inspect(inst_dir)
    assert inst.kind == minecraft.PRISM and inst.unreadable_jars == ["broken.jar"]
    assert compat.blocking(compat.evaluate(inst))


def test_unknown_folder_is_rejected(tmp_path):
    findings = compat.evaluate(minecraft.inspect(tmp_path))
    assert compat.blocking(findings) and findings[0].key == "instance"


def test_discover_finds_launcher_folders(tmp_path, vanilla_instance):
    assert [i.root for i in minecraft.discover(home=tmp_path)] == [vanilla_instance]
    assert minecraft.discover(home=tmp_path / "nowhere") == []
