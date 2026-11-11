// Manual diagnostic: join a real server with the bridge's own session code and print what it reports.
//   node test/probe.ts <port> [seconds] [username]
import { BotSession, ping } from '../src/bot.ts'

const port = Number(process.argv[2] ?? 25565)
const seconds = Number(process.argv[3] ?? 8)
const username = process.argv[4] ?? 'ProbeBot'
console.log('ping:', JSON.stringify(await ping({ host: '127.0.0.1', port })))
let states = 0
const session = new BotSession((method, params) => {
  if (method === 'bot.state') { states++; return }
  console.log(method, JSON.stringify(params).slice(0, 400))
}, '/tmp/prometheus-probe-auth')
session.connect({ host: '127.0.0.1', port, auth: 'offline', username, version: '1.20.1' })
setTimeout(() => {
  const s = session.snapshot()
  console.log('state updates:', states)
  console.log('snapshot:', JSON.stringify(s))
  const bot = (session as any).bot
  if (bot != null) {
    const below = bot.blockAt(bot.entity.position.offset(0, -1, 0))
    console.log('block below:', below?.name, '| loaded chunks:', Object.keys(bot.world?.async?.columns ?? bot.world?.columns ?? {}).length)
    const names = new Map<string, number>()
    for (let dx = -8; dx <= 8; dx++) for (let dz = -8; dz <= 8; dz++) for (let dy = -2; dy <= 3; dy++) { const b = bot.blockAt(bot.entity.position.offset(dx, dy, dz)); const n = b?.name ?? 'UNLOADED'; names.set(n, (names.get(n) ?? 0) + 1) }
    console.log('blocks nearby:', JSON.stringify([...names]))
    console.log('entities nearby:', JSON.stringify(Object.values(bot.entities).map((e: any) => e.name ?? e.type).slice(0, 10)))
  }
  session.disconnect()
  setTimeout(() => process.exit(0), 300)
}, seconds * 1000)
