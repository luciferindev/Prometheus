// Manual diagnostic: exercise the agent-facing actions on a real server.
//   node test/actions-probe.ts <port>
import { BotSession } from '../src/bot.ts'

const port = Number(process.argv[2] ?? 25565)
const session = new BotSession((m, p) => { if (m !== 'bot.state') console.log(m, JSON.stringify(p).slice(0, 200)) }, '/tmp/prometheus-probe-auth')
session.connect({ host: '127.0.0.1', port, auth: 'offline', username: 'ActionBot', version: '1.20.1' })
const step = async (label: string, fn: () => unknown) => {
  try { console.log(label, '->', JSON.stringify(await fn()).slice(0, 500)) } catch (e) { console.log(label, 'FAILED:', (e as Error).message) }
}
setTimeout(async () => {
  console.log('start position:', JSON.stringify(session.snapshot()?.position))
  await step('scan', () => { const s = session.scan(8) as any; return { blocks: s.blocks.slice(0, 8).map((b: any) => `${b.name}x${b.count}`), unloaded: s.unloadedPositions, entities: s.entities.length } })
  let log: any
  for (const name of ['jungle_log', 'oak_log', 'birch_log']) {
    const found = session.findBlocks(name, 48) as any
    console.log('findBlocks', name, found.positions.length, JSON.stringify(found.positions[0] ?? null))
    if (log === undefined && found.positions.length > 0) log = found.positions[0]
  }
  await step('findBlocks bogus', () => session.findBlocks('unobtainium', 16))
  if (log !== undefined) {
    await step('goto', () => session.goTo(log.x, log.y, log.z, 3))
    await step('dig log', () => session.dig(log.x, log.y, log.z))
    await step('dig same spot again', () => session.dig(log.x, log.y, log.z))
  }
  await step('dig stone bare-handed', async () => { const s = session.findBlocks('stone', 24) as any; return s.positions.length ? await session.dig(s.positions[0].x, s.positions[0].y, s.positions[0].z) : 'no stone' })

  await step('follow nobody', () => session.follow('Nobody'))
  await step('say', () => session.say('ActionBot checking in'))
  await step('stop', () => session.stopActions())
  console.log('inventory:', JSON.stringify(session.snapshot()?.inventory))
  session.disconnect(); setTimeout(() => process.exit(0), 300)
}, 6000)
