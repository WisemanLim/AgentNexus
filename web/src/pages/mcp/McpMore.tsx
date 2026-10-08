// SCR-F03 SaaS 참조 서버 설정(엔드포인트·인증·mTLS → 적용 전 시험 → 적용) · F04 Internal API 서버 생성(OpenAPI → 엔드포인트 선택·이름·위험도) · F05 Custom 서버 템플릿(4단계)·CLI 스캐폴딩
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Badge, Button, Card, CodeBlock, ErrorBox, Field, Gate, Input, Kv, Loading, PageHeader, Select, Stepper, Tabs, Textarea, cx } from '@/components/ui'
import type { Json } from '@/lib/api'
import { send, useGet } from '@/lib/query'

export function SaasConfig() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const s = useGet(`/api/v1/mcp-servers/${id}`)
  const [step, setStep] = useState(0)
  const [f, setF] = useState<Json>({ endpoint: '', type: 'oauth2', mode: 'exchange', header: 'X-API-Key', client_id: '', secret: '', ca: '', cert: '', scopes: '' })
  const [res, setRes] = useState<Json | null>(null)
  const [trial, setTrial] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!s.data) return
    const a = s.data.auth ?? {}
    setF((x: Json) => ({ ...x, endpoint: s.data.endpoint ?? '', type: a.type ?? 'none', mode: a.mode ?? 'exchange', header: a.header ?? 'X-API-Key',
      client_id: a.client_id ?? '', ca: a.tls?.ca_secret ?? '', cert: a.tls?.client_cert_secret ?? '', scopes: (a.scopes ?? []).join(' ') }))
  }, [s.data])
  const candidate = () => {
    const tls = f.ca || f.cert ? { ...(f.ca ? { ca_secret: f.ca } : {}), ...(f.cert ? { client_cert_secret: f.cert } : {}) } : undefined
    const auth: Json = { type: f.type, ...(f.type === 'oauth2' ? { mode: f.mode, ...(f.mode === 'service' ? { client_id: f.client_id } : {}), scopes: f.scopes.split(/[\s,]+/).filter(Boolean) } : {}),
      ...(f.type === 'api_key' ? { header: f.header } : {}), tls: tls ?? null }
    return { endpoint: f.endpoint, auth, secret: f.secret || undefined }
  }
  const tryIt = async () => {  // 적용 전 시험 — 후보 설정으로 발견·tools/list·스코프 점검 (저장 안 함)
    setBusy(true)
    setErr(null)
    try {
      setTrial(await send<Json>('POST', `/api/v1/mcp-servers/${id}/test-config`, candidate()))
      setStep(3)
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  const apply = async () => {
    setBusy(true)
    setErr(null)
    try {
      await send('PATCH', `/api/v1/mcp-servers/${id}`, candidate())
      setRes(await send<Json>('POST', `/api/v1/mcp-servers/${id}/refresh`))
      setStep(4)
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  if (s.error) return <ErrorBox error={s.error} />
  if (!s.data) return <Loading />
  return (
    <>
      <PageHeader title={`서버 설정 — ${s.data.name}`} crumbs={['MCP 서버', id, '설정']} desc="SaaS 참조 서버(Jira·Confluence·Dynatrace)나 등록 서버의 연결·인증을 바꿉니다 (FR-S01·I03). 시크릿은 저장소에만." />
      <Card>
        <Stepper steps={['엔드포인트', '인증', 'mTLS', '적용 전 시험', '적용']} current={step} />
        {step === 0 && <Field label="Streamable HTTP 엔드포인트" hint="mcp-atlassian·dynatrace-mcp 등 실제 서버로 교체 가능"><Input className="mono" value={f.endpoint} onChange={(e) => setF({ ...f, endpoint: e.target.value })} /></Field>}
        {step === 1 && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="방식"><Select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
              <option value="oauth2">OAuth 2.1 (RFC 9728 발견)</option><option value="bearer">Bearer 토큰</option><option value="api_key">API 키 헤더</option><option value="none">없음 (신원 미전파)</option>
            </Select></Field>
            {f.type === 'oauth2' && <Field label="신원" hint="exchange = 사용자 위임(최소 스코프) · service = 서비스 계정"><Select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="exchange">사용자 위임 (RFC 8693)</option><option value="service">서비스 계정 (client_credentials)</option></Select></Field>}
            {f.type === 'oauth2' && f.mode === 'service' && <Field label="client_id"><Input value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })} /></Field>}
            {f.type === 'oauth2' && <Field label="요청 스코프 (공백·쉼표)" hint="도구가 쓰는 스코프만 — 시험에서 최소화 점검"><Input className="mono" value={f.scopes} onChange={(e) => setF({ ...f, scopes: e.target.value })} placeholder="issues:read" /></Field>}
            {f.type === 'api_key' && <Field label="헤더 이름"><Input value={f.header} onChange={(e) => setF({ ...f, header: e.target.value })} /></Field>}
            {(f.type !== 'none' && !(f.type === 'oauth2' && f.mode === 'exchange')) && (
              <Field label={s.data.auth?.secret_ref ? '시크릿 (비우면 유지)' : '시크릿'} hint="MSG-018 — 다시 표시되지 않습니다"><Input type="password" autoComplete="off" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></Field>
            )}
            {f.type === 'none' && <p className="col-span-2 text-xs text-warn">인증 없는 서버는 정책·감사는 적용되지만 사용자 신원이 전파되지 않습니다.</p>}
          </div>
        )}
        {step === 2 && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="CA 시크릿 (서버 인증서 검증)"><Input className="mono" value={f.ca} onChange={(e) => setF({ ...f, ca: e.target.value })} placeholder="tls/saas-ca" /></Field>
            <Field label="클라이언트 인증서 시크릿 (cert+key PEM)"><Input className="mono" value={f.cert} onChange={(e) => setF({ ...f, cert: e.target.value })} placeholder="tls/gateway-client" /></Field>
            <p className="col-span-2 text-xs text-fg-muted">https 엔드포인트에만 적용 · 정책은 <Link className="text-primary" to="/console/gateway/security">Security</Link>(required 이면 미설정 서버 차단) · 인증서는 <Link className="text-primary" to="/console/gateway/secrets">시크릿 저장소</Link>(kind=certificate)</p>
          </div>
        )}
        {step === 3 && trial && (
          <div className="space-y-3 text-sm">
            <p className={trial.ok ? 'text-ok' : 'text-danger'}>{trial.ok ? `✓ 시험 통과 — 아직 저장하지 않았습니다 (${trial.elapsed_ms}ms)` : '시험 실패 — 적용하지 않습니다. 설정을 고친 뒤 다시 시험하세요.'}</p>
            <ul className="space-y-1">{trial.checks.map((c: Json) => <li key={c.key} className={c.level === 'ok' ? 'text-ok' : c.level === 'warn' ? 'text-warn' : 'text-danger'}>{c.level === 'ok' ? '✓' : c.level === 'warn' ? '!' : '✗'} <span className="text-fg">{({ prm: '보호 리소스 메타데이터', authorization_server: '인가 서버', credential: '자격 증명', tools_list: 'tools/list', scopes: '스코프 최소화', protocol: '프로토콜' } as Record<string, string>)[c.key] ?? c.key}</span> <span className="text-xs text-fg-muted">{c.detail}</span></li>)}</ul>
            {trial.tools?.length > 0 && <div className="flex flex-wrap gap-1">{trial.tools.map((t: Json) => <Badge key={t.name} tone={t.risk === 'high' ? 'danger' : t.risk === 'medium' ? 'warn' : 'mcp'}>{t.name}</Badge>)}</div>}
            {(trial.added?.length > 0 || trial.removed?.length > 0) && <p className="text-xs">현재 대비 도구 <span className="text-ok">+{trial.added.length}</span> {trial.added.join(', ')} · <span className="text-danger">−{trial.removed.length}</span> {trial.removed.join(', ')}</p>}
          </div>
        )}
        {step === 4 && res && (
          <div className="space-y-2 text-sm">
            <p className={res.health === 'healthy' ? 'text-ok' : 'text-danger'}>{res.health === 'healthy' ? `연결 성공 — 도구 ${res.tools?.length ?? 0}개 · ${res.latency_ms}ms` : `연결 실패 — ${res.error}`}</p>
            <div className="flex flex-wrap gap-1">{(res.tools ?? []).map((t: string) => <Badge key={t} tone="mcp">{t}</Badge>)}</div>
          </div>
        )}
        {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
        <div className="mt-4 flex justify-end gap-2">
          {step > 0 && step < 4 && <Button onClick={() => setStep(step - 1)}>이전</Button>}
          {step < 2 && <Button variant="primary" disabled={!f.endpoint} onClick={() => setStep(step + 1)}>다음</Button>}
          {step === 2 && <Gate perm="mcp:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} loading={busy} onClick={tryIt}>적용 전 시험</Button>}</Gate>}
          {step === 3 && <Gate perm="mcp:write">{(ok, why) => <Button variant="primary" disabled={!ok || !trial?.ok} title={!trial?.ok ? '시험을 통과해야 적용할 수 있습니다' : why} loading={busy} onClick={apply}>적용</Button>}</Gate>}
          {step === 4 && <><Button onClick={() => setStep(0)}>다시 설정</Button><Button variant="primary" onClick={() => nav(`/console/mcp-servers/${id}`)}>서버 상세 →</Button></>}
        </div>
      </Card>
    </>
  )
}

const PETSTORE = JSON.stringify({ openapi: '3.0.3', info: { title: 'Orders API', version: '1.0.0' }, paths: {
  '/orders/{id}': { get: { operationId: 'orders.get', summary: '주문 조회', parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'string' } }], responses: { 200: { description: 'ok' } } } },
} }, null, 2)

const HTTP_METHODS = new Set(['get', 'put', 'post', 'delete', 'patch', 'head', 'options', 'trace'])
const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const TOOL_NAME = /^[A-Za-z0-9_.-]{1,64}$/
type Op = { key: string; p: string; method: string; id?: string; summary?: string; tags: string[]; params: Json[]; body?: Json }
type OpCfg = { include: boolean; name: string; risk: string }

function parseSpec(text: string): { doc: Json; ops: Op[] } | null {
  try {
    const doc = JSON.parse(text)
    if (!doc || typeof doc !== 'object') return null
    const ops = Object.entries<Json>(doc.paths ?? {}).flatMap(([p, m]) => Object.entries<Json>(m ?? {})
      .filter(([k, o]) => HTTP_METHODS.has(k) && o && typeof o === 'object')
      .map(([method, o]) => ({ key: `${method} ${p}`, p, method: method.toUpperCase(), id: o.operationId, summary: o.summary ?? o.description, tags: o.tags ?? [],
        params: [...(m.parameters ?? []), ...(o.parameters ?? [])], body: o.requestBody?.content?.['application/json']?.schema })))
    return { doc, ops }
  } catch {
    return null
  }
}
// 쓰기·삭제는 기본 해제 + 고위험 (SCR-F04 ⑤, MSG-010)
const defaultCfg = (o: Op): OpCfg => ({ include: !WRITE_METHODS.has(o.method), name: '', risk: WRITE_METHODS.has(o.method) ? 'high' : 'low' })
const toolName = (o: Op, c: OpCfg) => (c.name.trim() || o.id || '').replace(/\./g, '_')

export function OpenApiWizard() {
  const nav = useNavigate()
  const [step, setStep] = useState(0)
  const [f, setF] = useState({ namespace: 'orders-api', title: 'Orders API (내부)', base_url: 'http://orders.internal', auth_header: 'Authorization', secret: '',
    identity: 'service' as 'service' | 'obo', scopes: 'api:call' })
  const [spec, setSpec] = useState(PETSTORE)
  const [url, setUrl] = useState('')
  const [fetching, setFetching] = useState(false)
  const [importErr, setImportErr] = useState('')
  const [cfg, setCfg] = useState<Record<string, OpCfg>>({})
  const [preview, setPreview] = useState('')
  const [res, setRes] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const parsed = useMemo(() => parseSpec(spec), [spec])
  const ops = parsed?.ops ?? null
  const doc = parsed?.doc
  const v3 = String(doc?.openapi ?? '').startsWith('3')
  const eff = (o: Op) => cfg[o.key] ?? defaultCfg(o)
  const setOp = (o: Op, patch: Partial<OpCfg>) => setCfg((x) => ({ ...x, [o.key]: { ...eff(o), ...patch } }))
  const selected = (ops ?? []).filter((o) => !o.id || eff(o).include)
  const writes = selected.filter((o) => WRITE_METHODS.has(o.method))
  const names = selected.map((o) => toolName(o, eff(o))).filter(Boolean)
  const badName = (ops ?? []).find((o) => o.id && eff(o).include && eff(o).name.trim() && !TOOL_NAME.test(eff(o).name.trim()))
  const dup = names.find((n, i) => names.indexOf(n) !== i)
  const nameErr = badName ? `도구 이름 형식 오류: ${eff(badName).name} — 영문·숫자·_ . - 64자 이내 (MSG-004)` : dup ? `도구 이름 중복: ${dup} (MSG-004)` : ''
  const load = (text: string) => {  // 가져온 문서의 제목·서버 URL 로 연결 단계를 미리 채운다
    setSpec(text)
    setCfg({})
    const p = parseSpec(text)
    if (!p) {
      setImportErr('JSON 형식 오류 — OpenAPI 3.x JSON 문서가 필요합니다 (MSG-004)')
      return
    }
    setImportErr('')
    setF((x) => ({ ...x, ...(p.doc.info?.title ? { title: String(p.doc.info.title).slice(0, 100) } : {}), ...(typeof p.doc.servers?.[0]?.url === 'string' && /^https?:\/\//.test(p.doc.servers[0].url) ? { base_url: p.doc.servers[0].url } : {}) }))
  }
  const fetchUrl = async () => {  // 절대 URL 은 게이트웨이가 가져온다(CORS 무관, egress·2MB 제한) · 같은 출처 경로(/…)는 브라우저
    setFetching(true)
    setImportErr('')
    try {
      if (url.startsWith('/')) {
        const r = await fetch(url, { headers: { Accept: 'application/json' } })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        load(await r.text())
      } else {
        load(JSON.stringify((await send<Json>('POST', '/api/v1/mcp-servers/openapi/fetch', { url })).openapi))
      }
    } catch (e: any) {
      setImportErr(`URL 가져오기 실패 (${e.message}) — 파일 열기나 붙여넣기를 쓰세요 (MSG-004)`)
    } finally {
      setFetching(false)
    }
  }
  const create = async () => {
    setErr(null)
    try {
      const operations = (ops ?? []).filter((o) => o.id).map((o) => {
        const c = eff(o)
        return { operationId: o.id, include: c.include, ...(c.name.trim() ? { name: c.name.trim() } : {}), risk: c.risk }
      })
      setRes(await send<Json>('POST', '/api/v1/mcp-servers/openapi', { ...f, openapi: doc, operations, scopes: f.scopes.split(/[\s,]+/).filter(Boolean),
        secret: f.identity === 'service' ? f.secret || undefined : undefined }))
      setStep(3)
    } catch (e) {
      setErr(e)
    }
  }
  const pv = (ops ?? []).find((o) => o.key === preview) ?? selected[0]
  return (
    <>
      <PageHeader title="Internal API 서버 생성 (OpenAPI → 도구)" crumbs={['MCP 서버', '새 서버', 'OpenAPI']} desc="사내 REST API 의 OpenAPI 문서로 MCP 도구를 만들고 게이트웨이 정책 아래에 둡니다 (FR-S02). operationId 의 '.' 는 '_' 로 바뀝니다." />
      <Card>
        <Stepper steps={['OpenAPI 가져오기', '연결·내부 인증', '엔드포인트 → 도구', '배포·등록']} current={step} />
        {step === 0 && (
          <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
            <div>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <label className="inline-block cursor-pointer rounded border border-line px-2 py-1 text-xs hover:bg-surface-2">파일 열기 (JSON)
                  <input type="file" accept="application/json" className="sr-only" onChange={async (e) => { const fl = e.target.files?.[0]; if (fl) load(await fl.text()) }} />
                </label>
                <Input aria-label="OpenAPI URL" className="mono h-7 w-80 text-xs" placeholder="https://orders.internal/openapi.json" value={url} onChange={(e) => setUrl(e.target.value)} />
                <Button size="sm" disabled={!/^(https?:\/\/|\/)/.test(url)} loading={fetching} onClick={fetchUrl}>URL 가져오기</Button>
              </div>
              <Textarea aria-label="OpenAPI 문서" className="mono h-64 text-xs" value={spec} onChange={(e) => { setSpec(e.target.value); setCfg({}) }} />
              {!ops && <p role="alert" className="mt-1 text-xs text-danger">JSON 형식 오류 (MSG-004)</p>}
              {importErr && ops && <p role="alert" className="mt-1 text-xs text-danger">{importErr}</p>}
              {ops && !v3 && <p role="alert" className="mt-1 text-xs text-danger">OpenAPI 3.x 문서가 필요합니다 (MSG-004)</p>}
            </div>
            <div className="rounded-md border border-line p-3">
              <h3 className="mb-2 text-sm font-semibold text-navy">가져온 명세</h3>
              {ops ? (
                <>
                  <Kv rows={[['제목', doc?.info?.title ?? '—'], ['형식', `OpenAPI ${doc?.openapi ?? '?'}`], ['버전', doc?.info?.version ?? '—'], ['엔드포인트', ops.length],
                    ['태그', [...new Set(ops.flatMap((o) => o.tags))].join(', ') || '—']]} />
                  <ul className="mt-2 space-y-0.5 text-xs">
                    <li className={v3 ? 'text-ok' : 'text-danger'}>{v3 ? '✓' : '✗'} 스키마 3.x</li>
                    <li className={ops.every((o) => o.id) ? 'text-ok' : 'text-warn'}>{ops.every((o) => o.id) ? '✓' : '!'} operationId {ops.filter((o) => o.id).length}/{ops.length}</li>
                  </ul>
                </>
              ) : <p className="text-xs text-danger">문서를 읽을 수 없습니다.</p>}
            </div>
          </div>
        )}
        {step === 1 && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="네임스페이스 (/mcp/{id})"><Input className="mono" value={f.namespace} onChange={(e) => setF({ ...f, namespace: e.target.value })} /></Field>
            <Field label="이름"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
            <Field label="API base_url"><Input className="mono" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} /></Field>
            <Field label="인증 헤더"><Input value={f.auth_header} onChange={(e) => setF({ ...f, auth_header: e.target.value })} /></Field>
            <fieldset className="col-span-2 rounded-md border border-line p-3 text-sm">
              <legend className="px-1 text-xs font-medium text-fg-muted">내부 인증 연동</legend>
              <label className="flex items-center gap-2"><input type="radio" name="identity" checked={f.identity === 'service'} onChange={() => setF({ ...f, identity: 'service' })} />서비스 클라이언트 자격 증명 — 게이트웨이가 아래 자격 증명을 인증 헤더로 주입</label>
              <label className="mt-1 flex items-center gap-2"><input type="radio" name="identity" checked={f.identity === 'obo'} onChange={() => setF({ ...f, identity: 'obo' })} />사용자 토큰 전파 (OBO) — 호출마다 사용자 토큰을 교환해 주입</label>
              <p className="mt-2 text-xs text-fg-muted">OBO: 사용자 토큰을 audience <span className="mono">api:{f.namespace}</span> 로 교환(RFC 8693, 5분 수명)해 <span className="mono">Authorization: Bearer</span> 로 전달합니다 — 내부 API 가 사용자 신원(sub·팀·역할)으로 판정. 교환 정책은 생성 시 함께 만듭니다(J04 에서 조정).</p>
            </fieldset>
            {f.identity === 'service'
              ? <Field label="API 자격 증명 (선택)" hint="시크릿 저장소에만 — 도구 호출 때 게이트웨이가 주입"><Input type="password" autoComplete="off" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></Field>
              : <Field label="교환 스코프" hint="공백·쉼표 구분 — 내부 API 가 확인할 스코프"><Input className="mono" value={f.scopes} onChange={(e) => setF({ ...f, scopes: e.target.value })} /></Field>}
          </div>
        )}
        {step === 2 && ops && (
          <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                <span className="text-fg-muted">선택 {selected.length} / {ops.length}</span>
                <Button size="sm" variant="ghost" onClick={() => setCfg(Object.fromEntries(ops.map((o) => [o.key, { ...eff(o), include: true }])))}>전체 선택</Button>
                <Button size="sm" variant="ghost" onClick={() => setCfg(Object.fromEntries(ops.map((o) => [o.key, { ...eff(o), include: !WRITE_METHODS.has(o.method) }])))}>읽기(GET)만</Button>
              </div>
              <table className="w-full text-sm"><thead className="text-left text-xs text-fg-muted"><tr><th className="w-8" /><th>도구</th><th>메서드</th><th>경로</th><th>이름 변경</th><th>위험</th><th>설명</th></tr></thead>
                <tbody>{ops.map((o) => {
                  const c = eff(o)
                  const on = !o.id || c.include
                  return (
                    <tr key={o.key} className={cx('border-t border-line', !on && 'text-fg-muted', pv?.key === o.key && 'bg-surface-2')} onClick={() => setPreview(o.key)}>
                      <td className="py-1.5"><input type="checkbox" aria-label={`${o.method} ${o.p} 노출`} checked={on} disabled={!o.id} title={!o.id ? 'operationId 가 없으면 서버가 자동 이름으로 항상 노출합니다' : undefined} onChange={(e) => setOp(o, { include: e.target.checked })} /></td>
                      <td className="mono py-1.5 text-xs">{toolName(o, c) || <span className="text-warn">(자동)</span>}</td>
                      <td><Badge tone={o.method === 'GET' ? 'ok' : 'warn'}>{o.method}</Badge></td>
                      <td className="mono text-xs">{o.p}</td>
                      <td>{o.id ? <Input aria-label={`${o.id} 도구 이름`} className="mono h-7 w-36 text-xs" placeholder={o.id} value={c.name} disabled={!c.include} onChange={(e) => setOp(o, { name: e.target.value })} /> : '—'}</td>
                      <td>{o.id ? <Select aria-label={`${o.id} 위험도`} className="h-7 w-24 text-xs" value={c.risk} disabled={!c.include} onChange={(e) => setOp(o, { risk: e.target.value })}><option>low</option><option>medium</option><option>high</option></Select> : '—'}</td>
                      <td className="text-xs">{o.summary}</td>
                    </tr>
                  )
                })}</tbody></table>
              <div role="note" className="mt-3 rounded-md border border-warn/50 bg-warn/10 p-3 text-xs">쓰기·삭제 엔드포인트(POST·PUT·PATCH·DELETE)는 기본 해제 — 선택하면 고위험 도구로 등록되고 호출 때 사람 승인을 받습니다 (MSG-010).{writes.length > 0 && <strong> 선택한 쓰기 도구 {writes.length}개: {writes.map((o) => toolName(o, eff(o))).join(', ')}</strong>}</div>
              {nameErr && <p role="alert" className="mt-2 text-xs text-danger">{nameErr}</p>}
              {!selected.length && <p role="alert" className="mt-2 text-xs text-danger">노출할 엔드포인트를 하나 이상 선택하세요.</p>}
            </div>
            <div className="rounded-md border border-line p-3">
              <h3 className="mb-1 text-sm font-semibold text-navy">도구 미리보기</h3>
              {pv ? (
                <>
                  <div className="mono text-sm">{toolName(pv, eff(pv)) || '(자동)'}</div>
                  <p className="mb-2 text-xs text-fg-muted">{pv.summary ?? '설명 없음'}</p>
                  <CodeBlock maxH="max-h-72" value={`input:\n${pv.params.map((x) => `  ${x.name}: ${x.schema?.type ?? 'any'}${x.required ? ' (필수)' : ''} · ${x.in}`).join('\n') || '  (매개변수 없음)'}${pv.body ? `\nbody: ${JSON.stringify(pv.body, null, 2)}` : ''}\nrisk: ${eff(pv).risk}${eff(pv).risk === 'high' ? ' → 사람 승인' : ''}`} />
                </>
              ) : <p className="text-xs text-fg-muted">행을 누르면 입력 스키마를 봅니다.</p>}
            </div>
          </div>
        )}
        {step === 3 && res && (
          <div className="space-y-2 text-sm">
            <p className="text-ok">생성 — /mcp/{res.instance?.id ?? f.namespace} · 도구 {res.instance?.refresh?.tools?.length ?? res.refresh?.tools?.length ?? '?'}개 (쓰기 도구는 승인 필요 기본 규칙)</p>
            <ul className="text-xs text-fg-muted">
              <li>✓ 게이트웨이 안 hosted-openapi(FastMCP) 로 배치 · 게이트웨이 뒤에서만 접근</li>
              <li>✓ server.json 자동 생성 → 레지스트리 게시·승인(E05) → 카탈로그 등록</li>
              {res.risks && <li>✓ 위험도 적용: {Object.entries<string>(res.risks).map(([k, v]) => `${k}=${v}`).join(', ')}</li>}
            </ul>
            <CodeBlock value={res} maxH="max-h-48" />
          </div>
        )}
        {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
        <div className="mt-4 flex justify-end gap-2">
          {step > 0 && step < 3 && <Button onClick={() => setStep(step - 1)}>이전</Button>}
          {step < 2 && <Button variant="primary" disabled={!ops || !v3} onClick={() => setStep(step + 1)}>다음</Button>}
          {step === 2 && <Gate perm="mcp:write">{(ok, why) => <Button variant="primary" disabled={!ok || !selected.length || !!nameErr} title={why} onClick={create}>생성·등록</Button>}</Gate>}
          {step === 3 && <Button variant="primary" onClick={() => nav(`/console/mcp-servers/${res?.instance?.id ?? f.namespace}`)}>서버 상세 →</Button>}
        </div>
      </Card>
    </>
  )
}

// SCR-F05 ③ 포함 항목 — 인증(anx_auth)·관측(telemetry)·server.json 은 항상 포함, 아래 셋만 선택 (서버 OPTIONAL 과 일치)
const OPTIONAL = [
  { id: 'docker', label: 'Dockerfile (컨테이너 이미지)', file: 'Dockerfile' },
  { id: 'register', label: '기동 시 자동 등록 (anx_register.py)', file: 'anx_register.py' },
  { id: 'readme', label: 'README.md (로컬 개발·게시 안내)', file: 'README.md' },
] as const
const FIXED = ['도구 예제 (server.py — echo·create_note)', 'OAuth 2.1 리소스 서버 검증 (anx_auth.py · RFC 9728)', 'OTel 계측 (telemetry.py)', 'server.json 생성 (도구별 스코프)', 'pyproject.toml (uv)']
const NAME_RE = /^[a-z0-9][a-z0-9-]{0,62}$/
const q2 = (s: string) => `"${s.replace(/(["\\$`])/g, '\\$1')}"`

export function CustomTemplate() {
  const [step, setStep] = useState(0)
  const [f, setF] = useState({ name: 'orders-tool', title: 'Orders Tool', description: '', public_url: '' })
  const [inc, setInc] = useState<string[]>(OPTIONAL.map((o) => o.id))
  const [applied, setApplied] = useState<{ f: typeof f; inc: string[] } | null>(null)
  const incQs = applied && !applied.inc.length ? 'include=none' : (applied?.inc ?? []).map((i) => `include=${i}`).join('&')
  // include=none = 선택 파일 없음 (include 를 비우면 서버는 전부) — 화면에서도 선택하지 않은 선택 파일을 뺀다
  const q = useGet(applied ? `/api/v1/mcp-servers/templates/custom${incQs ? `?${incQs}` : ''}` : null,
    applied ? { name: applied.f.name, title: applied.f.title || undefined, description: applied.f.description || undefined, public_url: applied.f.public_url || undefined } : undefined)
  const files: Record<string, string> = useMemo(() => Object.fromEntries(Object.entries<string>(q.data?.files ?? {})
    .filter(([k]) => !OPTIONAL.some((o) => o.file === k && !(applied?.inc ?? []).includes(o.id)))), [q.data, applied])
  const [tab, setTab] = useState('server.py')
  const nameOk = NAME_RE.test(f.name)
  const download = (fname: string) => {
    const url = URL.createObjectURL(new Blob([files[fname]], { type: 'text/plain' }))
    Object.assign(document.createElement('a'), { href: url, download: fname }).click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const a = step >= 2 && applied ? applied.f : f
  const ai = step >= 2 && applied ? applied.inc : inc
  const omitted = OPTIONAL.filter((o) => !ai.includes(o.id))
  // 콘솔 옵션과 1:1 인 CLI — --include 는 기본(전부)과 다를 때만
  const cli = [`nexus mcp scaffold ${a.name} --title ${q2(a.title || a.name)}${a.description ? ` --description ${q2(a.description)}` : ''}${a.public_url ? ` --public-url ${a.public_url}` : ''}${omitted.length ? (ai.length ? ai.map((i) => ` --include ${i}`).join('') : ' --include none') : ''}`,
    `cd ${a.name} && uv sync && uv run uvicorn server:app`, `nexus registry publish ${a.name}/server.json   # 또는 콘솔 › 카탈로그 › 등록`].join('\n')
  const toPreview = () => {
    setApplied({ f, inc })
    setTab('server.py')
    setStep(2)
  }
  return (
    <>
      <PageHeader title="Custom 서버 템플릿·CLI 스캐폴딩" crumbs={['MCP 서버', '새 서버', 'Custom']} desc="FastMCP + OAuth(리소스 서버) + OTel + server.json 이 포함된 서버 골격 (FR-S03). CLI 로도 같은 파일을 만듭니다." />
      <Card className="mb-4">
        <Stepper steps={['이름·언어', '포함 항목', '미리보기', '내려받기·CLI']} current={step} />
        {step === 0 && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="이름 (영문 소문자·하이픈)" error={nameOk ? undefined : '영문 소문자·숫자·하이픈, 63자 이내 (MSG-004)'}><Input className="mono" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label="제목"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
            <Field label="설명 (선택)"><Input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
            <Field label="공개 URL (선택)" hint={`비우면 http://${f.name || '<name>'}:8080`}><Input className="mono" value={f.public_url} onChange={(e) => setF({ ...f, public_url: e.target.value })} placeholder="https://orders-tool.internal" /></Field>
            <Field label="템플릿" hint="현재 템플릿은 FastMCP(Python) 하나입니다"><Select value="fastmcp-python" onChange={() => undefined}><option value="fastmcp-python">FastMCP · Python 3.12</option></Select></Field>
            <Field label="전송" hint="게이트웨이 뒤 원격 서버 — stdio 는 호스트 사이드카 전용"><Select value="streamable-http" onChange={() => undefined}><option value="streamable-http">Streamable HTTP</option></Select></Field>
          </div>
        )}
        {step === 1 && (
          <div className="grid gap-4 md:grid-cols-2">
            <fieldset className="space-y-1.5 text-sm">
              <legend className="mb-1 text-xs font-medium text-fg-muted">항상 포함</legend>
              {FIXED.map((x) => <label key={x} className="flex items-center gap-2 text-fg-muted"><input type="checkbox" checked disabled />{x}</label>)}
            </fieldset>
            <fieldset className="space-y-1.5 text-sm">
              <legend className="mb-1 text-xs font-medium text-fg-muted">선택</legend>
              {OPTIONAL.map((o) => (
                <label key={o.id} className="flex items-center gap-2"><input type="checkbox" checked={inc.includes(o.id)} onChange={(e) => setInc(e.target.checked ? [...inc, o.id] : inc.filter((x) => x !== o.id))} />{o.label}</label>
              ))}
            </fieldset>
          </div>
        )}
        {step >= 2 && (
          <>
            {q.isLoading && <Loading />}
            {q.error && <ErrorBox error={q.error} onRetry={() => q.refetch()} />}
            {q.data && step === 2 && (
              <div className="grid gap-4 lg:grid-cols-[200px_1fr]">
                <div>
                  <h3 className="mb-1 text-xs font-medium text-fg-muted">생성 파일 {Object.keys(files).length}개</h3>
                  <pre className="mono text-xs">{`${a.name}/\n${Object.keys(files).map((k, i, arr) => `${i === arr.length - 1 ? '└' : '├'}─ ${k}`).join('\n')}`}</pre>
                </div>
                <div className="min-w-0">
                  <Tabs tabs={Object.keys(files).map((x) => ({ id: x, label: x }))} value={tab} onChange={setTab} />
                  <CodeBlock value={files[tab] ?? ''} maxH="max-h-[480px]" />
                </div>
              </div>
            )}
            {q.data && step === 3 && (
              <div className="space-y-4">
                <div>
                  <h3 className="mb-1 text-xs font-medium text-fg-muted">파일 내려받기</h3>
                  <div className="flex flex-wrap gap-2">
                    {Object.keys(files).map((x) => <Button key={x} size="sm" onClick={() => download(x)}>{x} 내려받기</Button>)}
                    <Button size="sm" variant="primary" onClick={() => Object.keys(files).forEach((x, i) => setTimeout(() => download(x), i * 200))}>모두 내려받기</Button>
                  </div>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="text-sm">
                    <h3 className="mb-1 text-xs font-medium text-fg-muted">로컬 개발·점검</h3>
                    <p className="text-xs">로컬 기동 후 등록하면 <Link className="text-primary" to="/console/mcp-servers">Inspector(F06)</Link>로 사양 적합성·도구를 점검합니다.</p>
                  </div>
                  <div className="text-sm">
                    <h3 className="mb-1 text-xs font-medium text-fg-muted">게시·등록</h3>
                    <p className="text-xs">publish → 스키마·서명 검증 → 승인 대기(E05) → 호스팅(F01) → 카탈로그 노출</p>
                    <Link to="/console/registry/publish"><Button size="sm" variant="primary" className="mt-2">server.json 게시 →</Button></Link>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
        <h3 className="mt-4 mb-1 text-xs font-medium text-fg-muted">CLI 스캐폴딩 (콘솔 결과와 동일)</h3>
        <CodeBlock value={cli} maxH="max-h-28" />
        <div className="mt-4 flex justify-end gap-2">
          {step > 0 && <Button onClick={() => setStep(step - 1)}>이전</Button>}
          {step === 0 && <Button variant="primary" disabled={!nameOk} onClick={() => setStep(1)}>다음</Button>}
          {step === 1 && <Gate perm="mcp:write">{(ok, why) => <Button variant="primary" disabled={!ok || !nameOk} title={why} onClick={toPreview}>미리보기</Button>}</Gate>}
          {step === 2 && <Button variant="primary" disabled={!q.data} onClick={() => setStep(3)}>내려받기·CLI</Button>}
        </div>
      </Card>
    </>
  )
}
