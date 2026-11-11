// Wire contract between the Qt application and this bridge.
// JSON-RPC 2.0, one message per line, over the child process's stdin/stdout.
// Every request's params are validated against these schemas before a handler runs.
import { z } from 'zod'

export const MC_VERSION = '1.20.1'
export const BRIDGE_VERSION = '0.1.0'

const host = z.string().min(1).max(253).regex(/^[A-Za-z0-9._\-:[\]]+$/, 'invalid host')
const port = z.number().int().min(1).max(65535)
const accountLabel = z.string().min(1).max(64).regex(/^[^\u0000-\u001f/\\]+$/, 'invalid account label')

export const Empty = z.object({}).strict()
export const PingParams = z.object({ host, port }).strict()
export const SignInParams = z.object({ username: accountLabel }).strict()
export const ConnectParams = z.object({
  host,
  port,
  auth: z.enum(['microsoft', 'offline']),
  username: accountLabel,
  version: z.literal(MC_VERSION),
}).strict()

const coord = z.number().finite().min(-30_000_000).max(30_000_000)
const blockCoord = coord.int()
export const ScanParams = z.object({ radius: z.number().int().min(1).max(16) }).strict()
export const MapParams = z.object({ radius: z.number().int().min(8).max(40) }).strict()
export const VoxelParams = z.object({ radius: z.number().int().min(4).max(16) }).strict()
export const FindBlocksParams = z.object({
  block: z.string().min(1).max(64).regex(/^[a-z0-9_]+$/, 'block ids are lower-case, e.g. oak_log'),
  maxDistance: z.number().int().min(1).max(64),
}).strict()
export const GotoParams = z.object({ x: coord, y: coord, z: coord, range: z.number().int().min(0).max(32) }).strict()
export const FollowParams = z.object({ player: z.string().min(1).max(16).regex(/^[A-Za-z0-9_]+$/) }).strict()
export const DigParams = z.object({ x: blockCoord, y: blockCoord, z: blockCoord }).strict()
const itemId = z.string().min(1).max(64).regex(/^[a-z0-9_]+$/, 'ids are lower-case, e.g. oak_planks')
export const ItemParams = z.object({ item: itemId }).strict()
export const CraftParams = z.object({ item: itemId, count: z.number().int().min(1).max(64) }).strict()
export const PlaceParams = z.object({ item: itemId, x: blockCoord, y: blockCoord, z: blockCoord }).strict()
export const EquipParams = z.object({ item: itemId, slot: z.enum(['hand', 'off-hand', 'head', 'torso', 'legs', 'feet']) }).strict()
export const EatParams = z.object({ item: itemId.or(z.literal('')) }).strict()
export const AttackParams = z.object({ target: itemId }).strict()
export const SmeltParams = z.object({ item: itemId, fuel: itemId, count: z.number().int().min(1).max(16) }).strict()
export const MineManyParams = z.object({ block: itemId, count: z.number().int().min(1).max(32), maxDistance: z.number().int().min(1).max(64) }).strict()
export const ExploreParams = z.object({ direction: z.enum(['north', 'south', 'east', 'west']), distance: z.number().int().min(8).max(96) }).strict()

export const MovementParams = z.object({ mode: z.enum(['free', 'careful']) }).strict()

const fightNumber = (min: number, max: number) => z.number().finite().min(min).max(max)
export const FightParamsSchema = z.object({
  range: fightNumber(1.5, 7), minSelfDist: fightNumber(1, 7), selfWeight: fightNumber(0, 4),
  crystalInterval: fightNumber(1, 20), meleeRange: fightNumber(0, 3.5), eatHealth: fightNumber(0, 18),
  strafePeriod: fightNumber(2, 60), retreatHealth: fightNumber(0, 14),
  defendRate: fightNumber(0, 1), defendThreat: fightNumber(0, 60), habitWeight: fightNumber(0, 4),
}).strict()
export const FightStartParams = z.object({
  opponent: z.string().min(3).max(16).regex(/^[A-Za-z0-9_]+$/),
  params: FightParamsSchema,
  seconds: z.number().int().min(5).max(180),
  // learned frequency of opponent crystals by offset from their target, "dx,dz" -> 0..1
  habits: z.record(z.string().regex(/^-?[0-4],-?[0-4]$/), z.number().min(0).max(1)),
  // weights of the policy network (293 numbers), or null to use the hand-written tactics
  policy: z.array(z.number().finite().min(-50).max(50)).length(293).nullable(),
}).strict()

// Chat only: a leading slash would be a server command, which the agent is not allowed to run.
export const ChatParams = z.object({
  message: z.string().min(1).max(256).regex(/^[^/\u0000-\u001f][^\u0000-\u001f]*$/, 'commands and control characters are not allowed'),
}).strict()

export type PingParams = z.infer<typeof PingParams>
export type ConnectParams = z.infer<typeof ConnectParams>

export type BotPhase = 'idle' | 'connecting' | 'logged_in' | 'spawned' | 'disconnected'

export interface InventoryItem { slot: number, name: string, displayName: string, count: number }

// Everything in here is read from the live Mineflayer bot. Fields the server has
// not told us yet are null rather than guessed.
export interface BotState {
  username: string | null
  health: number | null
  food: number | null
  saturation: number | null
  armor: number | null
  level: number | null
  position: { x: number, y: number, z: number } | null
  dimension: string | null
  biome: string | null
  gameMode: string | null
  timeOfDay: number | null
  heldItem: string | null
  inventory: InventoryItem[]
}

export const RpcError = {
  parse: -32700,
  invalidRequest: -32600,
  methodNotFound: -32601,
  invalidParams: -32602,
  application: -32000,
} as const
