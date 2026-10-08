// CMP-03 표 — 정렬·필터·행 클릭, 200행 초과 시 가상 스크롤(1만 행), 빈 결과 ST-02
import { flexRender, getCoreRowModel, getFilteredRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState } from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useRef, useState, type ReactNode } from 'react'
import { useT } from '@/lib/i18n'
import { Empty, Input, cx } from './ui'

export type Col<T> = ColumnDef<T, any>

export function DataTable<T>({ data, columns, onRowClick, filter = true, empty, toolbar, height = 560, rowKey }: {
  data: T[]; columns: Col<T>[]; onRowClick?: (row: T) => void; filter?: boolean; empty?: ReactNode; toolbar?: ReactNode; height?: number; rowKey?: (row: T) => string
}) {
  const { t } = useT()
  const [sorting, setSorting] = useState<SortingState>([])
  const [q, setQ] = useState('')
  const table = useReactTable({
    data, columns, state: { sorting, globalFilter: q }, onSortingChange: setSorting, onGlobalFilterChange: setQ,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getFilteredRowModel: getFilteredRowModel(),
    globalFilterFn: (row, _id, value) => JSON.stringify(row.original).toLowerCase().includes(String(value).toLowerCase()),
    getRowId: rowKey ? (r) => rowKey(r) : undefined,
  })
  const rows = table.getRowModel().rows
  const parentRef = useRef<HTMLDivElement>(null)
  const virtual = rows.length > 200
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => parentRef.current, estimateSize: () => 37, overscan: 12, enabled: virtual })
  const items = virtual ? v.getVirtualItems() : rows.map((_, i) => ({ index: i, start: 0 }))
  const padTop = virtual && items.length ? items[0].start : 0
  const padBottom = virtual && items.length ? v.getTotalSize() - items[items.length - 1].start - 37 : 0

  return (
    <div>
      {(filter || toolbar) && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          {filter && <Input aria-label={t('common.search')} placeholder={`${t('common.search')}…`} value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />}
          {toolbar}
          <span className="ml-auto text-xs text-fg-muted">{rows.length.toLocaleString()} / {data.length.toLocaleString()}</span>
        </div>
      )}
      <div ref={parentRef} className="overflow-auto rounded-lg border border-line bg-surface" style={virtual ? { maxHeight: height } : undefined}>
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-surface-2 text-left text-xs text-fg-muted">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th key={h.id} className="border-b border-line px-3 py-2 font-medium whitespace-nowrap"
                    aria-sort={({ asc: 'ascending', desc: 'descending' } as const)[h.column.getIsSorted() as 'asc' | 'desc'] ?? undefined}>
                    {h.isPlaceholder ? null : !h.column.columnDef.header || !h.column.getCanSort() ? (  // 이름 없는 열(동작 열)은 버튼을 두지 않는다 (WCAG 4.1.2)
                      flexRender(h.column.columnDef.header, h.getContext())
                    ) : (
                      <button type="button" className="inline-flex cursor-pointer items-center gap-1 hover:text-fg" onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {{ asc: '▲', desc: '▼' }[h.column.getIsSorted() as string] ?? ''}
                      </button>
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {padTop > 0 && <tr style={{ height: padTop }} />}
            {items.map(({ index }) => {
              const row = rows[index]
              return (
                <tr key={row.id} onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                  className={cx('border-b border-line last:border-0', onRowClick && 'cursor-pointer hover:bg-surface-2')}>
                  {row.getVisibleCells().map((c) => <td key={c.id} className="px-3 py-2 align-middle">{flexRender(c.column.columnDef.cell, c.getContext())}</td>)}
                </tr>
              )
            })}
            {padBottom > 0 && <tr style={{ height: padBottom }} />}
          </tbody>
        </table>
        {!rows.length && <Empty>{q ? `"${q}" 결과 없음` : empty}</Empty>}
      </div>
    </div>
  )
}
