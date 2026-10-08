# web/src/lib/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../../../INDEX.md) · 상위: [src_Abstract.md](../src_Abstract.md)

콘솔 공용 라이브러리 — 인증(OIDC+PKCE), REST 클라이언트, SSE 파서, i18n, React Query 설정, 포맷 유틸.

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `auth.tsx` | 168 | OIDC+PKCE·토큰·refresh·AuthProvider·재로그인 | [auth.tsx.md](auth.tsx.md) |
| `api.ts` | 78 | REST 클라이언트·오류 정규화·401 재시도 | [api.ts.md](api.ts.md) |

## 미선택(부록)
`sse.ts`(47)·`i18n.tsx`(56)·`query.ts`(27)·`format.ts`(26) 는 부록 A.

## 구조 요약
`auth.tsx` 가 `configureApi`(api.ts)로 토큰 getter·refresh·만료 콜백을 주입한다 — api.ts 는 인증 구현을 모른다(의존성 역전). 토큰은 sessionStorage(탭 범위), 401 시 api.ts 가 refresh 1회 후 재시도·재로그인 트리거.

## 위험 신호
- sessionStorage 토큰의 XSS 노출(설계 trade-off).
- `api.ts` 비 JSON 500 본문에서 `JSON.parse` 예외가 ApiError 를 우회.
