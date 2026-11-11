// Manual diagnostic: a trained policy network against the hand-written tactics, in real rounds.
//   node test/network-probe.ts <port> <server console fifo> <weights.json> [rounds]
import { readFileSync, writeFileSync } from 'node:fs'
import { BotSession } from '../src/bot.ts'
import type { FightParams } from '../src/pvp.ts'

const port = Number(process.argv[2])
const consolePath = String(process.argv[3])
const weights: number[] = JSON.parse(readFileSync(String(process.argv[4]), 'utf8')).weights
const rounds = Number(process.argv[5] ?? 6)
const cmd = (...lines: string[]): void => { writeFileSync(consolePath, lines.join('\n') + '\n') }
const sleep = async (ms: number): Promise<void> => { await new Promise(resolve => setTimeout(resolve, ms)) }
const base: FightParams = { range: 4, minSelfDist: 3, selfWeight: 1, crystalInterval: 5, meleeRange: 0, eatHealth: 8, strafePeriod: 14, retreatHealth: 6, defendRate: 0.25, defendThreat: 12, habitWeight: 1 }
const results: Record<string, any> = {}
const mk = (name: string): BotSession => {
  const s = new BotSession((m, p) => { if (m === 'pvp.result') results[name] = p }, '/tmp/prometheus-probe-auth')
  s.connect({ host: '127.0.0.1', port, auth: 'offline', username: name, version: '1.20.1' })
  return s
}
const net = mk('Net_Fighter'); const rule = mk('Rule_Fighter')
const kit = (n: string): string[] => [
  `clear ${n}`, `item replace entity ${n} armor.head with diamond_helmet`, `item replace entity ${n} armor.chest with diamond_chestplate`,
  `item replace entity ${n} armor.legs with diamond_leggings`, `item replace entity ${n} armor.feet with diamond_boots`,
  `item replace entity ${n} weapon.offhand with totem_of_undying`, `give ${n} netherite_sword 1`, `give ${n} end_crystal 64`,
  `give ${n} totem_of_undying 1`, `give ${n} golden_apple 6`, `give ${n} crying_obsidian 48`,
  `effect give ${n} minecraft:instant_health 1 10 true`, `effect give ${n} minecraft:saturation 1 20 true`]
await sleep(6000)
cmd('difficulty easy', 'gamerule doImmediateRespawn true', 'forceload add 984 984 1016 1016',
  'fill 988 199 988 1012 199 1012 obsidian', 'fill 987 200 987 1013 206 1013 barrier hollow', 'fill 988 200 988 1012 205 1012 air')
await sleep(1500)
let points = 0
const choices: Record<string, number> = {}
for (let round = 1; round <= rounds; round++) {
  const [a, b] = round % 2 === 1 ? ['Net_Fighter', 'Rule_Fighter'] : ['Rule_Fighter', 'Net_Fighter']
  cmd('kill @e[type=end_crystal]', 'kill @e[type=item]', 'fill 988 200 988 1012 205 1012 air',
    `tp ${a} 994.5 200 1000.5 -90 0`, `tp ${b} 1006.5 200 1000.5 90 0`, ...kit('Net_Fighter'), ...kit('Rule_Fighter'))
  await sleep(3200)
  delete results.Net_Fighter; delete results.Rule_Fighter
  net.startFight('Rule_Fighter', base, 40, {}, weights)
  rule.startFight('Net_Fighter', base, 40, {}, null)
  for (let i = 0; i < 110 && !(results.Net_Fighter && results.Rule_Fighter); i++) {
    await sleep(500)
    if (results.Net_Fighter && !results.Rule_Fighter) rule.stopFight()
    if (results.Rule_Fighter && !results.Net_Fighter) net.stopFight()
  }
  const n = results.Net_Fighter ?? {}; const r = results.Rule_Fighter ?? {}
  const netDied = Boolean(n.died || r.opponentDied); const ruleDied = Boolean(r.died || n.opponentDied)
  const score = (x: any): number => (2 - (x.totemsUsed ?? 0)) * 20 + (x.healthEnd ?? 0)
  const pts = netDied !== ruleDied ? (ruleDied ? 1 : 0) : (score(n) > score(r) + 4 ? 1 : score(r) > score(n) + 4 ? 0 : 0.5)
  points += pts
  for (const [k, v] of Object.entries(n.choices ?? {})) choices[k] = (choices[k] ?? 0) + (v as number)
  console.log(`round ${round}: network ${pts === 1 ? 'WON' : pts === 0 ? 'lost' : 'drew'} in ${n.seconds}s | network: died=${netDied} totems used ${n.totemsUsed} crystals ${n.crystalsDetonated} blocks ${n.blocksPlaced} apples ${n.applesEaten} | rules: died=${ruleDied} totems used ${r.totemsUsed} crystals ${r.crystalsDetonated} blocks ${r.blocksPlaced} apples ${r.applesEaten}`)
  await sleep(2500)
}
const total = Object.values(choices).reduce((x, y) => x + y, 0) || 1
console.log(`RESULT: the network took ${points} of ${rounds} points. Its choices: ${Object.entries(choices).map(([k, v]) => `${k} ${Math.round(100 * v / total)}%`).join(', ')}`)
net.disconnect(); rule.disconnect(); setTimeout(() => process.exit(0), 400)
