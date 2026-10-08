// WCAG 2.2.1 시간 조절 (D14-③) — 동의·승인 기한을 버튼 한 번으로 기본 시간만큼 늘린다(최대 10회). 만료 20초 전 알린다.
import { useState } from 'react'
import type { Json } from '@/lib/api'
import { send } from '@/lib/query'
import { Button, useToast } from './ui'

export const WARN_S = 20

const stepLabel = (s?: number | null) => (!s ? '' : s >= 60 ? `${Math.round(s / 60)}분` : `${s}초`)

export function ExtendTime({ id, extensions = 0, max = 10, step, left, onExtended }: {
  id: string; extensions?: number; max?: number; step?: number | null; left: number | null; onExtended: (r: Json) => void
}) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  if (!step || left == null) return null
  const remaining = Math.max(0, max - extensions)
  const extend = async () => {
    setBusy(true)
    try {
      const r = await send<Json>('POST', `/api/v1/approvals/${id}/extend`)
      onExtended(r)
      toast('ok', `기한을 ${stepLabel(step)} 늘렸습니다 (남은 연장 ${Math.max(0, (r.max_extensions ?? max) - (r.extensions ?? 0))}회)`)
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Button size="sm" onClick={extend} loading={busy} disabled={left <= 0 || remaining === 0}
        title={remaining === 0 ? `연장은 최대 ${max}회입니다` : `기한을 ${stepLabel(step)} 늘립니다 — 남은 연장 ${remaining}회`}>
        시간 연장 (+{stepLabel(step)})
      </Button>
      {left > 0 && left <= WARN_S && remaining > 0 && (
        <span role="alert" className="text-xs font-semibold text-danger">곧 만료됩니다 — 「시간 연장」으로 {stepLabel(step)} 늘릴 수 있습니다.</span>
      )}
    </>
  )
}
