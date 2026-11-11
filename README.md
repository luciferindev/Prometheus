# PROMETHEUS

A native Qt6 desktop application for Arch Linux that configures, launches and monitors an
autonomous **vanilla Minecraft Java 1.20.1** agent driven by the Claude API.

**Current milestone: Phase 3** (survival actions, task plans, memory, in-game chat control and
the Vision page), on top of Phase 2 (Claude tool-calling agent) and Phase 1 (application, setup,
connection). The interface only contains what works.

Create and Fabric support were removed from the scope on 2026-11-10: the project targets vanilla
Minecraft only.

## What works today

| Area | State |
|---|---|
| Native Qt6 window, dark theme, keyboard shortcuts, Wayland and X11 | Working |
| Setup wizard: dependency checks and repairs, installation check, server test, account, API key | Working |
| Dashboard: connection, emergency stop, live vitals, position, inventory, current goal and subtask | Working |
| Agent: goals, pause / resume / stop, mid-run instructions, conversations that continue between goals | Working; verified with the live Claude API |
| Survival actions: bulk mining, crafting, placing, smelting, equipping, exploring | Working; verified on a real server |
| Survival actions: eating, fighting | Written and unit-tested; **not yet exercised on a real server** |
| Task plan shown live; notes the agent saves per server and gets back in later runs | Working |
| In-game chat control: one named player gives goals and instructions in chat, the agent answers in chat | Working in tests; not yet tried with a second real player |
| Vision page: 3D view (and a top-down map) with the pathfinder's route, searched positions and what Claude looked at | Working; verified on a real server |
| Training: crystal PvP duels between two bots in a private arena, with settings tuned across rounds | Working; verified with real fights |
| Training: Claude as coach between evaluations | Working in tests; **not yet run against the live API** |
| Activity view: timeline, search, filters, JSON inspector, run history, token and cost readout | Working |
| Limits: cost and step limits, optional approval before mining, free or careful movement | Working |
| Chests, sleeping, farming, trading, enchanting, brewing, the Nether and the End | Not started |
| PKGBUILD, release packaging | Not started |

### How it was verified

- **Live Claude API (Phase 2 tools).** Real runs with `claude-opus-5-5` completed with tool calls,
  prompt-cache hits and no API errors. The Phase 3 tools have not yet been used by the live API;
  if the API rejects the larger strict tool set, the app retries without strict schemas.
- **Real server.** On a local vanilla 1.20.1 server the bridge completed the whole early game from
  an empty inventory: logs, planks, crafting table (placed), sticks, wooden pickaxe, cobblestone,
  furnace (placed), and charcoal from smelting. Each step was confirmed from the bot's inventory
  or the block in the world.
- **Scripted agent on the real server.** The real window ran a planned, multi-step goal (find oak,
  walk there, mine, craft planks, save a note) while the Vision page drew the live route and the
  1,046 positions the A* search examined.
- **Training.** The application started the arena server itself, connected two bots and ran real
  crystal PvP rounds: crystals placed and detonated, totems used, deaths confirmed by the server
  ("was blown up by"). Twenty-eight rounds across three sessions; the best settings were replaced
  when a challenger won its match. A visitor that joined was given operator rights and put in spectator mode
  above the arena on the Nether roof, could see both fighters, and received the round announcements.
  In two further real rounds a defending bot placed seven blocking blocks, and 8 of 11 opponent
  crystals stood on one of the three predicted spots.
- **Neural network.** A network pre-trained for 40 generations took 77% of the points from the
  hand-written tactics in the simulator (300 rounds it never trained on; untrained networks take
  about 15-20%). Put into the real arena unchanged, it took 5 of 8 points from the hand-written
  tactics (4 wins, 2 losses, 2 draws). Eight rounds is a small sample.
- **Automated tests.** 78 Python tests and 17 Node tests, using a protocol-level fixture, stand-in
  fighters and a scripted Claude.

### Not yet verified

- Eating and fighting mobs on a real server (the test world was set to peaceful).
- Whether training actually converges on stronger play. Only a handful of generations have been
  run, no evaluation against the starting settings has completed on a real server yet, and four
  rounds per match is a noisy signal.
- Claude's coaching against the live API, and the arena's own download-and-install step in the app
  (the live sessions reused the local test server).
- Chat control with a real second player in the game.
- **Microsoft sign-in**, and therefore online-mode servers and "Open to LAN" worlds.
- Long goals such as "beat the game": the agent has the early-game tools, but nothing here shows it
  can get that far, and it cannot enter the Nether or the End yet.

## The agent

Claude receives the goal plus a structured observation (status, an 8-block scan and any notes it
saved earlier), then loops: it writes a short plan, calls tools, and reads what the bridge observed
afterwards. Low-level work is deterministic code, not Claude: pathfinding, tool selection, digging,
item pickup, the crafting clicks, furnace handling and the fight loop.

| Tool | What it does |
|---|---|
| `get_status`, `scan_surroundings`, `find_blocks` | Observe: vitals and inventory, nearby blocks and entities, nearest blocks of a type |
| `go_to`, `follow_player`, `explore`, `stop_moving` | Move: to a position, after a player, in a compass direction |
| `mine_block`, `mine_blocks` | Break one block, or up to 32 of a type in one call; drops are collected and counted |
| `recipe`, `craft` | Look up ingredients; craft, using a nearby crafting table when needed |
| `place_block`, `equip`, `eat` | Place from the inventory, hold or wear items, eat safe food |
| `smelt` | Run a nearby furnace with an item and a fuel, and take the result |
| `attack` | Fight the nearest mob of a type until it is defeated, 30 s pass, or health reaches 6. Never players |
| `update_plan` | Show the list of steps and their status in the application |
| `remember`, `forget` | Keep short notes per server for future runs |
| `say`, `wait` | Chat (anything starting with `/` is refused); pause 1-30 seconds |

**Movement.** In *free* mode (default) the pathfinder moves like a player: it may dig through
terrain and pillar or bridge with dirt and cobblestone it carries. In *careful* mode it only clears
leaves and vines and never places anything. Collecting a drop the bot has just mined may dig in
either mode. Containers, beds and similar blocks are never broken by movement.

**In-game chat control.** Off by default. When enabled in Settings with a player name, every chat
line from that player is a goal (when the agent is idle) or an instruction (while it works);
"stop" stops it; the agent answers in chat. Chat from everyone else reaches Claude marked as
untrusted. On offline-mode servers anyone can join under any name, so use this only on servers you
control.

**Vision page.** A 3D view of the blocks within 16 blocks of the agent (drag to rotate, scroll to
zoom), or a top-down map of 28 blocks. Both draw four layers: the route the pathfinder chose, the
positions its A* search examined, the block positions returned to Claude by its observation tools,
and nearby players and mobs. Overlays are drawn over the terrain so they stay visible behind hills.
It is rendered in the application, not inside the game.

## Crystal PvP training

Two bots duel with end crystals in a private arena that PROMETHEUS runs itself: a vanilla 1.20.1
server on 127.0.0.1, offline mode. The arena is an island of crying obsidian hanging eleven
blocks above an 89 by 89 block lake of lava, built on the Nether roof where nothing spawns. Crystals
cannot stand on the island itself, so a fighter places real obsidian next to its opponent and then a
crystal on top. A fighter that falls into the lava loses the round. Each fighter gets a crystal PvP
kit: Protection IV netherite armour, a Sharpness V netherite sword, end crystals, obsidian, crying
obsidian, golden apples and totems (no ender pearls or respawn anchors, which the fighters cannot use). The application owns the server
process, so it hands out kits and resets the arena through the server console using fixed command
templates. Nothing Claude writes reaches the console.

What "learning" means here:

- **The fighting is code.** A fight is decided in 50 ms ticks, far faster than a request to Claude,
  so a deterministic controller in the bridge does it: totem in the off-hand, golden apples when
  low, holding a distance while strafing, and choosing where to place and detonate crystals by
  comparing expected damage to the opponent and to itself. It runs on eleven numeric settings.
- **Predictive defence.** Each tick a fighter ranks the spots around itself where the opponent's
  next crystal is most likely: spots the opponent can reach, that would hurt the fighter, and
  would not hurt the opponent much. It can spend a turn covering the top spot with crying
  obsidian, which is blast-proof and cannot hold a crystal. How often it defends, and how
  dangerous a spot must be, are settings the tuning adjusts.
- **Prediction learns from data.** Every opponent crystal a fighter sees is counted by where it
  stood relative to that fighter. Those counts persist across rounds and sessions and shift the
  next prediction. The Training page reports the share of opponent crystals that stood on one of
  the three predicted spots.
- **Neural network (optional).** A small policy network (12 inputs describing the fight, 16 hidden
  units, 5 tactics: attack, block the predicted spot, eat, back off, close in; 293 weights) can
  replace the hand-written rules for choosing the tactic each turn. Real rounds are far too slow to
  train it from scratch, so it is pre-trained by evolution in a fast, simplified simulator of the
  duel (`agent/crystal_sim.py`, about four minutes for 40 generations), then evaluated and
  fine-tuned in real fights, where a slightly varied copy challenges the current network. The
  network runs inside the bridge; no machine-learning library is used.
- **Tuning** is an evolutionary search over the settings. A challenger (a changed copy of the
  best settings) fights the best so far over four rounds with sides swapped, and replaces it if it
  wins the match.
- **Coaching** is optional. After each evaluation Claude reads the recent fight statistics and the
  prediction record, writes lessons that are kept and given back to it next time, and proposes
  the next challenger. It counts against the cost limit in Settings.
- **Progress is measured.** Every three generations the best settings fight the original starting
  settings (with the network, if one is being trained); the chart is that result. There is no
  guarantee of "mastery": the simulator is a simplification, the network is small, and Claude's
  own model is not retrained.

**Watching the fights.** While training runs, join the arena from Minecraft Java 1.20.1 on this
computer: Multiplayer → Direct Connection → `127.0.0.1:25610`. Anyone who joins and is not one of
the two fighters is given operator rights, put in spectator mode at a viewing spot above the arena,
and sees each round and its result announced in chat. The arena only listens on this machine, so
those operator rights only ever go to someone using this computer.

If the application is closed or killed, the arena server is stopped with it; a server left over
from an earlier session is detected and stopped before a new one starts.

Player combat only exists here: the bridge refuses to start a fight unless the bot is connected to
a server on this machine, and the agent's own `attack` tool never targets players.

**What the activity view shows.** "Plan" entries are the text Claude wrote before acting.
"Reasoning" entries are the reasoning summaries the API returns when the model provides them;
Claude's private chain of thought is not available to any application and is never invented here.
"Result" entries state what the bridge read back from the game after the action.

**API details.** Requests use the official `anthropic` SDK with strict tool schemas, automatic
prompt caching, and the effort level from Settings. On models that support it, server-side refusal
fallbacks are enabled (`fallbacks: "default"`), and dropped automatically if the API rejects the
parameter. Cost is an estimate from token counts and list prices dated 2026-11-03; a model without
a known price shows no estimate and is limited by the step count only.

## Compatibility (checked 2026-11-10)

| Component | Version |
|---|---|
| Minecraft | 1.20.1 (protocol 763) |
| Mineflayer | 4.39.0 (needs Node 22+) |
| mineflayer-pathfinder | 2.4.5 |
| Anthropic SDK (Python) | 2.8.0 |
| Claude model | chosen from the live model list for your key; `claude-opus-5-5` is preselected when available |

## Architecture

```
prometheus/            Python 3 + PySide6 application
  core/                  settings, keyring, toolchain checks, verified installer, instance checks,
                         network policy, Claude key validation, bridge client
  agent/tools.py         tool definitions, argument validation, verification text
  agent/runner.py        the observe / plan / act / verify loop, limits, pause, stop
  agent/pricing.py       token usage and cost estimates
  agent/pvp.py           crystal PvP trainer: tuning, evaluation, Claude as coach
  agent/policy.py        the policy network (reference implementation)
  agent/crystal_sim.py   fast duel simulator and network pre-training
  agent/store.py         SQLite record of runs, activity events, notes and training progress
  core/arena.py          the private training server: install, start, console
  ui/                    theme, wizard, dashboard, agent page, vision page, settings, logs
bridge/                Node.js + TypeScript + Mineflayer
  src/protocol.ts        zod schemas for every request
  src/rpc.ts             JSON-RPC 2.0 over stdio
  src/bot.ts             bot session, observations, actions, Microsoft sign-in
  src/pvp.ts             crystal PvP combat controller
  test/                  automated tests, protocol fixture, manual real-server probes
packaging/             desktop entry, per-user installer
tests/                 pytest + pytest-qt
```

The application and the bridge talk JSON-RPC 2.0 over the bridge's stdin/stdout, so there is no
listening socket. Requests are schema-validated in the bridge, tool arguments are validated in
the application before that, and notifications are validated again on the way back.

## Install

System packages: `python`, `pyside6`, and optionally `nodejs` and `npm`.

```bash
packaging/install.sh
```

This creates `~/.local/share/prometheus`, a launcher in `~/.local/bin`, and an application-menu
entry, without root. Start PROMETHEUS from the application menu; the setup wizard does the rest.

## Development

```bash
python -m venv --system-site-packages .venv && .venv/bin/pip install -e '.[dev]'
```

```bash
cd bridge && npm ci && npm run build && npm test
```

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/python -m prometheus
```

`PROMETHEUS_HOME=/some/dir` redirects settings, logs, the activity database and runtimes.

## Keyboard shortcuts

`Ctrl+1` to `Ctrl+6` pages · `Ctrl+G` goal box · `Ctrl+.` stop the agent · `Ctrl+Return` connect ·
`Ctrl+D` disconnect · `Ctrl+Shift+E` emergency stop · `Ctrl+F` search logs · `Ctrl+Q` quit

## Security

- The Anthropic key lives only in the Secret Service keyring. Without a keyring it is held in
  memory for the session and never written to disk.
- Microsoft sign-in uses the device-code flow on microsoft.com; no password is entered here.
- Servers outside this machine and private networks are refused unless explicitly allowed.
- The agent has no tool for server commands, shell commands or code, and chat messages starting
  with `/` are refused in both the application and the bridge.
- In-game chat from other players is passed to Claude marked as untrusted, not as instructions.
- Every run has a cost limit and a step limit. Mining can require your approval.
- Chat control is off by default and tied to one player name; the agent never attacks players.
- Player combat is limited to the private training arena on this machine. Visitors to that arena
  are given operator rights there; it accepts connections from this computer only.
- Emergency stop kills the bridge process: the bot leaves the server and any run ends.
- Logs redact API keys, bearer tokens and JWTs. Activity history stays in a local SQLite file.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Could not reach host:port (connection refused)" | Nothing is listening. Start the world and use Open to LAN, or start the server; use the port shown in chat or `server.properties`. |
| "The server is X (protocol N)…" | The server is not 1.20.1. |
| Kicked with "Failed to verify username" | The server is in online mode and the agent uses an offline name. Switch to a Microsoft account in Settings. |
| "…resolves to a public address" | Public servers are blocked by default. Enable them in Settings only for servers you may automate on. |
| Agent: "The bot is not in a world" | Connect on the Dashboard first. |
| Agent: "No Anthropic API key is stored" | Add the key in Settings or Setup. |
| Agent: "Reached the cost limit" / "limit of N steps" | Raise the limits in Settings, or give a smaller goal. |
| Agent: "No walkable path was found" | Careful movement cannot reach it. Switch Movement to Free in Settings. |
| Agent: "…drops nothing without the right tool" | The bot has no suitable pickaxe or similar; it needs to craft one first. |
| Agent: "…needs a crafting table within 32 blocks" | It has to craft and place a crafting table first. |
| Chat control does nothing | Check that it is enabled in Settings and the player name matches your in-game name exactly. |
| Training: "Port 25610 is already in use" | Another program uses that port; close it and start training again. |
| Training: rounds end as draws with no crystals | A fighter could not see its opponent; stop and start training again. |
| Vision page is empty | Connect on the Dashboard; the map appears once the bot is in the world. |
| Keyring row says "no wallet yet" | Your desktop asks you to create a wallet the first time a key is saved. |
| Bridge "Dependencies not installed yet" | Open Setup and press "Install bridge dependencies". |
| Inventory shows "Unidentified modded item (id N)" | The server has mods; their items and blocks are not recognised. |
