// 공통 컴포넌트 (CMP-01~17) — shadcn 스타일의 얇은 Tailwind 래퍼
import { createContext, useCallback, useContext, useEffect, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type Ref, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { Link } from 'react-router'
import { ApiError } from '@/lib/api'
import { can, useAuth } from '@/lib/auth'
import { shortId } from '@/lib/format'
import { useT } from '@/lib/i18n'

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ')

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; size?: 'sm' | 'md'; loading?: boolean }
export function Button({ variant = 'secondary', size = 'md', loading, className, children, disabled, ...p }: BtnProps) {
  const v = {
    primary: 'bg-primary text-white hover:bg-primary/90 border-transparent',
    secondary: 'bg-surface text-fg border-line hover:bg-surface-2',
    ghost: 'bg-transparent border-transparent text-fg hover:bg-surface-2',
    danger: 'bg-danger text-white border-transparent hover:bg-danger/90',
  }[variant]
  return (
    <button
      {...p}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx('inline-flex items-center justify-center gap-1.5 rounded-md border font-medium whitespace-nowrap transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer',
        size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-8 px-3 text-sm', v, className)}
    >
      {loading && <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
      {children}
    </button>
  )
}

const TONES = {
  primary: 'bg-primary-soft text-primary', mcp: 'bg-mcp/10 text-mcp', a2a: 'bg-a2a/10 text-a2a', model: 'bg-model/10 text-model',
  profile: 'bg-profile/10 text-profile', ok: 'bg-ok/10 text-ok', warn: 'bg-warn/15 text-warn',
  danger: 'bg-danger/10 text-danger', muted: 'bg-muted/15 text-fg-muted',
}
export type Tone = keyof typeof TONES
export function Badge({ tone = 'muted', children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return <span title={title} className={cx('inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap', TONES[tone], className)}>{children}</span>
}

const TYPE: Record<string, [Tone, string]> = {
  mcp_server: ['mcp', 'MCP'], mcp: ['mcp', 'MCP'], a2a_agent: ['a2a', 'A2A'], a2a: ['a2a', 'A2A'], remote: ['a2a', 'A2A'],
  model: ['model', '모델'], agent_profile: ['profile', '프로파일'], profile: ['profile', '프로파일'], tool: ['mcp', '도구'],
  remote_agent: ['a2a', 'A2A'],
}
export const TypeBadge = ({ type }: { type: string }) => <Badge tone={TYPE[type]?.[0] ?? 'muted'}>{TYPE[type]?.[1] ?? type}</Badge>

const STATUS: Record<string, [Tone, string]> = {
  ok: ['ok', '정상'], healthy: ['ok', '정상'], active: ['ok', '활성'], approved: ['ok', '승인됨'], published: ['ok', '게시됨'],
  succeeded: ['ok', '완료'], completed: ['ok', '완료'], allow: ['ok', '허용'], valid: ['ok', '유효'], enabled: ['ok', '사용'],
  pending: ['warn', '대기'], waiting_approval: ['warn', '승인 대기'], waiting_input: ['warn', '입력 대기'], 'input-required': ['warn', '입력 필요'], degraded: ['warn', '저하'], replanned: ['muted', '재계획됨'], recovering: ['warn', '복구 중'],
  draft: ['muted', '초안'], partial: ['warn', '부분 완료'], running: ['primary', '실행 중'], working: ['primary', '작업 중'],
  submitted: ['primary', '제출됨'], queued: ['muted', '대기열'], unknown: ['muted', '미확인'], skipped: ['muted', '건너뜀'],
  canceled: ['muted', '취소'], expired: ['muted', '만료'], disabled: ['muted', '중지'], deprecated: ['muted', '폐기 예정'],
  failed: ['danger', '실패'], error: ['danger', '오류'], unhealthy: ['danger', '장애'], rejected: ['danger', '거부'],
  deny: ['danger', '거부'], denied: ['danger', '거부'], revoked: ['danger', '폐기'], suspended: ['danger', '중지'],
}
export function StatusBadge({ status }: { status?: string | null }) {
  const s = (status || 'unknown').replace(/^TASK_STATE_/, '').toLowerCase().replace(/_/g, status?.startsWith('TASK_STATE_') ? '-' : '_')
  const [tone, label] = STATUS[s] ?? ['muted', status || '—']
  return <Badge tone={tone}><span className="size-1.5 rounded-full bg-current" aria-hidden />{label}</Badge>
}

export function RiskChip({ risk }: { risk?: string | null }) {
  if (!risk) return null
  const m: Record<string, [Tone, string]> = { low: ['ok', '저위험'], medium: ['warn', '중위험'], high: ['danger', '고위험'] }
  const [tone, label] = m[risk] ?? ['muted', risk]
  return <Badge tone={tone}>{label}</Badge>
}

export function Card({ title, actions, children, className, bodyClass }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={cx('rounded-lg border border-line bg-surface', className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold text-navy">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={cx('p-4', bodyClass)}>{children}</div>
    </section>
  )
}

export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'ok' | 'warn' | 'danger' }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className="text-xs text-fg-muted">{label}</div>
      <div className={cx('mt-1 text-2xl font-semibold tabular-nums', tone === 'ok' && 'text-ok', tone === 'warn' && 'text-warn', tone === 'danger' && 'text-danger')}>{value}</div>
      {sub && <div className="mt-1 text-xs text-fg-muted">{sub}</div>}
    </div>
  )
}

export function PageHeader({ title, crumbs, actions, desc }: { title: ReactNode; crumbs?: string[]; actions?: ReactNode; desc?: ReactNode }) {
  useEffect(() => {  // KWCAG 2.2 제목 제공 — 화면마다 고유한 문서 제목
    const t = typeof title === 'string' ? title : crumbs?.[crumbs.length - 1]
    if (t) document.title = `${t} · AgentNexus`
  }, [title, crumbs])
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        {crumbs && <div className="mb-1 text-xs text-fg-muted">{crumbs.join(' › ')}</div>}
        <h1 className="text-xl font-semibold text-navy">{title}</h1>
        {desc && <p className="mt-1 text-sm text-fg-muted">{desc}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/** 본문 바로가기 (KWCAG 2.2 반복 영역 건너뛰기) — 키보드 첫 Tab 에서 보인다 */
export function SkipLink() {
  return (
    <a href="#main" onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus() }}
      className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-sm text-white focus:not-sr-only focus:fixed focus:left-3 focus:top-3">본문 바로가기</a>
  )
}

// ST-01: 300ms 이하 로딩은 표시하지 않는다
export function Loading({ rows = 3 }: { rows?: number }) {
  const [show, setShow] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setShow(true), 300)
    return () => clearTimeout(t)
  }, [])
  const { t } = useT()
  if (!show) return null
  return (
    <div role="status" aria-label={t('common.loading')} className="space-y-2">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="h-8 animate-pulse rounded bg-surface-2" />)}
    </div>
  )
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useT()
  const e = error as ApiError
  const forbidden = e?.status === 403
  return (
    <div role="alert" className={cx('rounded-md border px-3 py-2 text-sm', forbidden ? 'border-warn/40 bg-warn/10' : 'border-danger/30 bg-danger/5 text-danger')}>
      <div className="flex flex-wrap items-center gap-2">
        <strong>{forbidden ? t('common.noperm') : `오류${e?.status ? ` ${e.status}` : ''}`}</strong>
        <span className="text-fg">{e?.message || String(error)}</span>
        {e?.traceId && <TraceLink id={e.traceId} />}
        {onRetry && !forbidden && <Button size="sm" onClick={onRetry}>{t('common.retry')}</Button>}
      </div>
      {forbidden && <div className="mt-1 text-xs text-fg-muted">필요 권한은 운영자에게 요청하세요 (콘솔 역할·IdP 그룹 매핑).</div>}
    </div>
  )
}

export function Empty({ children, action }: { children?: ReactNode; action?: ReactNode }) {
  const { t } = useT()
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-sm text-fg-muted">
      <div>{children || t('common.empty')}</div>
      {action}
    </div>
  )
}

export function TraceLink({ id }: { id?: string | null }) {
  if (!id) return <span className="text-fg-muted">—</span>
  return <Link to={`/console/observe/traces/${id}`} title={`trace ${id}`} className="mono text-xs text-primary hover:underline">{shortId(id)} ↗</Link>
}

export const Field = ({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string; children: ReactNode }) => (
  <label className="block">
    <span className="mb-1 block text-xs font-medium text-fg-muted">{label}</span>
    {children}
    {hint && !error && <span className="mt-1 block text-xs text-fg-muted">{hint}</span>}
    {error && <span role="alert" className="mt-1 block text-xs text-danger">{error}</span>}
  </label>
)
const inputCls = 'w-full rounded-md border border-line bg-surface px-2.5 text-sm text-fg placeholder:text-fg-muted disabled:opacity-60'
export const Input = ({ className, ...p }: InputHTMLAttributes<HTMLInputElement>) => <input {...p} className={cx(inputCls, 'h-8', className)} />
export const Select = ({ className, children, ...p }: SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className={cx(inputCls, 'h-8', className)}>{children}</select>
export const Textarea = ({ className, ref, ...p }: TextareaHTMLAttributes<HTMLTextAreaElement> & { ref?: Ref<HTMLTextAreaElement> }) => <textarea ref={ref} {...p} className={cx(inputCls, 'py-2', className)} />

export function CodeBlock({ value, className, maxH = 'max-h-96' }: { value: unknown; className?: string; maxH?: string }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  const [copied, setCopied] = useState(false)
  return (
    <div className={cx('group relative', className)}>
      <pre tabIndex={0} className={cx('overflow-auto rounded-md bg-[#111827] p-3 text-[#e5e7eb]', maxH)}>{text}</pre>
      <button type="button" onClick={() => navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200) })}
        className="absolute top-2 right-2 rounded bg-white/10 px-2 py-0.5 text-xs text-white opacity-0 group-hover:opacity-100 focus:opacity-100">{copied ? '복사됨' : '복사'}</button>
    </div>
  )
}

export function Modal({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined} className={cx('max-h-[90vh] w-full overflow-auto rounded-xl bg-surface shadow-xl', wide ? 'max-w-3xl' : 'max-w-lg')}>
        <header className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold text-navy">{title}</h2>
          <button type="button" aria-label="닫기" onClick={onClose} className="rounded p-1 text-fg-muted hover:bg-surface-2">✕</button>
        </header>
        <div className="p-5">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>
  )
}

export function Drawer({ open, onClose, title, children, width = 'w-[420px]' }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string }) {
  useEffect(() => {  // Esc 로 닫기 (모달과 같은 동작)
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/20" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside role="dialog" aria-label={typeof title === 'string' ? title : undefined} className={cx('h-full max-w-full overflow-auto border-l border-line bg-surface shadow-xl', width)}>
        <header className="sticky top-0 flex items-center justify-between border-b border-line bg-surface px-4 py-3">
          <h2 className="text-sm font-semibold text-navy">{title}</h2>
          <button type="button" aria-label="닫기" onClick={onClose} className="rounded p-1 text-fg-muted hover:bg-surface-2">✕</button>
        </header>
        <div className="p-4">{children}</div>
      </aside>
    </div>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="mb-4 flex gap-1 border-b border-line">
      {tabs.map((t) => (
        <button key={t.id} role="tab" type="button" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={cx('-mb-px border-b-2 px-3 py-2 text-sm', value === t.id ? 'border-primary font-semibold text-primary' : 'border-transparent text-fg-muted hover:text-fg')}>{t.label}</button>
      ))}
    </div>
  )
}

// ST-04: 권한 없는 동작은 숨기지 않고 비활성 + 사유 툴팁
export function Gate({ perm, children }: { perm: string; children: (allowed: boolean, reason?: string) => ReactNode }) {
  const { me } = useAuth()
  const ok = can(me, perm)
  return <>{children(ok, ok ? undefined : `권한 필요: ${perm}`)}</>
}

export function Masked({ value }: { value?: string | null }) {
  if (!value) return <span className="text-fg-muted">—</span>
  return <span className="mono text-xs" title="시크릿은 평문으로 표시하지 않습니다">{value.length > 8 ? `${value.slice(0, 4)}••••${value.slice(-2)}` : '••••'}</span>
}

export const Kv = ({ rows }: { rows: [ReactNode, ReactNode][] }) => (
  <dl className="grid grid-cols-[minmax(110px,max-content)_1fr] gap-x-4 gap-y-1.5 text-sm">
    {rows.map(([k, v], i) => (
      <div key={i} className="contents">
        <dt className="text-fg-muted">{k}</dt>
        <dd className="min-w-0 break-words">{v}</dd>
      </div>
    ))}
  </dl>
)

type Toast = { id: number; kind: 'ok' | 'error' | 'info'; text: string }
const ToastCtx = createContext<(kind: Toast['kind'], text: string) => void>(() => {})
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((kind: Toast['kind'], text: string) => {
    const id = Date.now() + Math.random()
    setItems((x) => [...x, { id, kind, text }])
    if (kind !== 'error') setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 3000)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="fixed top-14 right-4 z-[60] flex w-80 flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} role={t.kind === 'error' ? 'alert' : 'status'} className={cx('flex items-start gap-2 rounded-md border px-3 py-2 text-sm shadow-md bg-surface', t.kind === 'ok' && 'border-ok/40', t.kind === 'error' && 'border-danger/40 text-danger', t.kind === 'info' && 'border-line')}>
            <span className="flex-1">{t.text}</span>
            <button type="button" aria-label="닫기" className="text-fg-muted" onClick={() => setItems((x) => x.filter((y) => y.id !== t.id))}>✕</button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
export const useToast = () => useContext(ToastCtx)

// CMP-10 스테퍼 — 위저드 단계 표시 (완료·현재·예정)
export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol className="mb-4 flex gap-2 text-xs" aria-label="진행 단계">
      {steps.map((s, i) => (
        <li key={s} aria-current={i === current ? 'step' : undefined}
          className={cx('flex-1 rounded-full px-3 py-1 text-center', i < current ? 'bg-ok/15 text-ok' : i === current ? 'bg-primary text-white' : 'bg-surface-2 text-fg-muted')}>
          {i < current ? '✓' : i + 1}. {s}
        </li>
      ))}
    </ol>
  )
}

/** 거부·반려 사유 입력 (MSG-003 필수) — 취소하면 null. 사유는 요청자에게 전달되고 감사에 남는다. */
export function askReason(target?: string): string | null {
  for (;;) {
    const v = window.prompt(`${target ? `${target} — ` : ''}거부 사유를 입력하세요 (필수, MSG-003)`)
    if (v === null) return null
    if (v.trim()) return v.trim()
  }
}
