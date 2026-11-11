// Manual diagnostic: a full early-game sequence on a real server, using only bridge actions.
//   node test/survival-probe.ts <port>
import { Vec3 } from 'vec3'
import { BotSession } from '../src/bot.ts'

const port = Number(process.argv[2] ?? 25565)
const session = new BotSession((m, p) => { if (m === 'bot.event') console.log(m, JSON.stringify(p).slice(0, 200)) }, '/tmp/prometheus-probe-auth')
session.connect({ host: '127.0.0.1', port, auth: 'offline', username: 'SurvivalBot', version: '1.20.1' })
let failed = 0
const step = async (label: string, fn: () => unknown): Promise<any> => {
  try { const r = await fn(); console.log('ok  ', label, '->', JSON.stringify(r).slice(0, 330)); return r } catch (e) { failed++; console.log('FAIL', label, '->', (e as Error).message); return null }
}
const freeSpot = (): { x: number, y: number, z: number } => {
  const bot = (session as any).bot
  const base = bot.entity.position.floored()
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [2, 0], [0, 2], [-2, 0], [0, -2], [1, 1], [-1, -1]]) {
    for (const dy of [0, 1, -1]) {
      const p = base.offset(dx, dy, dz)
      if (bot.blockAt(p)?.name === 'air' && bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block') return { x: p.x, y: p.y, z: p.z }
    }
  }
  throw new Error('no free spot next to the bot')
}
setTimeout(async () => {
  const logs = ['jungle_log', 'oak_log', 'birch_log', 'acacia_log'].find(n => (session.findBlocks(n, 32).positions as any[]).length >= 3) ?? 'oak_log'
  const planks = logs.replace('_log', '_planks')
  await step(`mineMany 4 ${logs}`, () => session.mineMany(logs, 4, 32))
  await step('recipe crafting_table', () => session.recipe('crafting_table'))
  await step(`craft 12 ${planks}`, () => session.craft(planks, 12))
  await step('craft pickaxe without a table (must fail)', () => session.craft('wooden_pickaxe', 1))
  await step('craft crafting_table', () => session.craft('crafting_table', 1))
  await step('place crafting_table', () => { const s = freeSpot(); return session.place('crafting_table', s.x, s.y, s.z) })
  await step('craft 4 stick', () => session.craft('stick', 4))
  await step('craft wooden_pickaxe', () => session.craft('wooden_pickaxe', 1))
  await step('equip wooden_pickaxe', () => session.equip('wooden_pickaxe', 'hand'))
  await step('mineMany 9 stone', () => session.mineMany('stone', 9, 32))
  await step('craft furnace', () => session.craft('furnace', 1))
  await step('place furnace', () => { const s = freeSpot(); return session.place('furnace', s.x, s.y, s.z) })
  await step(`smelt 1 ${logs} with ${planks}`, () => session.smelt(logs, planks, 1))
  await step('smelt without the item (must fail)', () => session.smelt('raw_iron', planks, 1))
  await step('eat when full or without food (must fail)', () => session.eat(''))
  await step('attack absent mob (must fail)', () => session.attack('zombie'))
  await step('craft nonsense (must fail)', () => session.craft('unobtainium', 1))
  await step('explore north 24', () => session.explore('north', 24))
  console.log('inventory:', JSON.stringify(session.snapshot()?.inventory.map(i => `${i.count} ${i.name}`)))
  console.log('unexpected results are judged by the caller; FAIL lines total:', failed)
  session.disconnect(); setTimeout(() => process.exit(0), 300)
}, 6000)
