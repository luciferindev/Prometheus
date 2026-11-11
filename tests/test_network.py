"""The policy network, the duel simulator, and network training."""
import math
import random

import pytest

from prometheus.agent import crystal_sim as sim
from prometheus.agent import policy, pvp
from tests.test_pvp import FakeFighter, training  # noqa: F401  (fixture)

P = pvp.BASELINE


def test_forward_pass_matches_the_bridge_reference():
    # The same vector is checked in bridge/test/bridge.test.ts against these numbers.
    weights = [round(math.sin(i * 0.37) * 0.8, 6) for i in range(policy.SIZE)]
    features = [round(0.5 + 0.5 * math.cos(i * 1.1), 6) for i in range(policy.INPUTS)]
    assert policy.SIZE == 293
    assert policy.outputs(weights, features) == pytest.approx([2.4667782, 2.222370485, 1.687579305, 0.930804252, 0.04964309], abs=1e-6)
    assert policy.choose(weights, features) == 0
    weights[policy.SIZE - 1] = 40.0                       # a huge bias on the last action
    assert policy.ACTIONS[policy.choose(weights, features)] == "close_in"


def test_weight_validation_and_mutation():
    rng = random.Random(1)
    w = policy.new_weights(rng)
    assert policy.valid(w) and len(w) == 293
    child = policy.mutate(w, rng, 0.1)
    assert policy.valid(child) and child != w and max(abs(a - b) for a, b in zip(w, child)) < 1.0
    for bad in (None, w[:-1], w + [0.0], [float("nan")] * 293, [1e6] * 293, ["x"] * 293):
        assert not policy.valid(bad)


def test_simulator_is_fair_deterministic_and_rounds_are_decided():
    def rounds(seed, n=300):
        rng = random.Random(seed)
        out = []
        for _ in range(n):
            a, b = sim.Fighter(P, sim.rule_based), sim.Fighter(P, sim.rule_based)
            out.append((sim.play(a, b, rng), a, b))
        return out

    first, again = rounds(3), rounds(3)
    assert [r[0] for r in first] == [r[0] for r in again]                      # same seed, same rounds
    assert 0.42 < sum(r[0] for r in first) / len(first) < 0.58                 # identical fighters split the points
    assert sum(r[1].died or r[2].died for r in first) / len(first) > 0.5       # most rounds end with a death
    assert all(r[1].health <= 20 and 0 <= r[1].totems <= 2 and 0 <= r[1].apples <= 6 and 0 <= r[1].cover <= 8 for r in first)


def test_simulator_rewards_sensible_play():
    def score(chooser):
        rng = random.Random(5)
        return sum(sim.play(sim.Fighter(P, chooser), sim.Fighter(P, sim.rule_based), rng) for _ in range(200)) / 200

    only_attacks = score(lambda feats, me, rng: sim.ATTACK)
    attacks_and_eats = score(lambda feats, me, rng: sim.EAT if me.health <= 8 and me.apples else sim.ATTACK)
    runs_away = score(lambda feats, me, rng: sim.BACK_OFF)
    assert only_attacks < 0.3 and runs_away < 0.3          # never eating, or never engaging, loses
    assert attacks_and_eats > only_attacks + 0.3           # looking after its health is what turns that around


def test_pretraining_improves_a_network_in_the_simulator():
    seen = []
    result = sim.pretrain(P, generations=10, population=12, seed=4, progress=lambda g, n, m: seen.append((g, n)))
    assert policy.valid(result["weights"]) and result["generations"] == 10 and seen[-1] == (10, 10)
    untrained = sim.win_rate(policy.new_weights(random.Random(4), 0.3), P, 150, 11)
    assert result["win_rate"] > untrained + 0.15, (result["win_rate"], untrained)
    assert [h["generation"] for h in result["history"]] == [0, 5, 10]


def test_pretraining_can_be_cancelled():
    import threading
    cancel = threading.Event()
    result = sim.pretrain(P, generations=50, population=8, seed=1, cancel=cancel,
                          progress=lambda g, n, m: cancel.set())
    assert result["generations"] == 5 and policy.valid(result["weights"])


def test_network_training_in_the_arena(training, qtbot, monkeypatch):
    t = training.trainer
    t.start(coach=False, mode="network")
    assert t.state == "idle" and any("no network yet" in line for line in training.log)     # nothing to train yet

    fake = {"weights": [0.0] * 293, "win_rate": 0.8, "generations": 40,
            "history": [{"generation": 0, "win_rate": 0.2}, {"generation": 40, "win_rate": 0.8}]}
    monkeypatch.setattr(sim, "pretrain", lambda *args, **kwargs: fake)
    t.pretrain(40)
    qtbot.waitUntil(lambda: t.policy is not None and not t.pretraining(), timeout=10000)
    assert t.mode == "network" and t.sim_info["generations"] == 40 and t.sim_info["win_rate"] == 0.8
    assert any("80% of the points" in line and "Real fights are the real test" in line for line in training.log)

    t.start(coach=True)                                   # coaching is refused in network mode
    assert not t.coach_enabled and any("Coaching adjusts the settings" in line for line in training.log)
    qtbot.waitUntil(lambda: len(t.evaluations()) >= 2, timeout=30000)
    t.stop()
    # challenge matches: a varied network against the current one; evaluations: network against plain settings
    assert any(p is not None for p in FakeFighter.policies) and any(p is None for p in FakeFighter.policies)
    assert t.policy[0] > 0                                # the stand-in rewards a larger first weight: it was selected for
    assert t.champion == pvp.BASELINE                     # the settings were left alone
    assert all(r["network"] for r in training.rounds) and t.evaluations()[-1]["share"] == 1.0
    again = training.make()                               # the network survives a restart
    assert again.policy == t.policy and again.mode == "network" and again.sim_info == t.sim_info
    again.reset_progress()
    assert again.policy is None and again.mode == "settings" and training.make().policy is None


def test_training_page_network_controls(training, qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from prometheus.ui import theme
    from prometheus.ui.pages.training import TrainingPage
    theme.apply(QApplication.instance())
    monkeypatch.setattr(sim, "pretrain", lambda *a, **k: {"weights": [0.1] * 293, "win_rate": 0.74, "generations": 40,
                                                          "history": [{"generation": 0, "win_rate": 0.2}]})
    page = TrainingPage(training.ctx, training.trainer)
    qtbot.addWidget(page)
    assert "No network yet" in page.network_info.text() and page.pretrain_btn.text() == "Pre-train in simulator"
    page.pretrain_btn.click()
    qtbot.waitUntil(lambda: training.trainer.policy is not None and not training.trainer.pretraining(), timeout=10000)
    assert "293 weights" in page.network_info.text() and "74%" in page.network_info.text()
    assert page.pretrain_btn.text() == "Pre-train more in simulator"
    page.mode.setCurrentIndex(page.mode.findData("network"))
    page.start_btn.click()
    qtbot.waitUntil(lambda: training.trainer.rounds_played >= 2, timeout=20000)
    training.trainer.stop()
    assert training.trainer.mode == "network" and any(p is not None for p in FakeFighter.policies)
