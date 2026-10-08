// SCR-I02 공급자 어댑터 설정·연결 시험 · I03 도메인 특화 모델 등록(4단계) · I05 Access Tokens 발급·폐기 (단수명·스코프 제한, FR-L03)
import { createColumnHelper } from '@tanstack/react-table'
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, cx, Empty, ErrorBox, Field, Gate, Input, Loading, Modal, PageHeader, Select, StatusBadge, Stepper, useToast } from '@/components/ui'
import type { Json } from '@/lib/api'
import { fmtMs, fmtTime } from '@/lib/format'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()
const DCS = ['public', 'internal', 'confidential']
const DC_LABEL: Record<string, string> = { public: '공개', internal: '내부', confidential: '기밀' }
const COMMERCIAL = ['openai', 'anthropic', 'gemini']

/** 자체 호스팅·외부 egress 없음 판정 — 사설 주소·클러스터 내부 이름만 '로컬' (FR-L02·MSG-011) */
function localVerdict(kind: string, baseUrl: string): [boolean, string] {
  if (COMMERCIAL.includes(kind)) return [false, '상용 공급자 — 외부 egress']
  if (kind === 'mock') return [true, '내장 Mock — 외부 호출 없음']
  let host = ''
  try {
    host = new URL(baseUrl).hostname
  } catch {
    return [false, 'base_url 형식 오류']
  }
  const priv = !host.includes('.') || /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host) || /\.(svc|local|internal|lan)$/.test(host)
  return priv ? [true, `자체 호스팅 · 외부 egress 없음 (${host})`] : [false, `외부 주소 ${host} — egress 가능`]
}

// ───────── I02 공급자 어댑터 설정·연결 시험 ─────────
const KINDS: [string, string][] = [['openai', 'OpenAI'], ['anthropic', 'Anthropic'], ['gemini', 'Google Gemini'], ['vllm', 'vLLM'], ['ollama', 'Ollama'], ['litellm', 'LiteLLM'], ['openai_compat', 'OpenAI 호환'], ['mock', '내장 Mock']]
type PForm = { id: string; name: string; kind: string; base_url: string; region: string; secret_ref: string; api_key: string; zdr: boolean; data_classes: string[]; enabled: boolean }
const NEW_PROVIDER: PForm = { id: '', name: '', kind: 'openai', base_url: '', region: '', secret_ref: '', api_key: '', zdr: true, data_classes: ['public'], enabled: true }
// zdr·data_classes 는 아직 공급자 1급 필드가 아니어서 options 에 보관한다 (백엔드가 필드를 주면 그것을 우선)
const zdrOf = (p: Json) => !!(p.zdr ?? p.options?.zdr)
const toForm = (p: Json): PForm => ({
  id: p.id, name: p.name ?? '', kind: p.kind, base_url: p.base_url ?? '', region: p.region ?? '', secret_ref: p.secret_ref ?? '', api_key: '',
  zdr: zdrOf(p), data_classes: p.data_classes ?? p.options?.data_classes ?? (COMMERCIAL.includes(p.kind) ? ['public'] : DCS), enabled: !!p.enabled,
})

export function Providers() {
  const [sp, setSp] = useSearchParams()
  const sel = sp.get('id')
  const list = useGet('/api/v1/providers')
  const items: Json[] = list.data?.items ?? []
  const cur = sel ? items.find((p) => p.id === sel) : undefined
  const pick = (id?: string) => setSp(id ? { id } : {})
  return (
    <>
      <PageHeader title="공급자 어댑터 설정" crumbs={['모델', '공급자 어댑터']}
        desc="공급자 어댑터의 엔드포인트·리전·데이터 처리 옵션을 정하고 연결을 시험합니다. API 키는 게이트웨이 시크릿 저장소의 참조로만 연결합니다 (FR-L01·G02, MSG-018)."
        actions={<>
          <Link to="/console/models" className="text-sm text-primary hover:underline">← 공급자·모델</Link>
          <Gate perm="models:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => pick()}>+ 공급자 추가</Button>}</Gate>
        </>} />
      {list.error ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Card title="공급자 목록" bodyClass="p-0">
          <table className="w-full text-sm" aria-label="공급자 목록">
            <thead className="text-left text-xs text-fg-muted"><tr><th className="px-3 py-1.5">공급자</th><th>종류</th><th>상태</th><th>데이터 처리</th></tr></thead>
            <tbody>{items.map((p) => (
              <tr key={p.id} className={cx('border-t border-line', p.id === sel && 'bg-primary-soft')}>
                <td className="px-3 py-1.5">
                  <button type="button" className="mono text-left text-primary hover:underline" aria-current={p.id === sel || undefined} onClick={() => pick(p.id)}>{p.id}</button>
                  <div className="text-[11px] text-fg-muted">{p.name}</div>
                </td>
                <td><Badge tone="model">{p.kind}</Badge></td>
                <td className="space-x-1">{p.enabled ? <StatusBadge status={p.status} /> : <Badge title="키 없음·비활성 공급자는 라우팅 후보에서 제외">비활성</Badge>}{!p.secret_ref && COMMERCIAL.includes(p.kind) && <Badge tone="warn">키 없음</Badge>}</td>
                <td>{zdrOf(p) ? <Badge tone="ok">ZDR</Badge> : <span className="text-xs text-fg-muted">—</span>}</td>
              </tr>
            ))}</tbody>
          </table>
          {list.isLoading && <div className="p-3"><Loading /></div>}
        </Card>
        {list.isLoading ? null : sel && !cur ? <Card><Empty>공급자 "{sel}" 를 찾을 수 없습니다.</Empty></Card>
          : <ProviderForm key={cur?.id ?? 'new'} cur={cur} onChanged={async (id) => { await list.refetch(); if (id) pick(id) }} />}
      </div>
    </>
  )
}

function ProviderForm({ cur, onChanged }: { cur?: Json; onChanged: (id?: string) => Promise<void> }) {
  const toast = useToast()
  const [f, setF] = useState<PForm>(() => (cur ? toForm(cur) : NEW_PROVIDER))
  const [newKey, setNewKey] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const secretList = useGet('/api/v1/gateway/secrets', undefined, { retry: false })
  const set = (p: Partial<PForm>) => setF((x) => ({ ...x, ...p }))
  const commercial = COMMERCIAL.includes(f.kind)
  const eff = commercial ? f.data_classes.filter((d) => d === 'public') : f.data_classes
  const idErr = !cur && f.id && !/^[a-z0-9][a-z0-9-]{1,62}$/.test(f.id) ? '소문자·숫자·- 2~63자 (MSG-004)' : undefined
  const refs = [...new Set([...(cur?.secret_ref ? [cur.secret_ref] : []), ...(secretList.data?.items ?? []).filter((s: Json) => s.kind === 'api_key').map((s: Json) => s.name as string)])]

  const save = async () => {
    setBusy(true)
    setErr(null)
    try {
      const options = { ...(cur?.options ?? {}), zdr: f.zdr, data_classes: eff }
      const wantRef = !f.api_key && f.secret_ref && f.secret_ref !== (cur?.secret_ref ?? '') ? f.secret_ref : undefined
      const common = { name: f.name || f.id, base_url: f.base_url || null, region: f.region || null, options, enabled: f.enabled,
        ...(f.api_key ? { api_key: f.api_key } : {}), ...(wantRef ? { secret_ref: wantRef } : {}) }
      const r = cur ? await send<Json>('PATCH', `/api/v1/providers/${encodeURIComponent(cur.id)}`, common)
        : await send<Json>('POST', '/api/v1/providers', { id: f.id, kind: f.kind, ...common })
      if (wantRef && r.secret_ref !== wantRef) toast('error', `시크릿 참조 "${wantRef}" 연결은 서버에 반영되지 않았습니다 — '새 키 입력' 으로 등록하세요.`)
      else toast('ok', f.api_key ? `${r.id} 저장 — 입력한 키는 시크릿 저장소(${r.secret_ref})로 이동했고 다시 표시되지 않습니다 (MSG-018)` : `${r.id} 저장됨`)
      set({ api_key: '', secret_ref: r.secret_ref ?? '' })
      setNewKey(false)
      await onChanged(r.id)
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card title={cur ? `공급자 설정 — ${cur.name || cur.id}` : '새 공급자'}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="공급자 ID" error={idErr}><Input className="mono" value={f.id} disabled={!!cur} onChange={(e) => set({ id: e.target.value })} placeholder="openai-eu" /></Field>
          <Field label="이름"><Input value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label="어댑터 종류" hint={cur ? '종류는 생성할 때만 정합니다' : undefined}>
            <Select value={f.kind} disabled={!!cur} onChange={(e) => set({ kind: e.target.value, data_classes: COMMERCIAL.includes(e.target.value) ? ['public'] : DCS })}>
              {KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </Select>
          </Field>
          <Field label="base URL (엔드포인트)" hint="비우면 공급자 기본 엔드포인트 · 사내 프록시는 사용자 지정 URL">
            <Input className="mono" value={f.base_url} onChange={(e) => set({ base_url: e.target.value })} placeholder={commercial ? '공급자 기본값' : 'http://vllm:8000/v1'} />
          </Field>
          <Field label="리전 (데이터 거주)" hint="비우면 글로벌 기본 · 예: eu (EU 데이터 거주)">
            <Input className="mono" list="provider-regions" value={f.region} onChange={(e) => set({ region: e.target.value })} placeholder="글로벌 기본" />
          </Field>
          <datalist id="provider-regions"><option value="eu" /><option value="us" /><option value="kr" /><option value="ap-northeast-2" /></datalist>
          <Field label="API 키 (게이트웨이 시크릿 참조)" hint={newKey ? undefined : '평문 키는 표시·저장하지 않고 secret:// 참조만 연결합니다 (MSG-018)'}>
            <Select className="mono" value={f.secret_ref} disabled={newKey} onChange={(e) => set({ secret_ref: e.target.value })}>
              <option value="">{commercial ? '— 키 없음' : '없음 (자체 호스팅)'}</option>
              {refs.map((r) => <option key={r} value={r}>secret://{r}</option>)}
            </Select>
          </Field>
          <div className="col-span-2 flex items-end gap-2">
            {newKey && <div className="flex-1"><Field label="새 API 키" hint="저장하면 즉시 시크릿 저장소로 이동하고 다시 표시되지 않습니다 (MSG-018)"><Input type="password" autoComplete="off" value={f.api_key} onChange={(e) => set({ api_key: e.target.value })} /></Field></div>}
            <Button type="button" onClick={() => { setNewKey((v) => !v); set({ api_key: '' }) }}>{newKey ? '새 키 입력 취소' : '새 키 입력'}</Button>
          </div>
          <fieldset className="col-span-2">
            <legend className="mb-1 text-xs font-medium text-fg-muted">데이터 처리 옵션</legend>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.zdr} onChange={(e) => set({ zdr: e.target.checked })} />보존 최소화 (ZDR 요청 · 학습 사용 거부)</label>
          </fieldset>
          <fieldset className="col-span-2">
            <legend className="mb-1 text-xs font-medium text-fg-muted">허용 데이터 분류</legend>
            <div className="flex flex-wrap gap-4 text-sm">
              {DCS.map((d) => {
                const blocked = commercial && d !== 'public'
                return (
                  <label key={d} className={cx('flex items-center gap-2', blocked && 'text-fg-muted')} title={blocked ? '정책상 불가 — 내부·기밀 데이터는 로컬 모델만 (MSG-011)' : undefined}>
                    <input type="checkbox" disabled={blocked} checked={eff.includes(d)} onChange={(e) => set({ data_classes: e.target.checked ? [...f.data_classes, d] : f.data_classes.filter((x) => x !== d) })} />
                    {DC_LABEL[d]} ({d}){blocked ? ' — 정책상 불가' : ''}
                  </label>
                )
              })}
            </div>
            {commercial && <p className="mt-1 text-xs text-fg-muted">근거: 모델 정책 "내부·기밀 데이터는 로컬 모델만" — 내부 데이터 요청은 로컬 모델로 재라우팅됩니다 (MSG-011).</p>}
          </fieldset>
          <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={f.enabled} onChange={(e) => set({ enabled: e.target.checked })} />사용 (라우팅 후보에 포함)</label>
        </div>
        {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => { setF(cur ? toForm(cur) : NEW_PROVIDER); setNewKey(false); setErr(null) }}>취소</Button>
          <Gate perm="models:write">{(ok, why) => <Button variant="primary" loading={busy} disabled={!ok || (!cur && (!f.id || !!idErr)) || (newKey && !f.api_key)} title={why} onClick={save}>저장</Button>}</Gate>
        </div>
      </Card>
      {cur ? <ProviderTest p={cur} zdr={zdrOf(cur)} onTested={() => onChanged()} /> : <Card title="연결 시험"><p className="text-sm text-fg-muted">공급자를 저장한 뒤 연결을 시험할 수 있습니다.</p></Card>}
    </div>
  )
}

function ProviderTest({ p, zdr, onTested }: { p: Json; zdr: boolean; onTested: () => void }) {
  const models = useGet('/api/v1/models')
  const own: Json[] = (models.data?.items ?? []).filter((m: Json) => m.provider_id === p.id)
  const [model, setModel] = useState('')
  const [res, setRes] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      setRes(await send<Json>('POST', `/api/v1/providers/${encodeURIComponent(p.id)}/test`, model ? { model } : undefined))
      onTested()
    } catch (e: any) {
      setRes({ ok: false, error: e.message, status: e.status })
    } finally {
      setBusy(false)
    }
  }
  const needsKey = COMMERCIAL.includes(p.kind)
  const checks: [boolean | null, ReactNode][] = [
    [p.secret_ref ? (res ? res.ok : null) : needsKey ? false : null,
      p.secret_ref ? <>시크릿 참조 <span className="mono">secret://{p.secret_ref}</span> {res?.ok ? '해석 ✓' : ''} (값 비노출)</> : needsKey ? '키 없음 — 라우팅 후보에서 제외 (H02)' : '키 불필요 (자체 호스팅)'],
    [res ? !!res.ok : null, !res ? 'chat 1회 (짧은 프롬프트, max_tokens 8)' : res.ok ? `chat 1회 ${fmtMs(res.latency_ms)} · ${res.model}${res.reply ? ` — "${String(res.reply).slice(0, 40)}"` : ''}` : `chat 실패 — ${res.error}${res.status ? ` (HTTP ${res.status})` : ''}`],
    [null, <>엔드포인트 <span className="mono">{p.base_url || '기본값'}</span> · 리전 {p.region || '글로벌 기본'}</>],
    [null, `데이터 처리 — ZDR ${zdr ? '요청' : '미요청'}`],
  ]
  return (
    <Card title="연결 시험">
      <ul className="space-y-1.5 text-sm" aria-label="연결 시험 결과">
        {checks.map(([ok, text], i) => (
          <li key={i} className="flex items-start gap-2">
            <span aria-hidden className={cx('mt-1.5 size-2 shrink-0 rounded-full', ok === true ? 'bg-ok' : ok === false ? 'bg-danger' : 'bg-muted/40')} />
            <span className={cx(ok === false && 'text-danger')}>{text}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-fg-muted">호출 경로: 게이트웨이 → 공급자 (앱·브라우저의 직접 호출 경로 없음, MSG-005) · 마지막 시험 {fmtTime(p.last_tested_at, true)}</p>
      <div className="mt-3 flex items-end justify-end gap-2">
        <div className="w-56"><Field label="시험 모델"><Select value={model} onChange={(e) => setModel(e.target.value)}><option value="">자동 (등록 모델 중 첫 번째)</option>{own.map((m) => <option key={m.id} value={m.upstream_model}>{m.id} → {m.upstream_model}</option>)}</Select></Field></div>
        <Gate perm="models:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} loading={busy} onClick={run}>연결 시험</Button>}</Gate>
      </div>
    </Card>
  )
}

// ───────── I03 도메인 특화 모델 등록 ─────────
type Rule = Json & { id: string; match_model: string; targets: Json[]; fallbacks: string[] }

/** 저장 전 정책 반영 미리보기 — 모델 카탈로그·라우팅 별칭·데이터 분류별 모델 정책 판정 */
function policyDiff(pv: Json, m: Json, eff: string[], alias: string, fallback: string, rule: Rule | undefined, sim: Record<string, Json> | null, local: boolean) {
  const L = ['# 모델 카탈로그 (POST /api/v1/models)', `+ ${m.id} → ${pv.id}/${m.upstream_model}  category=${m.category}${local ? '  [로컬]' : ''}`, `+   data_classes = [${eff.join(', ')}]`, '', '# 라우팅 별칭 (gateway/routing/rules)']
  if (!alias) L.push(`  변경 없음 — 앱은 논리 모델 ID "${m.id}" 로 직접 호출`)
  else if (rule) {
    const tg = rule.targets.map((t) => t.model)
    L.push(tg.includes(m.id) ? `  "${alias}" targets 에 이미 포함` : `~ "${alias}" targets: [${tg.join(', ')}] → [${[...tg, m.id].join(', ')}]`)
    if (fallback && !rule.fallbacks.includes(fallback)) L.push(`~ "${alias}" fallbacks: [${rule.fallbacks.join(', ')}] → [${[...rule.fallbacks, fallback].join(', ')}]`)
  } else L.push(`+ "${alias}" (신규 규칙 · priority) targets: [${m.id}]${fallback ? `  fallbacks: [${fallback}]` : ''}`)
  L.push('', '# 데이터 분류별 모델 정책 판정 (policies/simulate · category 기준)')
  for (const dc of DCS) {
    const d = sim?.[dc]
    const verdict = !d ? '…' : d.allow ? `allow · ${d.policy_name ?? d.reason}` : `deny · ${d.policy_name ?? d.reason}${d.reroute ? ` → 재라우팅 ${d.reroute}` : ''}`
    L.push(`${eff.includes(dc) ? '+' : ' '} ${dc.padEnd(13)}${eff.includes(dc) ? verdict : '처리 안 함'}`)
  }
  return L.join('\n')
}

export function ModelWizard() {
  const nav = useNavigate()
  const [step, setStep] = useState(0)
  const [pv, setPv] = useState({ id: 'vllm-internal', name: '사내 vLLM', kind: 'vllm', base_url: 'http://vllm:8000/v1', api_key: '', ca: '', cert: '' })
  const [m, setM] = useState({ id: 'domain-7b-ft', upstream_model: 'domain-7b', display_name: '도메인 7B (사내)', category: 'domain', context_window: 32768, quality: 0.7 })
  const [dcs, setDcs] = useState<string[]>(['public', 'internal', 'confidential'])
  const [alias, setAlias] = useState('')
  const [fallback, setFallback] = useState('')
  const [result, setResult] = useState<Json | null>(null)
  const [linked, setLinked] = useState('')
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const rules = useGet('/api/v1/gateway/routing/rules', undefined, { retry: false })
  const models = useGet('/api/v1/models')
  const [sim, setSim] = useState<Record<string, Json> | null>(null)
  const [simErr, setSimErr] = useState<unknown>(null)
  const [local, localWhy] = localVerdict(pv.kind, pv.base_url)
  const eff = local ? dcs : dcs.filter((d) => d === 'public')
  const ruleItems: Rule[] = rules.data?.items ?? []
  const aliasName = alias.trim()
  const rule = ruleItems.find((r) => r.match_model === aliasName)
  const aliasErr = aliasName && aliasName === m.id ? '별칭은 논리 모델 ID 와 달라야 합니다' : undefined
  const fallbacks: Json[] = (models.data?.items ?? []).filter((x: Json) => x.enabled && ['local', 'domain'].includes(x.category) && !(x.capabilities ?? []).includes('embeddings'))

  useEffect(() => { // 데이터 분류 단계에서 등급별 모델 정책 판정을 미리 본다 (부작용 없는 시뮬레이션)
    if (step !== 2) return
    let live = true
    setSim(null)
    setSimErr(null)
    Promise.all(DCS.map((dc) => send<Json>('POST', '/api/v1/gateway/policies/simulate', { kind: 'model', target: m.id, data_class: dc, attrs: { category: m.category } }).then((r) => [dc, r.decision] as const)))
      .then((rs) => live && setSim(Object.fromEntries(rs)), (e) => live && setSimErr(e))
    return () => { live = false }
  }, [step, m.id, m.category])

  const linkAlias = async () => {
    const fb = fallback ? [fallback] : []
    if (rule) {
      const targets = rule.targets.some((t) => t.model === m.id) ? rule.targets : [...rule.targets, { model: m.id }]
      await send('PUT', `/api/v1/gateway/routing/rules/${rule.id}`, { ...rule, targets, fallbacks: [...new Set([...(rule.fallbacks ?? []), ...fb])] })
      return `별칭 "${aliasName}" 규칙에 추가됨`
    }
    await send('POST', '/api/v1/gateway/routing/rules', { name: `${aliasName} → ${m.id}`, match_model: aliasName, strategy: 'priority', targets: [{ model: m.id }], fallbacks: fb })
    return `별칭 "${aliasName}" 규칙 생성됨`
  }
  const finish = async () => {
    setBusy(true)
    setErr(null)
    try {
      const tls = pv.ca || pv.cert ? { tls: { ...(pv.ca ? { ca_secret: pv.ca } : {}), ...(pv.cert ? { client_cert_secret: pv.cert } : {}) } } : {}
      await send('POST', '/api/v1/providers', { id: pv.id, name: pv.name, kind: pv.kind, base_url: pv.base_url, api_key: pv.api_key || undefined, options: tls })
      await send('POST', '/api/v1/models', { ...m, provider_id: pv.id, data_classes: eff, price_in: 0, price_out: 0, context_window: Number(m.context_window), quality: Number(m.quality) })
      setLinked(aliasName ? await linkAlias() : '')
      const test = await send<Json>('POST', `/api/v1/providers/${pv.id}/test`, { model: m.upstream_model })
      setResult(test)
      setStep(3)
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  const denied = eff.filter((d) => sim?.[d] && !sim[d].allow)
  return (
    <>
      <PageHeader title="도메인 특화 모델 등록" crumbs={['모델', '등록']} desc="vLLM·Ollama 등 내부 모델을 등록하면 internal·confidential 데이터는 이 모델로만 라우팅됩니다 (FR-L02·G03)." />
      <Card>
        <Stepper steps={['공급자', '모델', '데이터 등급', '연결 시험']} current={step} />
        {step === 0 && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="공급자 ID"><Input className="mono" value={pv.id} onChange={(e) => setPv({ ...pv, id: e.target.value })} /></Field>
            <Field label="이름"><Input value={pv.name} onChange={(e) => setPv({ ...pv, name: e.target.value })} /></Field>
            <Field label="종류"><Select value={pv.kind} onChange={(e) => setPv({ ...pv, kind: e.target.value })}><option value="vllm">vLLM</option><option value="ollama">Ollama</option><option value="openai_compat">OpenAI 호환</option><option value="litellm">LiteLLM</option></Select></Field>
            <Field label="base_url"><Input className="mono" value={pv.base_url} onChange={(e) => setPv({ ...pv, base_url: e.target.value })} /></Field>
            <Field label="API 키 (선택)" hint="시크릿 저장소에만 보관"><Input type="password" autoComplete="off" value={pv.api_key} onChange={(e) => setPv({ ...pv, api_key: e.target.value })} /></Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="mTLS CA 시크릿 (선택)"><Input className="mono" value={pv.ca} onChange={(e) => setPv({ ...pv, ca: e.target.value })} placeholder="tls/vllm-ca" /></Field>
              <Field label="클라이언트 인증서 시크릿"><Input className="mono" value={pv.cert} onChange={(e) => setPv({ ...pv, cert: e.target.value })} placeholder="tls/gateway-client" /></Field>
            </div>
          </div>
        )}
        {step === 1 && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="논리 모델 ID (앱이 쓰는 이름)"><Input className="mono" value={m.id} onChange={(e) => setM({ ...m, id: e.target.value })} /></Field>
            <Field label="업스트림 모델명"><Input className="mono" value={m.upstream_model} onChange={(e) => setM({ ...m, upstream_model: e.target.value })} /></Field>
            <Field label="표시 이름"><Input value={m.display_name} onChange={(e) => setM({ ...m, display_name: e.target.value })} /></Field>
            <Field label="분류"><Select value={m.category} onChange={(e) => setM({ ...m, category: e.target.value })}><option value="domain">domain</option><option value="local">local</option></Select></Field>
            <Field label="컨텍스트(토큰)"><Input type="number" value={m.context_window} onChange={(e) => setM({ ...m, context_window: Number(e.target.value) })} /></Field>
            <Field label="품질 점수(0~1)" hint="라우팅 전략 quality 에서 높은 순으로 고른다 (FR-G01)"><Input type="number" min={0} max={1} step={0.05} value={m.quality} onChange={(e) => setM({ ...m, quality: Number(e.target.value) })} /></Field>
          </div>
        )}
        {step === 2 && (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-3 text-sm">
              <p className="text-fg-muted">이 모델이 처리해도 되는 데이터 등급 — 내부 데이터는 등록된 로컬·도메인 모델로만 보냅니다 (MSG-011).</p>
              <div className="flex flex-wrap items-center gap-2">
                {local ? <Badge tone="ok">로컬 모델 판정 ✓</Badge> : <Badge tone="warn">로컬 아님</Badge>}
                <span className="text-xs text-fg-muted">{localWhy}{local ? ' → 내부 데이터 라우팅 대상' : ' → 내부·기밀 비활성'}</span>
              </div>
              {DCS.map((d) => {
                const blocked = !local && d !== 'public'
                return (
                  <label key={d} className={cx('flex items-center gap-2', blocked && 'text-fg-muted')} title={blocked ? '외부 egress 가능 — 내부·기밀은 로컬 모델만 (MSG-011)' : undefined}>
                    <input type="checkbox" disabled={blocked} checked={eff.includes(d)} onChange={(e) => setDcs(e.target.checked ? [...dcs, d] : dcs.filter((x) => x !== d))} />{d}
                  </label>
                )
              })}
              <Gate perm="gateway:write">{(ok, why) => (
                <div className="grid grid-cols-2 gap-3">
                  <Field label="별칭 연결 (선택)" error={aliasErr} hint={ok ? '라우팅 규칙의 match_model — 앱은 이 별칭으로 호출' : why}>
                    <Input className="mono" list="wizard-aliases" disabled={!ok} title={why} value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="internal-data" />
                  </Field>
                  <datalist id="wizard-aliases">{ruleItems.filter((r) => !r.match_model.includes('*')).map((r) => <option key={r.id} value={r.match_model} />)}</datalist>
                  <Field label="폴백 (로컬 모델만)" hint="상용 모델은 선택할 수 없습니다">
                    <Select disabled={!ok || !aliasName} title={!aliasName ? '별칭을 먼저 정하세요' : why} value={fallback} onChange={(e) => setFallback(e.target.value)}>
                      <option value="">없음</option>
                      {fallbacks.filter((x) => x.id !== m.id).map((x) => <option key={x.id} value={x.id}>{x.id}</option>)}
                    </Select>
                  </Field>
                </div>
              )}</Gate>
            </div>
            <section aria-label="정책 반영 미리보기" className="space-y-2">
              <div className="text-xs font-medium text-fg-muted">정책 반영 미리보기 — 저장하면 바뀌는 것</div>
              <CodeBlock value={policyDiff(pv, m, eff, aliasName, fallback, rule, sim, local)} maxH="max-h-80" />
              {simErr ? <ErrorBox error={simErr} /> : null}
              {denied.length > 0 && <p role="alert" className="text-xs text-danger">{denied.join('·')} 데이터는 정책상 이 모델로 처리되지 않습니다 — {denied.map((d) => sim?.[d]?.reroute ? `${d}→${sim[d].reroute}` : `${d} 거부`).join(', ')}</p>}
              <p className="text-xs text-fg-muted">내부·기밀 재라우팅 기본 대상(data_class_routing)을 이 모델로 바꾸려면 <Link className="text-primary hover:underline" to="/console/gateway/policies">모델 정책</Link> 초안 → 보안 승인(H08) 으로 게시합니다.</p>
            </section>
          </div>
        )}
        {step === 3 && result && (
          <div className="space-y-2 text-sm">
            <p className={result.ok ? 'text-ok' : 'text-danger'}>{result.ok ? `연결 성공 ${fmtMs(result.latency_ms)}` : `연결 실패 — ${result.error}`}</p>
            {linked && <p className="text-xs">{linked} — 앱은 <span className="mono">{aliasName}</span> 로 호출합니다.</p>}
            <p className="text-xs text-fg-muted">{result.ok ? '라우팅 규칙(모델 라우팅)에 추가하면 바로 사용됩니다.' : '공급자는 등록됐습니다 — base_url·키·mTLS 설정을 고친 뒤 공급자 목록에서 다시 시험하세요.'}</p>
            <CodeBlock value={result} maxH="max-h-40" />
          </div>
        )}
        {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
        <div className="mt-4 flex justify-end gap-2">
          {step > 0 && step < 3 && <Button onClick={() => setStep(step - 1)}>이전</Button>}
          {step < 2 && <Button variant="primary" onClick={() => setStep(step + 1)}>다음</Button>}
          {step === 2 && <Gate perm="models:write">{(ok, why) => <Button variant="primary" disabled={!ok || !eff.length || !!aliasErr} title={why} loading={busy} onClick={finish}>등록·연결 시험</Button>}</Gate>}
          {step === 3 && <Button variant="primary" onClick={() => nav('/console/models')}>완료</Button>}
        </div>
      </Card>
    </>
  )
}

// ───────── I05 Access Tokens ─────────
const TTL_MIN = [5, 10, 15, 30, 60] // API 허용 범위 ttl_seconds 300~3600 (SCR-I05)
const lifetime = (t: Json) => {
  const min = Math.round((new Date(t.expires_at).getTime() - new Date(t.created_at).getTime()) / 60000)
  return Number.isFinite(min) ? `${min}분` : '—'
}

export function AccessTokens() {
  const toast = useToast()
  const q = useGet('/api/v1/access-tokens')
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ name: '', subject: 'a2a:observability-analyst', scopes: 'gateway:invoke', ttl_seconds: 900 })
  const [token, setToken] = useState('')
  const issue = async () => {
    try {
      const r = await send<Json>('POST', '/api/v1/access-tokens', { ...f, scopes: f.scopes.split(/[\s,]+/).filter(Boolean), ttl_seconds: Number(f.ttl_seconds) })
      setToken(r.token)
      setOpen(false)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  return (
    <>
      <PageHeader title="Access Tokens 발급·폐기" crumbs={['모델', 'Access Tokens']} desc="원격 에이전트·도구가 게이트웨이를 부를 때 쓰는 단수명·스코프 제한 토큰 (FR-L03·R06)."
        actions={<Gate perm="tokens:issue">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => setOpen(true)}>+ 발급</Button>}</Gate>} />
      {token && <Card title="발급된 토큰 — 지금만 표시 (MSG-018)" className="mb-4"><CodeBlock value={token} maxH="max-h-24" /></Card>}
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={q.data?.items ?? []} empty="발급한 토큰이 없습니다." columns={[
        col.accessor('name', { header: '이름' }), col.accessor('subject', { header: '주체', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
        col.accessor('audience', { header: 'aud', cell: (c) => <span className="mono text-[11px]">{c.getValue()}</span> }),
        col.accessor('scopes', { header: '스코프', enableSorting: false, cell: (c) => <span className="mono text-xs">{(c.getValue() ?? []).join(' ')}</span> }),
        col.display({ id: 'ttl', header: '수명', cell: ({ row }) => <span className="text-xs tabular-nums">{lifetime(row.original)}</span> }),
        col.accessor('expires_at', { header: '만료', cell: (c) => fmtTime(c.getValue(), true) }),
        col.accessor('revoked_at', { header: '상태', cell: ({ row }) => <StatusBadge status={row.original.revoked_at ? 'revoked' : new Date(row.original.expires_at) < new Date() ? 'expired' : 'active'} /> }),
        col.display({ id: 'r', header: '', cell: ({ row }) => !row.original.revoked_at && <Gate perm="tokens:issue">{(ok, why) => <Button size="sm" variant="danger" disabled={!ok} title={why} onClick={() => confirm(`Access Token "${row.original.name}" 을 폐기할까요? 이 토큰을 쓰는 주체는 즉시 401 을 받습니다 — 게이트웨이 거부목록에 바로 반영되고 감사에 기록됩니다 (MSG-002).`) && send('POST', `/api/v1/access-tokens/${row.original.id}/revoke`).then(() => { toast('ok', `"${row.original.name}" 폐기됨`); q.refetch() }).catch((e) => toast('error', e.message))}>폐기</Button>}</Gate> }),
      ]} />
      <Modal open={open} onClose={() => setOpen(false)} title="Access Token 발급" footer={<><Button onClick={() => setOpen(false)}>취소</Button><Button variant="primary" disabled={!f.name} onClick={issue}>발급</Button></>}>
        <div className="space-y-3">
          <Field label="이름"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="주체" hint="예: a2a:observability-analyst · mcp:internal-api"><Input className="mono" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} /></Field>
          <Field label="스코프"><Input className="mono" value={f.scopes} onChange={(e) => setF({ ...f, scopes: e.target.value })} /></Field>
          <Field label="수명 (5~60분)" hint="짧은 수명만 발급합니다 — 기본 15분, 최대 60분 (FR-L03)">
            <Select value={f.ttl_seconds} onChange={(e) => setF({ ...f, ttl_seconds: Number(e.target.value) })}>
              {TTL_MIN.map((n) => <option key={n} value={n * 60}>{n}분</option>)}
            </Select>
          </Field>
        </div>
      </Modal>
    </>
  )
}
