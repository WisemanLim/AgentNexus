# impl 16th — 남은 P0 콘솔 화면 (Epic 16 · FR-P03(UI))

## 범위 (화면 26종 추가 → 콘솔 약 86종)

| 그룹 | 화면 | 라우트 |
|------|------|--------|
| H 게이트웨이 | H03 MCP·A2A 라우팅(가상 서버·집계 도구·스킬) · H05 Security(가드·mTLS 정책·가드 시험) · H06 시크릿(인증서 포함) · H10 허용목록·신뢰 등급 · H11 소독 규칙 · H13 Model API·클라이언트 키 · H14 MCP 프록시 세션 · H15 A2A 카드 캐시 | `/console/gateway/routing/tools·security·secrets·governance[/rules]·model-api·mcp·a2a` |
| I 모델 | I03 도메인 모델 등록(4단계·mTLS) · I05 Access Tokens | `/console/models/new·tokens` |
| J Identity | J01 IdP 연동 · J02 IdP 추가(4단계·디스커버리 시험) · J04 토큰 교환 정책 · J06 MCP Authorization · J07 워크로드 | `/console/identity/idp[/new]·token-exchange·mcp-auth·workloads` |
| E 카탈로그 | E04 server.json 등록(검증 MSG-007 → 승인 요청) · E06 소스·미러(동기화·가져오기) · E08 SBOM(CycloneDX) | `/console/registry/publish·sources` · `/console/catalog/sbom` |
| F MCP | F03 서버 설정(엔드포인트·인증·mTLS·연결 시험) · F04 OpenAPI → 도구 · F05 Custom 템플릿 | `/console/mcp-servers/{id}/config·new/openapi·new/custom` |
| G A2A | G04 원격 에이전트 인증(위임·정적·API 키·mTLS) | `/console/a2a/{id}` 인증 탭 |
| K 관측 | K01 파이프라인 · K05 메트릭·알림 규칙·Grafana JSON | `/console/observe/pipeline·metrics` |
| C 채팅 | C05 원격 위임 카드(Task 상태·Artifacts·취소) · C07 대화 이력(검색·미리보기·이어하기·.md·삭제) | 채팅 카드 · `/chat/history` |

백엔드 보강: `POST /api/v1/registry/validate` (게시 전 server.json 검증, E04 ②).

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C16-01 | 게시 전 검증 API — 유효/무효·권한 | `test_registry_validate_before_publish` | pytest |
| C16-02 | 타입 검사·번들 빌드 | `make web-build` | 빌드 |
| E16-01 | 신규 화면 24 라우트 렌더 + G04 탭 | Playwright | 브라우저 |
| E16-02 | 인증서 시크릿 저장 → 목록에 평문 없음 | Playwright | 브라우저 |
| E16-03 | server.json 검증 실패 표시 → 수정 → 승인 요청 | Playwright | 브라우저 |
| E16-04 | OpenAPI 마법사(operationId '.'→'_' 미리보기) → 생성 · IdP 마법사 단계 | Playwright | 브라우저 |
| E16-05 | 원격 위임 카드 Task completed · 대화 이력 → 이어하기 | Playwright | 브라우저 |
| E16-06 | 가드 시험 | Playwright | 브라우저 |
| E16-07 | 런타임 오류 0 | Playwright | 브라우저 |
| R | 전체 회귀 SQLite·PG·Vitest·Playwright 18·helm-check | logs | make |
