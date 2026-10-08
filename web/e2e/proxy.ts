import net from 'node:net'

export default async function globalSetup() {
  const [host, port] = (process.env.E2E_TARGET || 'api:8000').split(':')
  const server = net.createServer((c) => {
    const up = net.connect(Number(port), host)
    c.pipe(up).pipe(c)
    c.on('error', () => up.destroy())
    up.on('error', () => c.destroy())
  })
  await new Promise<void>((r) => server.listen(Number(process.env.E2E_PORT || 8800), '127.0.0.1', r))
  return async () => server.close()
}
