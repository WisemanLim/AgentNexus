# web/src/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../../INDEX.md)

콘솔 SPA(React 19 + Vite + Tailwind v4) 소스 루트 — 부트스트랩(`main.tsx`)·라우팅(`App.tsx`)·공통 라이브러리(`lib/`)·컴포넌트(`components/`)·화면(`pages/`). `web/dist`·`web/node_modules` 는 리뷰 제외.

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `main.tsx` | 34 | Provider 체인·테마 복원·DOM 마운트 | [main.tsx.md](main.tsx.md) |
| `App.tsx` | 179 | 라우팅 표(60여 화면 코드 분할·보호 경로) | [App.tsx.md](App.tsx.md) |
| `lib/auth.tsx` | 168 | OIDC+PKCE·토큰·refresh·AuthProvider·재로그인 | [lib/auth.tsx.md](lib/auth.tsx.md) |
| `lib/api.ts` | 78 | REST 클라이언트·오류 정규화·401 재시도 | [lib/api.ts.md](lib/api.ts.md) |

## 미선택(부록)
`components/`(Shell·ui·DataTable·CommandPalette 등 6)·`lib/`(sse·i18n·query·format)·`pages/`(화면 30여 개, 가장 큰 `gateway/GatewayMore.tsx` 1116)·`test/`(6) 는 부록 A. 화면 로직은 서버 API 호출·표시가 대부분이라 Level 2 파일 선정에서 후순위.

## 구조 요약
`main.tsx` → Provider(Query·i18n·Auth·Toast·Router) → `App.tsx` 라우팅. `lib/auth.tsx` 가 `lib/api.ts` 에 토큰 훅을 주입(의존성 역전). 화면은 `@/lib/query`(React Query)로 서버 상태를, `can()`(auth)로 UI 게이팅.

## 위험 신호
- 전역 Error Boundary 부재(청크 로드 실패 시 빈 화면).
- `App.tsx` 의 `as any`/`as never` 로 lazy export 이름 오타가 런타임에만 드러남.
- sessionStorage 토큰의 XSS 노출(설계 trade-off — CSP 전제).
