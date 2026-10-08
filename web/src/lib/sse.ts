// SSE 파서 — fetch 스트림(POST + Authorization 헤더 필요)이라 EventSource 대신 직접 파싱 (ST-10)
export type SseEvent = { id?: string; event: string; data: any }

export function parseSseChunk(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = []
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n')
  const rest = blocks.pop() ?? ''
  for (const block of blocks) {
    let event = 'message'
    let id: string | undefined
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue
      const i = line.indexOf(':')
      const field = i < 0 ? line : line.slice(0, i)
      const value = i < 0 ? '' : line.slice(i + 1).replace(/^ /, '')
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
      else if (field === 'id') id = value
    }
    if (!data.length) continue
    const raw = data.join('\n')
    let parsed: any = raw
    try {
      parsed = JSON.parse(raw)
    } catch {
      /* 텍스트 데이터 */
    }
    events.push({ id, event, data: parsed })
  }
  return { events, rest }
}

export async function readSse(res: Response, onEvent: (e: SseEvent) => void, signal?: AbortSignal) {
  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (!signal?.aborted) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const { events, rest } = parseSseChunk(buf)
    buf = rest
    events.forEach(onEvent)
  }
  reader.cancel().catch(() => {})
}
