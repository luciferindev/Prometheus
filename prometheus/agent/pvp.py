"""Crystal PvP training.

What "learning" means here, precisely:

* The fighting is a deterministic controller in the bridge with a set of numeric settings.
* **Prediction** improves with data: every opponent crystal a fighter sees is counted by where it
  stood relative to that fighter, and those counts shift the next prediction. The share of
  opponent crystals that stood on one of the three predicted spots is reported.
* **Tuning** is an evolutionary search over those settings: a challenger (a changed copy of
  the best settings so far) fights the current best over several rounds with sides swapped,
  and replaces it if it wins the match.
* **Coaching** is Claude reading the recent fight statistics, writing down lessons, and
  proposing the next challenger. Its lessons are kept and given back to it next time.
* Progress is measured, not assumed: every few generations the best settings fight the
  original starting settings, and that win rate is what the chart shows.

Claude's own model is not retrained by any of this.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Callable

import anthropic
from PySide6.QtCore import QObject, QTimer, Signal

from ..context import AppContext
from ..core import arena as arena_mod
from ..core.bridge import BridgeClient
from ..core.secrets import ANTHROPIC_KEY
from ..ui.workers import run_async
from . import crystal_sim, policy as policy_net
from .pricing import Usage
from .store import ActivityStore

log = logging.getLogger("prometheus.pvp")

# setting -> (lowest, highest, starting value, what it does)
SPACE: dict[str, tuple[float, float, float, str]] = {
    "range": (1.5, 7.0, 4.0, "preferred distance to the opponent (blocks)"),
    "minSelfDist": (1.0, 7.0, 3.0, "never detonate a crystal closer to itself than this (blocks)"),
    "selfWeight": (0.0, 4.0, 1.0, "how much its own expected damage counts against a crystal"),
    "crystalInterval": (1.0, 20.0, 5.0, "game ticks between crystal attempts"),
    "meleeRange": (0.0, 3.5, 0.0, "use the sword inside this distance (0 = never)"),
    "eatHealth": (0.0, 18.0, 8.0, "eat a golden apple at or below this health"),
    "strafePeriod": (2.0, 60.0, 14.0, "game ticks between changes of strafe direction"),
    "retreatHealth": (0.0, 14.0, 6.0, "at or below this health, stay 3 blocks further away"),
    "defendRate": (0.0, 1.0, 0.25, "share of its turns spent blocking the spot where it predicts the next crystal"),
    "defendThreat": (0.0, 60.0, 12.0, "only block predicted spots that would hurt it more than this"),
    "habitWeight": (0.0, 4.0, 1.0, "how strongly learned opponent habits shift its prediction"),
}
BASELINE = {k: v[2] for k, v in SPACE.items()}
FIGHTERS = ("Prometheus_A", "Prometheus_B")
START_TOTEMS = 2
ROUND_SECONDS = 60        # enchanted netherite takes a while to break down
ROUNDS_PER_MATCH = 4
EVAL_EVERY = 3            # generations between evaluations against the starting settings
# The arena is an island hanging over a lake of lava, built on the Nether roof (the flat bedrock
# ceiling at y=127, where nothing spawns). The island's surface is crying obsidian: blast-proof, and
# end crystals cannot stand on it, so a fighter has to place real obsidian before it can place a crystal.
NETHER = "execute in minecraft:the_nether run "
LAVA_Y = 128
FLOOR_Y = 139             # where the fighters' feet are; the island's surface is the block below
SPAWNS = ((-5.5, FLOOR_Y, 0.5, -90), (5.5, FLOOR_Y, 0.5, 90))
VIEWPOINT = f"0.5 {FLOOR_Y + 9} -19.5 0 30"      # off one edge of the island, looking down at the middle
CLEAR_ARENA = NETHER + f"fill -13 {FLOOR_Y} -13 13 {FLOOR_Y + 6} 13 air"     # removes the blocks the fighters placed
_TOP = FLOOR_Y - 1


def _layer(y: int, block: str, *boxes: tuple[int, int]) -> list[str]:
    """One level of the island: overlapping rectangles (half-widths along x and z) that make a rounded shape."""
    return [NETHER + f"fill {-x} {y} {-z} {x} {y} {z} {block}" for x, z in boxes]


ARENA_SETUP = (
    "gamerule doImmediateRespawn true", "gamerule doDaylightCycle false", "gamerule doWeatherCycle false",
    "gamerule announceAdvancements false", "gamerule doFireTick false", "difficulty hard",
    NETHER + "forceload add -48 -48 47 47",
    NETHER + f"fill -14 {LAVA_Y} -14 14 {LAVA_Y + 7} 14 air",                 # the walled arena older versions built here
    NETHER + f"fill -45 {LAVA_Y} -45 45 {LAVA_Y} 45 blackstone",              # a rim, so the lava stays in its basin
    NETHER + f"fill -44 {LAVA_Y} -44 44 {LAVA_Y} 44 lava",                    # the lake: 89 by 89 blocks
    *_layer(_TOP, "crying_obsidian", (12, 5), (11, 8), (8, 11), (5, 12)),
    *_layer(_TOP - 1, "blackstone", (10, 4), (9, 7), (7, 9), (4, 10)),
    *_layer(_TOP - 2, "blackstone", (7, 3), (5, 5), (3, 7)),
    *_layer(_TOP - 3, "basalt", (4, 2), (2, 4)),
    *_layer(_TOP - 4, "basalt", (1, 1)),
    CLEAR_ARENA,
)

_PROTECTION = "{Enchantments:[{id:protection,lvl:4},{id:unbreaking,lvl:3}]}"
_SWORD = "{Enchantments:[{id:sharpness,lvl:5},{id:unbreaking,lvl:3}]}"


def kit_commands(name: str) -> list[str]:
    """A crystal PvP kit: enchanted netherite, totems, crystals, obsidian to stand them on.

    Left out on purpose: ender pearls, respawn anchors and experience bottles, which the
    fighters have no code to use.
    """
    return [
        f"clear {name}",
        f"item replace entity {name} armor.head with netherite_helmet{_PROTECTION}",
        f"item replace entity {name} armor.chest with netherite_chestplate{_PROTECTION}",
        f"item replace entity {name} armor.legs with netherite_leggings{_PROTECTION}",
        f"item replace entity {name} armor.feet with netherite_boots{_PROTECTION}",
        f"item replace entity {name} weapon.offhand with totem_of_undying",
        f"give {name} netherite_sword{_SWORD} 1", f"give {name} end_crystal 128", f"give {name} obsidian 64",
        f"give {name} totem_of_undying {START_TOTEMS - 1}", f"give {name} golden_apple 6", f"give {name} crying_obsidian 32",
        f"effect give {name} minecraft:instant_health 1 10 true",
        f"effect give {name} minecraft:saturation 1 20 true",
    ]


def clamp(params: dict[str, Any]) -> dict[str, float]:
    """Keep only known settings, as numbers inside their allowed range."""
    out = dict(BASELINE)
    for key, (low, high, _default, _doc) in SPACE.items():
        value = params.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
            out[key] = round(min(high, max(low, float(value))), 2)
    return out


def mutate(params: dict[str, float], rng: random.Random, strength: float = 0.18) -> dict[str, float]:
    """A changed copy: two or three settings nudged by a share of their range."""
    child = dict(params)
    for key in rng.sample(list(SPACE), rng.choice((2, 3))):
        low, high, _default, _doc = SPACE[key]
        child[key] = child[key] + rng.gauss(0, strength) * (high - low)
    return clamp(child)


def round_points(mine: dict, theirs: dict) -> float:
    """Points for one round from this fighter's side: 1 win, 0.5 draw, 0 loss."""
    # Falling off the island into the lava loses the round, the same as dying.
    i_died = bool(mine.get("died") or mine.get("fell"))
    they_died = bool(theirs.get("died") or theirs.get("fell"))
    if they_died and not i_died:
        return 1.0
    if i_died and not they_died:
        return 0.0
    # Nobody (or both) died: whoever kept more totems and health is ahead.
    my = (START_TOTEMS - mine.get("totemsUsed", 0)) * 20 + mine.get("healthEnd", 0)
    their = (START_TOTEMS - theirs.get("totemsUsed", 0)) * 20 + theirs.get("healthEnd", 0)
    return 1.0 if my > their + 4 else 0.0 if their > my + 4 else 0.5


COACH_SYSTEM = """You coach a bot that fights Minecraft crystal PvP duels in a private training arena. \
The fighting itself is automatic; you can only change its settings. After each batch of fights you \
get the statistics. Work out what went wrong, write lessons you want to remember, and propose \
settings for the next challenger.

Settings (allowed range, meaning):
{space}

Facts about the arena: an island of crying obsidian hanging over a lake of lava on the Nether roof. \
Crystals cannot stand on the island itself, so a fighter first places obsidian next to the opponent and \
then a crystal on top of it; obsidian placed earlier in the round stays and can be reused by either side. \
A fighter that falls off the island loses the round. Each fighter starts with enchanted netherite armour, \
{totems} totems, 6 golden apples, end crystals, obsidian and crying obsidian. A fighter predicts where the \
opponent's next crystal will stand and can cover that spot with crying obsidian (blast-proof, and crystals \
cannot be placed on it); it also learns where opponents really place crystals. Blocking costs a turn that \
could have been an attack. A crystal hurts everyone near it, including whoever set it off. \
A round lasts at most {seconds} seconds; a fighter that dies loses it.

Reply with one JSON object and nothing else:
{{"lessons": ["one or two short sentences each, at most 3"], "parameters": {{"range": 4.0, ...every setting...}}, "why": "one sentence"}}"""


def parse_coach_reply(text: str) -> tuple[list[str], dict[str, float], str]:
    """Extract lessons and settings from Claude's reply. Raises ValueError if unusable."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("The coach's reply contained no JSON.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"The coach's reply was not valid JSON: {exc.msg}.") from exc
    params = data.get("parameters")
    if not isinstance(data, dict) or not isinstance(params, dict) or not (set(params) & set(SPACE)):
        raise ValueError("The coach's reply had no usable settings.")
    lessons = [str(item).strip()[:400] for item in data.get("lessons", []) if str(item).strip()][:3]
    return lessons, clamp(params), str(data.get("why", ""))[:300]


class Trainer(QObject):
    stateChanged = Signal(str)        # idle | preparing | fighting | coaching | stopping
    message = Signal(str)             # one line for the page's log
    roundFinished = Signal(dict)
    progressChanged = Signal()        # generation, champion, evaluations or lessons changed
    spectatorsChanged = Signal()
    simProgress = Signal(int, int, str)   # pre-training in the simulator: generation, of, text

    def __init__(self, ctx: AppContext, store: ActivityStore, arena: Any = None,
                 fighter_factory: Callable[[], Any] | None = None,
                 client_factory: Callable[[str], Any] | None = None, seed: int | None = None):
        super().__init__(ctx)
        self.ctx, self.store = ctx, store
        self.arena = arena if arena is not None else arena_mod.ArenaServer(self)
        self._make_fighter = fighter_factory or (lambda: BridgeClient(self))
        self._client_factory = client_factory or (lambda key: anthropic.Anthropic(api_key=key, max_retries=2, timeout=120.0))
        self.rng = random.Random(seed)
        self.state = "idle"
        self.coach_enabled = False
        self.usage = Usage()
        self.port = arena_mod.PORT
        self.pace = 1.0                  # multiplier on the pauses between steps (tests use 0)
        saved = store.pvp_state()
        self.champion: dict[str, float] = clamp(saved.get("champion", BASELINE))
        self.generation: int = int(saved.get("generation", 0))
        self.rounds_played: int = int(saved.get("rounds", 0))
        # opponent crystal placements seen so far, by offset from the target: "dx,dz" -> count
        self.habit_counts: dict[str, int] = {k: int(v) for k, v in saved.get("habits", {}).items()
                                             if re.fullmatch(r"-?[0-4],-?[0-4]", str(k)) and isinstance(v, int) and v > 0}
        self.predicted: list[int] = [int(v) for v in saved.get("predicted", [0, 0])][:2] or [0, 0]   # hits, seen
        # The policy network (None until one has been pre-trained) and what the simulator said about it.
        self.policy: list[float] | None = saved.get("policy") if policy_net.valid(saved.get("policy")) else None
        self.sim_info: dict = saved.get("sim") if isinstance(saved.get("sim"), dict) else {}
        self.mode = "network" if saved.get("mode") == "network" and self.policy else "settings"
        self._pretraining: Any = None
        self._completed = self.generation     # last generation whose match was fought to the end
        self.fighters: list[Any] = []
        self.spectators: list[str] = []
        self._spawned = [False, False]
        self._gen = 0
        self._suggestion: dict[str, float] | None = None
        self._recent: list[dict] = []
        self._match: dict = {}
        self._results: dict[int, dict] = {}
        self.arena.stateChanged.connect(self._arena_state)
        self.arena.failed.connect(lambda text: self._abort(text))
        self.arena.playerJoined.connect(self._player_joined)
        self.arena.playerLeft.connect(self._player_left)

    # ---- public ----
    def habits(self) -> dict[str, float]:
        """What the fighters are told: how often opponents used each spot, as a share of all seen."""
        total = sum(self.habit_counts.values())
        return {k: round(v / total, 4) for k, v in self.habit_counts.items()} if total else {}

    def _state_to_save(self) -> dict:
        return {"champion": self.champion, "generation": self._completed, "rounds": self.rounds_played,
                "habits": self.habit_counts, "predicted": self.predicted, "policy": self.policy,
                "sim": self.sim_info, "mode": self.mode}

    # ---- the neural network: pre-training in the simulator ----
    def pretraining(self) -> bool:
        return self._pretraining is not None

    def pretrain(self, generations: int = 40) -> None:
        """Train (or continue training) the policy network in the simulator, off the GUI thread."""
        if self.state != "idle" or self._pretraining is not None:
            return
        self.message.emit(f"Pre-training the network in the simulator for {generations} generations…")
        self._pretraining = run_async(
            crystal_sim.pretrain, dict(self.champion), generations, 24, self.rng.randrange(1 << 30), self.policy,
            on_progress=self.simProgress.emit, on_done=self._pretrained, on_error=self._pretrain_failed)
        self.progressChanged.emit()

    def cancel_pretraining(self) -> None:
        if self._pretraining is not None:
            self._pretraining.cancel.set()

    def _pretrained(self, result: dict) -> None:
        self._pretraining = None
        if not policy_net.valid(result.get("weights")):
            return self._pretrain_failed("the simulator returned an unusable network")
        earlier = int(self.sim_info.get("generations", 0))
        self.policy = result["weights"]
        self.sim_info = {"generations": earlier + int(result["generations"]), "win_rate": float(result["win_rate"]),
                         "history": (self.sim_info.get("history", []) + [
                             {"generation": earlier + h["generation"], "win_rate": h["win_rate"]}
                             for h in result["history"][1:]])[-200:]}
        self.mode = "network"
        self.store.pvp_save_state(self._state_to_save())
        self.message.emit(f"Pre-training finished: in the simulator the network takes {result['win_rate']:.0%} of the "
                          "points from the hand-written tactics (300 rounds it never trained on). Real fights are "
                          "the real test: start training to evaluate it in the arena.")
        self.progressChanged.emit()

    def _pretrain_failed(self, error: str) -> None:
        self._pretraining = None
        self.message.emit(f"Pre-training failed: {error}")
        self.progressChanged.emit()

    def evaluations(self) -> list[dict]:
        return self.store.pvp_evaluations()

    def lessons(self) -> list[str]:
        return self.store.pvp_lessons()

    def start(self, coach: bool, mode: str | None = None) -> None:
        if self.state != "idle" or self._pretraining is not None:
            return
        if mode in ("settings", "network"):
            self.mode = mode
        if self.mode == "network" and self.policy is None:
            self.mode = "settings"
            return self.message.emit("There is no network yet. Pre-train one in the simulator first.")
        if self.mode == "network" and coach:
            coach = False
            self.message.emit("Coaching adjusts the settings, so it is off while the network is being trained.")
        self.coach_enabled = coach
        self._gen += 1
        self.usage = Usage()
        self._set("preparing")
        if self.arena.state == "running":
            self._connect_fighters()
        else:
            self.message.emit("Starting the training arena server…")
            self.arena.start()

    def stop(self, reason: str = "Training stopped.", wait: bool = False) -> None:
        if self.state in ("idle", "stopping"):
            return
        self._gen += 1
        self._set("stopping")
        for fighter in self.fighters:
            fighter.stop()
        self.fighters, self._spawned = [], [False, False]
        self.arena.stop(wait=wait)
        if self.spectators:
            self.spectators = []
            self.spectatorsChanged.emit()
        # A generation interrupted part-way does not count.
        self.generation = self._completed
        self.store.pvp_save_state(self._state_to_save())
        self.message.emit(reason)
        self._set("idle")
        self.progressChanged.emit()

    def reset_progress(self) -> None:
        if self.state != "idle":
            return
        self.store.pvp_reset()
        self.champion, self.generation, self.rounds_played, self._completed = dict(BASELINE), 0, 0, 0
        self.habit_counts, self.predicted = {}, [0, 0]
        self.policy, self.sim_info, self.mode = None, {}, "settings"
        self.progressChanged.emit()

    # ---- visitors ----
    def _player_joined(self, name: str) -> None:
        """Anyone who joins the arena and is not a fighter becomes a spectator at the viewing spot."""
        if name in FIGHTERS or not re.fullmatch(r"[A-Za-z0-9_]{3,16}", name):
            return
        if name not in self.spectators:
            self.spectators.append(name)
        try:
            # Visitors get operator rights on this private, local-only server, as its owner asked.
            self._console(f"op {name}", f"gamemode spectator {name}", NETHER + f"tp {name} {VIEWPOINT}")
        except (RuntimeError, ValueError):
            return
        self.message.emit(f"{name} joined the arena: operator rights given, spectating from above the arena.")
        self.spectatorsChanged.emit()

    def _player_left(self, name: str) -> None:
        if name in self.spectators:
            self.spectators.remove(name)
            self.message.emit(f"{name} left the arena.")
            self.spectatorsChanged.emit()

    def _announce(self, text: str) -> None:
        """A line in the arena's chat for whoever is watching. Fixed wording, safe characters only."""
        if self.spectators:
            try:
                self._console("say " + re.sub(r"[^A-Za-z0-9_ .:,\-]", "", text)[:150])
            except (RuntimeError, ValueError):
                pass

    # ---- plumbing ----
    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.stateChanged.emit(state)

    def _abort(self, text: str) -> None:
        if self.state != "idle":
            self.stop(f"Training stopped: {text}")

    def _later(self, ms: int, fn: Callable[[], None]) -> None:
        gen = self._gen
        QTimer.singleShot(int(ms * self.pace), lambda: fn() if gen == self._gen and self.state != "idle" else None)

    def _arena_state(self, state: str) -> None:
        if self.state == "preparing" and state in ("stopped", "missing"):
            return self._abort("the arena server did not start. See the Logs page.")
        if self.state == "preparing" and state == "running" and not self.fighters:
            self._connect_fighters()
        elif self.state in ("fighting", "coaching") and state != "running":
            self._abort("the arena server stopped.")

    def _connect_fighters(self) -> None:
        self.message.emit("Arena is running. Connecting the two fighters…")
        self.fighters, self._spawned = [self._make_fighter(), self._make_fighter()], [False, False]
        for index, fighter in enumerate(self.fighters):
            fighter.notification.connect(lambda method, params, i=index: self._on_notification(i, method, params))
            fighter.call("bot.connect", {"host": "127.0.0.1", "port": self.port, "auth": "offline",
                                         "username": FIGHTERS[index], "version": "1.20.1"},
                         None, lambda error: self._abort(error))
        self._later(45_000, lambda: self._abort("the fighters could not join the arena in time.")
                    if not all(self._spawned) else None)

    def _on_notification(self, index: int, method: str, params: dict) -> None:
        if self.state == "idle":
            return
        if method == "bot.status":
            if params["phase"] == "spawned" and not self._spawned[index]:
                self._spawned[index] = True
                if all(self._spawned):
                    self._build_arena()
            elif params["phase"] == "disconnected":
                self._abort(f"{FIGHTERS[index]} left the arena ({params.get('reason', '')}).")
        elif method == "pvp.result" and self.state == "fighting":
            self._results[index] = params
            other = 1 - index
            if other not in self._results:        # the round is decided: stop the other side too
                self.fighters[other].call("pvp.stop", {}, None, lambda _e: None)
            else:
                self._round_done()

    def _console(self, *lines: str) -> None:
        for line in lines:
            self.arena.command(line)

    def _build_arena(self) -> None:
        try:
            self._console(*ARENA_SETUP)
        except (RuntimeError, ValueError) as exc:
            return self._abort(str(exc))
        self.message.emit("Arena built. Training begins.")
        self._later(1500, self._next_generation)

    # ---- generations and matches ----
    def _next_generation(self) -> None:
        self._set("fighting")
        self.generation += 1
        if self.mode == "network" and self.policy is not None:
            # Fine-tuning on real fights: a slightly changed copy of the network challenges the current one.
            challenger_net = policy_net.mutate(self.policy, self.rng, 0.08)
            self.message.emit(f"Generation {self.generation}: a varied copy of the network challenges the current one.")
            return self._start_match("challenge", self.champion, self.champion, (challenger_net, self.policy))
        if self._suggestion is not None:
            challenger, source = self._suggestion, "Claude's suggestion"
            self._suggestion = None
        else:
            challenger, source = mutate(self.champion, self.rng), "a random variation"
        changed = ", ".join(f"{k} {self.champion[k]:g} → {v:g}" for k, v in challenger.items() if v != self.champion[k])
        self.message.emit(f"Generation {self.generation}: challenger from {source} ({changed or 'no change'}).")
        self._start_match("challenge", challenger, self.champion)

    def _start_match(self, kind: str, first: dict, second: dict,
                     policies: tuple[list[float] | None, list[float] | None] = (None, None)) -> None:
        self._match = {"kind": kind, "params": (first, second), "policies": policies, "points": 0.0, "round": 0,
                       "stats": []}
        self._next_round()

    def _next_round(self) -> None:
        match = self._match
        match["round"] += 1
        swap = match["round"] % 2 == 0          # alternate sides so neither set of settings owns a corner
        self._results = {}
        match.setdefault("policies", (None, None))
        commands = ["kill @e[type=end_crystal]", "kill @e[type=item]", CLEAR_ARENA]
        for index, name in enumerate(FIGHTERS):
            x, y, z, yaw = SPAWNS[index ^ swap]
            commands.append(NETHER + f"tp {name} {x} {y} {z} {yaw} 0")
            # Respawn in the arena too, so a death does not send the fighter back to the overworld.
            commands.append(NETHER + f"spawnpoint {name} {int(x - 0.5)} {y} {int(z - 0.5)}")
            commands += kit_commands(name)
        try:
            self._console(*commands)
        except (RuntimeError, ValueError) as exc:
            return self._abort(str(exc))
        who = ("challenger against the best so far" if match["kind"] == "challenge"
               else "best settings against the starting settings")
        first_name = FIGHTERS[0]
        self._announce(f"Generation {self.generation}, round {match['round']} of {ROUNDS_PER_MATCH}: "
                       f"{first_name} plays the {who.split(' against ')[0]}")
        # The first round also has to load the Nether for both bots.
        self._later(6000 if self.rounds_played == 0 or match["round"] == 1 else 3200, self._fight)

    def _fight(self) -> None:
        round_id = (self._gen, self.generation, self._match["kind"], self._match["round"])
        for index, fighter in enumerate(self.fighters):
            fighter.call("pvp.start", {"opponent": FIGHTERS[1 - index], "params": self._match["params"][index],
                                       "seconds": ROUND_SECONDS, "habits": self.habits(),
                                       "policy": self._match.get("policies", (None, None))[index]},
                         None, lambda error: self._abort(error))
        QTimer.singleShot((ROUND_SECONDS + 12) * 1000, lambda: self._abort("a round did not finish.")
                    if round_id == (self._gen, self.generation, self._match["kind"], self._match["round"])
                    and len(self._results) < 2 else None)

    def _round_done(self) -> None:
        match = self._match
        first, second = dict(self._results[0]), dict(self._results[1])
        # Each bot also reports what it saw happen to the other; a death counts if either saw it.
        first["died"] = bool(first.get("died") or second.get("opponentDied"))
        second["died"] = bool(second.get("died") or first.get("opponentDied"))
        first["fell"] = bool(first.get("fell") or second.get("opponentFell"))
        second["fell"] = bool(second.get("fell") or first.get("opponentFell"))
        # Learn from what both fighters saw: where the opponent's crystals really stood, and whether
        # that had been predicted.
        for result in (first, second):
            for offset in result.get("enemyOffsets") or []:
                if isinstance(offset, str) and re.fullmatch(r"-?[0-4],-?[0-4]", offset):
                    self.habit_counts[offset] = self.habit_counts.get(offset, 0) + 1
            self.predicted[0] += int(result.get("predictedTop3") or 0)
            self.predicted[1] += int(result.get("enemyCrystals") or 0)
        points = round_points(first, second)
        match["points"] += points
        self.rounds_played += 1
        record = {"generation": self.generation, "kind": match["kind"], "round": match["round"], "points": points,
                  "first": {k: first.get(k) for k in ("died", "healthEnd", "damageTaken", "totemsUsed", "crystalsPlaced",
                                                      "crystalsDetonated", "swordHits", "applesEaten", "blocksPlaced",
                                                      "obsidianPlaced", "fell", "enemyCrystals", "predictedTop3",
                                                      "seconds", "endedBy")},
                  "second": {k: second.get(k) for k in ("died", "healthEnd", "damageTaken", "totemsUsed", "crystalsPlaced",
                                                        "crystalsDetonated", "swordHits", "applesEaten", "blocksPlaced",
                                                        "obsidianPlaced", "fell", "enemyCrystals", "predictedTop3")},
                  "params": match["params"][0], "network": match.get("policies", (None, None))[0] is not None,
                  "choices": first.get("choices") if isinstance(first.get("choices"), dict) else {}}
        match["stats"].append(record)
        self._recent = (self._recent + [record])[-12:]
        self.store.pvp_add_round(record)
        self.roundFinished.emit(record)
        # A fighter that fell is swimming in lava with a totem's fire resistance. It has lost the
        # round already, so end it there; it respawns on the island, no longer burning.
        fallen = [FIGHTERS[i] for i, result in enumerate((first, second)) if result["fell"] and not result["died"]]
        try:
            self._console(*[f"kill {name}" for name in fallen])
        except (RuntimeError, ValueError) as exc:
            return self._abort(str(exc))
        for name in fallen:
            self._announce(f"{name} fell into the lava")
        self._announce({1.0: f"{FIGHTERS[0]} won the round", 0.0: f"{FIGHTERS[1]} won the round", 0.5: "Draw"}[points])
        if match["round"] < ROUNDS_PER_MATCH:
            return self._later(1500, self._next_round)
        self._match_done()

    def _match_done(self) -> None:
        match = self._match
        share = match["points"] / ROUNDS_PER_MATCH
        if match["kind"] == "challenge":
            if match["points"] > ROUNDS_PER_MATCH / 2:
                if match.get("policies", (None, None))[0] is not None:
                    self.policy = list(match.get("policies", (None, None))[0])
                self.champion = dict(match["params"][0])
                self.message.emit(f"The challenger won {match['points']:g} of {ROUNDS_PER_MATCH}: it is the new best.")
            else:
                self.message.emit(f"The challenger scored {match['points']:g} of {ROUNDS_PER_MATCH}: the best settings stay.")
            self._completed = self.generation
            self.store.pvp_save_state(self._state_to_save())
            self.progressChanged.emit()
            if self.generation % EVAL_EVERY == 0:
                self.message.emit("Evaluation: the best settings against the starting settings.")
                network = self.policy if self.mode == "network" else None
                return self._later(1500, lambda: self._start_match("evaluation", self.champion, dict(BASELINE),
                                                                   (network, None)))
            return self._later(1500, self._next_generation)
        # evaluation finished
        self.store.pvp_add_evaluation(self.generation, share)
        self.store.pvp_save_state(self._state_to_save())
        self.message.emit(f"Evaluation after generation {self.generation}: {share:.0%} of the points against the "
                          "starting settings.")
        self.progressChanged.emit()
        if self.coach_enabled:
            return self._coach()
        self._later(1500, self._next_generation)

    # ---- Claude as coach ----
    def _coach(self) -> None:
        model = self.ctx.config.anthropic.model
        cost = self.usage.cost(model)
        if not model:
            self.message.emit("Coaching skipped: no Claude model is configured.")
            return self._later(1000, self._next_generation)
        if cost is not None and cost >= self.ctx.config.agent.max_cost_usd:
            self.coach_enabled = False
            self.message.emit(f"Coaching switched off: the cost limit of ${self.ctx.config.agent.max_cost_usd:.2f} was "
                              "reached. Tuning continues without it.")
            return self._later(1000, self._next_generation)
        self._set("coaching")
        self.message.emit("Claude is reviewing the recent fights…")
        report = {"best_settings_so_far": self.champion, "starting_settings": BASELINE,
                  "prediction": {"opponent_crystals_seen": self.predicted[1],
                                 "stood_on_one_of_three_predicted_spots": self.predicted[0],
                                 "most_used_spots_relative_to_target": dict(sorted(self.habits().items(), key=lambda kv: -kv[1])[:6])},
                  "evaluations_vs_starting_settings": self.evaluations()[-6:],
                  "recent_rounds": self._recent, "your_earlier_lessons": self.lessons()[-10:]}
        gen = self._gen
        run_async(self._ask_coach, model, json.dumps(report),
                  on_done=lambda r: self._coached(gen, r), on_error=lambda e: self._coach_failed(gen, e))

    def _ask_coach(self, model: str, report: str) -> tuple[str, Any, float]:
        key = self.ctx.secrets.get(ANTHROPIC_KEY)
        if not key:
            raise RuntimeError("no Anthropic API key is stored")
        client = self._client_factory(key)
        system = COACH_SYSTEM.format(
            space="\n".join(f"- {k}: {low:g} to {high:g}, {doc}" for k, (low, high, _d, doc) in SPACE.items()),
            totems=START_TOTEMS, seconds=ROUND_SECONDS)
        started = time.monotonic()
        try:
            response = client.messages.create(model=model, max_tokens=4000, system=system,
                                              messages=[{"role": "user", "content": report}])
        except anthropic.APIError as exc:
            raise RuntimeError(f"the Claude API call failed ({type(exc).__name__})") from None
        finally:
            if hasattr(client, "close"):
                client.close()
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        return text, response.usage, time.monotonic() - started

    def _coached(self, gen: int, result: tuple[str, Any, float]) -> None:
        if gen != self._gen or self.state == "idle":
            return
        text, usage, latency = result
        self.usage.add(usage, latency)
        try:
            lessons, params, why = parse_coach_reply(text)
        except ValueError as exc:
            self.message.emit(f"Claude's advice could not be used ({exc}); continuing with a random variation.")
        else:
            for lesson in lessons:
                self.store.pvp_add_lesson(lesson)
            self._suggestion = params
            self.message.emit(f"Claude proposed the next challenger. {why}")
        self.progressChanged.emit()
        self._set("fighting")
        self._later(800, self._next_generation)

    def _coach_failed(self, gen: int, error: str) -> None:
        if gen != self._gen or self.state == "idle":
            return
        self.message.emit(f"Coaching failed: {error}. Continuing with a random variation.")
        self._set("fighting")
        self._later(800, self._next_generation)
