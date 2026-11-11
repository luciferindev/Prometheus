import assert from 'node:assert/strict'
import { PassThrough } from 'node:stream'
import { test } from 'node:test'
import { BotSession, armorPoints, commandTreeIsValid, itemLabel, ping } from '../src/bot.ts'
import {
  AttackParams, ChatParams, ConnectParams, CraftParams, DigParams, EatParams, Empty, EquipParams, ExploreParams,
  FightStartParams, FindBlocksParams, GotoParams, MapParams, MineManyParams, MovementParams, PingParams, PlaceParams, ScanParams, SmeltParams,
} from '../src/protocol.ts'
import { AppError, RpcServer } from '../src/rpc.ts'
import { POLICY_SIZE, crystalDamage, placementScore, policyChoose, policyOutputs, rankThreats } from '../src/pvp.ts'
import { startFakeServer } from './fake-server.ts'

function harness () {
  const input = new PassThrough()
  const output = new PassThrough()
  const rpc = new RpcServer(input, output)
  const lines: any[] = []
  let buf = ''
  output.on('data', chunk => {
    buf += String(chunk)
    let i
    while ((i = buf.indexOf('\n')) >= 0) { lines.push(JSON.parse(buf.slice(0, i))); buf = buf.slice(i + 1) }
  })
  const call = async (raw: string) => { const n = lines.length; await rpc.handleLine(raw); return lines[n] }
  return { rpc, call, lines }
}

test('rpc rejects malformed and unknown requests', async () => {
  const { rpc, call } = harness()
  rpc.register('echo', Empty, () => ({ ok: true }))
  assert.equal((await call('{nope')).error.code, -32700)
  assert.equal((await call('{"jsonrpc":"2.0","method":"echo"}')).error.code, -32600)
  assert.equal((await call('{"jsonrpc":"2.0","id":1,"method":"shell.exec","params":{}}')).error.code, -32601)
  assert.equal((await call('{"jsonrpc":"2.0","id":2,"method":"echo","params":{"extra":1}}')).error.code, -32602)
  assert.deepEqual((await call('{"jsonrpc":"2.0","id":3,"method":"echo","params":{}}')).result, { ok: true })
})

test('rpc reports handler failures as application errors', async () => {
  const { rpc, call } = harness()
  rpc.register('boom', Empty, () => { throw new AppError('nope') })
  const res = await call('{"jsonrpc":"2.0","id":1,"method":"boom"}')
  assert.equal(res.error.code, -32000)
  assert.equal(res.error.message, 'nope')
})

test('param schemas reject bad hosts, ports and versions', () => {
  assert.equal(PingParams.safeParse({ host: 'localhost', port: 25565 }).success, true)
  assert.equal(PingParams.safeParse({ host: 'a b; rm -rf', port: 25565 }).success, false)
  assert.equal(PingParams.safeParse({ host: 'localhost', port: 70000 }).success, false)
  const base = { host: '127.0.0.1', port: 25565, auth: 'offline', username: 'Bot' }
  assert.equal(ConnectParams.safeParse({ ...base, version: '1.20.1' }).success, true)
  assert.equal(ConnectParams.safeParse({ ...base, version: '1.21' }).success, false)
  assert.equal(ConnectParams.safeParse({ ...base, version: '1.20.1', auth: 'password' }).success, false)
  assert.equal(ConnectParams.safeParse({ ...base, version: '1.20.1', username: '../x' }).success, false)
})

test('armor points come from worn vanilla pieces; unknown (modded) pieces give null', () => {
  assert.equal(armorPoints([null, null, null, null]), 0)
  assert.equal(armorPoints(['iron_helmet', 'iron_chestplate', 'iron_leggings', 'iron_boots']), 15)
  assert.equal(armorPoints(['diamond_helmet', 'netherite_chestplate', 'diamond_leggings', 'diamond_boots']), 20)
  assert.equal(armorPoints(['turtle_helmet', 'elytra', null, 'leather_boots']), 3)
  assert.equal(armorPoints(['create:copper_diving_helmet', null, null, null]), null)
  assert.equal(armorPoints(['copper_backtank', null, null, null]), null)
})

test('modded items are labelled as unidentified, never given a made-up name', () => {
  assert.deepEqual(itemLabel({ name: 'iron_ingot', displayName: 'Iron Ingot', type: 770 }), { name: 'iron_ingot', displayName: 'Iron Ingot' })
  assert.deepEqual(itemLabel({ name: 'unknown', displayName: 'unknown', type: 1301 }), { name: 'unidentified:1301', displayName: 'Unidentified modded item (id 1301)' })
})

test('garbled command trees (modded argument types) are recognised', () => {
  const leaf = { children: [] }
  assert.equal(commandTreeIsValid({ rootIndex: 0, nodes: [{ children: [1, 2] }, leaf, { children: [], redirectNode: 1 }] }), true)
  assert.equal(commandTreeIsValid({ rootIndex: 61, nodes: [leaf, leaf] }), false, 'root outside the node list')
  assert.equal(commandTreeIsValid({ rootIndex: 0, nodes: [{ children: [7] }, leaf] }), false, 'child outside the node list')
  assert.equal(commandTreeIsValid({ rootIndex: 0, nodes: [{ children: [1] }, { children: [0] }] }), false, 'cycle')
  assert.equal(commandTreeIsValid({ rootIndex: 0, nodes: [{}] }), false)
  assert.equal(commandTreeIsValid(undefined), false)
})

test('agent action schemas: no server commands through chat, bounded scans and coordinates', () => {
  assert.equal(ChatParams.safeParse({ message: 'hello there' }).success, true)
  assert.equal(ChatParams.safeParse({ message: '/op ActionBot' }).success, false)
  assert.equal(ChatParams.safeParse({ message: 'line\nbreak' }).success, false)
  assert.equal(ChatParams.safeParse({ message: 'x'.repeat(300) }).success, false)
  assert.equal(ScanParams.safeParse({ radius: 16 }).success, true)
  assert.equal(ScanParams.safeParse({ radius: 200 }).success, false)
  assert.equal(FindBlocksParams.safeParse({ block: 'oak_log', maxDistance: 64 }).success, true)
  assert.equal(FindBlocksParams.safeParse({ block: 'Oak Log; drop', maxDistance: 64 }).success, false)
  assert.equal(GotoParams.safeParse({ x: 1.5, y: 64, z: -3, range: 2 }).success, true)
  assert.equal(GotoParams.safeParse({ x: Infinity, y: 64, z: -3, range: 2 }).success, false)
  assert.equal(DigParams.safeParse({ x: 1.5, y: 64, z: -3 }).success, false)
})

test('phase 3 action schemas are bounded', () => {
  assert.equal(CraftParams.safeParse({ item: 'oak_planks', count: 8 }).success, true)
  assert.equal(CraftParams.safeParse({ item: 'oak_planks', count: 9999 }).success, false)
  assert.equal(CraftParams.safeParse({ item: 'minecraft:oak planks', count: 1 }).success, false)
  assert.equal(PlaceParams.safeParse({ item: 'furnace', x: 1, y: 64, z: 2 }).success, true)
  assert.equal(PlaceParams.safeParse({ item: 'furnace', x: 1.5, y: 64, z: 2 }).success, false)
  assert.equal(EquipParams.safeParse({ item: 'iron_helmet', slot: 'head' }).success, true)
  assert.equal(EquipParams.safeParse({ item: 'iron_helmet', slot: 'tail' }).success, false)
  assert.equal(EatParams.safeParse({ item: '' }).success, true)
  assert.equal(AttackParams.safeParse({ target: 'zombie' }).success, true)
  assert.equal(AttackParams.safeParse({ target: 'Steve the Player' }).success, false)
  assert.equal(SmeltParams.safeParse({ item: 'raw_iron', fuel: 'coal', count: 17 }).success, false)
  assert.equal(MineManyParams.safeParse({ block: 'stone', count: 32, maxDistance: 64 }).success, true)
  assert.equal(MineManyParams.safeParse({ block: 'stone', count: 33, maxDistance: 64 }).success, false)
  assert.equal(ExploreParams.safeParse({ direction: 'up', distance: 20 }).success, false)
  assert.equal(MovementParams.safeParse({ mode: 'noclip' }).success, false)
  assert.equal(MapParams.safeParse({ radius: 400 }).success, false)
})

test('crystal placement scoring: closer to the opponent is better, too close to ourselves is refused', () => {
  assert.ok(crystalDamage(1) > crystalDamage(5) && crystalDamage(5) > crystalDamage(11))
  assert.equal(crystalDamage(12), 0)
  const p = { minSelfDist: 3, selfWeight: 1 }
  assert.equal(placementScore(2, 1, p), -Infinity)
  assert.ok(placementScore(5, 1, p) > 0)
  assert.ok(placementScore(5, 1, p) > placementScore(5, 3, p))
  assert.ok(placementScore(4, 4, p) === 0)
  assert.ok(placementScore(5, 1, { minSelfDist: 3, selfWeight: 0 }) > placementScore(5, 1, { minSelfDist: 3, selfWeight: 3 }))
})

test('threat prediction: nearest reachable spots first, shifted by learned habits', () => {
  const open = [
    { dx: 1, dz: 0, toUs: 1.1, toThem: 3.0 },
    { dx: -1, dz: 0, toUs: 1.1, toThem: 5.0 },
    { dx: 3, dz: 0, toUs: 3.2, toThem: 1.0 },      // right next to the opponent: a much smaller threat
    { dx: 4, dz: 4, toUs: 11.5, toThem: 0.9 },     // would only hurt the opponent: not a threat at all
  ]
  const plain = rankThreats(open, {}, 1)
  assert.deepEqual(plain.map(t => [t.dx, t.dz]), [[-1, 0], [1, 0], [3, 0]])   // far from the opponent = bigger threat
  const learned = rankThreats(open, { '1,0': 0.9 }, 2)
  assert.deepEqual([learned[0]?.dx, learned[0]?.dz], [1, 0])           // but if they always use the other one, expect that
  assert.deepEqual(rankThreats(open, { '1,0': 0.9 }, 0).map(t => t.dx), plain.map(t => t.dx))   // weight 0 ignores habits
  assert.equal(FightStartParams.safeParse({ opponent: 'Prometheus_B', seconds: 40, habits: { 'rm -rf': 1 }, params: {} }).success, false)
})

test('the policy network computes exactly what the Python reference computes', () => {
  // Same weights and inputs as tests/test_network.py; the expected numbers come from agent/policy.py.
  const weights = Array.from({ length: POLICY_SIZE }, (_, i) => Math.round(Math.sin(i * 0.37) * 0.8 * 1e6) / 1e6)
  const features = Array.from({ length: 12 }, (_, i) => Math.round((0.5 + 0.5 * Math.cos(i * 1.1)) * 1e6) / 1e6)
  assert.equal(POLICY_SIZE, 293)
  const expected = [2.4667782, 2.222370485, 1.687579305, 0.930804252, 0.04964309]
  policyOutputs(weights, features).forEach((value, k) => assert.ok(Math.abs(value - (expected[k] as number)) < 1e-6, `output ${k}: ${value}`))
  assert.equal(policyChoose(weights, features), 0)
  const params = { range: 4, minSelfDist: 3, selfWeight: 1, crystalInterval: 5, meleeRange: 0, eatHealth: 8, strafePeriod: 14, retreatHealth: 6, defendRate: 0.3, defendThreat: 10, habitWeight: 1 }
  assert.equal(FightStartParams.safeParse({ opponent: 'Prometheus_B', seconds: 40, habits: {}, params, policy: weights }).success, true)
  assert.equal(FightStartParams.safeParse({ opponent: 'Prometheus_B', seconds: 40, habits: {}, params, policy: weights.slice(1) }).success, false)
  assert.equal(FightStartParams.safeParse({ opponent: 'Prometheus_B', seconds: 40, habits: {}, params, policy: weights.map(() => 1e9) }).success, false)
})

test('fight requests are bounded and fighting is refused off this machine', () => {
  const params = { range: 4, minSelfDist: 3, selfWeight: 1, crystalInterval: 5, meleeRange: 0, eatHealth: 8, strafePeriod: 14, retreatHealth: 6, defendRate: 0.3, defendThreat: 10, habitWeight: 1 }
  assert.equal(FightStartParams.safeParse({ opponent: 'Prometheus_B', policy: null, habits: { '1,0': 0.5 }, params, seconds: 40 }).success, true)
  assert.equal(FightStartParams.safeParse({ opponent: 'Prometheus_B', policy: null, habits: {}, params: { ...params, range: 50 }, seconds: 40 }).success, false)
  assert.equal(FightStartParams.safeParse({ opponent: 'Prometheus_B', policy: null, habits: {}, params: { ...params, aimbot: 1 }, seconds: 40 }).success, false)
  assert.equal(FightStartParams.safeParse({ opponent: 'Prometheus_B', policy: null, habits: { '1,0': 0.5 }, params, seconds: 4000 }).success, false)
  const session = new BotSession(() => {}, '/tmp/prometheus-test-auth')
  assert.throws(() => session.startFight('Someone', params, 40), /not in the world/)
  assert.deepEqual(session.stopFight(), { wasRunning: false })
})

test('actions are refused while the bot is not in the world', async () => {
  const session = new BotSession(() => {}, '/tmp/prometheus-test-auth')
  assert.throws(() => session.scan(4), /not in the world/)
  assert.throws(() => session.say('hi'), /not in the world/)
  await assert.rejects(session.goTo(0, 64, 0, 1), /not in the world/)
  await assert.rejects(session.dig(0, 64, 0), /not in the world/)
  await assert.rejects(session.craft('stick', 1), /not in the world/)
  await assert.rejects(session.attack('zombie'), /not in the world/)
  assert.throws(() => session.map(16), /not in the world/)
  assert.deepEqual(session.setMovementMode('careful'), { movementMode: 'careful' })
})

test('ping reports a refused connection clearly', async () => {
  await assert.rejects(ping({ host: '127.0.0.1', port: 1 }), /Could not reach 127\.0\.0\.1:1/)
})

test('ping, connect, observe state and disconnect against the protocol fixture', async () => {
  const srv = await startFakeServer()
  try {
    const status = await ping({ host: '127.0.0.1', port: srv.port })
    assert.equal(status.protocol, 763)
    assert.equal(status.motd, 'PROMETHEUS test fixture')

    const events: Array<{ method: string, params: any }> = []
    const session = new BotSession((method, params) => events.push({ method, params }), '/tmp/prometheus-test-auth')
    session.connect({ host: '127.0.0.1', port: srv.port, auth: 'offline', username: 'TestBot', version: '1.20.1' })
    assert.throws(() => session.connect({ host: '127.0.0.1', port: srv.port, auth: 'offline', username: 'TestBot', version: '1.20.1' }), /Already connected/)

    const deadline = Date.now() + 15000
    while (!events.some(e => e.method === 'bot.state') && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50))
    const phases = events.filter(e => e.method === 'bot.status').map(e => e.params.phase)
    assert.deepEqual(phases, ['connecting', 'logged_in', 'spawned'], JSON.stringify(events))

    const state = session.snapshot()
    assert.ok(state)
    assert.equal(state.username, 'TestBot')
    assert.equal(state.health, 17)
    assert.equal(state.food, 14)
    assert.deepEqual(state.position, { x: 8.5, y: 64, z: -3.5 })
    assert.equal(state.dimension, 'overworld')
    assert.equal(state.armor, 0, 'nothing worn')
    assert.deepEqual(state.inventory, [])

    assert.deepEqual(session.disconnect(), { wasConnected: true })
    assert.equal(session.snapshot(), null)
    assert.equal(events.at(-1)?.params.phase, 'disconnected')
  } finally {
    await srv.close()
  }
})

test('a kick is reported and ends the session', async () => {
  const srv = await startFakeServer()
  try {
    const events: Array<{ method: string, params: any }> = []
    const session = new BotSession((method, params) => events.push({ method, params }), '/tmp/prometheus-test-auth')
    session.connect({ host: '127.0.0.1', port: srv.port, auth: 'offline', username: 'KickMe', version: '1.20.1' })
    const until = async (pred: () => boolean) => {
      const deadline = Date.now() + 15000
      while (!pred() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50))
    }
    await until(() => events.some(e => e.params.phase === 'spawned'))
    srv.kickAll('kicked by test')
    await until(() => events.some(e => e.params.phase === 'disconnected'))
    assert.ok(events.some(e => e.params.phase === 'disconnected'), JSON.stringify(events))
    assert.equal(session.snapshot(), null)
    // A fresh connect must be possible after a server-side disconnect.
    session.connect({ host: '127.0.0.1', port: srv.port, auth: 'offline', username: 'Again', version: '1.20.1' })
    session.disconnect()
  } finally {
    await srv.close()
  }
})

test('a public-server session can never start a player fight', async () => {
  const srv = await startFakeServer()
  try {
    const session = new BotSession(() => {}, '/tmp/prometheus-test-auth')
    session.connect({ host: '127.0.0.1', port: srv.port, auth: 'offline', username: 'Guarded', version: '1.20.1' })
    const deadline = Date.now() + 15000
    while (session.snapshot() === null && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50))
    const params = { range: 4, minSelfDist: 3, selfWeight: 1, crystalInterval: 5, meleeRange: 0, eatHealth: 8, strafePeriod: 14, retreatHealth: 6, defendRate: 0.3, defendThreat: 10, habitWeight: 1 }
    ;(session as any).host = 'play.example.org'
    assert.throws(() => session.startFight('Victim', params, 40), /only available in the local training arena/)
    ;(session as any).host = '127.0.0.1'
    assert.throws(() => session.startFight('Nobody', params, 40), /not on this server/)
    session.disconnect()
  } finally {
    await srv.close()
  }
})
