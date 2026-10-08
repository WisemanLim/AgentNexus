# 코드 단위 리뷰 보고서 (2차) / Line-by-Line Code Review — Round 2
> 분석일 / Date: 2026-10-07~08 · 대상 / Target: `/Users/wise/Developer/wise-AgentNexus` — 1차 리뷰(2026-10-07 14:03 스냅샷) 이후 **바뀐 영역**
> 리뷰 레벨 / Level: 2 — ~3년 주니어 인수인계 수준 · 파일 / Files: Python 31개(파일별 라인·블록 리뷰) + 배포·빌드·CI 7개(요약 리뷰 1건 [INFRA.md](INFRA.md)) = 대상 38개, 보고서 32건 · 분석 / Analyst: Claude
> 방법: `wds-review` code-method(`~/.claude/plugins/cache/wise-vibe/wise-vibe/2.0.0/skills/wds-review/references/code-method.md`) Level 2 · `max_lines_per_file=600`(초과 6개는 블록 모드) · 공통 지시서 `scratchpad/BRIEF.md` · 1차 보고서·`.review/delegated/`·`.review/round2/delegated/` 대조

> **스냅샷 고지.** 라인 번호·인용은 **2026-10-07 21:03:39 KST 스냅샷** 기준이다(sha1 목록 `scratchpad/snapshot-start.sha`). 스냅샷 이후 구현자가 확인된 지적을 Epic 51 로 고쳤고, 다음 파일은 **현재 디스크가 스냅샷과 다르다**: `app/main.py`, `app/services/{mcp_gateway,a2a_gateway,a2a_server,secrets,policy,mcp_consent,llm,governance}.py`, `app/core/http.py`, `app/api/{idp,mcp,a2a,identity}.py`, `deploy/helm`(`templates/api.yaml`·`templates/networkpolicy.yaml`·`values.yaml` 외). 이 파일들의 보고서 줄 번호는 현재 디스크와 어긋날 수 있다 — 처리 결과는 §5 "처리" 열(`.review/round2/VERIFY.md`, 2026-10-08 08:55 판 — 85줄)을 볼 것. `app/services/registry.py` 는 스냅샷 전(14:25)에 바뀌었고 Epic 51 대상이 아니다(현재 sha1 = 스냅샷 `cb9e2fa5…` 확인).
>
> **작성 고지.** 일부 보고서는 중복 실행된 에이전트가 같은 경로에 다시 썼다 — `app/db.py`·`app/api/observe.py`·`app/api/identity.py` 묶음과 `app/core/http.py`·`app/services/{tls,policy,ratelimit,retention}.py` 묶음. 최종 파일만 남아 있으며, `app/api/identity.py.md` 는 인용을 스냅샷 원문과 다시 대조했다(불일치 0, 파일 첫 줄 메모). 보고서 품질 점검 결과는 §1.4.

---

## 1. 변경 영역 인벤토리 / Inventory of the Changed Area

### 1.1 범위
1차 리뷰 이후 7~10차 구현(Epic 39~50 — `test/impl/{39th..50th}/`)으로 바뀐 파일 38개(`scratchpad/files.txt`). Python 31개는 파일별 보고서, Helm·Dockerfile·compose·CI 7개는 [INFRA.md](INFRA.md) 한 건으로 묶었다. 1차에서 라인 리뷰했던 파일은 1차 보고서와, 1차 부록 A(미선택)였던 파일은 1차 INDEX §4·위임 리뷰와 대조했다.

### 1.2 디렉터리별
| 영역 | 파일 | 스냅샷 LOC 합 | 1차 라인 리뷰 대상 | Epic 51 수정(스냅샷 이후) |
|---|--:|--:|--:|--:|
| `app/`(루트) | 2 (`main.py`·`db.py`) | 288 | 2 | 1 |
| `app/api/` | 6 | 3,727 | 4 (`mcp`·`a2a`·`gateway_admin`·`observe`) | 4 (`idp`·`mcp`·`a2a`·`identity`) |
| `app/core/` | 4 | 748 | 4 | 1 (`http`) |
| `app/services/` | 17 | 6,265 | 10 | 8 |
| `app/cli/` | 1 (`nexus.py`) | 768 | 1 | 0 |
| `migrations/versions/` | 1 (`0019_refresh_family.py`, 신규) | 26 | 0 | 0 |
| 배포·빌드·CI | 7 | 605 | 0(부록 A) | Helm 3(`api.yaml`·`networkpolicy.yaml`·`values.yaml`) 외 |

### 1.3 파일 목록 (번호 = 보고서 번호)
| # | 파일 | LOC 1차 → 2차(스냅샷) | 모드 | 1차 | Epic 51 | 리뷰 |
|--:|---|---|:--:|:--:|:--:|---|
| 01 | `app/main.py` | 195 → 203 | line | O | 수정 | [app/main.py.md](app/main.py.md) |
| 02 | `app/cli/nexus.py` | 752 → 768 | block | O | — | [app/cli/nexus.py.md](app/cli/nexus.py.md) |
| 03 | `app/core/config.py` | 90 → 92 | line | O | — | [app/core/config.py.md](app/core/config.py.md) |
| 04 | `app/db.py` | 80 → 85 | line | O | — | [app/db.py.md](app/db.py.md) |
| 05 | `app/core/auth.py` | 295 → 295(4줄 변경) | line | O | — | [app/core/auth.py.md](app/core/auth.py.md) |
| 06 | `app/core/http.py` | 225 → 273 | line | O | 수정 | [app/core/http.py.md](app/core/http.py.md) |
| 07 | `app/core/security.py` | 67 → 88 | line | O | — | [app/core/security.py.md](app/core/security.py.md) |
| 08 | `app/services/mcp_gateway.py` | 1031(스냅샷)/1041 → 1096 | block | O | 수정 | [app/services/mcp_gateway.py.md](app/services/mcp_gateway.py.md) |
| 09 | `app/services/mcp_modern.py` | 415 → 500 | line | O | — | [app/services/mcp_modern.py.md](app/services/mcp_modern.py.md) **(미완 — §1.4)** |
| 10 | `app/services/mcp_tasks.py` | 231 → 231 | line | O | — | [app/services/mcp_tasks.py.md](app/services/mcp_tasks.py.md) |
| 11 | `app/services/mcp_consent.py` | 333 → 337 | line | O | 수정 | [app/services/mcp_consent.py.md](app/services/mcp_consent.py.md) |
| 12 | `app/services/a2a_gateway.py` | 819 → 886 | block | O | 수정 | [app/services/a2a_gateway.py.md](app/services/a2a_gateway.py.md) |
| 13 | `app/services/a2a_server.py` | 361 → 394 | line | 부록 A | 수정 | [app/services/a2a_server.py.md](app/services/a2a_server.py.md) |
| 14 | `app/services/a2a_grpc.py` | 254 → 277 | line | O | — | [app/services/a2a_grpc.py.md](app/services/a2a_grpc.py.md) |
| 15 | `app/services/governance.py` | 318 → 333 | line | O | 수정 | [app/services/governance.py.md](app/services/governance.py.md) |
| 16 | `app/services/policy.py` | 157 → 211 | line | 부록 A | 수정 | [app/services/policy.py.md](app/services/policy.py.md) |
| 17 | `app/services/identity.py` | 196 → 217 | line | O | — | [app/services/identity.py.md](app/services/identity.py.md) |
| 18 | `app/services/secrets.py` | 67 → 77 | line | 부록 A | 수정 | [app/services/secrets.py.md](app/services/secrets.py.md) |
| 19 | `app/services/tls.py` | 94 → 122 | line | 부록 A | — | [app/services/tls.py.md](app/services/tls.py.md) |
| 20 | `app/services/ratelimit.py` | 277 → 301 | line | 부록 A | — | [app/services/ratelimit.py.md](app/services/ratelimit.py.md) |
| 21 | `app/services/llm.py` | 425 → 426 | line | O | 수정 | [app/services/llm.py.md](app/services/llm.py.md) |
| 22 | `app/services/audit.py` | 242 → 243 | line | O | — | [app/services/audit.py.md](app/services/audit.py.md) |
| 23 | `app/services/retention.py` | 39 → 39(내용 변경) | line | 부록 A | — | [app/services/retention.py.md](app/services/retention.py.md) |
| 24 | `app/services/registry.py` | 569(추정) → 575 | line | 부록 A | — | [app/services/registry.py.md](app/services/registry.py.md) |
| 25 | `app/api/mcp.py` | 760 → 778 | block | O | 수정 | [app/api/mcp.py.md](app/api/mcp.py.md) |
| 26 | `app/api/a2a.py` | 719 → 727 | block | O | 수정 | [app/api/a2a.py.md](app/api/a2a.py.md) |
| 27 | `app/api/gateway_admin.py` | 835 → 836 | block | O | — | [app/api/gateway_admin.py.md](app/api/gateway_admin.py.md) |
| 28 | `app/api/observe.py` | 471 → 475 | line | O | — | [app/api/observe.py.md](app/api/observe.py.md) |
| 29 | `app/api/idp.py` | 415 → 421 | line | 부록 A | 수정 | [app/api/idp.py.md](app/api/idp.py.md) |
| 30 | `app/api/identity.py` | 490 → 490 | line | 부록 A | 수정 | [app/api/identity.py.md](app/api/identity.py.md) |
| 31 | `migrations/versions/0019_refresh_family.py` | 신규 → 26 | line | — | — | [migrations/versions/0019_refresh_family.py.md](migrations/versions/0019_refresh_family.py.md) |
| 32 | 배포·빌드·CI 7개(`deploy/helm/agentnexus/{templates/a2a-grpc.yaml,templates/api.yaml,templates/networkpolicy.yaml,values.yaml}`·`Dockerfile`·`docker-compose.yml`·`.github/workflows/ci.yml`) | 605 | block(요약) | 부록 A | Helm 수정 | [INFRA.md](INFRA.md) |

LOC 는 각 보고서 헤더 값(스냅샷 `wc -l`). 디렉터리 개요: [app](app/app_Abstract.md) · [app/api](app/api/api_Abstract.md) · [app/services](app/services/services_Abstract.md) · [app/core](app/core/core_Abstract.md)

### 1.4 보고서 품질 점검 (2026-10-08, 인용 블록을 원문과 자동 대조)
- 대조 방법: 각 보고서의 인용 코드 블록(`python path=… lines=a-b` 머리줄)을 스냅샷 원문(변경 없는 파일은 현재 디스크, 바뀐 파일은 `scratchpad` 의 스냅샷 사본 — sha1 일치 확인)과 줄 단위 비교.
- **일치**: `app/main.py`·`db.py`·`core/{config,auth,http,security}`·`services/{identity,tls,ratelimit,retention,audit,a2a_grpc,mcp_tasks,mcp_consent,mcp_gateway,policy,secrets,registry}`·`api/{observe,idp,identity}`·`migrations/0019`.
- **줄 번호 어긋남(내용은 원문)**: [app/api/gateway_admin.py.md](app/api/gateway_admin.py.md) — L220 이후 블록 17개의 `lines=` 표기가 실제보다 1줄 뒤(예: `lines=220-256` 의 내용은 원문 L219-255). [app/cli/nexus.py.md](app/cli/nexus.py.md) — `lines=458-512`·`638-686` 블록의 생략 표기 뒤 구간이 2줄 어긋남. [app/services/llm.py.md](app/services/llm.py.md) — `lines=272-298` 블록이 원문 L273-299. 해당 블록 안 분석의 `L` 번호도 같은 만큼 어긋났을 수 있다.
- **미완**: [app/services/mcp_modern.py.md](app/services/mcp_modern.py.md) — L1-133 까지 쓰고 중단(코드 펜스 미종결, 파일 평가·위험 등급·1차 대비 없음). 아래 표의 등급은 "미산출".
- **스냅샷 사본 없음(대조 불가)**: `services/{a2a_gateway,a2a_server,governance}`·`api/{a2a,mcp}` 보고서.
- 다른 보고서가 링크하는 `app/cli/cli_Abstract.md`·`migrations/versions/versions_Abstract.md` 는 이번 작업 범위 밖이라 만들지 않았다(링크 끊김).

---

## 2. 파일별 위험 표 / Per-file Risk
등급·사유는 각 보고서 "파일 평가 / File Assessment" 의 "파일 위험 등급" 을 옮겼다. Watch-outs 는 보고서 본문의 `[위험:…]` bullet 원 개수(다른 파일 항목을 교차 표기한 것 포함 — 중복 제거 목록은 §5). 1차 대비는 각 보고서 "1차 리뷰 대비" 표의 상태 개수(1차 [낮음] 이월 포함).

| # | 파일 | 등급 | Watch-outs 높음/중간/낮음 | 1차 대비 해결/부분/미해결 | 등급 사유(보고서 요약) |
|--:|---|:--:|:--:|:--:|---|
| 25 | [app/api/mcp.py](app/api/mcp.py.md) | **높음** | 2/4/20 | 2/4/8 | 미해결 [높음] 1(사용자 토큰·저장 시크릿 외부 전송) + 진입 경계 [중간] 4 |
| 08 | [app/services/mcp_gateway.py](app/services/mcp_gateway.py.md) | **높음** | 4/4/21 | 4/7/29 | 미해결 [높음] 3(핫스팟 #1·#2 부분 해결 + 2단계 PATCH 우회) |
| 27 | [app/api/gateway_admin.py](app/api/gateway_admin.py.md) | **높음** | 1/1/12 | 3/0/7 | [높음] 1(기계 주체 자기 승인) + [중간] 1 |
| 15 | [app/services/governance.py](app/services/governance.py.md) | **높음** | 2/2/5 | 2/2/13 | [높음] 1 + 사람 승인 통제 [중간] 2 |
| 30 | [app/api/identity.py](app/api/identity.py.md) | **높음** | 1/5/10 | 2/2/2 | [높음] 1(토큰 승격) + [중간] 5, 신원·인가 경계 |
| 24 | [app/services/registry.py](app/services/registry.py.md) | **높음** | 1/5/24 | 1/1/4 | [높음] 1(`_anx_` 심사 기록 주입) + 카탈로그·공급망 경계 [중간] 5 |
| 21 | [app/services/llm.py](app/services/llm.py.md) | **높음** | 1/3/17 | 1/2/11 | [높음] 1(한도 폴백의 데이터 등급 우회) + [중간] 3 |
| 12 | [app/services/a2a_gateway.py](app/services/a2a_gateway.py.md) | **높음** | 0/6/47 | 4/4/26 | 보안 경계 [중간] 6(SSRF TOCTOU·루프 차단·가드 우회·감시기·내부 콜백·쓰레기 행) |
| 26 | [app/api/a2a.py](app/api/a2a.py.md) | **높음** | 0/4/19 | 0/3/10 | 보안 경계 [중간] 4(승인 우회·인증 잔여 경로·Task 열람 범위·SSRF) |
| 29 | [app/api/idp.py](app/api/idp.py.md) | **높음** | 0/4/15 | 4/3/3 | 인터넷 노출 인증 경계 [중간] 4 |
| 13 | [app/services/a2a_server.py](app/services/a2a_server.py.md) | **높음** | 0/3/17 | 2/3/3 | 보안 경계 [중간] 3(취소 비대칭·루프 정지 DNS·전달 시점 SSRF) |
| 06 | [app/core/http.py](app/core/http.py.md) | **높음** | 0/3/11 | 1/2/13 | SSRF 가드 핵심 [중간] 3 |
| 16 | [app/services/policy.py](app/services/policy.py.md) | **높음** | 0/2/9 | 1/3/0 | 인가 판정 핵심 [중간] 2 |
| 01 | [app/main.py](app/main.py.md) | 중간 | 0/3/9 | 2/1/12 | [중간] 3(조립 지점 — 핵심 모듈 기준 미적용) |
| 02 | [app/cli/nexus.py](app/cli/nexus.py.md) | 중간 | 0/2/12 | 4/0/30 | 공급망 경계 [중간] 2, 1차 [높음] 1·[중간] 2 해결 |
| 05 | [app/core/auth.py](app/core/auth.py.md) | 중간 | 0/1/12 | 1/3/11 | [중간] 1(교차 — 클라이언트 키 팀) + 1차 [낮음] 이월 |
| 03 | [app/core/config.py](app/core/config.py.md) | 중간 | 0/1/5 | 0/0/8 | [중간] 1(`app_env` fail-open) |
| 14 | [app/services/a2a_grpc.py](app/services/a2a_grpc.py.md) | 중간 | 0/1/19 | 2/1/8 | [중간] 1(평문 기본값) |
| 11 | [app/services/mcp_consent.py](app/services/mcp_consent.py.md) | 중간 | 0/1/10 | 4/1/12 | [중간] 1(중계 경로 정책 우회 — 스냅샷 이후 수정) |
| 20 | [app/services/ratelimit.py](app/services/ratelimit.py.md) | 중간 | 0/2/13 | 1/1/3 | [중간] 2(가용성·FinOps 통제) |
| 23 | [app/services/retention.py](app/services/retention.py.md) | 중간 | 0/1/3 | 4/0/1 | [중간] 1(컴플라이언스 통제 우회) |
| 28 | [app/api/observe.py](app/api/observe.py.md) | 중간 | 0/2/9 | 2/1/12 | [중간] 2(성능·가용성) |
| 32 | [INFRA.md](INFRA.md) | 중간 | 0/3/15 | 3/1/0 | 배포 진입점 [중간] 3(설정 수준) |
| 07 | [app/core/security.py](app/core/security.py.md) | 낮음 | 0/0/5 | 3/2/1 | [낮음]만, 해시 강도·형식 해결 |
| 04 | [app/db.py](app/db.py.md) | 낮음 | 0/0/4 | 0/0/6 | [낮음]만 |
| 17 | [app/services/identity.py](app/services/identity.py.md) | 낮음 | 0/2/12 | 6/1/8 | 자체는 [낮음] — [중간] 2 는 API 쪽 집계 |
| 18 | [app/services/secrets.py](app/services/secrets.py.md) | 낮음 | 0/0/5 | 3/1/0 | 운영·방어적 [낮음] |
| 19 | [app/services/tls.py](app/services/tls.py.md) | 낮음 | 0/0/10 | 0/1/0 | 정책 위반 fail closed |
| 22 | [app/services/audit.py](app/services/audit.py.md) | 낮음 | 0/0/11 | 1/1/13 | 체인 핵심 경로 정확 |
| 10 | [app/services/mcp_tasks.py](app/services/mcp_tasks.py.md) | 낮음 | 0/0/7 | 4/1/7 | 소유권·은닉 견고 |
| 31 | [migrations/versions/0019_refresh_family.py](migrations/versions/0019_refresh_family.py.md) | 낮음 | 0/0/2 | 2/0/0 | 스키마 변경 정확 |
| 09 | [app/services/mcp_modern.py](app/services/mcp_modern.py.md) | 미산출 | 0/1/2 (L1-133 까지) | — | 보고서 미완(§1.4) |

**분포**: 높음 13 · 중간 10 · 낮음 8 · 미산출 1. Watch-outs 원 개수 합 높음 12 · 중간 66 · 낮음 392(교차 표기 포함). 중복을 합친 실행 목록은 §5 — [위험:높음] 7건, [위험:중간] 59건.

---

## 3. 위험 핫스팟 Top-5 / Hotspots
(처리 상태는 `.review/round2/VERIFY.md` 2026-10-08 08:55 판 기준 — VERIFY 는 이 작업 중에도 갱신되었다.)
1. **카탈로그·등록 경로의 통제 공백 — 미검증.** 게시 server.json 의 `_anx_review` 가 승인 기록처럼 살아남아 등록 때 도구 위험도를 low·허용으로 적용 `app/services/registry.py:132-135` → `app/api/catalog.py:475-487` [높음]. 등록 옵션 `endpoint` 덮어쓰기로 저장 시크릿 전송처 변경 `registry.py:290`·`:311-312`, 게시 `_meta.auth` 전송처 키 복사(+auto_approve) `:297`, 게시자≠소유자 버전 선점 `:168-190`, 미러 독 항목이 동기화와 **예약 보존(FR-O02)** 을 함께 멈춤 `:457-466` + `app/services/scheduler.py:70-76`·`:125-129`, 익명 `/v0/servers` 전체 행 스캔 `:395-411` [중간]. VERIFY 는 `registry.py` 를 다루지 않았다(관련 결정은 VERIFY:53 하나).
2. **자격 증명 전송처 통제 — 일부 수정.** MCP·A2A `auth` 전송처 키(`authorization_server`·`token_endpoint`·`resource_metadata`·`mode`) 변경 [높음] → C51-08·VERIFY:73 수정. 남은 것: 2단계 PATCH(`secret_ref` 비우기 + endpoint 변경 → 자기 네임스페이스 `secret_ref` 재부착) `app/services/mcp_gateway.py:131-138` [높음, 미검증], 외부 AS 에 사용자 원본 토큰 `:173` [높음, 기존 결정], AS 미등록 시 업스트림 제시 AS 수용 `:201` [중간, 미검증], A2A `scheme`·`tls.verify`·`push_verify` 변경 `app/api/a2a.py:534-544` [중간, 부분].
3. **사람 승인·신원 경계 — 대부분 수정.** 기계 주체 자기 승인 [높음] → C51-13, 토큰으로 토큰 발급 [높음]·`ttl_days: null`·클라이언트 키 팀 → C51-14, IdP MFA·IP 허용 목록·이벤트 루프 scrypt → C51-12, 등록 승인 우회 PATCH → C51-13. 기록·결정으로 남은 것: `identity:write` 의 admin 부여·Access Token audience·IdP 삭제 세션(VERIFY:85 기록), 단독 롤백(VERIFY:76)·표적 잠금(VERIFY:82) 기존 결정, 새 도구 자동 허용(VERIFY:77 기록). 미검증: 기한 직후 결정 시 실행 영구 대기 `app/services/governance.py:286-290`.
4. **SSRF·egress 경계 — 대부분 수정 또는 결정.** 푸시 URL 의 등록 시점 검사만 `app/services/a2a_gateway.py:639-646`·`:834-836`, `app/services/a2a_server.py:119` → C51-03·C51-09, 이벤트 루프 동기 DNS `a2a_gateway.py:568`·`a2a_server.py:283` → C51-03, Alibaba IMDS `app/core/http.py:36-37` → C51-09. DNS 재바인딩 `http.py:56-73` → 기존 결정(SEC2-02). OpenAPI 가져오기·카드 조회의 사설망 조회 `app/api/mcp.py:557-569`·`app/api/a2a.py:458-461` → 기록(사설망 허용은 제품 결정).
5. **가용성·운영 정확성 — 미검증 잔여.** 위 1의 미러 독 항목, 로컬 취소가 오케스트레이션 run 을 멈추지 않음 `app/services/a2a_server.py:274-276`, 아무 taskId 로 쓰레기 Task 행 생성 `app/services/a2a_gateway.py:573`, 라우팅 규칙 `timeout_ms` 미적용·예산 사전 검사 초과 `app/services/llm.py:137`·`:221-225`, Inspector `data_class` 미판정 `app/api/mcp.py:458`. 헬스 루프 종료 `app/main.py:82-86` → C51-10, 관측 전체 행 집계·한도 카운터 TTL → 기록.

---

## 4. 1차 리뷰 대비 / vs 1st Review
1차: 40개 파일(Level 2, 14:03 스냅샷), INDEX §4.3 핫스팟 5건. 2차: 변경 영역 38개(보고서 32건). 상태는 각 2차 보고서의 "1차 리뷰 대비" 표 근거를 모은 것 — Epic 51(스냅샷 이후) 처리는 괄호로 덧붙였다.

### 4.1 1차 핫스팟 Top-5 (`.review/CODE-REVIEW-Lv2/INDEX.md:228-232`)
| 1차 핫스팟 | 2차 상태(스냅샷) | 근거 | 스냅샷 이후 |
|---|---|---|---|
| #1 [높음] 사용자 병합 `auth` 로 임의 시크릿 외부 전송 | 부분 해결 | 남의 `secret_ref`·tls 시크릿 차단 `app/services/secrets.py:17-28`, endpoint 변경 통제 `:31-38`(Epic 39), 게시 메타데이터 참조 제거 `app/services/registry.py:297-303`. 남음: 전송처 키·2단계 PATCH(`mcp_gateway.py.md`), A2A scheme 등(`api/a2a.py.md`), 등록 옵션(`registry.py.md`) | C51-08(MCP)·VERIFY:73(A2A 전송처 키) 반영. 2단계 PATCH·A2A `scheme`·registry 경로는 VERIFY 밖 |
| #2 [높음] 외부 AS 에 사용자 원본 토큰 + 업스트림 지정 URL 조회 | 부분 해결 | AS 고정 `_pinned` `app/services/mcp_gateway.py:196-204`(39th). 원본 토큰 전송 `:173`, 미고정 시 수용 `:201`, 401 challenge URL 조회 `:217-219` 남음 | VERIFY: 원본 토큰 = 기존 결정, challenge 조회 = 기록 |
| #3 [중간] 클라이언트 웹훅·카드·OpenAPI URL SSRF | 부분 해결 | 푸시 등록 시 `internal_url` 검사(운영 모드), 실패 로그. 전달 시점·카드·OpenAPI·gRPC 는 남음(`a2a_gateway.py.md`, `a2a_server.py.md`, `api/mcp.py.md`, `api/a2a.py.md`, `a2a_grpc.py.md`) | C51-03·C51-09 가 전달 시점 재검사. 카드·OpenAPI 사설망 조회는 기록(VERIFY:71·74), gRPC 평문 기록(VERIFY:68) |
| #4 [중간] 사람 승인 통제의 경합·우회 | 부분 해결 | 승인 소비 TOCTOU 해결(조건부 UPDATE, `governance.py.md` 1차 대비), 교환 무인증 해결(`services/identity.py.md`). 새 도구 자동 허용 미해결 + **신규 [높음] 2건**(기계 주체 자기 승인, `_anx_` 주입) | 자기 승인 C51-13 수정, 새 도구 자동 허용 기록(VERIFY:77), 단독 롤백 기존 결정(VERIFY:76), `_anx_` 주입·기한 직후 결정은 VERIFY 밖 |
| #5 [중간] 관측 집계 전체 행 메모리 로드 | 부분 해결 | `summary_data`·`evaluate` 해결, `usage`·`_overview_extra`·`timeseries` 남음(`api/observe.py.md`) | VERIFY: 기록(다음 성능 정리 후보) |

### 4.2 1차 반복 패턴(§4.1)·계층 위반(§4.2)
| 1차 항목 | 2차 상태 | 근거 |
|---|---|---|
| §4.1-1 분리 스냅샷 + `run_in_threadpool` | 유지 — 단 스냅샷에서 위반 신규: async 경로의 동기 DNS(C51-03), `/idp/token` 의 동기 scrypt·JWKS(C51-12) | `app/api/idp.py:288-337`(`api/idp.py.md`), `app/services/a2a_server.py:283` |
| §4.1-2 워커 로컬 캐시 상한 | 부분 해결 | 상한·축출 추가(`a2a_server.py.md` `MAX_TASKS`, `mcp_gateway.py.md`), `_UPSTREAM_SEEN` 정리 누락은 CR2-04 → C51-05 |
| §4.1-4 클라이언트 헤더를 신뢰 입력으로 | 부분 해결 | Epic 40 `caller_context`·`declared_data_class`. 남음: Inspector `data_class` `app/api/mcp.py:458`(미검증), 모르는 등급 문자열 `app/services/policy.py:48`(C51-13), `caller_context` 동기 DB 를 스레드풀로(VERIFY:69) |
| §4.1-5 사용자 병합 가능한 `auth` JSON | 부분 해결 | 위 핫스팟 #1 |
| §4.1-6 조용한 예외 삼킴 | 부분 해결 | 헬스 루프 C51-10. 남음: `on_change` 실패 무로그 `app/services/a2a_server.py:101-103`. 반대로 너무 좁은 예외 처리(`ApiError` 만) `app/services/registry.py:457-466` |
| §4.1-7 상태 전이 비원자성 | 부분 해결 | 승인 소비·refresh 회전 해결(`app/services/identity.py:93-96`). 남음: 로그인 실패 확인-증가 `app/api/idp.py:104-117`, 한도·예산 사전 검사 `app/services/llm.py:221-225`, 카탈로그 게시 `app/services/registry.py:168-190` |
| §4.2 라우터 import 부수효과(`@registry.register_handler`) | 미해결(설계 유지) | `app/services/registry.py:250-258`, `app/api/a2a.py:718`, `app/api/llm.py:271` |
| §4.2 `services.scheduler` → `api.observe`·`api.ops` | 미해결 | `app/services/scheduler.py:46`, `:116` |

### 4.3 1차에 없던 큰 지적(2차 신규)
- 기계 주체 자기 승인 [높음] — 1차는 `approver="requester"` 기본값만 지적, 주체 종류 문제는 2차에서 연결(`gateway_admin.py.md`, `governance.py.md`) → C51-13.
- 토큰으로 토큰 발급 [높음] — 1차 부록 A 파일(`api/identity.py.md`) → C51-14.
- `_anx_` 심사 기록 주입 [높음] — 1차 부록 A 파일(`registry.py.md`) → 미검증.
- 한도 폴백의 데이터 등급 우회 [높음] — 2차 `llm.py.md`(C51-11 수정).
- 2단계 PATCH 로 `check_destination` 우회 [높음] — Epic 39 통제의 빈틈(`mcp_gateway.py.md`) → 미검증.

---

## 5. 수정 대상 지적 목록 / Findings for Fixers
- 줄 번호는 **스냅샷 기준**(Epic 51 수정 파일은 현재 디스크와 다를 수 있음). 같은 근본 원인은 한 행으로 합치고 관련 위치를 함께 적었다.
- "처리" = `.review/round2/VERIFY.md` 판정: **수정됨 C51-xx** · **기존 결정** · **기록** · **결정 필요**, VERIFY 가 다루지 않은 항목은 **미검증**.

### 5.1 [위험:높음]
| # | 위치 | 결함 · 실패 시나리오 | 최소 수정 | 처리 |
|--:|---|---|---|---|
| H1 | `app/api/gateway_admin.py:746-755` · `app/services/governance.py:281`·`:70` | requester 모드 자기 승인을 주체 종류와 무관하게 허용. `gateway:invoke` access token 이 `tools/call` → `approval_id` 수신 → 같은 토큰으로 `POST /approvals/{id}/approve` → 재호출로 고위험 도구 실행(사람 없음) | 자기 승인은 `p.kind == "user"` 세션만, 기계 주체 403 + 감사 `actor_kind` | 수정됨 C51-13(VERIFY:75 — 사람 주체 `user`·`pat` 만 결정, PAT 는 허용 유지) |
| H2 | `app/services/registry.py:132-135`·`:172`·`:192` → `app/services/review.py:176-179`, `app/api/catalog.py:475-487`, `app/services/governance.py:60` | 게시 server.json 최상위 `_anx_review`(`tool_risks`·`trust`)가 승인 기록으로 남는다. 개발자가 `delete_repository: low` 를 넣어 게시 → 승인자 기본값 승인 → 등록 시 사람 승인 없이 허용 | 게시 입력의 `_anx_` 접두 키 거부·제거, `_apply_version` 은 item 쪽 `_anx_*` 만 유지 | 미검증 |
| H3 | `app/api/identity.py:55-57`(+`:35`) | `POST /api/v1/me/tokens` 가 `current_principal` 만 요구 → 스코프 PAT·클라이언트 키·위임 토큰이 소유자 역할 전체·무기한 PAT 발급(유출 클라이언트 키 → 운영자 권한 PAT) | 대화형 사용자 세션(`p.kind == "user"`)만, 새 스코프 ⊆ 호출자 스코프, `ttl_days` None 금지 | 수정됨 C51-14(VERIFY:83) |
| H4 | `app/services/mcp_gateway.py:131-138` · `app/api/mcp.py:404-405` | 2단계 PATCH: ① `endpoint` 변경 + `secret_ref: null`(통과) ② 자기 네임스페이스 `secret_ref` 재부착(통과) → 저장된 백엔드 시크릿이 공격자 endpoint 로 | 저장값과 다른 `secret_ref` 부착도 `gateway:write`, 또는 시크릿에 등록 당시 전송처 지문 기록·사용 시 비교 | 미검증(C51-08·VERIFY:52·70 은 같은 요청 안의 전송처 키만) |
| H5 | `app/api/mcp.py:403-405`·`:410-411`·`:436` · `app/services/mcp_gateway.py:124-182`·`:759` | `check_destination` 이 `endpoint` 만 비교 — `mcp:write` 가 `authorization_server`·`token_endpoint`·`resource_metadata`·`mode` 를 바꿔 사용자 플랫폼 토큰·서비스 시크릿을 외부로 | 전송처 키 변경도 `changed`, oauth2 는 `gateway:write` | 수정됨 C51-08(VERIFY:52·70, `mode` 포함) |
| H6 | `app/services/mcp_gateway.py:173` | 외부 AS 에 사용자 원본 토큰(`subject_token=p.raw_token`) 전송, 운영자 AS 허용 목록 없음 | 외부 AS 허용 목록 + audience 한정 교환 토큰 | 기존 결정(VERIFY:53 — 카탈로그 승인 전제) |
| H7 | `app/services/llm.py:226-232` | 한도 폴백 모델이 정책·허용 등급·에어갭 검사를 건너뛰고 1순위 — 예산 초과 팀의 confidential 요청이 상용 모델로 | 폴백에도 같은 검사, 막히면 429 | 수정됨 C51-11(VERIFY:58) |

### 5.2 [위험:중간] — 신원·인증·인가
| # | 위치 | 결함 · 실패 시나리오 | 최소 수정 | 처리 |
|--:|---|---|---|---|
| M1 | `app/api/identity.py:35` · `app/services/identity.py:159` | `ttl_days: int \| None = Field(90, ge=1, le=90)` — JSON `null` 통과 → 만료 없는 PAT·클라이언트 키(90일 정책 우회) | `ttl_days: int = Field(90, ge=1, le=90)` | 수정됨 C51-14(VERIFY:84) |
| M2 | `app/api/identity.py:36`·`:70-71` · `app/core/auth.py:211` | 클라이언트 키 `team` 을 발급자가 자유 지정 → 남의 팀 KB 검색·팀 정책·팀 한도/비용 전가 | `body.team` 은 `user.team` 이거나 관리자일 때만 | 수정됨 C51-14(VERIFY:84) |
| M3 | `app/api/identity.py:138-163`·`:202-203`·`:225-226` | `identity:write`(security 역할)가 자기에게 `admin` 부여 가능(IdP 역할 매핑 경로 포함) | 부여 역할 권한 ⊆ 호출자 권한, `admin` 부여는 admin 만 | 기록(VERIFY:85) |
| M4 | `app/api/identity.py:253-261` | IdP **삭제**는 세션을 끝내지 않음(중지는 끝냄) → 탈취 IdP 로 로그인한 세션이 refresh 로 계속 연장 | 삭제 전 `_end_idp_sessions` | 기록(VERIFY:85) |
| M5 | `app/api/identity.py:445` · `app/services/identity.py:144` | Access Token `audience` 무제약 → `aud=mcp:<서버>` 토큰을 직접 발급해 교환 정책·게이트웨이 승인 우회 | `audience` 를 `API_AUDIENCE` 로 고정 | 기록(VERIFY:85) |
| M6 | `app/api/idp.py:104-117` | 사용자 이름 단독 고정 창 — 비인증 표적 잠금(관리자 상시 LOCKED), 분사, 비원자 확인-증가 | 원자 `window_add` 선차감, (이름, IP) 두 축, 잠금 대신 점진 지연 | 기존 결정(VERIFY:82) |
| M7 | `app/api/idp.py:120-129` | MFA 실패가 한도에 집계되지 않고 TOTP 재사용 가능 → 비밀번호 아는 공격자의 OTP 무제한 추측 | MFA 실패 집계, 마지막 성공 스텝 저장 | 수정됨 C51-12(VERIFY:81) · TOTP 재사용은 기존 결정(VERIFY:82) |
| M8 | `app/api/idp.py:262-267` | 로그인 IP 허용 목록이 브라우저 폼에만 — 디바이스 승인·브로커·password grant 로 우회 | 로그인 성공 직후 공통 IP 검사 | 수정됨 C51-12(VERIFY:80) |
| M9 | `app/api/idp.py:288-337` | async 토큰 핸들러에서 동기 scrypt·JWKS HTTP → 틀린 시크릿 반복으로 워커 이벤트 루프 정지 | `run_in_threadpool`, `client_id` 별 실패 한도 | 수정됨 C51-12(VERIFY:79) |
| M10 | `app/services/policy.py:48` | 모르는 데이터 등급 문자열이 MCP·A2A 에서 통과 → 등급 조건 deny 정책 미매치 | 정규화 + 미지 값 400 | 수정됨 C51-13(VERIFY:60) |
| M11 | `app/services/policy.py:118` | user 주체를 표시 이름으로도 매치 → IdP 표시 이름 변경으로 allow 획득·deny 회피 | `sub`·로컬 `username` 만 매치 | 수정됨 C51-15(VERIFY:61 — 이름 매치는 내장 IdP 사용자만) |
| M12 | `app/core/config.py:17` | `app_env` 기본 `local` fail-open — 누락 시 `admin/admin-pass` 개발 계정·password grant·푸시 SSRF 가드 해제 | 기본 `prod` 또는 미설정 시 기동 거부 | 기존 결정(VERIFY:66) |

### 5.3 [위험:중간] — 거버넌스·승인·카탈로그
| # | 위치 | 결함 · 실패 시나리오 | 최소 수정 | 처리 |
|--:|---|---|---|---|
| M13 | `app/services/governance.py:62-73` | 새 도구 `allowed=True` + 시드 전체 허용 → low 분류 새 도구가 검토 없이 즉시 호출 | untrusted 서버 새 도구는 `allowed=False`/승인 필요, 읽기 동사 허용 목록 | 기록(VERIFY:77) |
| M14 | `app/services/governance.py:286-290` | 기한 직후 결정 → `expired` 커밋 후 예외, 실행 훅 미호출 → 실행 `waiting_approval` 영구 대기 | 만료 처리 공통 함수 + `_on_decision` | 미검증 |
| M15 | `app/api/gateway_admin.py:160-183` | 단독 롤백이 검토 안 된 리비전도 즉시 재적용 → 2인 검토 없이 정책 완화 | 승인된 리비전·직전 승인 버전만, 그 밖은 롤백 초안 | 기존 결정(VERIFY:76) |
| M16 | `app/services/registry.py:284`·`:290`·`:311-312` | 등록 옵션 `endpoint` 무검증 + `auth or srv.auth` 유지 → `catalog:write` 스코프 PAT 로 활성 서버 endpoint 를 바꿔 저장 시크릿 유출, `check_destination` 미경유 | principal 전달, 승인 remote 와 다르면 `gateway:write` + `check_destination`, 감사에 endpoint | 미검증 |
| M17 | `app/services/registry.py:297`(+`:199-200`) | 게시 `_meta.auth` 의 전송처 키 그대로 복사 — auto_approve 미러는 사람 승인 없이 외부 AS 로 사용자 토큰 | 외부 AS 허용 목록, auto_approve 항목은 auth 전송처 있으면 pending, 승인 diff 에 auth 표시 | 기존 결정(VERIFY:53) — auto_approve 경로는 미검증 |
| M18 | `app/services/registry.py:457-466`(+`:102-109`, `:44`, `app/services/scheduler.py:70-76`) | 미러 항목의 비 ApiError(서명 블록 형식·PEM·PG 길이 초과)가 동기화를 중단, `last_sync_at` 미갱신 → 매 주기 같은 항목에서 실패, `tick` 이 보존 작업까지 건너뜀 | 항목·소스 단위 `except Exception` 격리, 서명 형식 검사, 스키마 길이 = DB | 미검증 |
| M19 | `app/services/registry.py:168-190` | 기존 항목 새 버전 게시에 소유자 확인 없음 → 다른 팀·미러 항목 버전 선점(미러 정상 버전 409) | 소유자·승인자만, 미러 항목은 해당 미러만 | 미검증 |
| M20 | `app/services/registry.py:395-411` · `app/api/catalog.py:495-498` | 익명 `/v0/servers` 가 DISCOVERABLE 전체 행·JSON 을 매 요청 메모리 로드(가능성, 규모 의존) | DB 페이지네이션, 요청 한도 | 미검증 |
| M21 | `app/services/retention.py:26-27` | `overrides` 값 무검증 → security 1인이 `0` 으로 텔레메트리 삭제·감사 전체 아카이브 후 파기 | `audit_days >= 365`·나머지 `>= 1` 강제 또는 2인 승인 | 기존 결정(VERIFY:78) |

### 5.4 [위험:중간] — MCP·A2A 게이트웨이·API
| # | 위치 | 결함 · 실패 시나리오 | 최소 수정 | 처리 |
|--:|---|---|---|---|
| M22 | `app/api/mcp.py:452`·`:473`·`:476-479` | Inspector 가상 주체 `team` 무제한 + 드라이런 규칙을 원문 `(server_id, tool)` 로 찾음 → 남의 팀 도구 실제 호출, `_all` 경로 medium 쓰기 도구 실행 | 비관리자는 자기 팀만, `_resolve_tool` 후 판정·실패 시 드라이런 | 기록(VERIFY:71) |
| M23 | `app/api/mcp.py:458`·`:507` | Inspector `data_class` 를 서버 판정 없이 사용(기본 internal) — confidential 정책·`data:declare` 회수 무력화 | `policy.declared_data_class(...)` | 미검증 |
| M24 | `app/api/mcp.py:557-569` | OpenAPI 가져오기: 사설망 포트 스캔 오라클, 본문 전체 수신 후 크기 검사, YAML 별칭 폭탄 | 스트리밍 상한·총 시간 제한, 별칭 금지 로더, 오류는 타입명만 | 기록(VERIFY:71) |
| M25 | `app/api/mcp.py:594-595` | 선택 목록이 있어도 `operationId` 없는 operation 은 남겨 hosted 도구로 노출 가능 | 선택이 있으면 식별 불가 operation 제거 | 미검증 |
| M26 | `app/services/mcp_gateway.py:217-219`·`:230`·`:240` | 401 challenge·PRM 이 정한 URL 을 그대로 GET(블라인드 SSRF), PRM `resource` 일치 미확인 | 같은 origin 만, `resource` 확인 | 기록(VERIFY:54) |
| M27 | `app/services/mcp_gateway.py:201` | AS 미등록 oauth2 서버는 401 이 제시한 AS 를 수용 → H6 경로로 원본 토큰 | AS 없으면 플랫폼 issuer 고정 또는 실패 | 미검증 |
| M28 | `app/services/mcp_gateway.py:536` | stdio 허용 목록 단일 토큰 항목이 인터프리터면 인자 자유(등록 경로는 전체 명령 일치 불가) | 인터프리터 단일 항목 거부, 등록이 전체 명령 생성 | 기록(VERIFY:54) |
| M29 | `app/services/mcp_gateway.py:1009-1047` | resources·prompts 경로에 도구 통제(규칙·정책·승인·한도·감사) 없음 | `policy.evaluate(kind=resource/prompt)` + 서버별 허용 | 기록(VERIFY:54) |
| M30 | `app/services/mcp_consent.py:47-52` | 중계 분기가 서버 sampling `deny` 정책보다 먼저 | 중계 전 정책 확인 | 수정됨 C51-10(VERIFY:55) |
| M31 | `app/services/mcp_modern.py:60`·`:68` · `app/services/ratelimit.py:59-60`·`:134-135` | 증감 카운터 TTL 이 변경마다 연장 → 죽은 워커 몫이 활동 중 영구 잔류(listen 429 고착) | 구독별 id ZSET + heartbeat | 기록(VERIFY:63) |
| M32 | `app/services/ratelimit.py:266-269` | `any` 예산이 팀 없는 주체에 0, `client_key` 예산 조용히 무시 | `any`+`*` 는 전체 합계, client_key 예산 400 | 기록(VERIFY:64) |
| M33 | `app/services/llm.py:202-206` | `Model.data_classes` 저장·표시만, 라우팅 미적용 | 모델·공급자 허용 등급 모두 | 수정됨 C51-11(VERIFY:59) |
| M34 | `app/services/llm.py:137` | 라우팅 규칙 `timeout_ms` 미적용 — 콘솔 5초, 실제 최악 수 분 | 시도마다 `asyncio.wait_for` + 전체 마감 | 미검증 |
| M35 | `app/services/llm.py:221-225` | 한도·예산이 예약 없는 사전 검사 → 동시 요청으로 예산 초과 | 예상 토큰 예약 가산, 폴백 후보마다 검사 | 미검증 |
| M36 | `app/services/a2a_gateway.py:568` · `app/services/a2a_server.py:283` | 푸시 URL 검사의 동기 `getaddrinfo` 가 이벤트 루프에서 → 느린 DNS 로 워커 정지 | 스레드풀 | 수정됨 C51-03(VERIFY:24·50) |
| M37 | `app/services/a2a_gateway.py:639-646`·`:834-836` · `app/services/a2a_server.py:119` · `app/core/http.py:119-120` | 등록 시점 검사만, 전달 시 재검사 없음·해석 실패 통과 → DNS 변경으로 내부 POST | 전달 직전 재검사(스레드풀), 해석 실패 거부 | 수정됨 C51-03·C51-09(VERIFY:47·51) |
| M38 | `app/core/http.py:56-73` | 가드 해석과 실제 연결 해석이 다름 + 60초 캐시 → DNS 재바인딩, 차트에 egress NetworkPolicy 없음 | 해석 IP 고정 연결 또는 egress NetworkPolicy | 기존 결정(VERIFY:12 SEC2-02) |
| M39 | `app/core/http.py:36-37` | Alibaba IMDS `100.100.100.200` 미차단 | 차단 목록 추가 | 수정됨 C51-09(VERIFY:62) |
| M40 | `app/services/a2a_gateway.py:571` | 원격 에이전트 푸시 콜백 기준이 내부 `GATEWAY_URL` → 클러스터 밖 에이전트 푸시 불가 | 사설망만 `gateway_url`, 그 밖 `public_base_url` | 수정됨 C51-04(VERIFY:36) |
| M41 | `app/services/a2a_gateway.py:573`(+`:651-655`) | `_save_push` 가 아무 taskId 로 행 생성 → 쓰레기 행이 감시기 순환을 희석·영구 폴링 | 업스트림 성공 후 저장, 없는 행은 -32001, 연속 실패 시 failed | 미검증 |
| M42 | `app/services/a2a_gateway.py:817-827`·`:874` | 푸시·폴링 결과가 `_sanitize_in` 없이 저장·중계(간접 인젝션) | 같은 소독 | 수정됨 C51-07(VERIFY:48) |
| M43 | `app/services/a2a_gateway.py:869-874` | 감시기가 blocked 에이전트·비활성 사용자에도 위임 토큰으로 폴링 | 에이전트 상태·사용자 활성 확인 | 수정됨 C51-07(VERIFY:49) |
| M44 | `app/services/a2a_grpc.py:52`·`:59-60` | `grpcs://` 아니면 평문 HTTP/2 로 authorization metadata 전송 | prod 는 `grpcs://` 만 | 기록(VERIFY:68) |
| M45 | `app/services/a2a_server.py:274-276` | 로컬 취소가 실행기 Task 만 끊고 오케스트레이션 run 은 계속(비용·부작용) | `on_cancel` 훅 또는 `CancelledError` 에서 `cancel(run_id)` | 미검증 |
| M46 | `app/api/a2a.py:530-531` | `PATCH {"status":"active"}` 가 `a2a:write` 만으로 등록 승인(E05) 우회·보안 담당의 비활성화 되돌림 | `active` 전환은 `catalog:approve` + 카탈로그 승인 상태 | 수정됨 C51-13(VERIFY:72) |
| M47 | `app/api/a2a.py:534-544` | auth 병합 잔여: `scheme`·`tls.verify`·`scopes`·`push_verify` 변경과 객체 수준 인가 부재 → client_secret 을 헤더로 원격에 | auth pydantic 스키마, 위 키도 `check_destination` 규칙, 소유 팀만 | 부분 — 전송처 키는 수정(VERIFY:73, 번호 미기재), `scheme`·`tls.verify`·`push_verify`·객체 인가는 미검증 |
| M48 | `app/api/a2a.py:631-632` | 특권 판정(`a2a:write`+`gateway:read`)을 developer 가 보유 → 모든 사용자 A2A Task 이력 열람 | 감사·운영 권한으로 좁히거나 팀 범위 | 기록(VERIFY:74) |
| M49 | `app/api/a2a.py:458-461` | 카드 조회가 사설망 GET + 오류 상태 반환(포트 스캔 오라클), `tls.verify=false` 수용 | 운영에서 `internal_url` 거부, 사설망은 `gateway:write` | 기록(VERIFY:74) |

### 5.5 [위험:중간] — 앱 조립·관측·CLI·배포
| # | 위치 | 결함 · 실패 시나리오 | 최소 수정 | 처리 |
|--:|---|---|---|---|
| M50 | `app/main.py:82-86` | 헬스 루프가 DB 오류 한 번에 종료, 리스 예외 시 fail-open | 주기 단위 예외 처리, 리스 예외 시 건너뜀 | 수정됨 C51-10(VERIFY:56) |
| M51 | `app/main.py:112` | 스케줄러가 host·observe 일 때만 기동 → gateway-only·registry-only 단독 배포에서 보존·승인 만료·레지스트리 작업 정지 | 조건 확대 또는 작업별 판정 | 기록(VERIFY:57) |
| M52 | `app/main.py`(미들웨어 부재) | 보안 응답 헤더·TrustedHost 없음 → 내장 IdP 로그인 클릭재킹, Host 기반 카드 URL | 헤더 미들웨어, TrustedHost | 수정됨 C51-02(VERIFY:15, 헤더) · TrustedHost 기록(VERIFY:57) |
| M53 | `app/api/observe.py:254-265` | `_overview_extra` N+1·RunEvent 전체 로드(인덱스 없음), 경보 평가마다 반복 | 집계 쿼리·인덱스, 경보에서 제외 | 기록(VERIFY:65) |
| M54 | `app/api/observe.py:338`·`:352-361` | `usage` 가 UsageRecord ORM 전체를 메모리 집계, `since` 하한 없음 | SQL `GROUP BY`, 기간 상한 | 기록(VERIFY:65) |
| M55 | `app/cli/nexus.py:342`·`:335` | cosign 검증 뒤 `up -d --build` 로 미검증 이미지 기동, `config --images` 실패 시 0/0 통과 | 서명 경로는 `--pull always`·digest, 실패·0개 중단 | 기록(VERIFY:67) |
| M56 | `app/cli/nexus.py:693` | `bundle import --verify` 기본 False — 번들 바꿔치기 이미지가 사설 레지스트리로 push | 기본 True + 명시 우회, 매니페스트 서명 | 기록(VERIFY:67) |
| M57 | `deploy/helm/agentnexus/templates/networkpolicy.yaml:31-36` | `ingressFrom` 지정 시 api↔api·데모·Prometheus 내부 호출 차단 | 같은 릴리스 컴포넌트 자동 허용 | 수정됨 C51-06(VERIFY:33) |
| M58 | `deploy/helm/agentnexus/templates/api.yaml:43` | `--forwarded-allow-ips "*"` → `X-Forwarded-For` 위조로 로그인 IP 정책·감사 IP 우회 | 값으로 신뢰 CIDR 지정 | 부분 → 설정화 C51-06(VERIFY:34) |
| M59 | `deploy/helm/agentnexus/templates/configmap.yaml:8-44` | `SEED_DEMO` 미배선 → `demo.enabled=false` 에도 trusted 데모 에이전트·상시 워크로드 시드 | `SEED_DEMO: demo.enabled` | 수정됨 C51-06(VERIFY:35) |

**집계**(VERIFY 08:55 판): [위험:높음] 7건 — 수정됨 4(H1·H3·H5·H7) · 기존 결정 1(H6) · 미검증 2(H2·H4). [위험:중간] 59건(M1-M59) — 수정됨 20 · 부분 2(M47·M58) · 기존 결정 5 + 부분 1(M17) · 기록 19 · 미검증 12(M14·M16·M18·M19·M20·M23·M25·M27·M34·M35·M41·M45).
VERIFY 가 다룬 위임 리뷰 항목(SEC2-01·05, CR2-01·04)과 [위험:낮음] 은 이 목록에 따로 싣지 않았다 — 각 보고서와 VERIFY 를 볼 것.

---

## 6. 다음 단계 / Next Steps (영향순)
1. **미검증 [높음] 2건**: H2(`_anx_` 주입) — `publish`·`import_entries` 입력에서 `_anx_` 접두 키 거부 + `_apply_version` 수정 + 등록 후 `ToolRule` 회귀 시험. H4(2단계 PATCH) — `secret_ref` 재부착을 전송처 변경으로 취급하는지 C51-08 이후 코드로 재확인하고 시험 추가.
2. **카탈로그 경로 [중간]**(M16-M20): 미러 독 항목 격리(M18)는 예약 보존까지 멈추므로 우선, 등록 옵션 `endpoint` 에 `check_destination` 적용(M16), auto_approve 항목의 `_meta.auth` 전송처 보류(M17).
3. **나머지 미검증 [중간]**: 기한 직후 결정(M14), A2A 로컬 취소(M45)·쓰레기 Task 행(M41), LLM 규칙 타임아웃·예산 예약(M34·M35), Inspector `data_class`(M23)·operationId 없는 operation(M25), AS 미고정 수용(M27).
4. **Epic 51 수정분 재리뷰**: 스냅샷 이후 바뀐 14개 파일·Helm 을 C51-01~15 기준으로 다시 읽고(특히 H1 의 PAT 허용 유지, M47 의 `scheme` 미포함), §1.4 의 줄 번호 어긋남·미완 보고서(`mcp_modern.py.md`)를 바로잡는다.

## 부록 / Appendix
- A. 대상 목록 원본: `scratchpad/files.txt`(38개), 스냅샷 해시 `scratchpad/snapshot-start.sha`, 시각 `scratchpad/snapshot-start.time`.
- B. 1차 리뷰: [`.review/CODE-REVIEW-Lv2/INDEX.md`](../../CODE-REVIEW-Lv2/INDEX.md) · 위임: `.review/delegated/{code-review,security-review}.md`, `.review/round2/delegated/{code-review,security-review}.md` · 처리: [`.review/round2/VERIFY.md`](../VERIFY.md).
- C. 용어(Level 2 선택): requester 모드(요청자 본인이 승인자인 기본 규칙), 전송처 키(자격 증명이 가는 주소를 정하는 `auth` 키 — `secrets.DESTINATION_KEYS`), DISCOVERABLE(일반 사용자·익명에게 보이는 카탈로그 상태 `approved`·`active`), 독 항목(poison entry — 처리할 때마다 실패해 뒤 작업을 막는 입력).
