// Manual diagnostic: two bots fight a crystal PvP round in an arena built through the server console.
//   node test/pvp-probe.ts <port> <path to the server's console fifo> [rounds]
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
const kit = (n: string): string[] => [
  `clear ${n}`, `item replace entity ${n} armor.head with diamond_helmet`, `item replace entity ${n} armor.chest with diamond_chestplate`,
  `item replace entity ${n} armor.legs with diamond_leggings`, `item replace entity ${n} armor.feet with diamond_boots`,
  `item replace entity ${n} weapon.offhand with totem_of_undying`, `give ${n} netherite_sword 1`, `give ${n} end_crystal 64`,
  `give ${n} totem_of_undying 1`, `give ${n} golden_apple 6`, `give ${n} crying_obsidian 32`, `effect give ${n} minecraft:instant_health 1 10 true`, `effect give ${n} minecraft:saturation 1 20 true`]
await sleep(6000)
cmd('difficulty easy', 'gamerule doImmediateRespawn true', 'forceload add 984 984 1016 1016',
  'fill 988 199 988 1012 199 1012 obsidian', 'fill 987 200 987 1013 206 1013 barrier hollow', 'fill 988 200 988 1012 205 1012 air')
await sleep(1500)
for (let round = 1; round <= rounds; round++) {
  cmd('kill @e[type=end_crystal]', 'kill @e[type=item]', 'fill 988 200 988 1012 205 1012 air', 'tp Fighter_A 994.5 200 1000.5 -90 0', 'tp Fighter_B 1006.5 200 1000.5 90 0', ...kit('Fighter_A'), ...kit('Fighter_B'))
  await sleep(3000)
  delete results.Fighter_A; delete results.Fighter_B
  const pa: FightParams = round === 1 ? { ...base, defendRate: 0.6, defendThreat: 0 } : { ...base, range: 5.5, minSelfDist: 4.5, selfWeight: 2 }
  try {
    console.log(`round ${round}: A`, JSON.stringify(a.startFight('Fighter_B', pa, 45)), 'B', JSON.stringify(b.startFight('Fighter_A', base, 45)))
  } catch (e) { console.log('start failed:', (e as Error).message); break }
  for (let i = 0; i < 120 && !(results.Fighter_A && results.Fighter_B); i++) {
    await sleep(500)
    if (results.Fighter_A && !results.Fighter_B) b.stopFight()
    if (results.Fighter_B && !results.Fighter_A) a.stopFight()
  }
  console.log(' A:', JSON.stringify(results.Fighter_A))
  console.log(' B:', JSON.stringify(results.Fighter_B))
  await sleep(2500)
}
a.disconnect(); b.disconnect(); setTimeout(() => process.exit(0), 400)
