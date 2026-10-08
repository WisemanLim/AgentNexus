// SCR-C08 MCP 리소스·프롬프트·샘플링 승인 — ① `/` 메뉴 ②③④ 리소스·프롬프트 삽입 · ⑤~⑧ 샘플링 동의 카드(편집·모델 라우팅·검토·허용/거부)
// + elicitation(추가 입력) 카드. 무응답 60초 자동 거부는 게이트웨이가 집행하고 카드는 남은 시간을 보여 준다 —
// 「시간 연장」으로 60초씩 최대 10회 늘리고, 만료 20초 전에 알린다 (WCAG 2.2.1, D14-③).
import { useEffect, useMemo, useState } from 'react'
import { ExtendTime } from '@/components/ExtendTime'
import { Badge, Button, Card, ErrorBox, Input, Kv, Loading, Select, Tabs, Textarea, TypeBadge, cx, useToast } from '@/components/ui'
import type { Json } from '@/lib/api'
import { send, useGet } from '@/lib/query'
import type { Consent } from './runState'

function useCountdown(until?: string) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  if (!until) return null
  return Math.max(0, Math.round((new Date(until).getTime() - now) / 1000))
}

const textOf = (c: any): string => (Array.isArray(c) ? c.map(textOf).join('\n') : c?.type === 'text' ? c.text : c?.type ? `[${c.type}]` : '')

export function ConsentCard({ c, onDone }: { c: Consent; onDone: (id: string, status: string) => void }) {
  const toast = useToast()
  const [until, setUntil] = useState(c.expiresAt)
  const [ext, setExt] = useState(c.extensions ?? 0)
  useEffect(() => setUntil(c.expiresAt), [c.expiresAt])
  useEffect(() => setExt((e) => Math.max(e, c.extensions ?? 0)), [c.extensions])
  const left = useCountdown(until)
  const original = useMemo(() => (c.params.messages ?? []).filter((m: Json) => m.role !== 'assistant').map((m: Json) => textOf(m.content)).join('\n\n'), [c.params])
  const [prompt, setPrompt] = useState(original)
  const [review, setReview] = useState(true)
  const [draft, setDraft] = useState(c.draft ?? '')
  const [busy, setBusy] = useState('')
  useEffect(() => setDraft(c.draft ?? ''), [c.draft])
  const decide = async (body: Json, label: string) => {
    setBusy(label)
    try {
      const r = await send<Json>('POST', `/api/v1/approvals/${c.id}/consent`, body)
      onDone(c.id, r.status)
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const expired = left === 0
  const timer = left != null && <>
    <span className={cx('text-xs', left < 15 ? 'text-danger' : 'text-fg-muted')}>⏱ {left}초 남음 — 무응답 시 자동 거부·감사 기록</span>
    <ExtendTime id={c.id} extensions={ext} max={c.maxExtensions ?? 10} step={c.extendStep ?? 60} left={left}
      onExtended={(r) => { setUntil(r.expires_at); setExt(r.extensions ?? ext + 1) }} />
  </>

  if (c.kind === 'elicitation') return <ElicitationCard c={c} timer={timer} busy={busy} expired={expired} decide={decide} />
  const routing = c.routing ?? {}
  const internal = ['internal', 'confidential'].includes(routing.data_class)
  return (
    <Card className="border-warn/60" title={<span className="flex items-center gap-2">{c.status === 'review' ? '샘플링 응답 검토' : '샘플링 요청 — 동의 필요'}<TypeBadge type="mcp" /><span className="mono text-xs">mcp/{c.server}</span></span>}
      actions={<Badge tone="warn">{c.status === 'review' ? '검토 대기' : '승인 대기'}</Badge>}>
      {c.status === 'review' ? (
        <div className="space-y-3 text-sm">
          <p className="text-fg-muted">게이트웨이가 생성한 응답입니다. 서버로 보내기 전에 확인·수정하세요.</p>
          <Kv rows={[['모델', <span className="mono">{c.model}</span>]]} />
          <Textarea aria-label="샘플링 응답 초안" className="h-28" value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="flex items-center gap-2">{timer}<span className="flex-1" />
            <Button onClick={() => decide({ decision: 'deny' }, 'deny')} loading={busy === 'deny'} disabled={expired}>거부</Button>
            <Button variant="primary" onClick={() => decide({ decision: 'send', text: draft !== c.draft ? draft : undefined }, 'send')} loading={busy === 'send'} disabled={expired}>서버로 보내기</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-3 text-sm">
          <p className="text-[#8a5a00] dark:text-warn">mcp/{c.server} 가 모델 호출(샘플링)을 요청합니다. 프롬프트를 확인하고 허용하세요. (MSG-016)</p>
          <Kv rows={[['요청 서버', <span className="mono">mcp/{c.server}</span>], ['목적', c.params.systemPrompt || '—'], ['포함 컨텍스트', c.params.includeContext || 'none'],
            ['maxTokens', c.params.maxTokens ?? '—'], ['서버 모델 선호', (routing.hints ?? []).join(', ') || '—']]} />
          <label className="block text-xs font-medium text-fg-muted">프롬프트 (편집 가능)
            <Textarea aria-label="샘플링 프롬프트" className="mono mt-1 h-32 text-xs" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </label>
          <div className={cx('rounded-md border px-3 py-2 text-xs', internal ? 'border-model/40 bg-model/5' : 'border-line')}>
            <strong>모델: 게이트웨이 라우팅</strong> — {routing.error ? <span className="text-danger">{routing.error}</span> : <span className="mono">{routing.model}</span>}
            {internal && <div>내부 데이터({routing.data_class}) → 로컬 모델로만 처리합니다 (MSG-011). 모델은 서버가 아니라 게이트웨이가 정합니다.</div>}
          </div>
          <label className="flex items-center gap-2"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} />응답을 서버로 보내기 전에 검토</label>
          <div className="flex flex-wrap items-center gap-2">{timer}<span className="flex-1" />
            <Button onClick={() => decide({ decision: 'deny' }, 'deny')} loading={busy === 'deny'} disabled={expired}>거부</Button>
            <Button disabled={expired || prompt === original} loading={busy === 'edit'} onClick={() => decide({ decision: 'allow', review, messages: [{ role: 'user', text: prompt }] }, 'edit')}>편집 후 허용</Button>
            <Button variant="primary" disabled={expired} loading={busy === 'allow'} onClick={() => decide({ decision: 'allow', review }, 'allow')}>허용</Button>
          </div>
        </div>
      )}
    </Card>
  )
}

function ElicitationCard({ c, timer, busy, expired, decide }: { c: Consent; timer: React.ReactNode; busy: string; expired: boolean; decide: (b: Json, l: string) => void }) {
  const schema = c.params.requestedSchema ?? { properties: {} }
  const props: [string, Json][] = Object.entries(schema.properties ?? {})
  const required: string[] = schema.required ?? []
  const [values, setValues] = useState<Json>(() => Object.fromEntries(props.map(([k, p]) => [k, p.default ?? (p.type === 'boolean' ? false : p.enum ? p.enum[0] : '')])))
  const content = Object.fromEntries(props.map(([k, p]) => [k, p.type === 'integer' ? parseInt(values[k], 10) : p.type === 'number' ? Number(values[k]) : values[k]]))
  const missing = required.filter((k) => values[k] === '' || values[k] == null)
  return (
    <Card className="border-a2a/50" title={<span className="flex items-center gap-2">추가 입력 요청<TypeBadge type="mcp" /><span className="mono text-xs">mcp/{c.server}</span></span>} actions={<Badge tone="warn">입력 대기</Badge>}>
      <div className="space-y-3 text-sm">
        <p>{c.params.message} <span className="text-xs text-fg-muted">(MSG-017)</span></p>
        <div className="grid grid-cols-2 gap-3">
          {props.map(([k, p]) => (
            <label key={k} className="block text-xs font-medium text-fg-muted">{p.title || k}{required.includes(k) && ' *'}
              {p.enum ? (
                <Select aria-label={k} className="mt-1" value={values[k]} onChange={(e) => setValues({ ...values, [k]: e.target.value })}>{p.enum.map((o: string) => <option key={o}>{o}</option>)}</Select>
              ) : p.type === 'boolean' ? (
                <input aria-label={k} type="checkbox" className="mt-2 block" checked={!!values[k]} onChange={(e) => setValues({ ...values, [k]: e.target.checked })} />
              ) : (
                <Input aria-label={k} className="mt-1" type={p.type === 'integer' || p.type === 'number' ? 'number' : 'text'} value={values[k]} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
              )}
              {p.description && <span className="mt-0.5 block font-normal">{p.description}</span>}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">{timer}<span className="flex-1" />
          <Button onClick={() => decide({ decision: 'cancel' }, 'cancel')} loading={busy === 'cancel'} disabled={expired}>취소</Button>
          <Button onClick={() => decide({ decision: 'decline' }, 'decline')} loading={busy === 'decline'} disabled={expired}>거절</Button>
          <Button variant="primary" onClick={() => decide({ decision: 'accept', content }, 'accept')} loading={busy === 'accept'} disabled={expired || missing.length > 0}>보내기</Button>
        </div>
      </div>
    </Card>
  )
}

// ① `/` 메뉴 — 프로파일이 쓰는 MCP 서버의 리소스(컨텍스트 첨부)·프롬프트 템플릿(인자 입력 후 삽입)
export function SlashMenu({ profileId, onClose, onAttach, onInsert }: {
  profileId: string; onClose: () => void; onAttach: (a: { name: string; mime_type: string; text: string }) => void; onInsert: (text: string) => void
}) {
  const toast = useToast()
  const q = useGet('/api/v1/chat/mcp/context', { profile_id: profileId })
  const [tab, setTab] = useState<'resources' | 'prompts'>('resources')
  const [prompt, setPrompt] = useState<Json | null>(null)
  const [args, setArgs] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const attach = async (r: Json) => {
    setBusy(r.uri)
    try {
      const res = await send<Json>('POST', '/api/v1/chat/mcp/resource', { profile_id: profileId, server: r.server, uri: r.uri })
      onAttach({ name: r.uri, mime_type: res.mime_type, text: res.text })
      onClose()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const insert = async () => {
    if (!prompt) return
    setBusy('prompt')
    try {
      const res = await send<Json>('POST', '/api/v1/chat/mcp/prompt', { profile_id: profileId, server: prompt.server, name: prompt.name, arguments: args })
      onInsert(res.text)
      onClose()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const missing = (prompt?.arguments ?? []).filter((a: Json) => a.required && !args[a.name])
  return (
    <div role="dialog" aria-label="MCP 리소스·프롬프트" className="absolute bottom-full left-0 z-30 mb-2 w-[460px] rounded-lg border border-line bg-surface p-3 shadow-xl">
      <div className="mb-1 flex items-center justify-between"><strong className="text-sm text-navy">/ MCP 리소스·프롬프트</strong><button type="button" aria-label="닫기" onClick={onClose} className="text-fg-muted">✕</button></div>
      <Tabs tabs={[{ id: 'resources', label: `리소스 ${q.data?.resources?.length ?? ''}` }, { id: 'prompts', label: `프롬프트 ${q.data?.prompts?.length ?? ''}` }]} value={tab} onChange={setTab} />
      {q.isLoading && <Loading rows={2} />}
      {q.error && <ErrorBox error={q.error} />}
      {tab === 'resources' && (
        <ul className="max-h-64 space-y-1 overflow-auto">
          {(q.data?.resources ?? []).map((r: Json) => (
            <li key={r.server + r.uri}>
              <button type="button" disabled={!!busy} onClick={() => attach(r)} className="flex w-full items-center gap-2 rounded-md border border-line px-2 py-1.5 text-left text-sm hover:bg-surface-2">
                <TypeBadge type="mcp" /><span className="min-w-0 flex-1"><span className="block truncate">{r.name || r.uri}</span><span className="mono block truncate text-[11px] text-fg-muted">{r.server} · {r.uri}</span></span>
                {busy === r.uri ? <span className="text-xs">…</span> : <Badge>{r.mimeType ?? '첨부'}</Badge>}
              </button>
            </li>
          ))}
          {q.data && !q.data.resources?.length && <li className="text-xs text-fg-muted">이 프로파일의 서버가 노출한 리소스가 없습니다.</li>}
        </ul>
      )}
      {tab === 'prompts' && (
        <div className="space-y-2">
          <ul className="max-h-40 space-y-1 overflow-auto">
            {(q.data?.prompts ?? []).map((pr: Json) => (
              <li key={pr.server + pr.name}>
                <button type="button" onClick={() => { setPrompt(pr); setArgs({}) }} className={cx('w-full rounded-md border px-2 py-1.5 text-left text-sm hover:bg-surface-2', prompt?.name === pr.name ? 'border-primary' : 'border-line')}>
                  <span className="mono">{pr.server} › {pr.name}</span><span className="ml-2 text-xs text-fg-muted">{(pr.arguments ?? []).map((a: Json) => a.name).join(', ')}</span>
                  {pr.description && <span className="block text-xs text-fg-muted">{pr.description}</span>}
                </button>
              </li>
            ))}
          </ul>
          {prompt && (
            <div className="rounded-md border border-line p-2">
              <div className="mb-1 text-xs font-medium">{prompt.server} › {prompt.name} 인자</div>
              {(prompt.arguments ?? []).map((a: Json) => (
                <label key={a.name} className="mb-1 block text-xs text-fg-muted">{a.name}{a.required ? ' (필수)' : ''}
                  <Input aria-label={`인자 ${a.name}`} className="mt-0.5" value={args[a.name] ?? ''} onChange={(e) => setArgs({ ...args, [a.name]: e.target.value })} placeholder={a.description} />
                </label>
              ))}
              <Button size="sm" variant="primary" className="mt-1" disabled={missing.length > 0} loading={busy === 'prompt'} onClick={insert}>삽입</Button>
              {missing.length > 0 && <span className="ml-2 text-xs text-danger">필수 입력입니다 (MSG-003)</span>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
