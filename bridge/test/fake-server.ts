// TEST FIXTURE ONLY. A minimal protocol-level 1.20.1 server (minecraft-protocol) that
// answers status pings and lets one offline-mode client log in and spawn. It is used to
// exercise the bridge's real network path in automated tests; it is not a game server.
import mc from 'minecraft-protocol'
import minecraftData from 'minecraft-data'

export interface FakeServer { port: number, close: () => Promise<void>, kickAll: (reason: string) => void }

export async function startFakeServer (): Promise<FakeServer> {
  const data = minecraftData('1.20.1') as any
  const server = mc.createServer({
    'online-mode': false,
    host: '127.0.0.1',
    port: 0,
    version: '1.20.1',
    motd: 'PROMETHEUS test fixture',
    maxPlayers: 4,
  } as any)
  const clients = new Set<any>()
  server.on('playerJoin', (client: any) => {
    clients.add(client)
    client.on('end', () => clients.delete(client))
    client.write('login', { ...data.loginPacket, entityId: client.id, gameMode: 0, enableRespawnScreen: true })
    client.write('position', { x: 8.5, y: 64, z: -3.5, yaw: 0, pitch: 0, flags: 0, teleportId: 1 })
    client.write('update_health', { health: 17, food: 14, foodSaturation: 2.5 })
  })
  await new Promise<void>((resolve, reject) => {
    server.once('listening', () => resolve())
    server.once('error', reject)
  })
  const port = (server as any).socketServer.address().port as number
  return {
    port,
    kickAll: reason => { for (const c of clients) c.end(reason) },
    close: async () => { await new Promise<void>(resolve => { (server as any).socketServer.close(() => resolve()); server.close() }) },
  }
}

// Run standalone so the Python test-suite can use the same fixture: prints the port, exits on stdin EOF.
if (process.argv[1] !== undefined && import.meta.url.endsWith(process.argv[1].split('/').pop() ?? '')) {
  const srv = await startFakeServer()
  process.stdout.write(`${srv.port}\n`)
  process.stdin.resume()
  process.stdin.on('data', chunk => { if (String(chunk).includes('kick')) srv.kickAll('kicked by test') })
  process.stdin.on('end', () => process.exit(0))
}
