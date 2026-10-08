import { QueryClient, useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query'
import { ApiError, api, get, type Json, type Opts } from './api'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
    },
  },
})

export function useGet<T = Json>(path: string | null, query?: Record<string, unknown>, opts: Partial<UseQueryOptions<T>> = {}) {
  return useQuery<T>({ queryKey: [path, query ?? {}], queryFn: () => get<T>(path!, query), enabled: !!path, ...opts })
}

// 변경 후 관련 목록을 무효화 — invalidate 는 경로 접두사
export function useSend<T = Json, V = unknown>(fn: (v: V) => Promise<T>, invalidate: string[] = []) {
  const qc = useQueryClient()
  return useMutation<T, ApiError, V>({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ predicate: (q) => invalidate.some((p) => String(q.queryKey[0] ?? '').startsWith(p)) }),
  })
}

export const send = <T = Json>(method: string, path: string, body?: unknown, o: Opts = {}) => api<T>(path, { ...o, method, body })
