"""A fast, simplified simulator of a crystal PvP duel, for pre-training the policy network.

Real rounds take about 15 seconds each; a network needs many thousands of them. This model
plays one in well under a millisecond per turn. It is deliberately abstract: one number for
the distance between the fighters, counts instead of block positions, and damage figures
calibrated loosely against rounds fought in the real arena. It is a training aid, not a
faithful copy of Minecraft. What a network learns here still has to prove itself in real
fights, which is what the Training page's evaluation chart measures.

A turn is a quarter of a second (five game ticks), the pace at which the real controller
makes its decisions.
"""
from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass, field
from typing import Callable

from . import policy

MAX_TURNS = 160                # 40 seconds
REACH = 4.4
# Calibrated against rounds fought in the real arena: about 0.35 crystals a second per fighter,
# 5-6 damage per crystal to its target and nearly as much to whoever set it off, and about one
# blocking block every second while defending.
DAMAGE_SCALE = 0.1             # share of raw explosion damage that gets through diamond armour
ATTACK_LANDS = 0.12            # chance per turn that an attack turn ends in a detonation
BLOCK_LANDS = 0.3              # chance per turn that a defend turn ends with a block in place
START = {"health": 20.0, "totems": 2, "apples": 6}
ATTACK, DEFEND, EAT, BACK_OFF, CLOSE_IN = range(5)

Chooser = Callable[[list[float], "Fighter", random.Random], int]


def crystal_damage(distance: float) -> float:
    """Same curve as the bridge uses to compare placements."""
    impact = max(0.0, 1 - distance / 12)
    return 0.0 if impact == 0 else (impact * impact + impact) / 2 * 7 * 12 + 1


@dataclass
class Fighter:
    params: dict
    choose: Chooser
    health: float = START["health"]
    totems: int = START["totems"]
    apples: int = START["apples"]
    cover: int = 0                 # threat spots around it currently blocked
    blocks: int = 0                # blocking blocks placed this round
    eating: int = 0                # turns until the apple is eaten
    want: float = 4.0              # distance it is trying to hold
    want_turns: int = 0
    recent: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    dealt: float = 0.0
    taken: float = 0.0
    died: bool = False


def _closest_spot(defender: Fighter) -> float:
    """How near to the defender a crystal can still stand: every blocked spot pushes it out."""
    return 1.2 + 0.35 * defender.cover


def best_attack(attacker: Fighter, defender: Fighter, distance: float) -> tuple[float, float, float]:
    """(score, crystal distance to defender, crystal distance to attacker) of the best placement, score 0 if none."""
    p = attacker.params
    best = (0.0, 0.0, 0.0)
    r = _closest_spot(defender)
    while r <= 3.6:
        # the crystal can stand on the near side of the defender, the far side, or beside them
        for own in (abs(distance - r), distance + r, math.hypot(distance, r)):
            if p["minSelfDist"] <= own <= REACH:
                score = crystal_damage(r) - p["selfWeight"] * crystal_damage(own)
                if score > best[0]:
                    best = (score, r, own)
        r += 0.4
    return best


def features(me: Fighter, other: Fighter, distance: float, turn: int) -> list[float]:
    attack = best_attack(me, other, distance)[0]
    spot = _closest_spot(me)
    threat = max(0.0, crystal_damage(spot) - 0.5 * crystal_damage(max(0.5, abs(distance - spot))))
    return [
        me.health / 20, me.totems / 2, other.totems / 2, min(distance, 8) / 8,
        min(max(attack / 60, 0), 1), min(threat / 60, 1), (8 - me.cover) / 10, me.apples / 6,
        min(me.blocks, 10) / 10, min(sum(me.recent) / 10, 1), turn / MAX_TURNS, 0.0,
    ]


def rule_based(feats: list[float], me: Fighter, rng: random.Random) -> int:
    """The hand-written tactics the real controller uses when it has no network."""
    p = me.params
    if me.health <= p["eatHealth"] and me.apples > 0:
        return EAT
    if rng.random() < p["defendRate"] and feats[5] * 60 >= p["defendThreat"] and me.cover < 8:
        return DEFEND
    return BACK_OFF if me.health <= p["retreatHealth"] else ATTACK


def network(weights: list[float]) -> Chooser:
    return lambda feats, _me, _rng: policy.choose(weights, feats)


def _hurt(victim: Fighter, amount: float) -> None:
    victim.health -= amount
    victim.taken += amount
    victim.recent[-1] += amount
    if victim.health <= 0:
        if victim.totems > 0:
            victim.totems -= 1
            victim.health = 6.0       # a totem leaves one health plus absorption and regeneration
        else:
            victim.died = True


def play(a: Fighter, b: Fighter, rng: random.Random) -> float:
    """Play one round. Returns points for `a`: 1 win, 0.5 draw, 0 loss."""
    distance = 12.0
    for turn in range(MAX_TURNS):
        actions = []
        for me, other in ((a, b), (b, a)):
            me.recent = me.recent[1:] + [0.0]
            if me.eating > 0:
                me.eating -= 1
                if me.eating == 0:
                    me.health = min(20.0, me.health + 8)
                actions.append(None)
                continue
            action = me.choose(features(me, other, distance, turn), me, rng)
            if action == EAT and (me.apples == 0 or me.health >= 20):
                action = ATTACK
            if action == DEFEND and me.cover >= 8:
                action = ATTACK
            actions.append(action)
        # movement: each fighter pulls the gap towards the distance it wants
        for me, action in ((a, actions[0]), (b, actions[1])):
            if action == BACK_OFF:
                me.want, me.want_turns = 6.0, 8
            elif action == CLOSE_IN:
                me.want, me.want_turns = 2.5, 8
            elif me.want_turns > 0:
                me.want_turns -= 1
            else:
                me.want = me.params["range"] + (3 if me.health <= me.params["retreatHealth"] and me.choose is rule_based else 0)
            distance += max(-1.0, min(1.0, me.want - distance)) * 0.5
            if rng.random() < 0.15 and me.cover > 0:      # strafing leaves its cover behind
                me.cover -= 1
        distance = max(1.0, distance)
        # actions: both act on the same situation, and all damage lands together
        damage = {id(a): 0.0, id(b): 0.0}
        for (me, other), action in zip(((a, b), (b, a)), actions):
            if action is None:
                continue
            if action == EAT:
                me.apples -= 1
                me.eating = 6
            elif action == DEFEND:
                if rng.random() < BLOCK_LANDS:
                    me.cover += 1
                    me.blocks += 1
            else:
                score, to_other, to_me = best_attack(me, other, distance)
                if score > 0 and rng.random() < ATTACK_LANDS:
                    noise = rng.uniform(0.75, 1.25)
                    hit = crystal_damage(to_other) * DAMAGE_SCALE * noise
                    me.dealt += hit
                    damage[id(other)] += hit
                    damage[id(me)] += crystal_damage(to_me) * DAMAGE_SCALE * noise
        for me in (a, b):
            if damage[id(me)] > 0:
                _hurt(me, damage[id(me)])
        for me in (a, b):
            if not me.died:
                me.health = min(20.0, me.health + 0.08)
        if a.died or b.died:
            break
    if a.died != b.died:
        return 0.0 if a.died else 1.0
    mine, theirs = a.totems * 20 + max(a.health, 0), b.totems * 20 + max(b.health, 0)
    return 1.0 if mine > theirs + 4 else 0.0 if theirs > mine + 4 else 0.5


def win_rate(weights: list[float], params: dict, rounds: int, seed: int,
             opponent: Callable[[], Fighter] | None = None) -> float:
    """Share of points the network takes from an opponent (the rule-based fighter by default)."""
    rng = random.Random(seed)
    total = 0.0
    for _ in range(rounds):
        other = opponent() if opponent else Fighter(params, rule_based)
        total += play(Fighter(params, network(weights)), other, rng)
    return total / rounds


def pretrain(params: dict, generations: int = 120, population: int = 24, seed: int = 0,
             start: list[float] | None = None, progress: Callable[[int, int, str], None] | None = None,
             cancel: threading.Event | None = None) -> dict:
    """Train a network by evolution in the simulator.

    Each generation a population of networks (the current average plus noise) is scored on
    rounds against the rule-based fighter and against earlier versions of itself; the best
    quarter becomes the new average. Every five generations the average is checked on fresh
    rounds against the rule-based fighter, and the best-checked network is what is returned.
    """
    rng = random.Random(seed)
    mean = list(start) if start is not None else policy.new_weights(rng, 0.3)
    sigma, keep = 0.45, max(2, population // 4)
    pool: list[list[float]] = []
    history: list[dict] = []
    best = {"weights": list(mean), "win_rate": win_rate(mean, params, 60, seed + 1)}
    history.append({"generation": 0, "win_rate": best["win_rate"]})
    for generation in range(1, generations + 1):
        if cancel is not None and cancel.is_set():
            break
        candidates = [policy.mutate(mean, rng, sigma) for _ in range(population - 1)] + [list(mean)]
        round_seeds = [rng.randrange(1 << 30) for _ in range(14)]      # the same rounds for every candidate
        scored = []
        for weights in candidates:
            total = 0.0
            for i, round_seed in enumerate(round_seeds):
                me = Fighter(params, network(weights))
                rival = (Fighter(params, network(pool[round_seed % len(pool)])) if pool and i % 2
                         else Fighter(params, rule_based))
                total += play(me, rival, random.Random(round_seed)) + 0.004 * (me.dealt - me.taken)
            scored.append((total, weights))
        scored.sort(key=lambda item: -item[0])
        elite = [w for _score, w in scored[:keep]]
        mean = [sum(column) / keep for column in zip(*elite)]
        sigma = max(0.08, sigma * 0.985)
        if generation % 5 == 0 or generation == generations:
            rate = win_rate(mean, params, 60, seed + 1000 + generation)
            history.append({"generation": generation, "win_rate": rate})
            if rate >= best["win_rate"]:
                best = {"weights": list(mean), "win_rate": rate}
            if generation % 20 == 0:
                pool = (pool + [list(mean)])[-5:]
            if progress:
                progress(generation, generations, f"simulator generation {generation}: {rate:.0%} against the rule-based fighter")
    # an honest final number, on rounds the training never saw
    best["win_rate"] = win_rate(best["weights"], params, 300, seed + 99_999)
    return {"weights": best["weights"], "win_rate": best["win_rate"], "history": history,
            "generations": history[-1]["generation"]}
