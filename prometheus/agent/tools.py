"""The tools Claude may call. Each one maps to exactly one validated bridge method.

There is deliberately no tool that runs server commands, shell commands or arbitrary code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ToolInputError(ValueError):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict[str, dict] = field(default_factory=dict)
    limits: dict[str, tuple[float, float]] = field(default_factory=dict)   # numeric range or string length
    method: str | None = None            # bridge method; None = handled locally
    rename: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 15_000
    destructive: bool = False

    def api_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "strict": True,
            "input_schema": {"type": "object", "properties": self.properties,
                             "required": list(self.properties), "additionalProperties": False},
        }

    def validate(self, args: Any) -> dict:
        """Check Claude's arguments ourselves; never rely on the model or the API alone."""
        if not isinstance(args, dict):
            raise ToolInputError("Arguments must be an object.")
        extra = set(args) - set(self.properties)
        if extra:
            raise ToolInputError(f"Unknown argument(s): {', '.join(sorted(extra))}.")
        clean: dict[str, Any] = {}
        for key, spec in self.properties.items():
            if key not in args:
                raise ToolInputError(f"Missing argument: {key}.")
            value, kind = args[key], spec["type"]
            if kind == "array":
                clean[key] = _check_plan(value)
                continue
            ok = (isinstance(value, str) if kind == "string"
                  else isinstance(value, int) and not isinstance(value, bool) if kind == "integer"
                  else isinstance(value, (int, float)) and not isinstance(value, bool))
            if not ok:
                raise ToolInputError(f"{key} must be a {kind}.")
            if key in self.limits:
                low, high = self.limits[key]
                measure = len(value) if kind == "string" else value
                if not (low <= measure <= high):
                    what = "length" if kind == "string" else "value"
                    raise ToolInputError(f"{key} {what} must be between {low:g} and {high:g}.")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolInputError(f"{key} must be one of: {', '.join(spec['enum'])}.")
            clean[key] = value
        if self.name == "say" and clean["message"].lstrip().startswith("/"):
            raise ToolInputError("Messages starting with '/' are server commands and are not allowed.")
        return clean

    def bridge_params(self, args: dict) -> dict:
        return {self.rename.get(k, k): v for k, v in args.items()}

    def verification(self, result: dict) -> str:
        """Plain statement of what the world looked like after the action, from the bridge's own reading."""
        if self.name == "go_to":
            return f"Position after moving: {_pos(result.get('position'))}, {result.get('distanceToTarget')} blocks from the target."
        if self.name == "mine_block":
            picked = ", ".join(f"{n} {k}" for k, n in (result.get("pickedUp") or {}).items()) or "nothing"
            gone = result.get("blockNow") in ("air", "cave_air", "void_air")
            state = "the block is gone" if gone else f"the position now holds {result.get('blockNow')}"
            return f"After mining {result.get('dug')}: {state}; picked up: {picked}."
        if self.name == "mine_blocks":
            picked = ", ".join(f"{n} {k}" for k, n in (result.get("pickedUp") or {}).items()) or "nothing"
            return (f"Mined {result.get('mined')} of {result.get('requested')} {result.get('block')}; picked up: {picked}. "
                    f"Stopped because: {result.get('stoppedBecause')}.")
        if self.name in ("craft", "smelt"):
            gained = ", ".join(f"{n} {k}" for k, n in (result.get("gained") or {}).items()) or "nothing"
            return f"Inventory gained: {gained}." + (" Stopped early." if result.get("stoppedEarly") else "")
        if self.name == "place_block":
            ok = result.get("blockNow") == result.get("placed")
            return (f"The position now holds {result.get('blockNow')}." if ok
                    else f"Placement not confirmed: the position holds {result.get('blockNow')}.")
        if self.name == "eat":
            return f"Hunger went from {result.get('foodBefore')} to {result.get('foodNow')} of 20."
        if self.name == "attack":
            return (f"Outcome: {result.get('outcome')} after {result.get('hits')} hits; health "
                    f"{result.get('healthBefore')} -> {result.get('healthNow')}.")
        if self.name == "explore":
            return f"Moved {result.get('moved')} blocks {result.get('direction')}; now at {_pos(result.get('position'))} in {result.get('biome')}."
        if self.name == "equip":
            return f"Holding {result.get('heldItem')}; armor worn: {', '.join(result.get('armorWorn') or []) or 'none'}."
        if self.name in ("stop_moving", "follow_player"):
            return f"Position: {_pos(result.get('position'))}." if result.get("position") else ""
        return ""


PLAN_STATUSES = ("pending", "in_progress", "done", "failed")


def _check_plan(value: Any) -> list[dict]:
    if not isinstance(value, list) or not (1 <= len(value) <= 12):
        raise ToolInputError("steps must be a list of 1 to 12 steps.")
    steps = []
    for step in value:
        if (not isinstance(step, dict) or set(step) != {"title", "status"} or not isinstance(step["title"], str)
                or not (1 <= len(step["title"]) <= 120) or step["status"] not in PLAN_STATUSES):
            raise ToolInputError("Each step needs a title (1-120 characters) and a status of "
                                 + ", ".join(PLAN_STATUSES) + ".")
        steps.append({"title": step["title"], "status": step["status"]})
    return steps


def _pos(p: Any) -> str:
    return f"({p['x']}, {p['y']}, {p['z']})" if isinstance(p, dict) and {"x", "y", "z"} <= set(p) else "unknown"


_COORD = (-30_000_000, 30_000_000)
_NUM, _INT, _STR = {"type": "number"}, {"type": "integer"}, {"type": "string"}

TOOLS: tuple[Tool, ...] = (
    Tool("get_status", "Read the bot's current health, hunger, position, dimension, biome, held item and inventory. "
                       "Call this to check the outcome of earlier actions.", method="bot.snapshot"),
    Tool("scan_surroundings", "Count the block types within `radius` blocks (1-16) of the bot, with the nearest "
                              "position of each, and list nearby entities and players.",
         {"radius": {**_INT, "description": "1 to 16"}}, {"radius": (1, 16)}, "world.scan", timeout_ms=20_000),
    Tool("find_blocks", "Find the nearest blocks of one type, e.g. oak_log or iron_ore, within `max_distance` blocks (1-64).",
         {"block": {**_STR, "description": "Minecraft 1.20.1 block id without namespace, e.g. oak_log"},
          "max_distance": {**_INT, "description": "1 to 64"}},
         {"block": (1, 64), "max_distance": (1, 64)}, "world.findBlocks", {"max_distance": "maxDistance"}, 20_000),
    Tool("go_to", "Walk to within `range` blocks (0-32) of a position. The pathfinder may clear leaves and vines but "
                  "never digs terrain or places blocks, and gives up after 60 seconds.",
         {"x": _NUM, "y": _NUM, "z": _NUM, "range": {**_INT, "description": "0 to 32; use 1-3 to stand next to something"}},
         {"x": _COORD, "y": _COORD, "z": _COORD, "range": (0, 32)}, "bot.goto", timeout_ms=75_000),
    Tool("follow_player", "Start following a player who is within sight. Keeps following until stop_moving.",
         {"player": {**_STR, "description": "exact player name"}}, {"player": (1, 16)}, "bot.follow"),
    Tool("stop_moving", "Stop walking, following and digging.", method="bot.stopActions"),
    Tool("mine_block", "Walk to and break one block, using the best tool in the inventory, then collect what it drops. "
                       "Refuses blocks that would drop nothing with the tools available.",
         {"x": _INT, "y": _INT, "z": _INT}, {"x": _COORD, "y": _COORD, "z": _COORD}, "bot.dig",
         timeout_ms=110_000, destructive=True),
    Tool("say", "Send a chat message that other players can read. Commands (starting with /) are not allowed.",
         {"message": {**_STR, "description": "up to 200 characters"}}, {"message": (1, 200)}, "bot.chat"),
    Tool("mine_blocks", "Mine up to `count` (1-32) blocks of one type within `max_distance` (1-64), nearest first, "
                        "collecting the drops. One call replaces many mine_block calls. Stops after 150 seconds.",
         {"block": {**_STR, "description": "block id, e.g. oak_log, stone, iron_ore"},
          "count": {**_INT, "description": "1 to 32"}, "max_distance": {**_INT, "description": "1 to 64"}},
         {"block": (1, 64), "count": (1, 32), "max_distance": (1, 64)}, "bot.mineMany", {"max_distance": "maxDistance"},
         timeout_ms=175_000, destructive=True),
    Tool("recipe", "Look up how to craft an item: the ingredient sets and whether a crafting table is needed.",
         {"item": {**_STR, "description": "item id, e.g. wooden_pickaxe"}}, {"item": (1, 64)}, "world.recipe"),
    Tool("craft", "Craft `count` (1-64) of an item from the inventory. Uses a crafting table within 32 blocks when the "
                  "recipe needs one (place one first if there is none).",
         {"item": {**_STR, "description": "item id, e.g. oak_planks"}, "count": {**_INT, "description": "1 to 64"}},
         {"item": (1, 64), "count": (1, 64)}, "bot.craft", timeout_ms=120_000),
    Tool("place_block", "Place a block from the inventory at a position that is empty and touches something solid.",
         {"item": {**_STR, "description": "block item id, e.g. crafting_table"}, "x": _INT, "y": _INT, "z": _INT},
         {"item": (1, 64), "x": _COORD, "y": _COORD, "z": _COORD}, "bot.place", timeout_ms=80_000),
    Tool("equip", "Hold an item or put on armor from the inventory.",
         {"item": {**_STR, "description": "item id"},
          "slot": {**_STR, "enum": ["hand", "off-hand", "head", "torso", "legs", "feet"]}},
         {"item": (1, 64)}, "bot.equip"),
    Tool("eat", "Eat food from the inventory to restore hunger. Pass an empty string to let the bot pick its best safe food.",
         {"item": {**_STR, "description": "food item id, or an empty string"}}, {"item": (0, 64)}, "bot.eat", timeout_ms=30_000),
    Tool("attack", "Fight the nearest mob of one type within 24 blocks until it is defeated, 30 seconds pass, or health "
                   "falls to 6. Uses the best sword or axe. Players are never attacked.",
         {"target": {**_STR, "description": "mob id, e.g. zombie, cow, skeleton"}}, {"target": (1, 64)}, "bot.attack",
         timeout_ms=50_000),
    Tool("smelt", "Smelt up to 16 of an item in a furnace within 32 blocks, using a fuel item from the inventory, and "
                  "take the result. Takes about 10 seconds per item.",
         {"item": {**_STR, "description": "item to smelt, e.g. raw_iron"},
          "fuel": {**_STR, "description": "fuel item, e.g. coal, charcoal, oak_planks"},
          "count": {**_INT, "description": "1 to 16"}},
         {"item": (1, 64), "fuel": (1, 64), "count": (1, 16)}, "bot.smelt", timeout_ms=240_000),
    Tool("explore", "Walk `distance` blocks (8-96) in a compass direction to see new terrain.",
         {"direction": {**_STR, "enum": ["north", "south", "east", "west"]}, "distance": {**_INT, "description": "8 to 96"}},
         {"distance": (8, 96)}, "bot.explore", timeout_ms=80_000),
    Tool("update_plan", "Show the operator your plan: the full list of steps with each one's status. Call it when you "
                        "first plan a multi-step goal and whenever a step's status changes.",
         {"steps": {"type": "array", "description": "1 to 12 steps, in order",
                    "items": {"type": "object", "additionalProperties": False, "required": ["title", "status"],
                              "properties": {"title": {"type": "string"},
                                             "status": {"type": "string", "enum": list(PLAN_STATUSES)}}}}}),
    Tool("remember", "Save a short note that will be given to you at the start of future runs on this server, e.g. where "
                     "your base, furnace or a village is. Saving a topic again replaces its note.",
         {"topic": {**_STR, "description": "short label, e.g. base location"}, "note": {**_STR, "description": "up to 400 characters"}},
         {"topic": (1, 60), "note": (1, 400)}),
    Tool("forget", "Delete a saved note by its topic.", {"topic": _STR}, {"topic": (1, 60)}),
    Tool("wait", "Do nothing for a number of seconds (1-30), e.g. to let something finish.",
         {"seconds": {**_INT, "description": "1 to 30"}}, {"seconds": (1, 30)}),
)
BY_NAME = {t.name: t for t in TOOLS}


def api_tools() -> list[dict]:
    return [t.api_schema() for t in TOOLS]
