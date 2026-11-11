"""The fighters' policy network: a small multilayer perceptron that picks the tactic each turn.

12 inputs describing the fight -> 16 tanh units -> 5 actions; the largest output wins.
The same arithmetic is implemented in the bridge (bridge/src/pvp.ts), which is what runs
during a real fight. This module is the reference used for training and for tests.

No machine-learning library is involved: the network is small enough for plain Python.
"""
from __future__ import annotations

import math
import random

INPUTS = 12
HIDDEN = 16
ACTIONS = ("attack", "defend", "eat", "back_off", "close_in")
SIZE = HIDDEN * INPUTS + HIDDEN + len(ACTIONS) * HIDDEN + len(ACTIONS)      # 293 weights

FEATURES = (
    "own health (0-1)", "own totems left (0-1)", "opponent totems left (0-1)", "distance (0-1, 8 blocks = 1)",
    "best attack available (0-1)", "worst predicted threat (0-1)", "open threat spots (0-1)", "golden apples left (0-1)",
    "blocking blocks placed (0-1)", "damage taken in the last second (0-1)", "share of the round elapsed",
    "an opponent crystal is standing nearby (0 or 1)",
)
assert len(FEATURES) == INPUTS


def new_weights(rng: random.Random, scale: float = 0.5) -> list[float]:
    return [rng.gauss(0, scale) for _ in range(SIZE)]


def outputs(weights: list[float], features: list[float]) -> list[float]:
    """Forward pass. Layout: W1 (HIDDEN rows of INPUTS), b1, W2 (ACTIONS rows of HIDDEN), b2."""
    b1 = HIDDEN * INPUTS
    w2 = b1 + HIDDEN
    b2 = w2 + len(ACTIONS) * HIDDEN
    hidden = []
    for j in range(HIDDEN):
        total = weights[b1 + j]
        row = j * INPUTS
        for i in range(INPUTS):
            total += weights[row + i] * features[i]
        hidden.append(math.tanh(total))
    out = []
    for k in range(len(ACTIONS)):
        total = weights[b2 + k]
        row = w2 + k * HIDDEN
        for j in range(HIDDEN):
            total += weights[row + j] * hidden[j]
        out.append(total)
    return out


def choose(weights: list[float], features: list[float]) -> int:
    """Index of the chosen action (the largest output; the first one wins a tie)."""
    out = outputs(weights, features)
    return max(range(len(out)), key=lambda k: (out[k], -k))


def valid(weights: object) -> bool:
    return (isinstance(weights, list) and len(weights) == SIZE
            and all(isinstance(w, (int, float)) and not isinstance(w, bool) and abs(w) <= 50 and w == w for w in weights))


def mutate(weights: list[float], rng: random.Random, sigma: float) -> list[float]:
    return [max(-50.0, min(50.0, w + rng.gauss(0, sigma))) for w in weights]
