import { createInterface } from 'node:readline'
import type { Readable, Writable } from 'node:stream'
import type { z } from 'zod'
import { RpcError } from './protocol.ts'

const MAX_LINE = 1 << 20

export class AppError extends Error {}

type Handler = (params: any) => unknown | Promise<unknown>

export class RpcServer {
  private handlers = new Map<string, { schema: z.ZodType, fn: Handler }>()
  private output: Writable

  constructor (input: Readable, output: Writable) {
    this.output = output
    const rl = createInterface({ input, crlfDelay: Infinity })
    rl.on('line', line => { void this.handleLine(line) })
  }

  register<S extends z.ZodType> (method: string, schema: S, fn: (params: z.infer<S>) => unknown | Promise<unknown>): void {
    this.handlers.set(method, { schema, fn })
  }

  notify (method: string, params: Record<string, unknown>): void {
    this.send({ jsonrpc: '2.0', method, params })
  }

  private send (msg: unknown): void {
    this.output.write(JSON.stringify(msg) + '\n')
  }

  private fail (id: unknown, code: number, message: string): void {
    this.send({ jsonrpc: '2.0', id: id ?? null, error: { code, message } })
  }

  async handleLine (line: string): Promise<void> {
    if (line.trim() === '') return
    if (line.length > MAX_LINE) return this.fail(null, RpcError.invalidRequest, 'message too large')
    let msg: any
    try {
      msg = JSON.parse(line)
    } catch {
      return this.fail(null, RpcError.parse, 'invalid JSON')
    }
    const idOk = typeof msg?.id === 'number' || typeof msg?.id === 'string'
    if (msg === null || typeof msg !== 'object' || msg.jsonrpc !== '2.0' || typeof msg.method !== 'string' || !idOk) {
      return this.fail(idOk ? msg.id : null, RpcError.invalidRequest, 'not a JSON-RPC 2.0 request')
    }
    const handler = this.handlers.get(msg.method)
    if (handler === undefined) return this.fail(msg.id, RpcError.methodNotFound, `unknown method ${msg.method}`)
    const parsed = handler.schema.safeParse(msg.params ?? {})
    if (!parsed.success) {
      const issue = parsed.error.issues[0]
      const where = issue?.path.join('.') ?? ''
      return this.fail(msg.id, RpcError.invalidParams, `invalid params${where ? ` (${where})` : ''}: ${issue?.message ?? 'rejected'}`)
    }
    try {
      const result = await handler.fn(parsed.data)
      this.send({ jsonrpc: '2.0', id: msg.id, result: result ?? {} })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      this.fail(msg.id, RpcError.application, message)
    }
  }
}
