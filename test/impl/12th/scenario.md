# impl 12th — 웹 콘솔 (Epic 12 · React + Vite SPA · UI 설계서 핵심 화면)

## 범위

`web/` — React 19 + TypeScript + Vite 7 + React Router 7 + Tailwind 4 (shadcn 스타일 컴포넌트), TanStack Query/Table/Virtual,
Recharts, @xyflow/react, cmdk, ko/en 사전, Pretendard 로컬 번들(외부 CDN 없음 — 에어갭).
FastAPI 가 `web/dist` 를 `/console·/chat·/login·/setup`(SPA 폴백) + `/_app`(해시 자산) 으로 서빙. local 은 `make web-build`,
prod 이미지는 `web-build` 스테이지에서 번들을 포함. HMR 개발 서버는 compose `web` 프로파일(5173).

### 구현 화면 (38 라우트 · 설계서 화면 ID 기준 약 60종)

| 그룹 | 화면 |
|------|------|
| A 설치·첫 실행 | A05~A09 첫 실행 마법사(/setup) · A10 로그인(OIDC Code+PKCE) |
| B 메인·공통 | B01 공통 셀 · B02 운영 개요 · B03 ⌘K 팔레트 · B04 알림 센터 · B05 구성요소 상태 · B06 내 계정·PAT · B07 API 탐색기 |
| C 채팅 | C01 홈·프로파일 카드·예시·연결 요약·첨부 · C02 실행 계획 패널 · C03 스트리밍·근거 인용 · C04 승인 모달 · C06 부분 실패·재시도 · C07 대화 목록 |
| D 에이전트 | D01 목록 · D02 그래프 편집기 · D03~D06 노드·팬아웃·실패 정책 · D07 YAML · D08 테스트 · D09 Host 세션 · D10 A2A 노출 |
| E 카탈로그 | E01 통합 카탈로그 · E02 상세 · E03 즉시 사용 마법사 · E05 승인 대기열 · E07 버전 |
| F MCP | F01 목록 · F02 상세(도구 허용·위험도·승인 규칙) · F06 Inspector |
| G A2A | G01 목록 · G02 등록 · G03 Agent Card · G05/G06 Task · G07 시험 콘솔 |
| H 게이트웨이 | H01 개요 · H02 라우팅 규칙 · H04 결정 로그 · H05 보안 · H07 정책 · H08 편집기·시뮬레이터 · H09 한도·예산 · H12 사람 승인 |
| I 모델 | I01 공급자·모델 · I02 연결 시험 · I04 플레이그라운드(SSE) |
| J Identity | J03 사용자·역할 · J05 토큰 전파 |
| K 관측·감사 | K02 트레이스 · K03 워터폴 · K04 로그(가상 스크롤) · K06 Usage & Cost · K07 예산 · K08 리플레이 · K09 해시 체인 · K10 서명 리포트 |
| L 운영 | L01 배포 프로파일 · L02 커넥터 · L04 업데이트 · L05 백업·복구 · L06 일반 설정 |

나머지 화면(A01~A04·A11 CLI, H06·H10·H11·H13~H15, I03·I05, J01·J02·J04·J06·J07, K01·K05, E04·E06·E08, F03~F05)은 PRD D4 에 따라
REST API · `nexus` CLI 로 제공한다(B07 API 탐색기에서 확인).

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| E12-U1 | SSE 파서 — 부분 버퍼·주석·`[DONE]`·다중 data | `src/test/sse.test.ts` | Vitest |
| E12-U2 | 실행 이벤트 리듀서 — 계획·승인 대기·재개·부분 실패·재시도 | `src/test/runState.test.ts` | Vitest |
| E12-U3 | 그래프 모델 — 깊이 배치·순환 연결 거부·삭제 시 의존 정리·도구 허용 목록 반영 | `src/test/graphModel.test.ts` | Vitest |
| E12-U4 | PKCE S256(RFC 7636 벡터)·역할별 첫 화면(설계서 10장)·권한 판정 | `src/test/auth.test.ts` | Vitest |
| E12-U5 | 셀 권한 비활성+사유(ST-04)·⌘K·표 필터/빈 결과(ST-02)·1만 행 가상 스크롤·오류 정규화(ST-03)·401 갱신 | `src/test/components.test.tsx` | Vitest(jsdom) |
| E12-B1 | 빌드: `tsc --noEmit` + `vite build` (폰트 로컬 번들) | `make web-build` | 빌드 |
| E12-S1 | SPA 폴백·자산·`/`→`/console`·API 경로 비폴백·번들 없음 503 | `test_console_spec_fallback` | pytest |
| E12-S2 | 운영 개요 시계열 API (권한) | `test_observe_timeseries` | pytest |
| E12-01 | 운영자 PKCE 로그인 → 운영 개요 KPI·차트 | Playwright | 브라우저 |
| E12-02 | ⌘K 검색 → MCP 도구 → 서버 상세·승인 규칙 | Playwright | 브라우저 |
| E12-03 | 카탈로그 · 정책 시뮬레이터 · 그래프 캔버스·노드 설정 | Playwright | 브라우저 |
| E12-04 | 트레이스 목록 → 워터폴 | Playwright | 브라우저 |
| E12-05 | 최종 사용자 → /chat · 스트리밍 응답 · 근거 원문 패널 | Playwright | 브라우저 |
| E12-06 | 고위험 쓰기 도구 승인 모달 → 실행 재개 → succeeded | Playwright | 브라우저 |
| E12-07 | 권한 없는 메뉴 비활성+사유 | Playwright | 브라우저 |
| E12-08 | 콘솔 런타임 오류(pageerror·console.error) 0건 | Playwright | 브라우저 |
| E12-R | 백엔드 전체 회귀 | `make test` | pytest |

## 실행

```sh
make web-test     # Vitest
make web-build    # tsc + vite build → web/dist (local api 가 서빙)
make web-e2e      # Playwright(Chromium) — prod 번들 대상
# 개발 서버(HMR, StrictMode) 대상: COMPOSE_PROFILES=demo,web make local-all 후
#   docker compose … --profile e2e run --rm -e E2E_TARGET=web:5173 -e E2E_PORT=5173 e2e
make test         # 백엔드 회귀
```

## 환경 가정

- Chrome 확장(claude-in-chrome) 미연결 → wds-test §4 대체로 Playwright 컨테이너(compose `e2e` 프로파일) 사용.
- e2e 컨테이너 안에서 `127.0.0.1:8800 → api:8000` TCP 프록시를 열어 PUBLIC_BASE_URL 과 같은 origin 으로 OIDC redirect_uri 검증을 통과.
- 로그인은 시드 계정(local/test 전용, `app/seed.py`)으로 내장 IdP 폼을 사용.
