import { readdir, rm } from 'node:fs/promises'
import { join } from 'node:path'
import mineflayer from 'mineflayer'
import type { Bot } from 'mineflayer'
import mc from 'minecraft-protocol'
import prismarineAuth from 'prismarine-auth'
import pathfinderPkg from 'mineflayer-pathfinder'
import { Vec3 } from 'vec3'
import { AppError } from './rpc.ts'
import { CrystalFight } from './pvp.ts'
import type { FightParams, Habits } from './pvp.ts'
import { MC_VERSION } from './protocol.ts'
import type { BotPhase, BotState, ConnectParams, PingParams } from './protocol.ts'

const { Authflow, Titles } = prismarineAuth
const { pathfinder, Movements, goals } = pathfinderPkg

const GOTO_TIMEOUT_MS = 60_000
const AIR = new Set(['air', 'cave_air', 'void_air'])

type Notify = (method: string, params: Record<string, unknown>) => void

// Same title/flow minecraft-protocol uses, so a wizard sign-in and a later
// connect share one token cache.
const MSA_OPTIONS = { authTitle: Titles.MinecraftNintendoSwitch, deviceType: 'Nintendo', flow: 'live' } as const

function chatToText (value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || typeof value !== 'object') return String(value ?? '')
  const v = value as { text?: unknown, translate?: unknown, extra?: unknown, value?: unknown }
  if (v.value !== undefined && v.text === undefined) return chatToText(v.value)
  const extra = Array.isArray(v.extra) ? v.extra.map(chatToText).join('') : ''
  return `${chatToText(v.text ?? v.translate ?? '')}${extra}`
}

export async function ping (params: PingParams): Promise<Record<string, unknown>> {
  try {
    const res = await mc.ping({ host: params.host, port: params.port, closeTimeout: 8000 }) as any
    if (res.version === undefined) throw new AppError('The server answered with a legacy (pre-1.7) status.')
    return {
      motd: chatToText(res.description).trim(),
      versionName: String(res.version.name),
      protocol: Number(res.version.protocol),
      playersOnline: Number(res.players?.online ?? 0),
      playersMax: Number(res.players?.max ?? 0),
      latencyMs: Number(res.latency ?? 0),
    }
  } catch (err) {
    if (err instanceof AppError) throw err
    const code = (err as NodeJS.ErrnoException).code
    const why = code === 'ECONNREFUSED'
      ? 'connection refused: nothing is listening on that address and port'
      : code === 'ENOTFOUND'
        ? 'host name could not be resolved'
        : (err as Error).message
    throw new AppError(`Could not reach ${params.host}:${params.port} (${why}).`)
  }
}

export class BotSession {
  private bot: Bot | null = null
  private phase: BotPhase = 'idle'
  private timer: NodeJS.Timeout | null = null
  private lastState = ''
  private notify: Notify
  private moves: { free: unknown, careful: unknown } | null = null
  private movementMode: 'free' | 'careful' = 'free'
  private host = ''
  private fight: CrystalFight | null = null
  private authDir: string

  constructor (notify: Notify, authDir: string) {
    this.notify = notify
    this.authDir = authDir
  }

  private onMsaCode = (data: any): void => {
    // Device-code sign-in: the user types this code on microsoft.com. No password passes through us.
    this.notify('auth.deviceCode', {
      userCode: String(data.user_code),
      verificationUri: String(data.verification_uri),
      expiresIn: Number(data.expires_in ?? 900),
    })
  }

  async signIn (username: string): Promise<Record<string, unknown>> {
    const flow = new Authflow(username, this.authDir, { ...MSA_OPTIONS }, this.onMsaCode)
    try {
      const res = await flow.getMinecraftJavaToken({ fetchProfile: true }) as any
      if (res.profile?.name === undefined) throw new AppError('This Microsoft account does not own Minecraft: Java Edition.')
      return { profileName: String(res.profile.name), uuid: String(res.profile.id) }
    } catch (err) {
      if (err instanceof AppError) throw err
      throw new AppError(`Microsoft sign-in failed: ${(err as Error).message}`)
    }
  }

  async signOut (): Promise<Record<string, unknown>> {
    let removed = 0
    for (const name of await readdir(this.authDir).catch(() => [] as string[])) {
      if (name.endsWith('-cache.json')) {
        await rm(join(this.authDir, name), { force: true })
        removed++
      }
    }
    return { removed }
  }

  private setPhase (phase: BotPhase, reason?: string): void {
    this.phase = phase
    this.notify('bot.status', reason === undefined ? { phase } : { phase, reason })
  }

  connect (params: ConnectParams): Record<string, unknown> {
    if (this.bot !== null) throw new AppError('Already connected or connecting. Disconnect first.')
    this.setPhase('connecting')
    this.host = params.host
    const bot = mineflayer.createBot({
      host: params.host,
      port: params.port,
      username: params.username,
      auth: params.auth,
      version: MC_VERSION,
      profilesFolder: this.authDir,
      onMsaCode: this.onMsaCode,
      hideErrors: true,
      logErrors: false,
    } as any)
    this.bot = bot
    guardCommandTree((bot as any)._client, () => this.notify('bot.event', {
      kind: 'warning',
      text: 'The server\'s command list uses modded argument types this client cannot decode; it was ignored. ' +
        'Playing is unaffected, but commands with signed message arguments cannot be sent.',
    }))

    bot.loadPlugin(pathfinder)
    // Feed for the Vision page: the route the pathfinder chose and the positions its A* search
    // examined to find it. Throttled; a search can report many times a second.
    let lastPath = 0
    bot.on('path_update' as any, (r: any) => {
      const now = Date.now()
      if (now - lastPath < 200 && r.status !== 'success' && r.status !== 'noPath') return
      lastPath = now
      const explored: number[][] = []
      const closed: Set<string> | undefined = r.context?.closedDataSet
      if (closed !== undefined) {
        const every = Math.max(1, Math.ceil(closed.size / 2500))
        let i = 0
        for (const hash of closed) {
          if (i++ % every !== 0) continue
          const parts = String(hash).split(',').map(Number)
          if (parts.length >= 3 && parts.every(Number.isFinite)) explored.push([parts[0] as number, parts[1] as number, parts[2] as number])
        }
      }
      this.notify('path.update', {
        status: String(r.status), cost: num(r.cost) ?? 0, timeMs: Math.round(num(r.time) ?? 0),
        visited: num(r.visitedNodes) ?? 0, generated: num(r.generatedNodes) ?? 0,
        path: (r.path as any[] ?? []).slice(0, 600).map(n => [Math.floor(n.x), Math.floor(n.y), Math.floor(n.z)]),
        explored,
      })
    })
    const ended = (status: string) => () => this.notify('path.update', { status, cost: 0, timeMs: 0, visited: 0, generated: 0, path: [], explored: [] })
    bot.on('goal_reached' as any, ended('arrived'))
    bot.on('path_stop' as any, ended('stopped'))
    bot.once('login', () => this.setPhase('logged_in'))
    bot.on('chat', (username: string, message: string) => {
      if (username !== bot.username) this.notify('bot.chat', { username: String(username), message: String(message).slice(0, 256) })
    })
    bot.once('spawn', () => {
      // Two ways of getting around. "free": like a player, it may dig through terrain and pillar
      // or bridge with dirt and cobblestone from its inventory. "careful": it only clears leaves
      // and vines and never places anything. Containers, beds and similar are never broken.
      const free = new Movements(bot)
      const careful = new Movements(bot)
      careful.allow1by1towers = false
      careful.scafoldingBlocks = []
      for (const block of Object.values((bot.registry as any).blocksByName) as Array<{ id: number, name: string }>) {
        if (!block.name.endsWith('_leaves') && block.name !== 'vine') careful.blocksCantBreak.add(block.id)
      }
      this.moves = { free, careful }
      ;(bot as any).pathfinder.thinkTimeout = 10_000
      ;(bot as any).pathfinder.setMovements(this.moves[this.movementMode])
      this.setPhase('spawned')
      this.pushState(true)
    })
    bot.on('death', () => this.notify('bot.event', { kind: 'death', text: 'The bot died.' }))
    bot.on('kicked', reason => this.notify('bot.event', { kind: 'kicked', text: chatToText(parseMaybeJson(reason)) }))
    bot.on('error', err => this.notify('bot.event', { kind: 'error', text: explainError(err) }))
    bot.once('end', reason => {
      if (this.bot !== bot) return
      this.teardown()
      this.setPhase('disconnected', String(reason ?? 'connection closed'))
    })
    this.timer = setInterval(() => this.pushState(false), 500)
    return { accepted: true }
  }

  disconnect (): Record<string, unknown> {
    const bot = this.bot
    if (bot === null) return { wasConnected: false }
    this.teardown()
    try { bot.quit('disconnect requested') } catch { /* socket already gone */ }
    try { (bot as any)._client?.end?.() } catch { /* ignore */ }
    this.setPhase('disconnected', 'disconnect requested')
    return { wasConnected: true }
  }

  private teardown (): void {
    this.fight?.stop('disconnected')
    this.fight = null
    if (this.timer !== null) clearInterval(this.timer)
    this.timer = null
    this.bot = null
    this.lastState = ''
  }

  snapshot (): BotState | null {
    const bot = this.bot
    if (bot === null || this.phase !== 'spawned' || bot.entity === undefined) return null
    const pos = bot.entity.position
    let biome: string | null = null
    try {
      // prismarine-block leaves biome.name empty on 1.20.1; the id resolves through the registry
      // the server sent at login.
      const id = (bot.blockAt(pos) as any)?.biome?.id
      const name = typeof id === 'number' ? (bot.registry as any).biomes?.[id]?.name : undefined
      biome = typeof name === 'string' && name !== '' ? name : null
    } catch { /* chunk not loaded yet */ }
    const held = bot.heldItem
    return {
      username: bot.username ?? null,
      health: num(bot.health),
      food: num(bot.food),
      saturation: num(bot.foodSaturation),
      armor: armorPoints([5, 6, 7, 8].map(slot => bot.inventory.slots[slot]?.name ?? null)),
      level: num(bot.experience?.level),
      position: { x: round(pos.x), y: round(pos.y), z: round(pos.z) },
      dimension: bot.game?.dimension ?? null,
      biome,
      gameMode: bot.game?.gameMode ?? null,
      timeOfDay: num(bot.time?.timeOfDay),
      heldItem: held !== null && held !== undefined ? itemLabel(held).displayName : null,
      inventory: bot.inventory.items().map(i => ({ slot: i.slot, ...itemLabel(i), count: i.count })),
    }
  }

  // ---- observations and actions used by the agent ----

  private live (): Bot {
    if (this.bot === null || this.phase !== 'spawned' || this.bot.entity === undefined) {
      throw new AppError('The bot is not in the world.')
    }
    return this.bot
  }

  private here (bot: Bot): { x: number, y: number, z: number } {
    const p = bot.entity.position
    return { x: round(p.x), y: round(p.y), z: round(p.z) }
  }

  scan (radius: number): Record<string, unknown> {
    const bot = this.live()
    const origin = bot.entity.position.floored()
    const found = new Map<string, { count: number, nearest: Vec3, dist: number }>()
    let unloaded = 0
    const vertical = Math.min(radius, 8)
    for (let dx = -radius; dx <= radius; dx++) {
      for (let dz = -radius; dz <= radius; dz++) {
        for (let dy = -vertical; dy <= vertical; dy++) {
          const pos = origin.offset(dx, dy, dz)
          const block = bot.blockAt(pos)
          if (block === null) { unloaded++; continue }
          if (AIR.has(block.name)) continue
          const dist = pos.distanceTo(origin)
          const entry = found.get(block.name)
          if (entry === undefined) found.set(block.name, { count: 1, nearest: pos, dist })
          else { entry.count++; if (dist < entry.dist) { entry.nearest = pos; entry.dist = dist } }
        }
      }
    }
    const blocks = [...found].sort((a, b) => b[1].count - a[1].count).slice(0, 40).map(([name, e]) => ({
      name, count: e.count, nearest: { x: e.nearest.x, y: e.nearest.y, z: e.nearest.z }, distance: round(e.dist),
    }))
    const entities = Object.values(bot.entities)
      .filter(e => e !== bot.entity && e.position.distanceTo(bot.entity.position) <= 32)
      .map(e => ({
        kind: e.type === 'player' ? 'player' : String((e as any).kind ?? e.type),
        name: e.type === 'player' ? String(e.username) : String(e.name ?? 'unknown'),
        distance: round(e.position.distanceTo(bot.entity.position)),
        position: { x: round(e.position.x), y: round(e.position.y), z: round(e.position.z) },
      }))
      .sort((a, b) => a.distance - b.distance).slice(0, 25)
    return { center: { x: origin.x, y: origin.y, z: origin.z }, radius, blocks, blockTypesOmitted: Math.max(0, found.size - 40), unloadedPositions: unloaded, entities }
  }

  /** Top-down view around the bot: the highest non-air block of each column. */
  map (radius: number): Record<string, unknown> {
    const bot = this.live()
    const origin = bot.entity.position.floored()
    const palette: string[] = []
    const index = new Map<string, number>()
    const cells: number[] = []
    const heights: number[] = []
    for (let dz = -radius; dz <= radius; dz++) {
      for (let dx = -radius; dx <= radius; dx++) {
        let cell = -1
        let height = 0
        for (let dy = 14; dy >= -14; dy--) {
          const block = bot.blockAt(origin.offset(dx, dy, dz))
          if (block === null) break
          if (AIR.has(block.name)) continue
          let i = index.get(block.name)
          if (i === undefined) { i = palette.length; palette.push(block.name); index.set(block.name, i) }
          cell = i
          height = dy
          break
        }
        cells.push(cell)
        heights.push(height)
      }
    }
    const entities = Object.values(bot.entities)
      .filter(e => e !== bot.entity && Math.abs(e.position.x - origin.x) <= radius && Math.abs(e.position.z - origin.z) <= radius && e.name !== 'item')
      .slice(0, 60)
      .map(e => ({ kind: e.type === 'player' ? 'player' : String((e as any).kind ?? e.type), name: e.type === 'player' ? String(e.username) : String(e.name ?? 'unknown'), x: round(e.position.x), z: round(e.position.z) }))
    return { center: { x: origin.x, y: origin.y, z: origin.z }, radius, yaw: round(bot.entity.yaw), palette, cells, heights, entities }
  }

  /**
   * The blocks around the bot that have at least one face open to air, for the 3D view.
   * Each entry is [dx, dy, dz, palette index, face mask]; mask bits: +x 1, -x 2, +y 4, -y 8, +z 16, -z 32.
   */
  voxels (radius: number): Record<string, unknown> {
    const bot = this.live()
    const origin = bot.entity.position.floored()
    const up = Math.min(radius, 9)
    const w = 2 * radius + 1
    const h = 2 * up + 1
    // 0 = open (air, plants, unloaded), otherwise palette index + 1
    const grid = new Int16Array(w * h * w)
    const palette: string[] = []
    const index = new Map<string, number>()
    const at = (x: number, y: number, z: number): number => (x < 0 || y < 0 || z < 0 || x >= w || y >= h || z >= w) ? -1 : grid[(y * w + z) * w + x] as number
    for (let y = 0; y < h; y++) {
      for (let z = 0; z < w; z++) {
        for (let x = 0; x < w; x++) {
          const block = bot.blockAt(origin.offset(x - radius, y - up, z - radius))
          if (block === null || AIR.has(block.name)) continue
          const liquid = block.name === 'water' || block.name === 'lava'
          if (block.boundingBox !== 'block' && !liquid) continue
          let i = index.get(block.name)
          if (i === undefined) { i = palette.length; palette.push(block.name); index.set(block.name, i) }
          grid[(y * w + z) * w + x] = i + 1
        }
      }
    }
    const blocks: number[][] = []
    const sides: Array<[number, number, number, number]> = [[1, 0, 0, 1], [-1, 0, 0, 2], [0, 1, 0, 4], [0, -1, 0, 8], [0, 0, 1, 16], [0, 0, -1, 32]]
    for (let y = 0; y < h; y++) {
      for (let z = 0; z < w; z++) {
        for (let x = 0; x < w; x++) {
          const cell = at(x, y, z)
          if (cell <= 0) continue
          let mask = 0
          for (const [dx, dy, dz, bit] of sides) {
            const n = at(x + dx, y + dy, z + dz)
            // Faces on the outer boundary are hidden, except the top, so the cut edge is not a wall.
            if (n === 0 || (n === -1 && dy === 1)) mask |= bit
          }
          if (mask !== 0) blocks.push([x - radius, y - up, z - radius, cell - 1, mask])
        }
      }
    }
    const p = bot.entity.position
    const entities = Object.values(bot.entities)
      .filter(e => e !== bot.entity && e.name !== 'item' && Math.abs(e.position.x - p.x) <= radius && Math.abs(e.position.z - p.z) <= radius && Math.abs(e.position.y - p.y) <= up)
      .slice(0, 60)
      .map(e => ({ kind: e.type === 'player' ? 'player' : String((e as any).kind ?? e.type), name: e.type === 'player' ? String(e.username) : String(e.name ?? 'unknown'), x: round(e.position.x), y: round(e.position.y), z: round(e.position.z), height: round((e as any).height ?? 1) }))
    return {
      center: { x: origin.x, y: origin.y, z: origin.z }, radius, up, yaw: round(bot.entity.yaw),
      position: { x: round(p.x), y: round(p.y), z: round(p.z) }, palette, blocks: blocks.slice(0, 9000), truncated: blocks.length > 9000, entities,
    }
  }

  findBlocks (name: string, maxDistance: number): Record<string, unknown> {
    const bot = this.live()
    const type = (bot.registry as any).blocksByName[name]
    if (type === undefined) throw new AppError(`"${name}" is not a Minecraft 1.20.1 block id.`)
    const positions = bot.findBlocks({ matching: type.id, maxDistance, count: 20 })
    return {
      block: name,
      positions: positions.map(p => ({ x: p.x, y: p.y, z: p.z, distance: round(p.distanceTo(bot.entity.position)) })),
    }
  }

  private async walk (bot: Bot, goal: unknown): Promise<void> {
    const pf = (bot as any).pathfinder
    let timer: NodeJS.Timeout | undefined
    try {
      await Promise.race([
        pf.goto(goal),
        new Promise((_resolve, reject) => {
          timer = setTimeout(() => { pf.setGoal(null); reject(new AppError('Gave up after 60 seconds without arriving.')) }, GOTO_TIMEOUT_MS)
        }),
      ])
    } catch (err) {
      if (err instanceof AppError) throw err
      const text = (err as Error).message ?? String(err)
      if (/no path/i.test(text)) throw new AppError(this.movementMode === 'free' ? 'No path was found, even allowing digging and pillaring.' : 'No walkable path was found (careful movement only clears leaves and vines; it never digs or builds).')
      if (/took to+ long/i.test(text)) throw new AppError('Path planning timed out. Try a nearer target, a larger range, or a more open route.')
      if (/stop|goal changed|cancel/i.test(text)) throw new AppError('Movement was stopped before arriving.')
      throw new AppError(`Could not get there: ${text}`)
    } finally {
      if (timer !== undefined) clearTimeout(timer)
    }
  }

  async goTo (x: number, y: number, z: number, range: number): Promise<Record<string, unknown>> {
    const bot = this.live()
    await this.walk(bot, range === 0 ? new goals.GoalBlock(Math.floor(x), Math.floor(y), Math.floor(z)) : new goals.GoalNear(x, y, z, range))
    const position = this.here(bot)
    return { arrived: true, position, distanceToTarget: round(bot.entity.position.distanceTo(new Vec3(x, y, z))) }
  }

  follow (player: string): Record<string, unknown> {
    const bot = this.live()
    const entity = bot.players[player]?.entity
    if (entity === undefined || entity === null) {
      const online = Object.keys(bot.players).filter(n => n !== bot.username)
      throw new AppError(online.includes(player)
        ? `${player} is online but too far away to see.`
        : `No player named ${player} is online. Online: ${online.join(', ') || 'nobody'}.`)
    }
    ;(bot as any).pathfinder.setGoal(new goals.GoalFollow(entity, 3), true)
    return { following: player, distance: round(entity.position.distanceTo(bot.entity.position)) }
  }

  setMovementMode (mode: 'free' | 'careful'): Record<string, unknown> {
    this.movementMode = mode
    if (this.bot !== null && this.moves !== null) (this.bot as any).pathfinder.setMovements(this.moves[mode])
    return { movementMode: mode }
  }

  stopActions (): Record<string, unknown> {
    const bot = this.live()
    ;(bot as any).pathfinder.setGoal(null)
    try { bot.stopDigging() } catch { /* not digging */ }
    bot.clearControlStates()
    return { stopped: true, position: this.here(bot) }
  }

  private counts (bot: Bot): Map<string, number> {
    const counts = new Map<string, number>()
    for (const item of bot.inventory.items()) {
      const name = itemLabel(item).name
      counts.set(name, (counts.get(name) ?? 0) + item.count)
    }
    return counts
  }

  async dig (x: number, y: number, z: number): Promise<Record<string, unknown>> {
    const bot = this.live()
    const pos = new Vec3(x, y, z)
    const before = bot.blockAt(pos)
    if (before === null) throw new AppError('That position is not loaded.')
    if (AIR.has(before.name)) throw new AppError('There is no block there (air).')
    if (before.diggable === false) throw new AppError(`${before.name} cannot be broken.`)
    const tool = (bot as any).pathfinder.bestHarvestTool(before)
    if (before.harvestTools !== undefined && (tool === null || tool === undefined || before.harvestTools[tool.type] === undefined)) {
      throw new AppError(`${before.name} drops nothing without the right tool, and the bot has none; it was left in place.`)
    }
    // Reach is measured from the eyes. Close in until the block is comfortably within it.
    const reach = (): number => bot.entity.position.offset(0, 1.62, 0).distanceTo(pos.offset(0.5, 0.5, 0.5))
    if (reach() > 4.5) await this.walk(bot, new goals.GoalNear(x, y, z, 2))
    if (reach() > 4.9) await this.walk(bot, new goals.GoalNear(x, y, z, 1))
    const block = bot.blockAt(pos)
    if (block === null || AIR.has(block.name)) throw new AppError('The block was gone on arrival.')
    if (!bot.canDigBlock(block)) throw new AppError(`${block.name} is out of reach from here.`)
    if (tool !== null && tool !== undefined) await bot.equip(tool, 'hand')
    const had = this.counts(bot)
    const toolUsed = bot.heldItem?.name ?? 'hand'
    try {
      await bot.dig(block)
    } catch (err) {
      throw new AppError(`Digging ${block.name} failed: ${(err as Error).message}`)
    }
    // Drops appear a moment later and may fall; walk to the nearest dropped item if there is one.
    await new Promise(resolve => setTimeout(resolve, 700))
    const drop = Object.values(bot.entities)
      .filter(e => e.name === 'item' && e.position.distanceTo(pos) <= 8)
      .sort((p, q) => p.position.distanceTo(pos) - q.position.distanceTo(pos))[0]
    let dropLeftBehind = false
    if (drop !== undefined) {
      const pf = (bot as any).pathfinder
      pf.setMovements(this.moves?.free)   // fetching a drop it just mined may dig, in either mode
      try {
        await Promise.race([
          pf.goto(new goals.GoalNear(drop.position.x, drop.position.y, drop.position.z, 1)).catch(() => undefined),
          new Promise(resolve => setTimeout(resolve, 15_000)),
        ])
      } finally {
        pf.setGoal(null)
        pf.setMovements(this.moves?.[this.movementMode])
      }
      await new Promise(resolve => setTimeout(resolve, 600))
      dropLeftBehind = bot.entities[drop.id] !== undefined
    }
    const pickedUp: Record<string, number> = {}
    for (const [name, count] of this.counts(bot)) {
      const gained = count - (had.get(name) ?? 0)
      if (gained > 0) pickedUp[name] = gained
    }
    const after = bot.blockAt(pos)
    return { dug: block.name, toolUsed, blockNow: after?.name ?? 'unloaded', pickedUp, dropLeftBehind, position: this.here(bot) }
  }

  // ---- Phase 3 actions: every result is read back from the bot's own inventory or the world ----

  private gained (bot: Bot, had: Map<string, number>): Record<string, number> {
    const out: Record<string, number> = {}
    for (const [name, count] of this.counts(bot)) {
      const diff = count - (had.get(name) ?? 0)
      if (diff > 0) out[name] = diff
    }
    return out
  }

  private itemType (bot: Bot, name: string): { id: number, name: string } {
    const type = (bot.registry as any).itemsByName[name]
    if (type === undefined) throw new AppError(`"${name}" is not a Minecraft 1.20.1 item id.`)
    return type
  }

  private held (bot: Bot, name: string): any {
    const item = bot.inventory.items().find(i => i.name === name)
    if (item === undefined) throw new AppError(`There is no ${name} in the inventory.`)
    return item
  }

  private async nearBlock (bot: Bot, name: string, reach: number): Promise<any> {
    const type = (bot.registry as any).blocksByName[name]
    const pos = bot.findBlock({ matching: type.id, maxDistance: 32 })?.position
    if (pos === undefined) return null
    if (bot.entity.position.distanceTo(pos.offset(0.5, 0.5, 0.5)) > reach) await this.walk(bot, new goals.GoalNear(pos.x, pos.y, pos.z, 2))
    return bot.blockAt(pos)
  }

  private ingredients (bot: Bot, recipe: any): Record<string, number> {
    const need: Record<string, number> = {}
    for (const d of recipe.delta as Array<{ id: number, count: number }>) {
      if (d.count < 0) need[(bot.registry as any).items[d.id]?.name ?? `id ${d.id}`] = -d.count
    }
    return need
  }

  recipe (name: string): Record<string, unknown> {
    const bot = this.live()
    const type = this.itemType(bot, name)
    const all = bot.recipesAll(type.id, null, true) as any[]
    if (all.length === 0) return { item: name, craftable: false, note: 'No crafting recipe exists; it may come from smelting, mining, mobs or trading.' }
    const seen = new Set<string>()
    const options = []
    for (const r of all) {
      const entry = { needs: this.ingredients(bot, r), makes: r.result.count as number, needsCraftingTable: Boolean(r.requiresTable) }
      const key = JSON.stringify(entry)
      if (!seen.has(key)) { seen.add(key); options.push(entry) }
      if (options.length >= 6) break
    }
    return { item: name, craftable: true, options }
  }

  async craft (name: string, count: number): Promise<Record<string, unknown>> {
    const bot = this.live()
    const type = this.itemType(bot, name)
    let table: any = null
    let recipes = bot.recipesFor(type.id, null, 1, null) as any[]
    if (recipes.length === 0) {
      table = await this.nearBlock(bot, 'crafting_table', 3.5)
      if (table !== null) recipes = bot.recipesFor(type.id, null, 1, table) as any[]
    }
    if (recipes.length === 0) {
      const all = bot.recipesAll(type.id, null, true) as any[]
      if (all.length === 0) throw new AppError(`${name} cannot be crafted (no recipe).`)
      const needsTable = all.every(r => r.requiresTable)
      const options = all.slice(0, 3).map(r => JSON.stringify(this.ingredients(bot, r))).join(' or ')
      throw new AppError(needsTable && table === null
        ? `${name} needs a crafting table within 32 blocks, and none was found. Place one first. Ingredients: ${options}.`
        : `Missing ingredients for ${name}. One of these sets is needed per craft: ${options}.`)
    }
    // The server confirms inventory clicks late, and a pickup arriving mid-craft makes it reject
    // them. So: start from a quiet inventory, confirm every craft from the inventory itself, and
    // retry a craft once before giving up.
    const sleep = async (ms: number): Promise<void> => { await new Promise(resolve => setTimeout(resolve, ms)) }
    const settle = async (): Promise<void> => {
      for (let quiet = 0, last = ''; quiet < 2;) {
        await sleep(250)
        const now = JSON.stringify([...this.counts(bot)])
        quiet = now === last ? quiet + 1 : 0
        last = now
      }
    }
    await settle()
    const had = this.counts(bot)
    const perCraft = recipes[0].result.count as number
    const times = Math.ceil(count / perCraft)
    let done = 0
    let problem = ''
    for (let attempt = 0; done < times && attempt < times + 2; attempt++) {
      const usable = (bot.recipesFor(type.id, null, 1, table) as any[])[0]
      if (usable === undefined) { problem = 'ran out of ingredients'; break }
      const before = this.counts(bot).get(name) ?? 0
      try {
        await bot.craft(usable, 1, table ?? undefined)
      } catch (err) {
        problem = (err as Error).message
      }
      await settle()
      if ((this.counts(bot).get(name) ?? 0) > before) { done++; problem = '' }
    }
    const gained = this.gained(bot, had)
    if (done === 0) throw new AppError(`Crafting ${name} did not produce anything${problem !== '' ? ` (${problem})` : ''}.`)
    return { crafted: name, requested: count, gained, usedCraftingTable: table !== null, stoppedEarly: done < times, ...(problem !== '' ? { problem } : {}) }
  }

  async place (name: string, x: number, y: number, z: number): Promise<Record<string, unknown>> {
    const bot = this.live()
    this.itemType(bot, name)
    const item = this.held(bot, name)
    const target = new Vec3(x, y, z)
    const current = bot.blockAt(target)
    if (current === null) throw new AppError('That position is not loaded.')
    if (current.boundingBox === 'block') throw new AppError(`That position already holds ${current.name}.`)
    if (bot.entity.position.distanceTo(target.offset(0.5, 0.5, 0.5)) > 4) await this.walk(bot, new goals.GoalNear(x, y, z, 3))
    const feet = bot.entity.position.floored()
    if (feet.equals(target) || feet.offset(0, 1, 0).equals(target)) throw new AppError('The bot is standing in that position. Move away first.')
    const faces = [new Vec3(0, -1, 0), new Vec3(0, 1, 0), new Vec3(-1, 0, 0), new Vec3(1, 0, 0), new Vec3(0, 0, -1), new Vec3(0, 0, 1)]
    const face = faces.find(f => bot.blockAt(target.plus(f))?.boundingBox === 'block')
    if (face === undefined) throw new AppError('Nothing solid touches that position, so a block cannot be placed there.')
    const reference = bot.blockAt(target.plus(face))
    await bot.equip(item, 'hand')
    try {
      await bot.placeBlock(reference as any, face.scaled(-1))
    } catch (err) {
      throw new AppError(`Placing ${name} failed: ${(err as Error).message}`)
    }
    return { placed: name, at: { x, y, z }, blockNow: bot.blockAt(target)?.name ?? 'unloaded' }
  }

  async equip (name: string, slot: string): Promise<Record<string, unknown>> {
    const bot = this.live()
    this.itemType(bot, name)
    try {
      await bot.equip(this.held(bot, name), slot as any)
    } catch (err) {
      if (err instanceof AppError) throw err
      throw new AppError(`Could not equip ${name}: ${(err as Error).message}`)
    }
    const worn = [5, 6, 7, 8].map(s => bot.inventory.slots[s]?.name ?? null)
    return { equipped: name, slot, heldItem: bot.heldItem?.name ?? null, armorWorn: worn.filter(Boolean), armor: armorPoints(worn) }
  }

  async eat (name: string): Promise<Record<string, unknown>> {
    const bot = this.live()
    const foods = (bot.registry as any).foodsByName as Record<string, { foodPoints: number }>
    const risky = new Set(['rotten_flesh', 'spider_eye', 'poisonous_potato', 'pufferfish', 'chicken', 'suspicious_stew'])
    const item = name !== ''
      ? this.held(bot, name)
      : bot.inventory.items().filter(i => foods[i.name] !== undefined && !risky.has(i.name))
        .sort((p, q) => (foods[q.name]?.foodPoints ?? 0) - (foods[p.name]?.foodPoints ?? 0))[0]
    if (item === undefined) throw new AppError('There is no safe food in the inventory.')
    if (foods[item.name] === undefined) throw new AppError(`${item.name} is not food.`)
    if (bot.food >= 20) throw new AppError('Hunger is already full (20 / 20); eating is not possible.')
    const before = bot.food
    await bot.equip(item, 'hand')
    try {
      await bot.consume()
    } catch (err) {
      throw new AppError(`Eating ${item.name} failed: ${(err as Error).message}`)
    }
    return { ate: item.name, foodBefore: before, foodNow: bot.food, healthNow: round(bot.health) }
  }

  async attack (targetName: string): Promise<Record<string, unknown>> {
    const bot = this.live()
    const nearest = (): any => Object.values(bot.entities)
      .filter(e => e !== bot.entity && e.type !== 'player' && e.name === targetName && e.position.distanceTo(bot.entity.position) <= 24)
      .sort((p, q) => p.position.distanceTo(bot.entity.position) - q.position.distanceTo(bot.entity.position))[0]
    const target = nearest()
    if (target === undefined) throw new AppError(`No ${targetName} within 24 blocks. (Players are never attacked.)`)
    const weapon = bot.inventory.items()
      .filter(i => /_(sword|axe)$/.test(i.name))
      .sort((p, q) => Number(q.name.endsWith('_sword')) - Number(p.name.endsWith('_sword')))[0]
    if (weapon !== undefined) await bot.equip(weapon, 'hand')
    const pf = (bot as any).pathfinder
    const had = this.counts(bot)
    const startHealth = bot.health
    const deadline = Date.now() + 30_000
    let hits = 0
    let outcome = 'timed out after 30 seconds'
    pf.setGoal(new goals.GoalFollow(target, 2), true)
    try {
      while (Date.now() < deadline) {
        if (this.bot !== bot) throw new AppError('Disconnected during the fight.')
        if (bot.entities[target.id] === undefined || target.isValid === false) { outcome = 'target defeated'; break }
        if (bot.health <= 6) { outcome = 'retreated: health fell to 6 or below'; break }
        if (target.position.distanceTo(bot.entity.position) <= 3.5) {
          await bot.lookAt(target.position.offset(0, (target.height ?? 1) * 0.6, 0), true)
          bot.attack(target)
          hits++
        }
        await new Promise(resolve => setTimeout(resolve, 650))
      }
    } finally {
      pf.setGoal(null)
    }
    await new Promise(resolve => setTimeout(resolve, 900))   // let nearby drops be picked up
    return { target: targetName, outcome, hits, weapon: weapon?.name ?? 'hand', healthBefore: round(startHealth), healthNow: round(bot.health), pickedUp: this.gained(bot, had) }
  }

  async smelt (name: string, fuelName: string, count: number): Promise<Record<string, unknown>> {
    const bot = this.live()
    const input = this.itemType(bot, name)
    const fuel = this.itemType(bot, fuelName)
    const have = this.counts(bot)
    if ((have.get(name) ?? 0) < count) throw new AppError(`Only ${have.get(name) ?? 0} ${name} in the inventory, ${count} requested.`)
    if ((have.get(fuelName) ?? 0) < 1) throw new AppError(`There is no ${fuelName} in the inventory to use as fuel.`)
    const block = await this.nearBlock(bot, 'furnace', 3.5)
    if (block === null) throw new AppError('No furnace within 32 blocks. Craft one from 8 cobblestone and place it first.')
    const had = this.counts(bot)
    let furnace: any
    try {
      furnace = await bot.openFurnace(block)
    } catch (err) {
      throw new AppError(`Could not open the furnace: ${(err as Error).message}`)
    }
    let taken = 0
    try {
      const fuelCount = Math.min(have.get(fuelName) ?? 0, Math.max(1, Math.ceil(count / 8)))   // coal smelts 8; weaker fuels may run out
      await furnace.putFuel(fuel.id, null, fuelCount)
      await furnace.putInput(input.id, null, count)
      const deadline = Date.now() + count * 11_000 + 8_000
      while (Date.now() < deadline && taken < count) {
        await new Promise(resolve => setTimeout(resolve, 1000))
        const out = furnace.outputItem()
        if (out !== null && out !== undefined) { taken += out.count; await furnace.takeOutput() }
        if ((furnace.inputItem() === null || furnace.inputItem() === undefined) && (furnace.outputItem() === null || furnace.outputItem() === undefined)) break
      }
      if (furnace.inputItem() !== null && furnace.inputItem() !== undefined) await furnace.takeInput().catch(() => undefined)
    } catch (err) {
      throw new AppError(`Smelting ${name} failed: ${(err as Error).message}`)
    } finally {
      try { furnace.close() } catch { /* already closed */ }
    }
    await new Promise(resolve => setTimeout(resolve, 300))
    const gained = this.gained(bot, had)
    if (Object.keys(gained).length === 0) throw new AppError(`Nothing came out. ${name} may not be smeltable, or ${fuelName} is not a fuel.`)
    return { smelted: name, requested: count, gained }
  }

  async mineMany (name: string, count: number, maxDistance: number): Promise<Record<string, unknown>> {
    const bot = this.live()
    if ((bot.registry as any).blocksByName[name] === undefined) throw new AppError(`"${name}" is not a Minecraft 1.20.1 block id.`)
    const had = this.counts(bot)
    const deadline = Date.now() + 150_000
    const skipped = new Set<string>()
    let mined = 0
    let lastError = ''
    let failures = 0
    while (mined < count && Date.now() < deadline && failures < 4) {
      if (this.bot !== bot) throw new AppError('Disconnected while mining.')
      const next = (this.findBlocks(name, maxDistance).positions as Array<{ x: number, y: number, z: number }>)
        .find(p => !skipped.has(`${p.x},${p.y},${p.z}`))
      if (next === undefined) { lastError = lastError || `No more ${name} within ${maxDistance} blocks.`; break }
      try {
        await this.dig(next.x, next.y, next.z)
        mined++
        failures = 0
      } catch (err) {
        skipped.add(`${next.x},${next.y},${next.z}`)
        lastError = (err as Error).message
        failures++
        if (/drops nothing without the right tool/.test(lastError)) break
      }
    }
    if (mined === 0) throw new AppError(lastError || `Could not mine any ${name}.`)
    return {
      block: name, requested: count, mined, pickedUp: this.gained(bot, had), position: this.here(bot),
      stoppedBecause: mined >= count ? 'done' : (Date.now() >= deadline ? 'time limit of 150 seconds' : lastError),
    }
  }

  async explore (direction: string, distance: number): Promise<Record<string, unknown>> {
    const bot = this.live()
    const step = { north: [0, -1], south: [0, 1], east: [1, 0], west: [-1, 0] }[direction] as [number, number]
    const start = bot.entity.position.clone()
    const tx = Math.floor(start.x + step[0] * distance)
    const tz = Math.floor(start.z + step[1] * distance)
    let note = 'arrived'
    try {
      await this.walk(bot, new goals.GoalNearXZ(tx, tz, 4))
    } catch (err) {
      note = (err as Error).message
    }
    const moved = round(bot.entity.position.distanceTo(start))
    if (moved < 2) throw new AppError(`Could not make progress ${direction}: ${note}`)
    return { direction, moved, position: this.here(bot), biome: this.snapshot()?.biome ?? null, note }
  }

  // ---- crystal PvP, training arena only ----
  startFight (opponent: string, params: FightParams, seconds: number, habits: Habits = {}, policy: number[] | null = null): Record<string, unknown> {
    const bot = this.live()
    // Fighting players is only ever allowed on a server on this machine: the training arena.
    if (!['127.0.0.1', 'localhost', '::1', '[::1]'].includes(this.host)) {
      throw new AppError('Player combat is only available in the local training arena.')
    }
    if (this.fight !== null) throw new AppError('A fight is already running.')
    if (bot.players[opponent] === undefined || opponent === bot.username) throw new AppError(`${opponent} is not on this server.`)
    ;(bot as any).pathfinder.setGoal(null)
    this.fight = new CrystalFight(bot, opponent, params, habits, policy, seconds, result => {
      this.fight = null
      this.notify('pvp.result', { opponent, ...result })
    })
    return { started: true, opponent, usesNetwork: policy !== null }
  }

  stopFight (): Record<string, unknown> {
    const running = this.fight !== null
    this.fight?.stop('stopped')
    return { wasRunning: running }
  }

  say (message: string): Record<string, unknown> {
    this.live().chat(message)
    return { sent: true }
  }

  private pushState (force: boolean): void {
    const state = this.snapshot()
    if (state === null) return
    const encoded = JSON.stringify(state)
    if (!force && encoded === this.lastState) return
    this.lastState = encoded
    this.notify('bot.state', state as unknown as Record<string, unknown>)
  }
}

/** True if a declare_commands packet describes a tree that can actually be built. */
export function commandTreeIsValid (packet: any): boolean {
  const nodes = packet?.nodes
  if (!Array.isArray(nodes) || !Array.isArray(nodes[packet.rootIndex]?.children)) return false
  if (!nodes.every(node => Array.isArray(node.children))) return false
  const inRange = (i: unknown): boolean => Number.isInteger(i) && (i as number) >= 0 && (i as number) < nodes.length
  if (!nodes.every(n => n.children.every(inRange) && (n.redirectNode === undefined || inRange(n.redirectNode)))) return false
  const settles = (ready: (i: number, pending: Set<number>) => boolean): boolean => {
    const pending = new Set<number>(nodes.keys())
    for (let progressed = true; pending.size > 0 && progressed;) {
      progressed = false
      for (const i of [...pending]) {
        if (ready(i, pending)) { pending.delete(i); progressed = true }
      }
    }
    return pending.size === 0
  }
  return settles((i, pending) => nodes[i].redirectNode === undefined || !pending.has(nodes[i].redirectNode)) &&
    settles((i, pending) => nodes[i].children.every((c: number) => !pending.has(c)))
}

// Mods such as Create register command argument types that the vanilla protocol tables do not
// know, so the command-list packet decodes into a garbled tree. minecraft-protocol reacts to
// such a tree by closing the connection. The bot does not need the command list to play, so a
// tree that cannot be built is dropped before the library sees it.
function guardCommandTree (client: any, onDropped: () => void): void {
  const emit = client.emit.bind(client)
  client.emit = (name: string, ...args: unknown[]): boolean => {
    if (name === 'declare_commands' && !commandTreeIsValid(args[0])) {
      onDropped()
      return false
    }
    return emit(name, ...args)
  }
}

// Vanilla armor defense points per piece. Mineflayer 4.39 does not decode the 1.20.1 attribute
// packet's keys (they arrive as "undefined"), so armor is computed from what is actually worn.
const ARMOR_BY_MATERIAL: Record<string, [number, number, number, number]> = {
  // helmet, chestplate, leggings, boots
  leather: [1, 3, 2, 1],
  chainmail: [2, 5, 4, 1],
  iron: [2, 6, 5, 2],
  golden: [2, 5, 3, 1],
  diamond: [3, 8, 6, 3],
  netherite: [3, 8, 6, 3],
}
const ARMOR_PIECES = ['helmet', 'chestplate', 'leggings', 'boots']
const ARMOR_OTHER: Record<string, number> = {
  turtle_helmet: 2, elytra: 0, carved_pumpkin: 0, skeleton_skull: 0, wither_skeleton_skull: 0,
  zombie_head: 0, creeper_head: 0, dragon_head: 0, player_head: 0, piglin_head: 0,
}

/** Armor points for the worn items, or null if any worn item is not a known vanilla piece. */
export function armorPoints (worn: Array<string | null>): number | null {
  let total = 0
  for (const name of worn) {
    if (name === null) continue
    const other = ARMOR_OTHER[name]
    if (other !== undefined) { total += other; continue }
    const cut = name.lastIndexOf('_')
    const values = ARMOR_BY_MATERIAL[name.slice(0, cut)]
    const piece = ARMOR_PIECES.indexOf(name.slice(cut + 1))
    const points = values?.[piece]
    if (points === undefined) return null
    total += points
  }
  return total
}

// Mineflayer's item tables are vanilla-only. A modded item (Create's, for example) arrives with
// a numeric id it cannot name; say exactly that instead of passing "unknown" along as a name.
export function itemLabel (item: { name: string, displayName: string, type: number }): { name: string, displayName: string } {
  if (item.name !== 'unknown') return { name: item.name, displayName: item.displayName }
  return { name: `unidentified:${item.type}`, displayName: `Unidentified modded item (id ${item.type})` }
}

function num (v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function round (v: number): number {
  return Math.round(v * 10) / 10
}

function parseMaybeJson (v: unknown): unknown {
  if (typeof v !== 'string') return v
  try { return JSON.parse(v) } catch { return v }
}

function explainError (err: Error): string {
  const code = (err as NodeJS.ErrnoException).code
  if (code === 'ECONNREFUSED') return 'Connection refused: no server is listening on that address and port.'
  if (code === 'ETIMEDOUT') return 'The connection timed out.'
  if (code === 'ECONNRESET') return 'The server closed the connection.'
  if (code === 'ENOTFOUND') return 'The host name could not be resolved.'
  return err.message
}
