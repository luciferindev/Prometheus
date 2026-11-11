// Manual diagnostic: two bots fight crystal PvP rounds on an island over lava, with the real kit.
//   node test/island-probe.ts <port> <path to the server's console fifo> [rounds]
// The arena is built at x,z = 1000 in whatever dimension the bots are in.
import { writeFileSync } from 'node:fs'
import { BotSession } from '../src/bot.ts'
import type { FightParams } from '../src/pvp.ts'

const port = Number(process.argv[2])
const consolePath = String(process.argv[3])
const rounds = Number(process.argv[4] ?? 2)
const cmd = (...lines: string[]): void => { writeFileSync(consolePath, lines.join('\n') + '\n') }
const sleep = async (ms: number): Promise<void> => { await new Promise(resolve => setTimeout(resolve, ms)) }
const base: FightParams = { range: 4, minSelfDist: 3, selfWeight: 1, crystalInterval: 5, meleeRange: 0, eatHealth: 8, strafePeriod: 14, retreatHealth: 6, defendRate: 0.3, defendThreat: 10, habitWeight: 1 }
const results: Record<string, any> = {}
const mk = (name: string): BotSession => {
  const s = new BotSession((m, p) => { if (m === 'pvp.result') results[name] = p; if (m === 'bot.event') console.log(name, JSON.stringify(p).slice(0, 160)) }, '/tmp/prometheus-probe-auth')
  s.connect({ host: '127.0.0.1', port, auth: 'offline', username: name, version: '1.20.1' })
  return s
}
const a = mk('Fighter_A'); const b = mk('Fighter_B')
const prot = '{Enchantments:[{id:protection,lvl:4},{id:unbreaking,lvl:3}]}'
const kit = (n: string): string[] => [
  `clear ${n}`, `item replace entity ${n} armor.head with netherite_helmet${prot}`, `item replace entity ${n} armor.chest with netherite_chestplate${prot}`,
  `item replace entity ${n} armor.legs with netherite_leggings${prot}`, `item replace entity ${n} armor.feet with netherite_boots${prot}`,
  `item replace entity ${n} weapon.offhand with totem_of_undying`, `give ${n} netherite_sword{Enchantments:[{id:sharpness,lvl:5}]} 1`, `give ${n} end_crystal 128`, `give ${n} obsidian 64`,
  `give ${n} totem_of_undying 1`, `give ${n} golden_apple 6`, `give ${n} crying_obsidian 32`, `effect give ${n} minecraft:instant_health 1 10 true`, `effect give ${n} minecraft:saturation 1 20 true`]
const C = 1000; const TOP = 210
const layer = (y: number, block: string, ...boxes: Array<[number, number]>): string[] => boxes.map(([x, z]) => `fill ${C - x} ${y} ${C - z} ${C + x} ${y} ${C + z} ${block}`)
await sleep(6000)
cmd('difficulty hard', 'gamerule doImmediateRespawn true', 'gamerule doFireTick false', 'forceload add 968 968 1031 1031',
  'fill 986 199 986 1014 207 1014 air', `fill ${C - 26} 200 ${C - 26} ${C + 26} 200 ${C + 26} blackstone`, `fill ${C - 25} 200 ${C - 25} ${C + 25} 200 ${C + 25} lava`,
  ...layer(TOP, 'crying_obsidian', [12, 5], [11, 8], [8, 11], [5, 12]), ...layer(TOP - 1, 'blackstone', [10, 4], [9, 7], [7, 9], [4, 10]),
  ...layer(TOP - 2, 'blackstone', [7, 3], [5, 5], [3, 7]), ...layer(TOP - 3, 'basalt', [4, 2], [2, 4]))
await sleep(2500)
for (let round = 1; round <= rounds; round++) {
  cmd('kill @e[type=end_crystal]', 'kill @e[type=item]', `fill ${C - 13} ${TOP + 1} ${C - 13} ${C + 13} ${TOP + 7} ${C + 13} air`,
    `tp Fighter_A ${C - 5.5} ${TOP + 1} ${C}.5 -90 0`, `tp Fighter_B ${C + 5.5} ${TOP + 1} ${C}.5 90 0`,
    `spawnpoint Fighter_A ${C - 6} ${TOP + 1} ${C}`, `spawnpoint Fighter_B ${C + 5} ${TOP + 1} ${C}`, ...kit('Fighter_A'), ...kit('Fighter_B'))
  await sleep(3000)
  if (round === 1) cmd('data get entity Fighter_A Inventory[{Slot:103b}].tag.Enchantments', 'data get entity Fighter_A Inventory[{Slot:0b}]')
  delete results.Fighter_A; delete results.Fighter_B
  const pa: FightParams = round % 2 === 1 ? { ...base, defendRate: 0.6, defendThreat: 0 } : { ...base, range: 5.5, minSelfDist: 4.5, selfWeight: 2 }
  try {
    console.log(`round ${round}: A`, JSON.stringify(a.startFight('Fighter_B', pa, 45)), 'B', JSON.stringify(b.startFight('Fighter_A', base, 45)))
  } catch (e) { console.log('start failed:', (e as Error).message); break }
  for (let i = 0; i < 120 && !(results.Fighter_A && results.Fighter_B); i++) {
    await sleep(500)
    if (results.Fighter_A && !results.Fighter_B) b.stopFight()
    if (results.Fighter_B && !results.Fighter_A) a.stopFight()
  }
  for (const n of ['Fighter_A', 'Fighter_B']) { const r = results[n]; if (r) { delete r.enemyOffsets; delete r.choices } console.log(` ${n}:`, JSON.stringify(r)) }
  for (const n of ['Fighter_A', 'Fighter_B']) if (results[n]?.fell) cmd(`kill ${n}`)
  await sleep(2500)
}
a.disconnect(); b.disconnect(); setTimeout(() => process.exit(0), 400)
