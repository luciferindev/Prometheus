// PROMETHEUS bridge entry point. stdout carries JSON-RPC only; all diagnostics go to stderr.
import { mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BotSession, ping } from './bot.ts'
import {
  AttackParams, BRIDGE_VERSION, ChatParams, ConnectParams, CraftParams, DigParams, EatParams, Empty, EquipParams,
  ExploreParams, FightStartParams, FindBlocksParams, FollowParams, GotoParams, ItemParams, MC_VERSION, MapParams, MineManyParams,
  MovementParams,
  PingParams,
  PlaceParams, ScanParams, SignInParams, SmeltParams, VoxelParams,
} from './protocol.ts'
import { RpcServer } from './rpc.ts'

for (const level of ['log', 'info', 'warn', 'debug'] as const) {
  console[level] = (...args: unknown[]) => { console.error(...args) }
}

const authDir = process.env.PROMETHEUS_AUTH_CACHE ?? join(tmpdir(), 'prometheus-auth')
mkdirSync(authDir, { recursive: true, mode: 0o700 })

const rpc = new RpcServer(process.stdin, process.stdout)
const session = new BotSession((method, params) => rpc.notify(method, params), authDir)
const require = createRequire(import.meta.url)

rpc.register('bridge.hello', Empty, () => ({
  bridgeVersion: BRIDGE_VERSION,
  minecraftVersion: MC_VERSION,
  mineflayerVersion: require('mineflayer/package.json').version as string,
  nodeVersion: process.version,
}))
rpc.register('server.ping', PingParams, params => ping(params))
rpc.register('auth.signIn', SignInParams, params => session.signIn(params.username))
rpc.register('auth.signOut', Empty, () => session.signOut())
rpc.register('bot.connect', ConnectParams, params => session.connect(params))
rpc.register('bot.disconnect', Empty, () => session.disconnect())
rpc.register('bot.snapshot', Empty, () => ({ state: session.snapshot() }))
rpc.register('world.scan', ScanParams, params => session.scan(params.radius))
rpc.register('world.map', MapParams, params => session.map(params.radius))
rpc.register('world.voxels', VoxelParams, params => session.voxels(params.radius))
rpc.register('world.findBlocks', FindBlocksParams, params => session.findBlocks(params.block, params.maxDistance))
rpc.register('bot.goto', GotoParams, params => session.goTo(params.x, params.y, params.z, params.range))
rpc.register('bot.follow', FollowParams, params => session.follow(params.player))
rpc.register('bot.stopActions', Empty, () => session.stopActions())
rpc.register('bot.dig', DigParams, params => session.dig(params.x, params.y, params.z))
rpc.register('bot.chat', ChatParams, params => session.say(params.message))
rpc.register('pvp.start', FightStartParams, params => session.startFight(params.opponent, params.params, params.seconds, params.habits, params.policy))
rpc.register('pvp.stop', Empty, () => session.stopFight())
rpc.register('bot.setMovementMode', MovementParams, params => session.setMovementMode(params.mode))
rpc.register('world.recipe', ItemParams, params => session.recipe(params.item))
rpc.register('bot.craft', CraftParams, params => session.craft(params.item, params.count))
rpc.register('bot.place', PlaceParams, params => session.place(params.item, params.x, params.y, params.z))
rpc.register('bot.equip', EquipParams, params => session.equip(params.item, params.slot))
rpc.register('bot.eat', EatParams, params => session.eat(params.item))
rpc.register('bot.attack', AttackParams, params => session.attack(params.target))
rpc.register('bot.smelt', SmeltParams, params => session.smelt(params.item, params.fuel, params.count))
rpc.register('bot.mineMany', MineManyParams, params => session.mineMany(params.block, params.count, params.maxDistance))
rpc.register('bot.explore', ExploreParams, params => session.explore(params.direction, params.distance))

process.on('uncaughtException', err => {
  rpc.notify('bot.event', { kind: 'error', text: `bridge error: ${err.message}` })
})
process.on('unhandledRejection', err => {
  rpc.notify('bot.event', { kind: 'error', text: `bridge error: ${err instanceof Error ? err.message : String(err)}` })
})
// The parent closing our stdin means the application is gone: leave the server cleanly.
process.stdin.on('end', () => {
  session.disconnect()
  process.exit(0)
})
