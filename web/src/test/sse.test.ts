import { parseSseChunk } from '@/lib/sse'

describe('parseSseChunk', () => {
  it('완결된 이벤트만 파싱하고 나머지는 버퍼로 남긴다', () => {
    const { events, rest } = parseSseChunk('id: 3\nevent: delta\ndata: {"text":"안녕"}\n\n: keep-alive\n\nevent: run.completed\ndata: {"sta')
    expect(events).toEqual([{ id: '3', event: 'delta', data: { text: '안녕' } }])
    expect(rest).toBe('event: run.completed\ndata: {"sta')
  })
  it('event 가 없으면 message, JSON 이 아니면 문자열(OpenAI [DONE])', () => {
    const { events } = parseSseChunk('data: [DONE]\n\ndata: {"a":1}\r\n\r\n')
    expect(events).toEqual([{ id: undefined, event: 'message', data: '[DONE]' }, { id: undefined, event: 'message', data: { a: 1 } }])
  })
  it('여러 data 줄은 개행으로 합친다', () => {
    expect(parseSseChunk('data: line1\ndata: line2\n\n').events[0].data).toBe('line1\nline2')
  })
})
