// Crystal PvP combat controller for the training arena.
//
// A fight is decided in ticks (50 ms), far faster than any request to Claude, so the fighting
// is this deterministic controller. Two things are learned from experience:
//   * its numeric settings (FightParams), tuned by the trainer from fight results;
//   * a model of where opponents actually put their crystals relative to their target
//     (`habits`), counted across rounds and used to predict the next placement.
//
// It only ever runs against a named opponent on a loopback server (enforced by the caller).
import type { Bot } from 'mineflayer'
import { Vec3 } from 'vec3'

export interface FightParams {
  range: number            // preferred distance to the opponent, blocks
  minSelfDist: number      // never detonate a crystal closer to ourselves than this
  selfWeight: number       // how much our own expected damage counts against a placement
  crystalInterval: number  // ticks between crystal attempts
  meleeRange: number       // use the sword inside this distance (0 = never)
  eatHealth: number        // eat a golden apple at or below this health
  strafePeriod: number     // ticks between changes of strafe direction
  retreatHealth: number    // at or below this health, keep 3 blocks further away
  defendRate: number       // share of its turns spent blocking the predicted crystal spot
  defendThreat: number     // only block spots whose predicted damage to us exceeds this
  habitWeight: number      // how strongly learned opponent habits shift the prediction
}

/** Relative frequency of opponent crystals by offset from their target: "dx,dz" -> 0..1. */
export type Habits = Record<string, number>

export interface FightResult {
  died: boolean
  opponentDied: boolean
  healthEnd: number
  damageTaken: number
  totemsUsed: number
  opponentTotemsUsed: number
  crystalsPlaced: number
  crystalsDetonated: number
  swordHits: number
  applesEaten: number
  blocksPlaced: number            // blocking blocks put on predicted crystal spots
  obsidianPlaced: number         // obsidian put down to stand crystals on
  fell: boolean                  // dropped off the arena's island
  opponentFell: boolean
  enemyCrystals: number          // opponent crystals this bot saw appear
  predictedTop3: number          // how many of those stood on one of its three predicted spots
  enemyOffsets: string[]         // where they stood relative to this bot ("dx,dz"), for the habit model
  choices: Record<string, number> // with a network: how often it chose each tactic
  seconds: number
  endedBy: string
}

const UP = new Vec3(0, 1, 0)
const TOTEM_POP = 35
const BASES = new Set(['obsidian', 'bedrock'])      // the only blocks an end crystal can stand on
const DENY_BLOCK = 'crying_obsidian'                // blast-proof, and crystals cannot be placed on it
const BASE_ITEM = 'obsidian'                        // what a fighter puts down where the ground cannot hold a crystal
const FALL = 2.5                                    // this far below where the round started counts as off the island

/** Where a crystal could go: on a block that already holds crystals, or on obsidian that has to be placed first. */
interface Site { cell: Vec3, centre: Vec3, needsBase: boolean }

/** Raw (unarmoured) damage of an end crystal explosion at a distance; used only to compare placements. */
export function crystalDamage (distance: number): number {
  const impact = Math.max(0, 1 - distance / 12)
  return impact === 0 ? 0 : (impact * impact + impact) / 2 * 7 * 12 + 1
}

/** Score of detonating a crystal: damage to the opponent minus weighted damage to ourselves. */
export function placementScore (selfDist: number, enemyDist: number, p: Pick<FightParams, 'minSelfDist' | 'selfWeight'>): number {
  if (selfDist < p.minSelfDist) return -Infinity
  return crystalDamage(enemyDist) - p.selfWeight * crystalDamage(selfDist)
}

// ---- policy network: 12 inputs -> 16 tanh units -> 5 actions (same arithmetic as agent/policy.py) ----
export const POLICY_INPUTS = 12
export const POLICY_HIDDEN = 16
export const POLICY_ACTIONS = ['attack', 'defend', 'eat', 'back_off', 'close_in'] as const
export const POLICY_SIZE = POLICY_HIDDEN * POLICY_INPUTS + POLICY_HIDDEN + POLICY_ACTIONS.length * POLICY_HIDDEN + POLICY_ACTIONS.length

export function policyOutputs (weights: number[], features: number[]): number[] {
  const b1 = POLICY_HIDDEN * POLICY_INPUTS
  const w2 = b1 + POLICY_HIDDEN
  const b2 = w2 + POLICY_ACTIONS.length * POLICY_HIDDEN
  const hidden: number[] = []
  for (let j = 0; j < POLICY_HIDDEN; j++) {
    let total = weights[b1 + j] as number
    for (let i = 0; i < POLICY_INPUTS; i++) total += (weights[j * POLICY_INPUTS + i] as number) * (features[i] as number)
    hidden.push(Math.tanh(total))
  }
  const out: number[] = []
  for (let k = 0; k < POLICY_ACTIONS.length; k++) {
    let total = weights[b2 + k] as number
    for (let j = 0; j < POLICY_HIDDEN; j++) total += (weights[w2 + k * POLICY_HIDDEN + j] as number) * (hidden[j] as number)
    out.push(total)
  }
  return out
}

export function policyChoose (weights: number[], features: number[]): number {
  const out = policyOutputs(weights, features)
  let best = 0
  for (let k = 1; k < out.length; k++) if ((out[k] as number) > (out[best] as number)) best = k
  return best
}

export interface Threat { dx: number, dz: number, threat: number }

/**
 * Rank the spots around us where the opponent's next crystal is most likely and most harmful.
 * `open` lists candidate cells as offsets from our feet with the distances a crystal there would
 * have to us and to the opponent. The geometric estimate is shifted by learned habits.
 */
export function rankThreats (open: Array<{ dx: number, dz: number, toUs: number, toThem: number }>, habits: Habits, habitWeight: number): Threat[] {
  return open
    .map(c => ({
      dx: c.dx,
      dz: c.dz,
      // An opponent wants to hurt us without hurting itself (it values its own skin about half as much).
      threat: (crystalDamage(c.toUs) - 0.5 * crystalDamage(c.toThem)) * (1 + habitWeight * (habits[`${c.dx},${c.dz}`] ?? 0)),
    }))
    .filter(t => t.threat > 0)
    .sort((a, b) => b.threat - a.threat)
}

export class CrystalFight {
  private bot: Bot
  private opponent: string
  private p: FightParams
  private habits: Habits
  private policy: number[] | null
  private rangeOverride = 0
  private rangeUntil = 0
  private damageLog: Array<{ tick: number, amount: number }> = []
  private done: (r: FightResult) => void
  private tick = 0
  private busy = false
  private ended = false
  private strafe: 'left' | 'right' = 'left'
  private groundY: number | null = null      // the height the fighter stood at when the round began
  private lastHealth: number
  private started = Date.now()
  private maxMs: number
  private myCrystals: Array<{ at: Vec3, when: number }> = []
  private r: FightResult = {
    died: false, opponentDied: false, healthEnd: 0, damageTaken: 0, totemsUsed: 0, opponentTotemsUsed: 0,
    crystalsPlaced: 0, crystalsDetonated: 0, swordHits: 0, applesEaten: 0, blocksPlaced: 0,
    obsidianPlaced: 0, fell: false, opponentFell: false,
    enemyCrystals: 0, predictedTop3: 0, enemyOffsets: [], choices: {}, seconds: 0, endedBy: '',
  }

  private onTick = (): void => { this.step() }
  private onHealth = (): void => {
    const h = this.bot.health
    if (h < this.lastHealth) {
      this.r.damageTaken += this.lastHealth - h
      this.damageLog.push({ tick: this.tick, amount: this.lastHealth - h })
    }
    this.lastHealth = h
    if (h <= 0) { this.r.died = true; this.finish('this bot died') }
  }

  // A respawn packet also arrives when the bot is teleported into another dimension, which can
  // land just after a round starts; that is not a death, so the first seconds are ignored.
  private onRespawn = (): void => {
    if (this.tick < 60 && this.bot.health > 0) return
    this.r.died = true
    this.finish('this bot died')
  }

  private onDeath = (): void => { this.r.died = true; this.finish('this bot died') }
  private onStatus = (packet: any): void => {
    if (packet.entityStatus !== TOTEM_POP) return
    if (packet.entityId === this.bot.entity.id) this.r.totemsUsed++
    else if (packet.entityId === this.enemy()?.id) this.r.opponentTotemsUsed++
  }

  private onEntityDead = (entity: any): void => {
    if (entity.type === 'player' && entity.username === this.opponent) { this.r.opponentDied = true; this.finish('the opponent died') }
  }

  // Every crystal that appears and is not ours is a data point: where did the opponent put it
  // relative to us, and had we predicted that spot?
  private onEntitySpawn = (entity: any): void => {
    if (entity.name !== 'end_crystal' || this.ended) return
    const now = Date.now()
    this.myCrystals = this.myCrystals.filter(c => now - c.when < 2500)
    if (this.myCrystals.some(c => c.at.distanceTo(entity.position) < 0.8)) return
    const me = this.bot.entity.position.floored()
    const cell = entity.position.floored()
    const dx = cell.x - me.x
    const dz = cell.z - me.z
    if (Math.abs(dx) > 4 || Math.abs(dz) > 4) return
    this.r.enemyCrystals++
    if (this.r.enemyOffsets.length < 60) this.r.enemyOffsets.push(`${dx},${dz}`)
    if (this.lastThreats.slice(0, 3).some(t => t.dx === dx && t.dz === dz)) this.r.predictedTop3++
  }

  // The prediction made on the most recent tick, i.e. before the opponent's crystal appeared.
  private lastThreats: Threat[] = []

  constructor (bot: Bot, opponent: string, params: FightParams, habits: Habits, policy: number[] | null, seconds: number, done: (r: FightResult) => void) {
    this.bot = bot
    this.opponent = opponent
    this.p = params
    this.habits = habits
    this.policy = policy
    this.done = done
    this.maxMs = seconds * 1000
    this.lastHealth = bot.health
    bot.on('physicsTick', this.onTick)
    bot.on('health', this.onHealth)
    bot.on('death', this.onDeath)
    bot.on('entityDead', this.onEntityDead)
    bot.on('entitySpawn', this.onEntitySpawn)
    ;(bot as any)._client.on('entity_status', this.onStatus)
    ;(bot as any)._client.on('respawn', this.onRespawn)
  }

  stop (reason: string): void { this.finish(reason) }

  private finish (endedBy: string): void {
    if (this.ended) return
    this.ended = true
    const bot = this.bot
    bot.removeListener('physicsTick', this.onTick)
    bot.removeListener('health', this.onHealth)
    bot.removeListener('death', this.onDeath)
    bot.removeListener('entityDead', this.onEntityDead)
    bot.removeListener('entitySpawn', this.onEntitySpawn)
    ;(bot as any)._client.removeListener('entity_status', this.onStatus)
    ;(bot as any)._client.removeListener('respawn', this.onRespawn)
    bot.clearControlStates()
    this.r.healthEnd = this.r.died ? 0 : Math.round(bot.health * 10) / 10
    this.r.seconds = Math.round((Date.now() - this.started) / 100) / 10
    this.r.endedBy = endedBy
    this.done(this.r)
  }

  private enemy (): any { return this.bot.players[this.opponent]?.entity ?? null }

  private item (name: string): any { return this.bot.inventory.items().find(i => i.name === name) }

  /**
   * Can a crystal go on top of this block position? Either the block there already holds crystals
   * (obsidian or bedrock), or it is empty with solid ground below, so obsidian can be placed there first.
   */
  private site (cell: Vec3, a: Vec3, b: Vec3): Site | null {
    const bot = this.bot
    if (bot.blockAt(cell.offset(0, 1, 0))?.name !== 'air' || bot.blockAt(cell.offset(0, 2, 0))?.name !== 'air') return null
    const block = bot.blockAt(cell)
    if (block === null) return null
    const centre = cell.offset(0.5, 1, 0.5)
    if (BASES.has(block.name)) {
      if (centre.distanceTo(a) < 0.9 || centre.distanceTo(b) < 0.9) return null
      return { cell, centre, needsBase: false }
    }
    if (block.name !== 'air' || bot.blockAt(cell.offset(0, -1, 0))?.boundingBox !== 'block') return null
    // a block cannot be placed where somebody is standing
    for (const p of [a, b]) {
      if (Math.abs(p.x - cell.x - 0.5) < 0.8 && Math.abs(p.z - cell.z - 0.5) < 0.8 && p.y < cell.y + 1 && p.y + 1.8 > cell.y) return null
    }
    return { cell, centre, needsBase: true }
  }

  /** Is there ground to stand on a step or two away in this direction? */
  private groundAhead (dx: number, dz: number): boolean {
    const pos = this.bot.entity.position
    for (const d of [0.8, 1.8]) {
      const at = new Vec3(Math.floor(pos.x + dx * d), Math.floor(pos.y + 0.01), Math.floor(pos.z + dz * d))
      if (this.bot.blockAt(at.offset(0, -1, 0))?.boundingBox !== 'block' && this.bot.blockAt(at.offset(0, -2, 0))?.boundingBox !== 'block') return false
    }
    return true
  }

  /** Where the opponent's next crystal against us is most likely, best guess first. */
  private threats (enemy: any): Threat[] {
    const me = this.bot.entity.position
    const feet = me.floored()
    const theirEye = enemy.position.offset(0, 1.62, 0)
    const open = []
    for (let dx = -3; dx <= 3; dx++) {
      for (let dz = -3; dz <= 3; dz++) {
        // beside us at foot level (obsidian, or room to place it), or the ground itself where that holds crystals
        const site = this.site(feet.offset(dx, 0, dz), me, enemy.position) ?? this.site(feet.offset(dx, -1, dz), me, enemy.position)
        if (site === null || site.centre.distanceTo(theirEye) > 4.6) continue      // they could not reach it
        open.push({ dx, dz, toUs: site.centre.distanceTo(me), toThem: site.centre.distanceTo(enemy.position) })
      }
    }
    return rankThreats(open, this.habits, this.p.habitWeight)
  }

  private step (): void {
    if (this.ended) return
    const bot = this.bot
    this.tick++
    if (Date.now() - this.started > this.maxMs) return this.finish('time limit')
    // Falling off the island loses the round. The starting height is taken once the fighter stands.
    if (this.groundY === null && bot.entity.onGround) this.groundY = bot.entity.position.y
    if (this.groundY !== null && bot.entity.position.y < this.groundY - FALL) {
      this.r.fell = true
      return this.finish('this bot fell off the island')
    }
    const enemy = this.enemy()
    if (this.groundY !== null && enemy !== null && enemy.position.y < this.groundY - FALL) {
      this.r.opponentFell = true
      return this.finish('the opponent fell off the island')
    }
    if (enemy === null) {
      bot.clearControlStates()
      if (this.tick > 100) this.finish('the opponent is not in sight')
      return
    }
    const dist = bot.entity.position.distanceTo(enemy.position)
    void bot.lookAt(enemy.position.offset(0, 1.2, 0), true)
    if (this.tick % 2 === 0) this.lastThreats = this.threats(enemy)

    // movement: hold the preferred distance, strafing so we are not a standing target
    const want = this.policy !== null
      ? (this.tick < this.rangeUntil ? this.rangeOverride : this.p.range)
      : this.p.range + (bot.health <= this.p.retreatHealth ? 3 : 0)
    bot.setControlState('forward', dist > want + 0.4)
    bot.setControlState('sprint', dist > want + 2)
    bot.setControlState('back', dist < want - 0.4)
    if (this.tick % Math.max(2, Math.round(this.p.strafePeriod)) === 0) this.strafe = this.strafe === 'left' ? 'right' : 'left'
    bot.setControlState('left', this.strafe === 'left')
    bot.setControlState('right', this.strafe === 'right')
    // never walk off an edge: drop any movement that has no ground a step or two ahead
    const sin = Math.sin(bot.entity.yaw)
    const cos = Math.cos(bot.entity.yaw)
    const ways: Array<['forward' | 'back' | 'left' | 'right', number, number]> = [['forward', -sin, -cos], ['back', sin, cos], ['left', -cos, sin], ['right', cos, -sin]]
    for (const [control, dx, dz] of ways) {
      if (bot.getControlState(control) && !this.groundAhead(dx, dz)) {
        bot.setControlState(control, false)
        if (control === 'forward') bot.setControlState('sprint', false)
        if (control === this.strafe) this.strafe = this.strafe === 'left' ? 'right' : 'left'
      }
    }

    if (this.busy) return
    // survival first: keep a totem in the off-hand, eat when low
    const offhand = bot.inventory.slots[45]
    const totem = this.item('totem_of_undying')
    if ((offhand === null || offhand === undefined || offhand.name !== 'totem_of_undying') && totem !== undefined) {
      return this.act(async () => { await bot.equip(totem, 'off-hand') })
    }
    const apple = this.item('golden_apple')
    const eat = (): void => { this.act(async () => { await bot.equip(apple, 'hand'); await bot.consume(); this.r.applesEaten++ }) }
    if (this.policy === null && bot.health <= this.p.eatHealth && apple !== undefined) return eat()
    if (this.p.meleeRange > 0 && dist <= Math.min(this.p.meleeRange, 3.2) && this.tick % 13 === 0) {
      const sword = bot.inventory.items().find(i => i.name.endsWith('_sword'))
      return this.act(async () => {
        if (sword !== undefined) await bot.equip(sword, 'hand')
        bot.attack(enemy)
        this.r.swordHits++
      })
    }
    if (this.tick % Math.max(1, Math.round(this.p.crystalInterval)) !== 0) return
    if (this.policy !== null) {
      // The network picks the tactic for this turn from what the fighter can observe.
      const action = POLICY_ACTIONS[policyChoose(this.policy, this.features(enemy, dist))] ?? 'attack'
      this.r.choices[action] = (this.r.choices[action] ?? 0) + 1
      if (action === 'eat' && apple !== undefined && bot.health < 20) return eat()
      if (action === 'defend' && this.defend()) return
      if (action === 'back_off') { this.rangeOverride = 6; this.rangeUntil = this.tick + 40 }
      if (action === 'close_in') { this.rangeOverride = 2.5; this.rangeUntil = this.tick + 40 }
      return this.crystal(enemy)
    }
    // Hand-written tactics: each turn is spent either defending (blocking the predicted spot) or attacking.
    if (Math.random() < this.p.defendRate && this.defend()) return
    this.crystal(enemy)
  }

  /** The network's twelve inputs, in the order documented in agent/policy.py. */
  private features (enemy: any, dist: number): number[] {
    const bot = this.bot
    const count = (name: string): number => bot.inventory.items().filter(i => i.name === name).reduce((n, i) => n + i.count, 0)
    const offhand = bot.inventory.slots[45]
    const totems = count('totem_of_undying') + (offhand?.name === 'totem_of_undying' ? 1 : 0)
    const clamp = (v: number): number => Math.max(0, Math.min(1, v))
    const recent = this.damageLog.filter(d => this.tick - d.tick <= 20).reduce((n, d) => n + d.amount, 0)
    this.damageLog = this.damageLog.filter(d => this.tick - d.tick <= 20)
    const eye = bot.entity.position.offset(0, 1.62, 0)
    const crystalNear = Object.values(bot.entities).some(e => e.name === 'end_crystal' && e.position.distanceTo(eye) <= 4 &&
      !this.myCrystals.some(c => c.at.distanceTo(e.position) < 0.8))
    return [
      clamp(bot.health / 20), clamp(totems / 2), clamp((2 - this.r.opponentTotemsUsed) / 2), Math.min(dist, 8) / 8,
      clamp(this.bestSpot(enemy).score / 60), clamp((this.lastThreats[0]?.threat ?? 0) / 60), Math.min(this.lastThreats.length, 10) / 10,
      clamp(count('golden_apple') / 6), Math.min(this.r.blocksPlaced, 10) / 10, clamp(recent / 10),
      clamp((Date.now() - this.started) / this.maxMs), crystalNear ? 1 : 0,
    ]
  }

  /** The best place for a crystal near the opponent, by expected damage to them minus to us. */
  private bestSpot (enemy: any): { score: number, site: Site | null } {
    const bot = this.bot
    const eye = bot.entity.position.offset(0, 1.62, 0)
    const me = bot.entity.position
    const base = enemy.position.floored()
    const haveBase = this.item(BASE_ITEM) !== undefined
    let best: Site | null = null
    let bestScore = 0
    for (let dx = -3; dx <= 3; dx++) {
      for (let dz = -3; dz <= 3; dz++) {
        for (let dy = -2; dy <= 0; dy++) {
          const site = this.site(base.offset(dx, dy, dz), me, enemy.position)
          if (site === null || site.centre.distanceTo(eye) > 4.4 || (site.needsBase && !haveBase)) continue
          // obsidian that is already down saves the turn it takes to place it
          const score = placementScore(site.centre.distanceTo(me), site.centre.distanceTo(enemy.position), this.p) + (site.needsBase ? 0 : 2)
          if (score > bestScore) { bestScore = score; best = site }
        }
      }
    }
    return { score: bestScore, site: best }
  }

  private act (fn: () => Promise<void>): void {
    // An inventory or placement action that never completes must not freeze the fighter.
    this.busy = true
    const release = setTimeout(() => { this.busy = false }, 1500)
    fn().catch(() => undefined).finally(() => { clearTimeout(release); this.busy = false })
  }

  /** Block the most threatening predicted crystal spot with a blast-proof block. */
  private defend (): boolean {
    const bot = this.bot
    const block = this.item(DENY_BLOCK)
    if (block === undefined) return false
    const feet = bot.entity.position.floored()
    const eye = bot.entity.position.offset(0, 1.62, 0)
    for (const t of this.lastThreats.slice(0, 3)) {
      if (t.threat < this.p.defendThreat) break
      // Cover the spot: on the ground beside us, or on top of obsidian that already stands there.
      const beside = feet.offset(t.dx, 0, t.dz)
      const floor = bot.blockAt(beside)?.name === 'air' ? beside.offset(0, -1, 0) : beside
      const base = bot.blockAt(floor)
      const cell = floor.offset(0, 1, 0)
      if (base === null || base.boundingBox !== 'block' || bot.blockAt(cell)?.name !== 'air' || cell.offset(0.5, 0.5, 0.5).distanceTo(eye) > 4.4) continue
      this.act(async () => {
        await bot.equip(block, 'hand')
        // placeBlock waits for the server's confirmation; do not wait long for it
        await Promise.race([bot.placeBlock(base as any, UP), new Promise(resolve => setTimeout(resolve, 500))])
        if (bot.blockAt(cell)?.name === DENY_BLOCK) this.r.blocksPlaced++
      })
      return true
    }
    return false
  }

  private crystal (enemy: any): void {
    const bot = this.bot
    const eye = bot.entity.position.offset(0, 1.62, 0)
    const me = bot.entity.position
    // 1. A crystal already standing where a detonation favours us: hit it.
    let bestCrystal: any = null
    let bestScore = 0
    for (const e of Object.values(bot.entities)) {
      if (e.name !== 'end_crystal' || e.position.distanceTo(eye) > 4.5) continue
      const score = placementScore(e.position.distanceTo(me), e.position.distanceTo(enemy.position), this.p)
      if (score > bestScore) { bestScore = score; bestCrystal = e }
    }
    if (bestCrystal !== null) {
      bot.attack(bestCrystal)
      this.r.crystalsDetonated++
      return
    }
    // 2. Otherwise place one on the best spot near the opponent (putting obsidian down first where
    //    the ground cannot hold a crystal), then hit it.
    const stock = this.item('end_crystal')
    if (stock === undefined) return
    const site = this.bestSpot(enemy).site
    if (site === null) return
    const obsidian = this.item(BASE_ITEM)
    const ground = bot.blockAt(site.cell.offset(0, -1, 0))
    if (site.needsBase && (obsidian === undefined || ground === null)) return
    this.act(async () => {
      if (site.needsBase) {
        await bot.equip(obsidian, 'hand')
        await Promise.race([bot.placeBlock(ground as any, UP), new Promise(resolve => setTimeout(resolve, 500))])
        if (bot.blockAt(site.cell)?.name !== BASE_ITEM) return
        this.r.obsidianPlaced++
      }
      const target = bot.blockAt(site.cell)
      if (target === null || this.ended) return
      this.myCrystals.push({ at: site.centre, when: Date.now() })
      await bot.equip(stock, 'hand')
      const placed: any = await Promise.race([
        (bot as any).placeEntity(target, UP),
        new Promise((_resolve, reject) => setTimeout(() => reject(new Error('timeout')), 600)),
      ])
      this.r.crystalsPlaced++
      if (placed !== null && placed !== undefined && !this.ended) {
        bot.attack(placed)
        this.r.crystalsDetonated++
      }
    })
  }
}
