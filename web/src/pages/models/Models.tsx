// SCR-I01 공급자·모델 목록 (I02 공급자 어댑터 설정은 ModelsMore.Providers) · I04 모델 플레이그라운드(Unified Model API 비교 호출)
import { createColumnHelper } from '@tanstack/react-table'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, ErrorBox, Field, Gate, Input, Kv, Masked, PageHeader, Select, StatusBadge, Tabs, Textarea, TraceLink, useToast } from '@/components/ui'
import { request, type Json } from '@/lib/api'
import { fmtMs, fmtNum, fmtTime, fmtUsd } from '@/lib/format'
import { readSse } from '@/lib/sse'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()

export function Models() {
  const toast = useToast()
  const models = useGet('/api/v1/models')
  const providers = useGet('/api/v1/providers')
  const test = async (id: string) => {
    try {
      const r = await send<Json>('POST', `/api/v1/providers/${id}/test`)
      toast(r.ok ? 'ok' : 'error', r.ok ? `${id}: 정상 ${fmtMs(r.latency_ms)} · ${r.model}` : `${id}: 실패 — ${r.error}`)
      providers.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const toggle = async (m: Json) => {
    try {
      await send('PATCH', `/api/v1/models/${m.id}`, { enabled: !m.enabled })
      models.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const health = providers.data?.health ?? {}
  const columns = [
    col.accessor('display_name', { header: '모델', cell: ({ row }) => <div><div className="font-medium">{row.original.display_name || row.original.id}</div><div className="mono text-[11px] text-fg-muted">{row.original.id} → {row.original.upstream_model}</div></div> }),
    col.accessor('provider_id', { header: '공급자', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('category', { header: '분류', cell: (c) => <Badge tone={c.getValue() === 'local' || c.getValue() === 'domain' ? 'model' : 'primary'}>{c.getValue()}</Badge> }),
    col.accessor('data_classes', { header: '허용 데이터 등급', enableSorting: false, cell: (c) => (c.getValue() ?? []).map((d: string) => <Badge key={d} className="mr-1">{d}</Badge>) }),
    col.display({ id: 'price', header: '단가 in/out (1M)', cell: ({ row }) => <span className="text-xs tabular-nums">${row.original.price_in} / ${row.original.price_out}</span> }),
    col.accessor('context_window', { header: '컨텍스트', cell: (c) => c.getValue()?.toLocaleString() ?? '—' }),
    col.display({ id: 'lat', header: '지연(EWMA)', cell: ({ row }) => fmtMs(health.latency_ewma_ms?.[row.original.id]) }),
    col.display({ id: 'cb', header: '회로', cell: ({ row }) => (health.circuit_open ?? []).includes(row.original.id) ? <Badge tone="danger">차단</Badge> : <Badge tone="ok">정상</Badge> }),
    col.accessor('enabled', { header: '사용', cell: ({ row }) => <Gate perm="models:write">{(ok, why) => <input type="checkbox" aria-label={`${row.original.id} 사용`} disabled={!ok} title={why} checked={row.original.enabled} onChange={() => toggle(row.original)} onClick={(e) => e.stopPropagation()} />}</Gate> }),
  ]
  return (
    <>
      <PageHeader title="공급자·모델" crumbs={['모델']} desc="설정만으로 모델 교체 — 앱은 /v1/chat/completions 의 논리 모델명만 사용합니다 (FR-L00)."
        actions={<Gate perm="models:write">{(ok, why) => <>
          <Link to="/console/models/providers"><Button disabled={!ok} title={why}>+ 공급자 추가/연결</Button></Link>
          <Link to="/console/models/new"><Button variant="primary" disabled={!ok} title={why}>+ 도메인 모델 등록</Button></Link>
        </>}</Gate>} />
      <Card title="공급자 어댑터" className="mb-4">
        {providers.error && <ErrorBox error={providers.error} />}
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-fg-muted"><tr><th className="py-1">ID</th><th>종류</th><th>base_url</th><th>리전</th><th>자격증명</th><th>상태</th><th>마지막 시험</th><th /></tr></thead>
          <tbody>{(providers.data?.items ?? []).map((p: Json) => (
            <tr key={p.id} className="border-t border-line"><td className="mono py-1.5">{p.id}</td><td><Badge tone="model">{p.kind}</Badge></td><td className="mono text-xs">{p.base_url || '—'}</td><td>{p.region || '—'}</td>
              <td>{p.secret_ref ? <span className="text-xs">시크릿 저장소 <Masked value={p.secret_ref} /></span> : <span className="text-xs text-fg-muted">없음</span>}</td>
              <td><StatusBadge status={p.status} /></td><td className="text-xs">{fmtTime(p.last_tested_at, true)}</td>
              <td className="text-right"><Gate perm="models:write">{(ok, why) => <div className="flex justify-end gap-1">
                <Button size="sm" disabled={!ok} title={why} onClick={() => test(p.id)}>연결 시험</Button>
                <Link to={`/console/models/providers?id=${encodeURIComponent(p.id)}`} aria-label={`${p.id} 설정`}><Button size="sm" variant="ghost" disabled={!ok} title={why}>설정</Button></Link>
              </div>}</Gate></td></tr>
          ))}</tbody>
        </table>
        <p className="mt-2 text-xs text-fg-muted">백엔드 키는 게이트웨이 시크릿 저장소에만 있고 앱·브라우저에 노출되지 않습니다 (FR-G02·L01, MSG-018).</p>
      </Card>
      {models.error && <ErrorBox error={models.error} />}
      <DataTable data={models.data?.items ?? []} columns={columns} />
    </>
  )
}

// ── I04 플레이그라운드 — 같은 요청을 최대 4개 모델(별칭)에 병렬 호출해 응답·지연·토큰·비용 비교 ──
const LANES = ['A', 'B', 'C', 'D']
const LANE_GRID = ['', 'grid-cols-1', 'grid-cols-1 lg:grid-cols-2', 'grid-cols-1 lg:grid-cols-3', 'grid-cols-1 lg:grid-cols-2 2xl:grid-cols-4']
const TOOLS_PRESET = [{ type: 'function', function: { name: 'orders_search', description: '주문 번호로 배송 상태를 조회한다', parameters: { type: 'object', properties: { order_id: { type: 'string' } }, required: ['order_id'] } } }]
const SCHEMA_PRESET = { name: 'order_status', schema: { type: 'object', properties: { order_id: { type: 'string' }, status: { type: 'string' }, eta: { type: 'string' } }, required: ['order_id', 'status'] } }

type Lane = { status: 'idle' | 'running' | 'done' | 'error' | 'stopped'; out: string; tools: Json[]; meta: Json | null; err: unknown }
const IDLE: Lane = { status: 'idle', out: '', tools: [], meta: null, err: null }

const parseJson = (text: string): [any, string | null] => {
  try {
    return [JSON.parse(text), null]
  } catch (e: any) {
    return [null, e.message]
  }
}

/** json_schema 응답 검증 (type·required·properties 만 — 공급자 무관 정규화 확인용) */
function checkSchema(text: string, schema: Json): string | null {
  const [v, perr] = parseJson(text)
  if (perr) return `JSON 아님 — ${perr}`
  const is: Record<string, (x: any) => boolean> = {
    object: (x) => !!x && typeof x === 'object' && !Array.isArray(x), array: Array.isArray, string: (x) => typeof x === 'string',
    number: (x) => typeof x === 'number', integer: Number.isInteger, boolean: (x) => typeof x === 'boolean',
  }
  const walk = (x: any, s: Json, path: string): string | null => {
    if (s.type && is[s.type] && !is[s.type](x)) return `${path}: ${s.type} 형식 아님`
    if (s.type !== 'object') return null
    for (const k of s.required ?? []) if (!(k in x)) return `${path}.${k}: 필수 누락`
    for (const [k, ps] of Object.entries<Json>(s.properties ?? {})) {
      const r = k in x ? walk(x[k], ps, `${path}.${k}`) : null
      if (r) return r
    }
    return null
  }
  return walk(v, schema, '$')
}

function sampleCode(tab: 'python' | 'curl' | 'ts', body: Json, models: string[], dc: string) {
  const b = { ...body, model: models[0] ?? 'auto' }
  const note = models.length > 1 ? `비교 대상: ${models.join(', ')} — model 만 바꿔 같은 요청` : ''
  const origin = location.origin
  if (tab === 'curl') return `${note ? `# ${note}\n` : ''}curl ${origin}/v1/chat/completions \\\n  -H "Authorization: Bearer $ANX_KEY" -H "Content-Type: application/json" \\\n  -H "X-AgentNexus-Data-Class: ${dc}" \\\n  -d '${JSON.stringify(b)}'`
  if (tab === 'ts') return `${note ? `// ${note}\n` : ''}import OpenAI from 'openai'\nconst client = new OpenAI({ baseURL: '${origin}/v1', apiKey: process.env.ANX_KEY })\nconst res = await client.chat.completions.create(${JSON.stringify(b, null, 2)}, { headers: { 'X-AgentNexus-Data-Class': '${dc}' } })`
  return `${note ? `# ${note}\n` : ''}import json\nfrom openai import OpenAI\nclient = OpenAI(base_url="${origin}/v1", api_key="anx_key_…")\nbody = json.loads(r"""${JSON.stringify(b, null, 2)}""")\nclient.chat.completions.create(**body, extra_headers={"X-AgentNexus-Data-Class": "${dc}"})`
}

export function Playground() {
  const models = useGet('/v1/models')
  const [targets, setTargets] = useState([{ key: 0, model: 'auto' }])
  const [lanes, setLanes] = useState<Record<number, Lane>>({})
  const ctls = useRef<Record<number, AbortController | undefined>>({})
  const nextKey = useRef(1)
  const [dc, setDc] = useState('public')
  const [system, setSystem] = useState('')
  const [prompt, setPrompt] = useState('AgentNexus 게이트웨이를 한 문장으로 설명해줘')
  const [stream, setStream] = useState(true)
  const [temp, setTemp] = useState('')
  const [useTools, setUseTools] = useState(false)
  const [toolChoice, setToolChoice] = useState('auto')
  const [toolsText, setToolsText] = useState(() => JSON.stringify(TOOLS_PRESET, null, 2))
  const [useSchema, setUseSchema] = useState(false)
  const [schemaText, setSchemaText] = useState(() => JSON.stringify(SCHEMA_PRESET, null, 2))
  const [showCode, setShowCode] = useState(false)
  const [codeTab, setCodeTab] = useState<'python' | 'curl' | 'ts'>('python')
  useEffect(() => () => Object.values(ctls.current).forEach((c) => c?.abort()), []) // 화면을 떠나면 진행 중 호출 중지

  const catalog: Json[] = models.data?.data ?? []
  const price = (id?: string) => catalog.find((m) => m.id === id)?.agentnexus
  const [tools, toolsErr] = useTools ? parseJson(toolsText) : [null, null]
  const [schema, schemaErr] = useSchema ? parseJson(schemaText) : [null, null]
  const optErr = toolsErr ? `tools JSON 오류 — ${toolsErr}` : schemaErr ? `json_schema JSON 오류 — ${schemaErr}` : null
  const messages = [...(system ? [{ role: 'system', content: system }] : []), { role: 'user', content: prompt }]
  const body: Json = {
    messages, stream, ...(stream ? { stream_options: { include_usage: true } } : {}), ...(temp !== '' ? { temperature: Number(temp) } : {}),
    ...(useTools && tools ? { tools, tool_choice: toolChoice } : {}), ...(useSchema && schema ? { response_format: { type: 'json_schema', json_schema: schema } } : {}),
  }
  const running = targets.some((t) => lanes[t.key]?.status === 'running')
  const patch = (key: number, p: Partial<Lane> | ((l: Lane) => Partial<Lane>)) =>
    setLanes((ls) => {
      const l = ls[key] ?? IDLE
      return { ...ls, [key]: { ...l, ...(typeof p === 'function' ? p(l) : p) } }
    })

  // 칸마다 AbortController 를 따로 둔다 — 한 칸만 중지해도 나머지는 계속
  const runOne = async (key: number, model: string) => {
    ctls.current[key]?.abort()
    const ctl = new AbortController()
    ctls.current[key] = ctl
    patch(key, { ...IDLE, status: 'running' })
    const t0 = performance.now()
    let first: number | null = null
    let meta: Json = { requested: model }
    try {
      const res = await request('/v1/chat/completions', { method: 'POST', signal: ctl.signal, body: { ...body, model }, headers: { 'X-AgentNexus-Data-Class': dc, ...(stream ? { Accept: 'text/event-stream' } : {}) } })
      const hdr = Object.fromEntries([...res.headers.entries()].filter(([k]) => k.startsWith('x-agentnexus')))
      meta = { ...meta, headers: hdr, model: hdr['x-agentnexus-model'], fallback: hdr['x-agentnexus-fallback'] === 'true' }
      if (stream) {
        let usage: Json | null = null
        let routed = ''
        let upstreamErr: Json | null = null
        const calls: Json[] = []
        await readSse(res, (ev) => {
          if (ev.data === '[DONE]' || typeof ev.data !== 'object') return
          if (ev.data.error) {
            upstreamErr = ev.data.error
            return
          }
          first ??= performance.now() - t0
          routed ||= ev.data.model
          if (ev.data.usage) usage = ev.data.usage
          const d = ev.data.choices?.[0]?.delta
          if (d?.content) patch(key, (l) => ({ out: l.out + d.content }))
          for (const tc of d?.tool_calls ?? []) { // 공급자별 도구 호출 조각 → OpenAI 형식 tool_calls 로 이어 붙인다
            const c = (calls[tc.index ?? 0] ??= { id: tc.id, type: 'function', function: { name: '', arguments: '' } })
            c.id ||= tc.id
            c.function.name ||= tc.function?.name ?? ''
            c.function.arguments += tc.function?.arguments ?? ''
          }
        }, ctl.signal)
        meta = { ...meta, model: routed || meta.model, usage, ttft_ms: first, total_ms: performance.now() - t0 }
        if (ctl.signal.aborted) return patch(key, { status: 'stopped', tools: [...calls], meta })
        if (upstreamErr) return patch(key, { status: 'error', tools: [...calls], meta, err: new Error((upstreamErr as Json).message || '업스트림 오류') })
        patch(key, { status: 'done', tools: [...calls], meta })
      } else {
        const j = await res.json()
        const msg = j.choices?.[0]?.message ?? {}
        patch(key, { status: 'done', out: msg.content ?? '', tools: msg.tool_calls ?? [], meta: { ...meta, model: meta.model || j.model, usage: j.usage, total_ms: performance.now() - t0 } })
      }
    } catch (e) {
      meta = { ...meta, total_ms: performance.now() - t0 }
      patch(key, ctl.signal.aborted ? { status: 'stopped', meta } : { status: 'error', err: e, meta })
    }
  }
  const runAll = () => Promise.all(targets.map((t) => runOne(t.key, t.model)))
  const stopAll = () => targets.forEach((t) => ctls.current[t.key]?.abort())
  const addTarget = () => setTargets((ts) => (ts.length >= 4 ? ts : [...ts, { key: nextKey.current++, model: catalog[ts.length - 1]?.id ?? 'auto' }]))
  const removeTarget = (key: number) => {
    ctls.current[key]?.abort()
    setTargets((ts) => ts.filter((t) => t.key !== key))
  }

  return (
    <>
      <PageHeader title="모델 플레이그라운드" crumbs={['모델', '플레이그라운드']} desc="Unified Model API(OpenAI 호환) 로 최대 4개 모델(별칭)을 같은 요청으로 나란히 호출해 응답·지연·토큰·비용을 비교합니다 — 정책·라우팅·한도·가드가 그대로 적용됩니다." />
      <Card title="요청" className="mb-4">
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-2" role="group" aria-label="비교 대상">
            {targets.map((t, i) => (
              <div key={t.key} className="flex items-end gap-1">
                <Field label={`비교 대상 ${LANES[i]}`}>
                  <Select className="w-52" value={t.model} onChange={(e) => setTargets((ts) => ts.map((x) => (x.key === t.key ? { ...x, model: e.target.value } : x)))}>
                    <option value="auto">auto (라우팅 규칙)</option>
                    {catalog.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
                  </Select>
                </Field>
                {targets.length > 1 && <Button size="sm" variant="ghost" aria-label={`비교 대상 ${LANES[i]} 제거`} onClick={() => removeTarget(t.key)}>✕</Button>}
              </div>
            ))}
            <Button onClick={addTarget} disabled={targets.length >= 4} title={targets.length >= 4 ? '최대 4개 모델까지 비교합니다' : undefined}>+ 비교</Button>
            <div className="ml-auto w-64">
              <Field label="데이터 등급 (X-AgentNexus-Data-Class)"><Select value={dc} onChange={(e) => setDc(e.target.value)}><option>public</option><option>internal</option><option>confidential</option></Select></Field>
            </div>
          </div>
          <p className="text-xs text-fg-muted">internal·confidential 은 상용 모델 대신 로컬 모델로 재라우팅되거나 거부됩니다 (MSG-011) — 각 칸의 → 표시가 실제 라우팅 대상입니다.</p>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="space-y-3">
              <Field label="system"><Textarea className="h-16" value={system} onChange={(e) => setSystem(e.target.value)} /></Field>
              <Field label="user"><Textarea className="h-24" value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
            </div>
            <div className="space-y-2 text-sm" role="group" aria-label="호출 옵션">
              <div className="flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-2"><input type="checkbox" checked={stream} onChange={(e) => setStream(e.target.checked)} />스트리밍(SSE)</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={useTools} onChange={(e) => setUseTools(e.target.checked)} />tools</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={useSchema} onChange={(e) => setUseSchema(e.target.checked)} />response_format: json_schema</label>
                <label className="flex items-center gap-2">temperature<Input className="w-20" type="number" min={0} max={2} step={0.1} placeholder="기본" value={temp} onChange={(e) => setTemp(e.target.value)} /></label>
              </div>
              {useTools && (
                <div className="grid grid-cols-[1fr_140px] gap-2">
                  <Field label="tools (OpenAI function 형식)"><Textarea className="mono h-28 text-xs" value={toolsText} onChange={(e) => setToolsText(e.target.value)} /></Field>
                  <Field label="tool_choice"><Select value={toolChoice} onChange={(e) => setToolChoice(e.target.value)}><option value="auto">auto</option><option value="required">required</option><option value="none">none</option></Select></Field>
                </div>
              )}
              {useSchema && <Field label="json_schema ({ name, schema })"><Textarea className="mono h-28 text-xs" value={schemaText} onChange={(e) => setSchemaText(e.target.value)} /></Field>}
              {optErr && <p role="alert" className="text-xs text-danger">{optErr}</p>}
              <p className="text-xs text-fg-muted">옵션은 모든 비교 대상에 똑같이 적용됩니다. 도구는 실행하지 않고 tool_calls(정규화 결과)만 보여 줍니다.</p>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setShowCode((v) => !v)} aria-expanded={showCode}>코드 보기</Button>
            {running && <Button variant="danger" onClick={stopAll}>■ 중지</Button>}
            <Button variant="primary" onClick={runAll} loading={running} disabled={!prompt.trim() || !!optErr}>실행</Button>
          </div>
        </div>
      </Card>
      {showCode && (
        <Card title="코드 예시 (openai SDK) — 모델은 별칭만" className="mb-4">
          <Tabs tabs={[{ id: 'python', label: 'Python' }, { id: 'curl', label: 'curl' }, { id: 'ts', label: 'TypeScript' }]} value={codeTab} onChange={setCodeTab} />
          <CodeBlock value={sampleCode(codeTab, body, targets.map((t) => t.model), dc)} maxH="max-h-72" />
        </Card>
      )}
      <div className={`grid gap-4 ${LANE_GRID[targets.length]}`}>
        {targets.map((t, i) => (
          <LanePanel key={t.key} label={`${LANES[i]} · ${t.model}`} lane={lanes[t.key] ?? IDLE} schema={useSchema && schema ? schema.schema ?? {} : null}
            price={price(lanes[t.key]?.meta?.model)} onStop={() => ctls.current[t.key]?.abort()} />
        ))}
      </div>
    </>
  )
}

function LanePanel({ label, lane, schema, price, onStop }: { label: string; lane: Lane; schema: Json | null; price?: Json; onStop: () => void }) {
  const meta = lane.meta
  const usage = meta?.usage
  const routed: string | undefined = meta?.model
  const cost = usage && price ? ((usage.prompt_tokens ?? 0) * price.price_in + (usage.completion_tokens ?? 0) * price.price_out) / 1_000_000 : null
  const fellBack = !!routed && (meta?.fallback || (meta?.requested !== 'auto' && routed !== meta?.requested))
  const schemaErr = schema && lane.status === 'done' && lane.out ? checkSchema(lane.out, schema) : null
  return (
    <section aria-label={`비교 결과 ${label}`} className="rounded-lg border border-line bg-surface">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 className="mono text-sm font-semibold text-navy">{label}</h2>
        <div className="flex items-center gap-2">
          {routed && <Badge tone={fellBack ? 'warn' : 'model'} title="실제 라우팅 대상 (X-AgentNexus-Model)">→ {routed}{fellBack ? ' · 폴백 (MSG-015)' : ''}</Badge>}
          {lane.status === 'running' ? <Button size="sm" variant="ghost" onClick={onStop}>■ 중지</Button> : lane.status === 'stopped' ? <Badge>중지됨</Badge> : null}
        </div>
      </header>
      <div className="space-y-3 p-4">
        {lane.err ? <ErrorBox error={lane.err} /> : null}
        {lane.tools.length > 0 && (
          <div>
            <div className="mb-1 text-xs text-fg-muted">tool_calls (OpenAI 형식으로 정규화 — 실행은 게이트웨이·허용목록 경유)</div>
            <CodeBlock value={lane.tools.map((c) => `${c.function?.name}(${c.function?.arguments})`).join('\n')} maxH="max-h-32" />
          </div>
        )}
        {!lane.err && (lane.out || !lane.tools.length) && (
          <div>
            {schema && (
              <div className="mb-1 text-xs">
                {schemaErr ? <span className="text-danger">응답 json_schema 검증 실패 — {schemaErr}</span>
                  : lane.status === 'done' && lane.out ? <span className="text-ok">응답 json_schema 검증 ✓</span> : <span className="text-fg-muted">응답 (json_schema)</span>}
              </div>
            )}
            {schema && lane.out ? <CodeBlock value={lane.out} maxH="max-h-48" />
              : <p className="min-h-16 text-sm whitespace-pre-wrap">{lane.out || <span className="text-fg-muted">{lane.status === 'running' ? '응답 대기 중…' : '응답이 여기에 스트리밍됩니다.'}</span>}</p>}
          </div>
        )}
        {meta && (
          <Kv rows={[
            ['첫 토큰 · 전체', `${fmtMs(meta.ttft_ms)} · ${fmtMs(meta.total_ms)}`],
            ['토큰', usage ? `${fmtNum(usage.prompt_tokens)} in / ${fmtNum(usage.completion_tokens)} out` : '—'],
            ['비용 (단가표 추정)', cost == null ? '—' : fmtUsd(cost)],
            ['trace', <TraceLink id={meta.headers?.['x-agentnexus-trace-id']} />],
          ]} />
        )}
      </div>
    </section>
  )
}
