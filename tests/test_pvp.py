"""Crystal PvP training: the learning logic and the trainer's state machine, with stand-in
fighters whose outcome is a known function of the settings (so progress is checkable)."""
import json
import random
from types import SimpleNamespace as NS

import pytest
from PySide6.QtCore import QObject, QTimer, Signal

from prometheus.agent import pvp
from prometheus.agent.store import ActivityStore
from prometheus.context import AppContext
from prometheus.core import arena
from prometheus.core.bridge import validate_notification
from prometheus.core.secrets import ANTHROPIC_KEY, SecretStore


def test_settings_are_clamped_and_unknown_keys_dropped():
    out = pvp.clamp({"range": 99, "selfWeight": -3, "eatHealth": "lots", "meleeRange": True, "rm_rf": 1, "strafePeriod": float("nan")})
    assert out["range"] == 7.0 and out["selfWeight"] == 0.0
    assert out["eatHealth"] == pvp.BASELINE["eatHealth"] and out["meleeRange"] == pvp.BASELINE["meleeRange"]
    assert out["strafePeriod"] == pvp.BASELINE["strafePeriod"] and set(out) == set(pvp.SPACE)


def test_mutation_changes_a_few_settings_and_stays_in_range():
    rng = random.Random(7)
    for _ in range(200):
        child = pvp.mutate(pvp.BASELINE, rng)
        assert all(pvp.SPACE[k][0] <= v <= pvp.SPACE[k][1] for k, v in child.items())
        assert 0 <= sum(child[k] != pvp.BASELINE[k] for k in child) <= 3


def test_round_points():
    alive, dead = {"died": False, "healthEnd": 12, "totemsUsed": 1}, {"died": True, "healthEnd": 0, "totemsUsed": 2}
    assert pvp.round_points(alive, dead) == 1.0 and pvp.round_points(dead, alive) == 0.0
    assert pvp.round_points(alive, dict(alive)) == 0.5
    assert pvp.round_points({"died": False, "healthEnd": 20, "totemsUsed": 0}, {"died": False, "healthEnd": 6, "totemsUsed": 1}) == 1.0


def test_coach_reply_parsing():
    reply = 'Here you go:\n```json\n{"lessons": ["Stay further back.", ""], "parameters": {"range": 6.5, "selfWeight": 9, "bogus": 1}, "why": "less self damage"}\n```'
    lessons, params, why = pvp.parse_coach_reply(reply)
    assert lessons == ["Stay further back."] and params["range"] == 6.5 and params["selfWeight"] == 4.0 and "bogus" not in params
    assert why == "less self damage"
    for bad in ("no json here", "{not json}", '{"lessons": [], "parameters": {"unknown": 1}}', '{"parameters": "none"}'):
        with pytest.raises(ValueError):
            pvp.parse_coach_reply(bad)


def test_every_console_line_the_trainer_can_send_is_safe():
    lines = list(pvp.ARENA_SETUP) + pvp.kit_commands("Prometheus_A") + ["kill @e[type=end_crystal]", pvp.CLEAR_ARENA, pvp.NETHER + "tp Prometheus_B -5.5 139 0.5 -90 0", "op Steve_123", "kill Prometheus_A"]
    assert all(arena._SAFE_LINE.match(line) for line in lines)
    for evil in ("op Prometheus_A; stop", "say hi\nstop", "execute as @a run say $(id)", "x" * 300, 'tellraw @a {"text":"x"}'):
        assert not arena._SAFE_LINE.match(evil)


def test_arena_server_refuses_commands_when_not_running_and_unsafe_lines(qapp):
    server = arena.ArenaServer()
    assert server.state == "missing" and not server.eula_accepted()
    with pytest.raises(RuntimeError):
        server.command("list")
    failures = []
    server.failed.connect(failures.append)
    server.start()
    assert failures == ["The training arena is not installed yet."]


def test_pvp_result_notification_validation():
    good = {"died": False, "opponentDied": True, "healthEnd": 9.5, "damageTaken": 30, "totemsUsed": 1,
            "crystalsPlaced": 4, "crystalsDetonated": 4, "seconds": 12.5}
    assert validate_notification("pvp.result", good)
    assert validate_notification("pvp.result", {**good, "died": "no"}) is None


# ---- trainer with stand-ins: the fighter with the larger `range` setting wins ----
class FakeArena(QObject):
    stateChanged = Signal(str)
    failed = Signal(str)
    playerJoined = Signal(str)
    playerLeft = Signal(str)

    def __init__(self):
        super().__init__()
        self.state, self.lines = "stopped", []

    def installed(self): return True
    def eula_accepted(self): return True

    def start(self):
        self.state = "running"
        self.stateChanged.emit("running")

    def command(self, line):
        assert arena._SAFE_LINE.match(line), line
        self.lines.append(line)

    def stop(self, wait=False):
        self.state = "stopped"
        self.stateChanged.emit("stopped")


class FakeFighter(QObject):
    notification = Signal(str, dict)
    ring: dict = {}
    policies: list = []

    def __init__(self):
        super().__init__()
        self.stopped = False

    def call(self, method, params=None, on_result=None, on_error=None, timeout_ms=0):
        if method == "bot.connect":
            self.name = params["username"]
            assert params["host"] == "127.0.0.1" and params["auth"] == "offline"
            QTimer.singleShot(0, lambda: self.notification.emit("bot.status", {"phase": "spawned"}))
        elif method == "pvp.start":
            assert set(params["params"]) == set(pvp.SPACE) and params["opponent"] != self.name
            assert params["policy"] is None or len(params["policy"]) == 293
            FakeFighter.policies.append(params["policy"])
            # with a network, "strength" is its first weight; otherwise the range setting
            strength = 10 + params["policy"][0] if params["policy"] is not None else params["params"]["range"]
            FakeFighter.ring[self.name] = (self, strength)
            if len(FakeFighter.ring) == 2:
                (a, ra), (b, rb) = FakeFighter.ring.values()
                FakeFighter.ring = {}
                for me, mine, theirs in ((a, ra, rb), (b, rb, ra)):
                    result = {"died": mine < theirs, "opponentDied": mine > theirs, "healthEnd": 0 if mine < theirs else 10,
                              "damageTaken": 20, "totemsUsed": 2 if mine < theirs else 1, "crystalsPlaced": 3,
                              "crystalsDetonated": 3, "swordHits": 0, "applesEaten": 1, "seconds": 8.0, "endedBy": "test",
                              "blocksPlaced": 2, "enemyCrystals": 3, "predictedTop3": 2, "enemyOffsets": ["1,0", "1,0", "-2,1"]}
                    assert set(params["habits"]) <= {"1,0", "-2,1"}
                    QTimer.singleShot(0, lambda m=me, r=result: m.notification.emit("pvp.result", r))

    def stop(self):
        self.stopped = True


@pytest.fixture
def training(qapp, memory_keyring, tmp_path):
    FakeFighter.ring, FakeFighter.policies = {}, []
    memory_keyring.set_password("prometheus", ANTHROPIC_KEY, "sk-ant-test-0000")
    ctx = AppContext(SecretStore(memory_keyring))
    ctx.config.anthropic.model = "claude-opus-5-5"
    box = NS(ctx=ctx, store=ActivityStore(tmp_path / "a.sqlite"), arena=FakeArena(), log=[], rounds=[], coach_replies=[], coach_requests=[])

    def client_factory(key):
        def create(**kwargs):
            box.coach_requests.append(kwargs)
            return NS(content=[NS(type="text", text=box.coach_replies.pop(0))],
                      usage=NS(input_tokens=900, output_tokens=120, cache_read_input_tokens=0, cache_creation_input_tokens=0))
        return NS(messages=NS(create=create), close=lambda: None)

    box.make = lambda seed=3: pvp.Trainer(ctx, box.store, box.arena, FakeFighter, client_factory, seed=seed)
    box.trainer = box.make()
    box.trainer.pace = 0
    box.trainer.message.connect(box.log.append)
    box.trainer.roundFinished.connect(box.rounds.append)
    yield box
    box.trainer.stop()


def test_training_improves_and_measures_against_the_starting_settings(training, qtbot):
    t = training.trainer
    assert t.champion == pvp.BASELINE and t.generation == 0
    t.start(coach=False)
    qtbot.waitUntil(lambda: len(t.evaluations()) >= 4, timeout=30000)
    t.stop()
    assert t.state == "idle" and training.arena.state == "stopped"
    assert t.champion["range"] > pvp.BASELINE["range"]              # it climbed towards what wins
    shares = [e["share"] for e in t.evaluations()]
    assert shares[-1] == 1.0 and shares == sorted(shares)           # measured, and never got worse here
    assert all(r["kind"] in ("challenge", "evaluation") and r["points"] in (0.0, 0.5, 1.0) for r in training.rounds)
    assert len(training.rounds) == t.rounds_played

    lines = training.arena.lines
    assert lines[:len(pvp.ARENA_SETUP)] == list(pvp.ARENA_SETUP)
    teleports = [l for l in lines if l.startswith(pvp.NETHER + "tp Prometheus_A")]
    assert all(" 139 " in l for l in teleports)                               # fighters stand on the island, above the lava
    assert "give Prometheus_A obsidian 64" in lines and "give Prometheus_A crying_obsidian 32" in lines and lines.count(pvp.CLEAR_ARENA) > 4   # placed blocks are cleared
    assert teleports[0] != teleports[1] and teleports[0] == teleports[2]      # sides alternate every round
    assert "give Prometheus_B end_crystal 128" in lines and not any(l.startswith(("op ", "stop")) for l in lines)
    assert all(l.startswith(pvp.NETHER) for l in lines if l.startswith("execute"))

    again = training.make()                                          # progress survives a restart
    assert again.champion == t.champion and again.generation == t.generation and again.evaluations() == t.evaluations()
    again.reset_progress()
    assert again.champion == pvp.BASELINE and again.evaluations() == [] and training.make().generation == 0


def test_claude_coach_lessons_are_kept_and_its_suggestion_is_tried(training, qtbot):
    t = training.trainer
    training.coach_replies += [
        json.dumps({"lessons": ["Keeping more distance won every round."], "parameters": {**pvp.BASELINE, "range": 6.9}, "why": "range wins"}),
        "I think you should just try harder.",
    ]
    t.start(coach=True)
    qtbot.waitUntil(lambda: len(training.coach_requests) >= 2 and t.generation >= 8, timeout=30000)
    t.stop()
    assert t.lessons() == ["Keeping more distance won every round."]
    assert any("challenger from Claude's suggestion (range 4 → 6.9" in line.replace("→", "→") or
               ("Claude's suggestion" in line and "6.9" in line) for line in training.log)
    assert t.champion["range"] >= 6.9                                # Claude's challenger was fought and won
    assert any("Claude proposed the next challenger. range wins" in line for line in training.log)
    assert any("could not be used" in line for line in training.log)  # the unusable reply did not stop training
    first, second = training.coach_requests[:2]
    assert first["model"] == "claude-opus-5-5" and "crying obsidian" in first["system"] and "Nether roof" in first["system"]
    report = json.loads(first["messages"][0]["content"])
    assert report["recent_rounds"] and report["starting_settings"] == pvp.BASELINE
    assert json.loads(second["messages"][0]["content"])["your_earlier_lessons"] == ["Keeping more distance won every round."]
    assert t.usage.requests >= 2 and t.usage.cost("claude-opus-5-5") > 0


def test_coaching_stops_at_the_cost_limit_but_training_continues(training, qtbot):
    t = training.trainer
    training.ctx.config.agent.max_cost_usd = 0.01
    t.usage.input_tokens = 10_000_000                                # already far past one cent
    t.start(coach=True)
    t.usage.input_tokens = 10_000_000
    qtbot.waitUntil(lambda: t.generation >= 5, timeout=30000)
    t.stop()
    assert training.coach_requests == [] and any("cost limit" in line for line in training.log)


def test_training_stops_cleanly_when_a_fighter_leaves(training, qtbot):
    t = training.trainer
    t.start(coach=False)
    qtbot.waitUntil(lambda: t.state == "fighting", timeout=10000)
    t.fighters[1].notification.emit("bot.status", {"phase": "disconnected", "reason": "kicked"})
    assert t.state == "idle" and any("Prometheus_B left the arena" in line for line in training.log)
    assert training.arena.state == "stopped"


def test_training_page_shows_real_state(training, qtbot):
    from prometheus.ui import theme
    from prometheus.ui.pages.training import TrainingPage
    from PySide6.QtWidgets import QApplication
    theme.apply(QApplication.instance())
    page = TrainingPage(training.ctx, training.trainer)
    qtbot.addWidget(page)
    assert page.start_btn.isEnabled() and not page.stop_btn.isEnabled() and page.install_btn.isHidden()
    assert page.table.item(0, 0).text() == "range" and page.table.item(0, 2).text() == "4"
    assert "not evaluated yet" in page.summary.text()
    page.start_btn.click()
    qtbot.waitUntil(lambda: len(training.trainer.evaluations()) >= 2, timeout=30000)
    training.trainer.stop()
    assert page.chart.points == training.trainer.evaluations() and not page.chart.grab().isNull()
    assert float(page.table.item(0, 2).text()) == training.trainer.champion["range"]
    assert page.log.count() > 5 and "latest evaluation" in page.summary.text()


def test_visitors_become_spectators_and_get_round_announcements(training, qtbot):
    t = training.trainer
    t.start(coach=False)
    qtbot.waitUntil(lambda: t.state == "fighting", timeout=10000)
    for name in ("Steve_123", "Prometheus_A", "bad name; op me"):       # only the real visitor is acted on
        training.arena.playerJoined.emit(name)
    assert t.spectators == ["Steve_123"]
    lines = training.arena.lines
    assert "gamemode spectator Steve_123" in lines and pvp.NETHER + f"tp Steve_123 {pvp.VIEWPOINT}" in lines
    assert lines.count("op Steve_123") == 1                                   # visitors get operator rights
    assert not any(l.startswith(("op Prometheus", "gamemode spectator Prometheus")) for l in lines)   # the bots do not
    assert not any("op me" in l for l in lines)
    before = len(lines)
    qtbot.waitUntil(lambda: any(l.startswith("say ") and "won the round" in l for l in lines[before:]), timeout=10000)
    assert any(l.startswith("say Generation") and "Prometheus_A plays the" in l for l in lines[before:])
    training.arena.playerLeft.emit("Steve_123")
    assert t.spectators == [] and any("Steve_123 left the arena" in m for m in training.log)
    quiet = len([l for l in lines if l.startswith("say ")])
    qtbot.wait(150)
    assert len([l for l in lines if l.startswith("say ")]) == quiet    # nobody watching: nothing is announced
    t.stop()


def test_arena_reads_joins_from_server_log_lines_not_from_chat(qapp):
    server = arena.ArenaServer()
    joined, left = [], []
    server.playerJoined.connect(joined.append)
    server.playerLeft.connect(left.append)

    class Pipe:
        def __init__(self, text): self.text = text
        def readAllStandardOutput(self): return NS(data=lambda: self.text.encode())

    server.state = "running"
    server._proc = Pipe("[12:00:01] [Server thread/INFO]: Steve_123 joined the game\n"
                        "[12:00:02] [Server thread/INFO]: <Griefer> Admin joined the game\n"
                        "[12:00:03] [Server thread/INFO]: Steve_123 left the game\n")
    server._on_output()
    server._proc = None
    assert joined == ["Steve_123"] and left == ["Steve_123"]


def test_trainer_gives_up_instead_of_looping_when_the_arena_will_not_start(training, qtbot):
    t = training.trainer
    starts = []
    training.arena.start = lambda: (starts.append(1), training.arena.failed.emit("Port 25610 is already in use by another program, so the arena could not start."))
    t.start(coach=False)
    qtbot.wait(300)
    assert t.state == "idle" and len(starts) == 1
    assert any("Port 25610 is already in use" in line for line in training.log)


def test_stale_arena_server_is_only_stopped_when_it_really_is_ours(tmp_path, monkeypatch):
    import subprocess, sys
    monkeypatch.setattr(arena, "arena_dir", lambda: tmp_path)
    assert arena.stop_stale_server() is False                            # no pid file
    (tmp_path / "server.pid").write_text("not a number")
    assert arena.stop_stale_server() is False and not (tmp_path / "server.pid").exists()

    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd="/")
    try:
        (tmp_path / "server.pid").write_text(str(other.pid))             # a recycled pid: some unrelated program
        assert arena.stop_stale_server() is False and other.poll() is None
    finally:
        other.kill()

    ours = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "server.jar"], cwd=tmp_path)
    (tmp_path / "server.pid").write_text(str(ours.pid))
    assert arena.stop_stale_server() is True
    assert ours.wait(timeout=5) is not None and not (tmp_path / "server.pid").exists()


def test_opponent_habits_are_learned_and_fed_back_to_the_fighters(training, qtbot):
    t = training.trainer
    assert t.habits() == {} and t.predicted == [0, 0]
    seen = []
    original = FakeFighter.call

    def spy(self, method, params=None, *args, **kwargs):
        if method == "pvp.start":
            seen.append(dict(params["habits"]))
        return original(self, method, params, *args, **kwargs)

    FakeFighter.call = spy
    try:
        t.start(coach=False)
        qtbot.waitUntil(lambda: t.rounds_played >= 6, timeout=20000)
        t.stop()
    finally:
        FakeFighter.call = original
    assert seen[0] == {}                                                # nothing known before the first round
    assert seen[-1] == {"1,0": 0.6667, "-2,1": 0.3333}                  # later rounds are told what was observed
    assert t.habit_counts["1,0"] == 2 * t.habit_counts["-2,1"] and t.predicted[1] == 6 * t.rounds_played
    assert t.predicted[0] / t.predicted[1] == pytest.approx(2 / 3)
    again = training.make()                                             # the model survives a restart
    assert again.habits() == t.habits() and again.predicted == t.predicted
    again.reset_progress()
    assert again.habits() == {} and training.make().predicted == [0, 0]


def test_habit_data_from_a_fighter_is_validated(training, qtbot):
    t = training.trainer
    t.start(coach=False)
    qtbot.waitUntil(lambda: t.state == "fighting" and t.rounds_played >= 1, timeout=10000)
    t.stop()
    before = dict(t.habit_counts)
    t._results = {0: {"died": True, "enemyOffsets": ["9,9", "drop table", 5, "1,0"], "predictedTop3": 0, "enemyCrystals": 1},
                  1: {"died": False, "enemyOffsets": None}}
    t._match = {"kind": "challenge", "params": (pvp.BASELINE, pvp.BASELINE), "points": 0.0, "round": pvp.ROUNDS_PER_MATCH, "stats": []}
    t.state = "fighting"
    t._round_done()
    t.state = "idle"
    assert t.habit_counts == {**before, "1,0": before.get("1,0", 0) + 1}


def test_arena_is_an_island_over_lava_with_a_real_kit():
    setup = list(pvp.ARENA_SETUP)
    lava = next(l for l in setup if l.endswith(" lava"))
    surface = [l for l in setup if l.endswith(" crying_obsidian")]
    assert f" {pvp.LAVA_Y} " in lava and len(surface) == 4
    assert all(f" {pvp.FLOOR_Y - 1} " in l for l in surface) and pvp.FLOOR_Y - pvp.LAVA_Y >= 8      # a real drop
    assert setup.index(lava) < setup.index(surface[0]) and "difficulty hard" in setup
    # every fill stays under the game's 32768-block limit
    for line in setup:
        if " fill " in line:
            x1, y1, z1, x2, y2, z2 = (int(v) for v in line.split(" fill ")[1].split()[:6])
            assert (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1) <= 32768
    kit = pvp.kit_commands("Prometheus_A")
    assert sum("netherite_" in l and "id:protection,lvl:4" in l for l in kit) == 4
    assert any("netherite_sword{Enchantments:[{id:sharpness,lvl:5}" in l for l in kit)
    assert all(x - 0.5 in range(-8, 8) for x, *_ in pvp.SPAWNS)                    # spawns are well inside the island


def test_falling_off_the_island_loses_the_round():
    standing = {"died": False, "totemsUsed": 2, "healthEnd": 3}
    fallen = {"died": False, "fell": True, "totemsUsed": 0, "healthEnd": 20}
    assert pvp.round_points(standing, fallen) == 1.0 and pvp.round_points(fallen, standing) == 0.0
