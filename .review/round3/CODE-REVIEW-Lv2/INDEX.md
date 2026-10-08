# 코드 단위 리뷰 보고서 (3차) / Line-by-Line Code Review — Round 3
> 분석일 / Date: 2026-10-08 · 대상 / Target: `/Users/wise/Developer/wise-AgentNexus` — 11~12차 구현(D20·D21, Epic 52~58·60) 변경분
> 리뷰 레벨 / Level: 2 — ~3년 주니어 인수인계 수준 · 파일 / Files: D20·D21 표시가 있는 파일 35개(표시 64개) 전부 + 근거용 인접 파일(§1.2 목록) → 보고서 13건(파일별 12 + [INFRA.md](INFRA.md) 1) · 분석 / Analyst: Claude
> 방법: `wds-review` code-method(`~/.claude/plugins/cache/wise-vibe/wise-vibe/2.0.0/skills/wds-review/references/code-method.md`) Step 1–8, Level 2 매트릭스 · **블록 모드** — 표시된 hunk 와 판단에 필요한 주변 코드만 인용·분석(파일 전체 라인 워크 아님) · 인용 코드는 스냅샷 원문에서 기계적으로 복사(줄 번호 = 스냅샷 줄 번호) · 2차 보고서(`.review/round2/CODE-REVIEW-Lv2/`)·`.review/round2/VERIFY.md`·`test/impl/{52nd..58th,60th}/` 와 대조
> **범위 고지(scope note).** 이번 리뷰는 **D20·D21 hunk 한정**이다 — 대상은 `grep -rn "D20-\|D21-" app/ deploy/ Dockerfile docker-compose*.yml migrations/` 로 찾은 표시와, 같은 차수에 표시 없이 바뀐 블록(키 회전 `app/core/keys.py` 전체, 원격 A2A 상한 `app/services/a2a_gateway.py:351-372` 등 — `test/impl/*/scenario.md` 범위 표로 확인)이다. 표시 밖의 코드는 hunk 판단에 필요할 때만 읽었고, 그때 찾은 결함은 "인접 코드" 로 구분했다. 파일 전체 품질 등급이 아니라 **변경분과 그 경계의 등급**이다.
>
> **스냅샷 고지.** 줄 번호·인용은 **2026-10-08 12:23:16 KST 스냅샷** 기준(sha1 목록: 세션 scratchpad `r3/snapshot.sha`, 대상 37개). 리뷰 중 소스는 수정되지 않았다(요청 전제).

---

## 1. 변경 영역 인벤토리 / Inventory of the Changed Area

### 1.1 범위 — 결정 항목별 (PRD `PRD.md:101-102`, `docs/implementation-plan.md:59-67`)
| 결정 | Epic · impl | 내용 | 표시 수 | 리뷰 위치 |
|---|---|---|--:|---|
| D20-① 모델 게이트웨이 정확성 | 52 · `test/impl/52nd` | 규칙 `timeout_ms` 시도별 적용, 토큰·예산 예약, `any` 예산 팀 없는 주체, 클라이언트 키 예산(migration 0020) | 8 | [llm.py.md](app/services/llm.py.md) · [ratelimit.py.md](app/services/ratelimit.py.md) |
| D20-② 신원·레지스트리 경계 | 53 · `53rd` | audience 허용 목록, admin 부여·IdP admin 매핑은 admin 만, IdP 삭제 세션 종료, 새 버전 소유자, 미러 자동 승인 보류, `push_verify` 통제, TOTP 1회 | 8 | [identity.py.md](app/api/identity.py.md) · [registry.py.md](app/services/registry.py.md) · [a2a_gateway.py.md](app/services/a2a_gateway.py.md) |
| D20-③ 운영 배포 보강 | 54 · `54th` | a2a-grpc gRPC 헬스·PDB·존 분산, `checksum/secret`, compose prod 최소 env, 잠금 파일·digest, 번들 반입 서명 기본, 검증 이미지로 설치 | 12 | [a2a_grpc.py.md](app/services/a2a_grpc.py.md) · [nexus.py.md](app/cli/nexus.py.md) · [INFRA.md](INFRA.md) |
| D20-④ 관측·카운터 성능 | 55 · `55th` | 개요 KPI·usage SQL 집계, listen 보유자 슬롯, 도구 위험도 단어 분류 | 9 | [observe.py.md](app/api/observe.py.md) · [mcp_modern.py.md](app/services/mcp_modern.py.md) · [registry.py.md](app/services/registry.py.md) |
| D21-① 원격 A2A·푸시 경계 | 56 · `56th` | 푸시 JWT `iss`·`jti`·본문 해시 필수, 원격 응답 상한·비 JSON, history 상한, 카드 URL = `PUBLIC_BASE_URL` | 4 | [a2a_gateway.py.md](app/services/a2a_gateway.py.md) |
| D21-② 구성요소 단독 배포 | 57 · `57th` | 스케줄러 상시 기동, 구성요소별 작업 | 2 | [scheduler.py.md](app/services/scheduler.py.md) |
| D21-③ 키 회전 | 58 · `58th` | 서명 키 유예·보관 공개키, 암호화 키 재암호화·이전 키 제거, 관리 API·CLI, env 모드 `*_PREVIOUS` | 10 | [keys.py.md](app/core/keys.py.md) · [admin.py.md](app/api/admin.py.md) · [nexus.py.md](app/cli/nexus.py.md) · [INFRA.md](INFRA.md) |
| D21-⑤ 남은 기록 항목 | 60 · `60th` | 신뢰 어휘, 발견 API SQL 페이지, mTLS verify, 토큰 캐시 폐기, OpenAPI 시크릿, CLI 0600, OTLP 상한, A2A 사용량, 웹훅 토큰 암호화, 실행기 오류 원문 | 11 | 여러 보고서(§6 표) |

D21-④(3차 품질 검토)가 이 보고서다(Epic 59, `docs/implementation-plan.md:66` — TODO).

### 1.2 파일 목록 (번호 = 보고서 번호)
| # | 보고서 | 대상 파일 (스냅샷 LOC, 2차 스냅샷 대비) | 표시 hunk | 모드 |
|--:|---|---|---|:--:|
| 01 | [app/core/keys.py.md](app/core/keys.py.md) | `app/core/keys.py`(232, 신규 재작성) + `app/core/config.py:31-35` + `app/core/security.py:28-38` | D21-③ 3 + 1 + 1 | block |
| 02 | [app/api/admin.py.md](app/api/admin.py.md) | `app/api/admin.py`(588) 키 회전 절 L522-588·백업 L275-306 (+ `app/api/ops.py:858`) | D21-③ 2 + 1 | block |
| 03 | [app/services/llm.py.md](app/services/llm.py.md) | `app/services/llm.py`(471, 426→) + `app/api/llm.py:91-120`(호출처) + `app/models.py:753` + `migrations/versions/0020_usage_client_key.py`(26) | D20-① 3 + 1 + 1 | block |
| 04 | [app/services/ratelimit.py.md](app/services/ratelimit.py.md) | `app/services/ratelimit.py`(403, 301→) | D20-①·④ 5 | block |
| 05 | [app/api/observe.py.md](app/api/observe.py.md) | `app/api/observe.py`(489, 475→) | D20-④ 3, D21-⑤ 1 | block |
| 06 | [app/services/a2a_gateway.py.md](app/services/a2a_gateway.py.md) | `app/services/a2a_gateway.py`(956, 886→) + `app/api/a2a.py`(751) 카드·푸시·PATCH + `app/services/a2a_server.py:175-187` | D20-②·D21-①⑤ 5 + 4 + 1 | block |
| 07 | [app/services/a2a_grpc.py.md](app/services/a2a_grpc.py.md) | `app/services/a2a_grpc.py`(309, 277→) + `app/grpc_server.py`(46) | D20-③ 1 + 1 | block |
| 08 | [app/services/mcp_modern.py.md](app/services/mcp_modern.py.md) | `app/services/mcp_modern.py`(507, 500→) listen + `app/api/mcp.py`(791, 778→) listen·Inspector·OpenAPI 시크릿 | D20-④ 1 + 1, D21-⑤ 1 | block |
| 09 | [app/api/identity.py.md](app/api/identity.py.md) | `app/api/identity.py`(519, 490→) + `app/api/idp.py`(454, 421→) | D20-② 4 + 1 | block |
| 10 | [app/services/registry.py.md](app/services/registry.py.md) | `app/services/registry.py`(591, 575→) + `app/api/catalog.py`(758) + `app/services/governance.py`(350)·`app/services/review.py`(199) 분류 hunk | D20-②④, D21-⑤ 2 + 1 + 2 + 1 | block |
| 11 | [app/services/scheduler.py.md](app/services/scheduler.py.md) | `app/services/scheduler.py`(145) + `app/main.py`(237, 203→) lifespan | D21-② 1 + 1 | block |
| 12 | [app/cli/nexus.py.md](app/cli/nexus.py.md) | `app/cli/nexus.py`(804, 768→) | D20-③ 2, D21-③ 1, D21-⑤ 1 | block |
| 13 | [INFRA.md](INFRA.md) | `deploy/helm/agentnexus/templates/{a2a-grpc,api,demo,secret}.yaml`·`values.yaml`·`deploy/helm/check-rendered.py`·`docker-compose.yml`·`docker-compose.prod.yml`·`Dockerfile` | D20-③·D21-③ 9 | block(요약) |

- 근거용으로 함께 읽은 인접 파일(인용·`path:line` 근거만, 등급 없음): `app/core/auth.py`(외부 IdP 토큰 검증), `app/services/totp.py`, `app/services/mcp_gateway.py`(AS 발견·교환), `app/services/orchestrator.py`(승인 후 재개), `app/services/secrets.py`, `app/builtin/mcp_auth.py`, `deploy/helm/agentnexus/templates/scaling.yaml`·`configmap.yaml`·`ci/ha-values.yaml`.
- `app/services/tls.py:76`(D21-⑤ 1건)은 짧아 §6 에서만 다룬다. 디렉터리 개요(`*_Abstract.md`)는 hunk 한정 범위라 만들지 않았다(보고서끼리 링크하지 않으므로 끊긴 링크 없음).


### 1.3 보고서 품질 점검 (2026-10-08, 자동)
- 인용 코드 블록 97개는 초안의 `path + 줄 범위` 표기에서 스냅샷 원문을 그대로 복사해 만들었다(손으로 옮겨 적지 않음 — 2차 §1.4 의 줄 번호 어긋남 재발 방지).
- 본문의 `path:line` 인용 612개(같은 줄의 `:N` 후속 인용 포함)와 보고서 간 상대 링크를 스크립트로 대조 — 파일 존재·줄 범위 위반 0건. 남은 표기 5건은 의도한 묶음 표기(`test/impl/{52nd..58th,60th}/` 등)다.
- 위험 bullet 수(§2)는 렌더 결과에서 다시 세어 표와 일치함을 확인했다.
---

## 2. 파일별 위험 표 / Per-file Risk
등급은 각 보고서 "파일 평가" 의 위험 등급. Watch-outs 는 라인 워크 본문의 `- [위험:…]` bullet 수(교차·이월 표기 포함 — 중복을 합친 목록은 §5).

| # | 보고서 | 등급 | Watch-outs 높음/중간/낮음 | 등급 사유 |
|--:|---|:--:|:--:|---|
| 09 | [identity.py + idp.py](app/api/identity.py.md) | **높음** | 1/3/3 | H1 IdP 검증 설정 변경으로 admin 위조 · M1 TOTP 재사용 · M8 비 admin 역할 상승 · M12 대상 토큰 스코프 |
| 03 | [llm.py](app/services/llm.py.md) | **높음** | 1/2/5 | H2 스트림 단절 시 정산·기록 누락(호출처) · M9 출력 토큰 미예약 · M13 폴백 대상 부재(인접) |
| 04 | [ratelimit.py](app/services/ratelimit.py.md) | **높음** | 1/0/5 | H2 근본 원인 — 예산 예약이 미끄러지는 TTL 카운터 |
| 12 | [nexus.py](app/cli/nexus.py.md) | **높음** | 1/1/4 | H3 번들 검증 키가 번들 안 · M11 태그 검증 vs 로컬 이미지 |
| 10 | [registry.py + catalog.py](app/services/registry.py.md) | 중간 | 0/3/3 | M5 자동 승인 + PRM TOFU · M6 미러의 로컬 항목 선점 · M2 위험도 분류 거짓 음성 |
| 06 | [a2a_gateway.py](app/services/a2a_gateway.py.md) | 중간 | 0/2/9 | M3 SSE 줄 상한이 버퍼 뒤 · M4(교차) 웹훅 토큰 재암호화 누락 |
| 02 | [admin.py](app/api/admin.py.md) | 중간 | 0/1/6 | M4 재암호화 범위 누락 |
| 01 | [keys.py](app/core/keys.py.md) | 중간 | 0/1/9 | 키 수명 핵심 — [낮음] 다수 + M4 교차(`drop_previous_encryption` 계약) |
| 05 | [observe.py](app/api/observe.py.md) | 중간 | 0/1/6 | M10 자식 스팬 전체 집계가 경보 주기마다 |
| 11 | [scheduler.py + main.py](app/services/scheduler.py.md) | 중간 | 0/1/1 | M7 분리 배포에서 gateway 프로세스가 Host 실행 재개 |
| 13 | [INFRA.md](INFRA.md) | 중간 | 0/1/9 | 키 회전 운영 공백(L7·L27) + M11 교차 |
| 08 | [mcp_modern.py + mcp.py](app/services/mcp_modern.py.md) | 낮음 | 0/1/4 | hunk 자체는 [낮음] — [중간] 1 은 2차 M22 이월 |
| 07 | [a2a_grpc.py + grpc_server.py](app/services/a2a_grpc.py.md) | 낮음 | 0/0/2 | [낮음]만(공개 readiness 증폭·TLS 프로브) |

**분포**: 높음 4 · 중간 7 · 낮음 2. bullet 합 높음 4(고유 3) · 중간 17(고유 13 + 2차 이월 1) · 낮음 66. 고유 목록은 §5 — [위험:높음] 3건, [위험:중간] 13건, [위험:낮음] 35건.

---

## 3. 위험 핫스팟 Top-5 / Hotspots
1. **신원·인가 경계 — D20-② 의 우회.** 이미 admin 으로 매핑된 외부 IdP 의 `jwks_uri`·`internal_url`·클레임 이름을 `identity:write`(보안 담당)가 바꿔 admin 토큰을 위조(H1, `app/api/identity.py:224-247` ↔ `app/core/auth.py:126-164`, `:251-259`). 같은 경계에서 TOTP 1회 키가 검증 정규화와 달라 공백만 넣으면 재사용(M1, `app/api/idp.py:149` ↔ `app/services/totp.py:27`), admin 외 역할 자기 부여(M8), 등록 대상 Access Token 스코프 무제한(M12).
2. **모델 비용 통제 — D20-① 예약의 생애.** 스트림 단절 시 `finalize` 가 돌지 않아 사용량이 기록되지 않고(2차부터 미해결), 새 예산 예약은 미끄러지는 TTL 카운터라 바쁜 팀에서 영영 풀리지 않아 팀 전원 429(H2, `app/api/llm.py:91-120` + `app/services/ratelimit.py:340`). 예약이 출력 토큰을 담지 않음(M9), 폴백 모델 부재 시 한도 무시(M13), 커밋 전 정산(L10), 분 창 경계 반환(L8).
3. **공급망 — D20-③ 의 검증이 실제 대상을 묶지 못함.** 번들 반입 서명 검증이 번들 안 `cosign.pub` 로 검증해 바꿔치기 번들이 기본 검증을 통과(H3, `app/cli/nexus.py:742-748`). 설치는 태그를 검증하고 로컬 이미지·로컬 빌드로 기동할 수 있음(M11, `app/cli/nexus.py:339-347` + `docker-compose.yml:4-6`). 보관 공개키는 폐기 수단이 없음(L2).
4. **카탈로그 신뢰 — "승인됨" 이 사람 승인을 뜻하지 않는 경로.** 외부 인가 보류가 메타데이터 키에만 의존해 `type: oauth2` 만 적은 항목이 자동 승인되고, 등록 때 업스트림 PRM 의 AS 를 TOFU 로 저장 → 사용자 원본 토큰이 외부로(M5). 미러 동기화가 로컬 항목 버전을 선점·자동 승인(M6). 도구 위험도 단어 분류가 `deleteuser`·`sendmail`·`deletes` 를 low 로 분류해 사람 승인 없이 허용(M2 — 카탈로그 심사 제안은 일부 퇴행).
5. **키 회전 운영 — D21-③ 이 같은 차수의 새 암호문을 모름.** 재암호화 목록에 D21-⑤ 의 웹훅 토큰 암호문이 없어 이전 키 삭제 뒤 A2A 웹훅 중계가 조용히 끊김(M4). env 모드 롤링 중 신·구 파드 키 불일치(L7), 권장 경로(`existingSecret`)에서 `checksum/secret` 이 고정되어 회전 후 자동 재기동 없음(L27).

그 밖 [중간]: 원격 SSE 단일 거대 줄로 워커 OOM(M3), 분리 배포에서 gateway 프로세스가 Host 실행 재개(M7), 개요 KPI 의 전체 스팬 집계가 경보 주기마다(M10).

---

## 4. 2차 리뷰 대비 / vs 2nd Review (D20·D21 이 다룬 항목)
| 2차 항목 | 3차 상태 | 근거 |
|---|---|---|
| H2 `_anx_` 심사 기록 주입 | 해결(D19-④, 이번 범위 확인) | `app/services/registry.py:164-166` |
| H6 외부 AS 에 사용자 원본 토큰(기존 결정 — 카탈로그 승인 전제) | 전제 약화 | 자동 승인 + PRM TOFU 로 사람 승인 없이 외부 AS 고정(M5) |
| M3 `identity:write` 가 admin 부여 | 부분 해결 + 우회 | admin 직접 부여 차단 `app/api/identity.py:129-130`. H1·M8 |
| M4 IdP 삭제 세션 | 해결 | `app/api/identity.py:266-277` |
| M5 Access Token audience | 부분 해결 | `app/api/identity.py:474-481`. 스코프 M12 |
| M7 TOTP 재사용 | 부분 해결(우회 가능) | `app/api/idp.py:148-149`. M1 |
| M13 새 도구 자동 허용 | 미해결(VERIFY:77) + 분류 거짓 음성 증가 | `app/services/governance.py:60`, `:79-83`. M2 |
| M17 auto_approve 항목 `_meta.auth` | 부분 해결 | `app/services/registry.py:209-211`. M5 |
| M19 게시자 ≠ 소유자 버전 선점 | 부분 해결 | `app/api/catalog.py:531-534`. M6·L25 |
| M20 익명 `/v0/servers` 전체 로드 | 부분 해결 | `app/services/registry.py:408-410`. L24 |
| M22 Inspector 가상 주체 팀 | 미해결(VERIFY:71 기록) | `app/api/mcp.py:456`, `:477` |
| M23 Inspector `data_class` | 부분 해결 | 실제 호출 `app/api/mcp.py:511`, 드라이런 L21 |
| M31 증감 카운터 TTL 연장 | 해결(listen) · 패턴이 예산에 재사용 | `app/services/ratelimit.py:64-83`. H2 |
| M32 `any` 예산·client_key 예산 | 부분 해결 | `app/services/ratelimit.py:251-254`, `:300-304`. L9 |
| M34 규칙 `timeout_ms` 미적용 | 해결 | `app/services/llm.py:265-273` |
| M35 예약 없는 사전 검사 | 부분 해결 | `app/services/ratelimit.py:326-352`. H2·M9 |
| M41 아무 taskId 로 행 생성 | 해결(D19-④) | `app/services/a2a_gateway.py:694-695` |
| M47 A2A auth 병합 잔여(`push_verify` 등) | 부분 해결 | `app/api/a2a.py:556-563` |
| M51 스케줄러가 host·observe 일 때만 | 해결 · 부작용 | `app/main.py:118-119`. M7 |
| M52 Host 기반 카드 URL | 해결(카드) | `app/api/a2a.py:254` |
| M53·M54 관측 전체 로드 | 부분 해결 | `app/api/observe.py:262-275`, `:361-370`. M10·L13 |
| M55 검증 뒤 `--build` | 부분 해결 | `app/cli/nexus.py:347`. M11 |
| M56 번들 반입 검증 기본 False | 형식상 해결 · 실질 미해결 | `app/cli/nexus.py:721-723`, `:742`. H3 |
| 2차 llm.py.md L501(교차) `_sse` 의 `_final` 이 `finally` 아님 | 미해결 + 영향 확대 | `app/api/llm.py:94-120`. H2 |
| 2차 INFRA.md L84·L85·L144·L350·L351·L421 | 해결(L84 은 평문만, L144 는 existingSecret 공백) | [INFRA.md](INFRA.md) 표 |

---

## 5. 수정 대상 지적 목록 / Findings for Fixers
- 줄 번호는 **스냅샷 기준**. 같은 근본 원인은 한 행으로 합치고 관련 위치를 함께 적었다. "인접" = D20·D21 표시 밖이지만 hunk 판단 중 확인한 코드.
- 심각도: **High** = 인증·인가 우회, 공급망 무결성 붕괴, 또는 일상 동작으로 서비스 전체가 막히는 결함 · **Medium** = 조건이 붙는 보안·비용·가용성 결함 · **Low** = 방어적·운영·정확성.

### 5.1 High
| # | 위치 | 실패 시나리오 | 최소 수정 |
|--:|---|---|---|
| H1 | `app/api/identity.py:224-247`(검증 L233-234 는 `role_mapping` 이 본문에 있을 때만) ↔ `app/core/auth.py:126-135`, `:144-164`, `:251-259` | 관리자가 IdP 를 `{"AgentNexus-Admins": "admin"}` 으로 매핑 → 보안 담당(`identity:write`, admin 아님)이 `PATCH /api/v1/identity/idps/<id> {"jwks_uri": "https://attacker/jwks.json"}`(200, 캐시도 비워 줌) → 자기 키로 `iss`=그 IdP, `groups`=`["AgentNexus-Admins"]` JWT 서명 → Bearer 제시 → 외부 사용자 upsert 로 `roles=["admin"]`. `internal_url`·`groups_claim`·`roles_claim` 변경도 같은 효과 | IdP 의 현재·새 `role_mapping` 에 호출자 권한 밖 역할(특히 `admin`)이 있으면 모든 패치를 admin 만. 최소한 `jwks_uri`·`internal_url`·`audience`·`client_id`·`groups_claim`·`roles_claim` 은 admin 만. 회귀 시험 추가 |
| H2 | `app/api/llm.py:91-120`(`_final` 이 `try` 뒤, `finally` 아님; 비스트림 `:73-74` 는 `ApiError` 만) + `app/services/ratelimit.py:340`(매 요청 `counter_add` 가 TTL 연장), `:55-62`, `:163-174` | 채팅 UI "생성 중지"·네트워크 단절마다 ① 사용 기록·토큰 창 기록이 빠져 예산·한도 우회(2차부터) ② 그 요청의 예산 예약이 `usdhold` 에 남고, 같은 범위의 다음 요청마다 TTL 이 300초로 연장되어 바쁜 팀에서는 영구 누적 → 팀 전원 429 "예산 초과 … 진행 중 예약". README 의 "300초 뒤 풀림"(`README.md:545`)은 활동 중 성립하지 않음 | `_sse` 를 `try/finally` 로, `finally` 에서는 `await` 없이 동기 정산(또는 `anyio.CancelScope(shield=True)`), 끊긴 스트림은 받은 청크 기준 추정 사용량 기록. 예산 예약은 예약 건별 고정 만료 구조(보유자 ZSET 재사용) |
| H3 | `app/cli/nexus.py:742-748`(검증 키 = 번들 안 `cosign.pub`) → `:776-785`(load·push), `:786-790`(기본 복원) | 에어갭 반입 경로에서 번들을 바꿔치기한 공격자가 자기 키로 `cosign.pub`·`manifest.json.sig` 를 함께 교체 → 기본 `--verify` 통과 → 공격자 이미지가 사설 레지스트리로 push 되어 클러스터에서 실행. 2차 M56 이 막으려던 시나리오가 기본값에서 그대로 성립 | 검증 키는 번들 밖에서만: `--cosign-key` 필수 또는 운영자 설정(`COSIGN_PUBLIC_KEY`·`install.yaml`) 지문과 번들 키 지문 일치 시에만 사용. 시험(`test/test_deploy_hardening.py:84`)도 외부 키로 |

### 5.2 Medium
| # | 위치 | 실패 시나리오 | 최소 수정 |
|--:|---|---|---|
| M1 | `app/api/idp.py:148-149` ↔ `app/services/totp.py:27` | 검증은 안쪽 공백 제거, 1회 키는 `strip()` 만 → 엿본 코드 `123456` 을 사용자가 쓴 뒤에도 `"123 456"` 으로 90초 안에 재로그인 | 같은 정규화로 키 생성, 더 낫게는 맞은 시간 단계로 `totp-step:<user>:<step>` 1회 |
| M2 | `app/services/governance.py:34-37`, `:60`, `:79-83` · `app/services/review.py:29-33` | 단어 단위 비교라 `deleteuser`·`sendmail`·`flushdb`(붙여 쓴 소문자), `deletes`·`deleted_items`(활용형), `overwriteFile`·`upsertRecord`·`saveFile`·`submitOrder`·`eraseDisk`·`shutdownServer`·`clearCache`(목록 밖) → low → 기본 규칙 허용·승인 불필요(게이트웨이는 전에도 구분자 경계라 같은 공백, 카탈로그 심사 제안은 부분 문자열 → 단어 전환으로 앞 두 부류·`overwriteFile` 이 low 로 퇴행) | 단어별 동사 접두 일치 + 어미 제거 + 동사 보강, 동사 미검출 도구는 untrusted 서버에서 medium. 위 이름들로 시험 |
| M3 | `app/services/a2a_gateway.py:724-728` | httpx `aiter_lines()` 가 줄바꿈까지 버퍼링한 뒤 길이를 검사 → 원격 에이전트가 줄바꿈 없는 수 GB `data:` 를 흘리면 워커 OOM, 같은 워커의 모든 요청 중단 | `aiter_bytes()` + 자체 줄 리더로 미완성 줄 버퍼가 8 MiB 넘으면 즉시 오류, 스트림 총량·시간 상한 |
| M4 | `app/api/admin.py:546`(재암호화 목록) ↔ `app/services/a2a_gateway.py:698-700`, `:899-902` · `app/services/mcp_modern.py:212` | 암호화 키 회전 → 재암호화 `failed: 0` → `drop_previous` → 진행 중 A2A Task 의 웹훅 토큰(`push_config.token_enc`)을 풀 수 없어 중계가 백그라운드 예외로 조용히 중단. MCP 요청 상태 토큰(≤600초)도 일시 실패 | 재암호화 목록에 `A2ATask.push_config.token_enc` 추가, 중계 복호화 실패 로그, drop 은 회전 후 `STATE_TTL_S` 경과 뒤. 암호문 컬럼 등록부 + 누락 탐지 시험 |
| M5 | `app/services/registry.py:151-155`, `:209-213` ↔ `app/services/mcp_gateway.py:717-728`, `:144-154`, `:173` | 공개 레지스트리 auto_approve 미러에 `type: oauth2` 만 적은 서버 게시 → 외부 인가 아님으로 자동 승인 → 운영자가 등록 → 업스트림 PRM 의 AS 를 TOFU 저장 → 사용자 원본 플랫폼 토큰이 공격자 AS 로 | 보류 조건에 "oauth2 인데 AS 가 플랫폼 issuer 로 명시되지 않음" 추가, 첫 발견 AS 가 issuer 가 아니면 저장하지 않고 운영자 재탐색 요구 |
| M6 | `app/services/registry.py:465-477`(→ `approve` `:233-245`) | 미러 동기화가 같은 이름의 로컬(수동 게시) 항목에 버전을 추가하고 auto_approve 면 승인 → 항목 메타데이터·이후 등록이 외부 게시자의 `remotes` 로 | `import_entries` 에서 기존 항목의 `source != "mirror"` 또는 `source_id != source.id` 면 건너뛰기(`name_owned_elsewhere`) |
| M7 | `app/services/scheduler.py:123-125` → `app/services/orchestrator.py:955-957`, `:294-305` · `app/main.py:100` | Host + gateway-only 프로세스가 같은 DB 를 쓰는 분리 배포에서 gateway 프로세스가 승인 만료 리스를 잡으면 Host 의 오케스트레이션 실행을 자기 프로세스에서 이어 실행(자원·이벤트 경계 붕괴) | 재개 예약은 `settings.enabled("host")` 일 때만, 그 밖은 상태만 바꾸고 Host 복구 작업이 집어 가게 |
| M8 | `app/api/identity.py:125-130`(+ `:163-166`) | 보안 담당이 `PATCH /api/v1/identity/users/<자기>` `{"roles": ["security","operator"]}` → `admin:write`·`gateway:write`·`catalog:write` 등 운영자 권한 획득(2차 M3 수정안 앞부분 미반영) | 부여 역할마다 `role_perms(r) <= p.permissions()`(admin 은 예외) — `app/api/mcp.py:472-476` 과 같은 규칙. 사용자 결정 범위와 다르면 결정 기록 |
| M9 | `app/services/llm.py:239-241` | 토큰 예약 = 입력 토큰만, 예산 예약 = `max_tokens` 없으면 출력 0 → 입력 1k·출력 8k 요청 20개 동시면 분당 100k 한도에 예약 20k 로 모두 통과, 실제 180k | `estimated_tokens = 입력 + (max_tokens or 모델 기본 출력 허용치)`, 예산도 같은 허용치 |
| M10 | `app/api/observe.py:262-267`(+ `:472-480`) | 자식 스팬 최대값 서브쿼리에 시간 조건이 없고 `spans.parent_id` 인덱스도 없음(`app/models.py:828`) → 경보 평가(60초 주기)마다 보존 기간 전체 스팬 `GROUP BY`, 리스 TTL 초과 시 평가 중복 | 서브쿼리에 `kid.start_time >= since`, `(parent_id, trace_id)` 인덱스, 경보 경로에서 `_overview_extra` 제외 |
| M11 | `app/cli/nexus.py:339-347` + `docker-compose.yml:4-6` | 태그로 `cosign verify` 후 `up -d`(기본 pull 정책) → 같은 태그 로컬 이미지(미서명 `prod-build` 결과)나 `build:` 절의 로컬 빌드로 기동. 앱 이미지 이름이 레지스트리 접두 없는 로컬 태그라 실제로는 검증 대상이 엉뚱함. `config --images` 실패 시 "0/0" 통과 | 검증 결과 다이제스트로 오버라이드 파일(`image: repo@sha256:…`, `build:` 제거) + `up -d --pull always --no-build`, 목록 비면 중단, 이미지 저장소 설정화 |
| M12 | `app/api/identity.py:458-463`, `:500-504` | `tokens:issue` 보유자가 `aud=mcp:<서버>`·임의 스코프(`["jira:admin"]`) Access Token 을 직접 발급 → 플랫폼 JWKS 를 믿는 MCP 서버에 직접 제시해 게이트웨이 도구 규칙·사람 승인·감사 우회(그 서버에 닿을 수 있을 때) | `mcp:`·`a2a:` audience 면 `scopes ⊆ 그 대상 TokenExchangePolicy.allowed_scopes`(교환과 같은 규칙, `app/services/identity.py:191-193`), 정책 없으면 거부 |
| M13 | `app/services/llm.py:246-257`(인접) ← `app/services/ratelimit.py:361-365` | `on_exceed=fallback` 한도 초과인데 폴백 모델 행이 없거나 비활성(`_target` None) → 두 분기 모두 건너뛰어 원래(비싼) 모델로 한도 없이 진행 | `lim.fallback_model` 이 있는데 대상이 없으면 예약 반환 후 429 |

### 5.3 Low
| # | 위치 | 결함 · 시나리오 | 최소 수정 |
|--:|---|---|---|
| L1 | `app/core/keys.py:58-62`, `:20-23`, `:136`·`:142` | 세대 = `generation` mtime — 거친 mtime FS 에서 같은 틱 두 번째 회전 미감지. 호출마다 mkdir·stat 다수 | 세대 값을 파일 내용(카운터)로, 짧은 캐시, 한 번 읽은 튜플 재사용 |
| L2 | `app/core/keys.py:113-122`, `:147-153` · `app/core/config.py:34` · `deploy/helm/agentnexus/values.yaml:75` | env 이전 서명 키는 만료 없음·개인키 PEM 필수(공개키면 인증 전체 실패), 보관 공개키는 폐기 불가 → 유출 키로 서명한 "백업" 이 영구 통과 | 공개키 PEM 허용, env 이전 키 유예 시각, 폐기 kid 목록 |
| L3 | `app/core/keys.py:188-203`, `:212-214` | 회전 잠금 없음(동시 회전 시 새 키 유실), `secrets.prev.key` 를 umask 권한으로 만든 뒤 chmod | `flock`, `os.open(…, O_APPEND, 0o600)` |
| L4 | `app/api/admin.py:302` · `app/api/ops.py:858` | 백업 서명 검증에 `issuer="agentnexus-backup"`·필수 클레임 없음(감사 보고서 검증 `app/services/audit.py:237-238` 과 불일치) | iss·`require` 추가 |
| L5 | `app/api/admin.py:547-553` | 재암호화가 `yield_per` 로 읽으며 수정해 전 행이 세션에 남고 한 트랜잭션 — 대형 `log_entries` 에서 메모리·잠금·타임아웃 | 배치 커밋 + `expunge_all()` |
| L6 | `app/api/admin.py:533-536`, `:571-576` | 감사가 성공 뒤에만(재암호화 예외 시 회전 기록 없음), 넓은 `RuntimeError`, 안내하는 "README 키 회전" 절 없음(`README.md:250` 표 한 줄), 유예 계산이 서비스 쪽 TTL 상한(`app/services/identity.py:142`)과 분리 | 회전 직후 감사, 전용 예외, 절차 문서, 상수 공유 |
| L7 | `deploy/helm/agentnexus/templates/secret.yaml:10-13` + `templates/api.yaml:12` | env 모드 회전의 롤링 구간에 옛 파드가 새 kid·새 Fernet 키를 몰라 간헐 401·복호화 실패 | 2단계 배포(새 키를 먼저 검증·복호화 전용으로) 또는 "next" 키 env |
| L8 | `app/services/ratelimit.py:331`, `:376`, `:386` | 예약 반환·정산이 그 시점의 분 창에 적용 → 다음 분이 음수에서 시작해 한도 약 2배까지 | 예약에 슬롯 기록, 지난 슬롯 반환 생략 |
| L9 | `app/services/ratelimit.py:300-304` · `app/api/gateway_admin.py:496-498` | `any`+`*` 예산이 팀마다 전액, `any`+`per-subject` 가 팀 합계 — 버킷 의미와 다르고 현황 화면에 안 보임 | `*` = 전체 합, per-subject = 사용자 합, 화면 표시 |
| L10 | `app/services/llm.py:432-450` | `settle`(예약 반환·캐시 비움)이 사용 기록 커밋 전 → 5초간 이 비용이 빠진 합계 캐시 | 커밋 뒤 정산 |
| L11 | `app/services/llm.py:265-306` | 최악 = 규칙 시간 × 후보 × 시도(시드 30초면 180초) — 인그레스 타임아웃 뒤에도 시도·비용 계속 | 콘솔에 최악 시간 표시, 요청 단위 마감 |
| L12 | `app/api/observe.py:102-117` | OTLP 상한이 본문 전체 읽은 뒤, 구조 오류는 여전히 500, 파싱이 이벤트 루프 위 | `Content-Length`·스트림 상한, `_otlp_rows` 까지 `try`, 스레드풀 |
| L13 | `app/api/observe.py:267`, `:275` | 순서 없는 `limit(500)`(p95 흔들림), `run_events.ts·type` 인덱스 없음 | `ORDER BY`, 인덱스 |
| L14 | `app/services/a2a_gateway.py:260`, `:276-281` · `app/api/a2a.py:568` | `drop_tokens` 두 번째 조건이 실제 교환 키 `(sub, id)` 와 안 맞아 죽은 코드(시험은 합성 키 `test/test_remaining_items.py:55-61`), `("cc", id)` 와 sub `"cc"` 키 충돌, 커밋 전 폐기 | 키에 종류 접두, 조건 수정, 커밋 뒤 폐기, 실제 경로 시험 |
| L15 | `app/services/a2a_gateway.py:443-445` | artifacts 개수·크기 상한 없음(history 는 200) | 개수·총량 상한 |
| L16 | `app/api/a2a.py:298-302` | 인증 없는 푸시 수신이 본문 전체를 상한 없이 읽음 | 스트림 누적 상한 |
| L17 | `app/services/a2a_gateway.py:803-844` · `app/api/a2a.py:560` | D21 이전 issuer 없는 설정은 모든 푸시 401(이행 안내 없음), 모르는 kid 에 JWKS 재조회 없음, Valkey 장애 5초간 jti 재전송 가능, JWKS `http://` 허용 | 이행 점검, 모르는 kid 1회 갱신, prod https |
| L18 | `app/services/a2a_grpc.py:274-294` | 길이 1바이트 가정, 인증 없는 공개 readiness 가 api `/health`·DB 로 증폭, 요청마다 새 HTTP 클라이언트 | 결과 1~2초 캐시, 클라이언트 재사용, 길이 단언 |
| L19 | `app/grpc_server.py:21-38` · `deploy/helm/agentnexus/ci/ha-values.yaml:12` | 프로브는 항상 127.0.0.1, TLS 면 TCP 만이라 readiness 의미 상실 — HA 참조 프로파일이 TLS | 리슨 호스트로 접속, TLS `exec` 프로브 |
| L20 | `app/api/mcp.py:643-651` | 서버 행 없는 같은 이름 시크릿은 조용히 덮어씀, 확인-쓰기 경합, 게시 실패 시 시크릿 잔존 | 같은 이름 시크릿 409, 한 트랜잭션 |
| L21 | `app/api/mcp.py:498-503` | Inspector 드라이런이 클라이언트 `data_class` 원문으로 판정 | 서버 판정을 드라이런 앞으로 |
| L22 | `app/services/mcp_modern.py:504-507` | 끊김마다 동기 Valkey 반납이 이벤트 루프를 최대 0.5초 멈춤(의도된 선택) | `run_in_executor` 로 던지고 기다리지 않기 |
| L23 | `app/api/identity.py:250-277` | 비활성화·삭제의 사용자 선택 기준 불일치, 내장 issuer 와 같은 IdP 삭제 시 로컬 사용자 전원 로그아웃, 대형 IdP 에서 `IN` 파라미터 상한 | 공용 함수, 하위 질의 UPDATE, 내장 issuer 거부 |
| L24 | `app/services/registry.py:411-427` | 익명 `/v0/servers?search=` 는 여전히 전체 로드·파이썬 거름 | SQL 검색, 익명 요청 한도 |
| L25 | `app/api/catalog.py:532` | 소유자 없는 항목은 누구나 새 버전, 소유가 사람 단위 | `owner is None` 은 승인 권한자만, 팀 소유 검토 |
| L26 | `app/services/governance.py:56-59`, `:71-72` · `test/test_remaining_items.py:21-28` | 신뢰 어휘 수정으로 official·partner 의 `readOnlyHint` 가 이름과 무관하게 low(통제 완화 방향), 시험은 식 재작성·소스 문자열 단언 | 고위험 동사는 힌트로 low 까지 내리지 않기, `default_rules` 호출 시험 |
| L27 | `templates/api.yaml:20` · `templates/demo.yaml:21` · `templates/secret.yaml:1-11` · `values.yaml:1` · `ci/ha-values.yaml:6` | 권장 `existingSecret` 에서 `checksum/secret` 이 상수 → 시크릿·키 교체 후 자동 재기동 없음. `helm template` GitOps 는 렌더마다 키 재생성(+ 이제 매 동기화 재기동) | 절차서에 `rollout restart`·GitOps 는 existingSecret 필수, `rotationNonce` 값 |
| L28 | `docker-compose.prod.yml:18-19` | a2a-grpc TLS 경로를 넘기지만 인증서 볼륨 없음 → 경로 지정 시 재시작 반복 | 오버레이 예시·문서 |
| L29 | `templates/a2a-grpc.yaml:47-50` | readiness 가 api `/health`(DB) 에 묶여 DB 장애조치 순간 전 파드 NotReady | 완화된 readiness 또는 `failureThreshold` |
| L30 | `app/cli/nexus.py:49-50`, `:57` | 경로 기반 `chmod`(→ `fchmod`), 설정 디렉터리 0755 | `os.fchmod`, `mkdir(mode=0o700)` |
| L31 | `migrations/versions/0020_usage_client_key.py:18-20` | PostgreSQL 인덱스 생성이 `usage_records` 쓰기를 막음(대형 설치) | `postgresql_concurrently` |
| L32 | `app/api/identity.py:129` | 역할 기준이라 `identity:write` 로 좁힌 admin PAT 도 새 admin 생성 가능(스코프 축소 무력화) | `p.has("*")` 또는 대화형 세션 조건 |
| L33 | `app/cli/nexus.py:594`, `:719`, `:789` | CLI 가 `ROTATE`·`RESTORE` 확인을 대신 채움, 반입 기본값이 구성 복원 | 사용자가 확인 입력, 복원 기본 False |
| L34 | `app/services/a2a_gateway.py:358` | 응답 상한을 압축 해제 조각 단위로 검사 — 한 조각이 수십 MB 로 풀릴 수 있음 | `Accept-Encoding: identity` 또는 `aiter_raw` 병행 계수 |
| L35 | `app/services/ratelimit.py:107-113`, `:178` | 보유자 만료가 워커 시계 기준(파드 시계 차이만큼 오차), 예약 방식상 한 사람이 큰 `max_tokens` 동시 요청으로 팀 예산을 잠글 수 있음 | Valkey `TIME`, 주체별 동시 요청·예약 상한 |

**집계**: High 3 · Medium 13 · Low 35. 2차 이월 [중간] 1건(M22 Inspector 팀, VERIFY:71 기록)은 목록에서 제외.

---

## 6. 우선순위 목록 밖의 D20·D21 hunk / Other Marked Hunks
| 위치 | 표시 | 판단 | 보고서 |
|---|---|---|---|
| `app/services/tls.py:76-77` | D21-⑤ | 정확 — mTLS 필수 정책에서 `verify: false` 거부. 아래 `:85` 도 `is not False` 로 같은 판정(문자열 `"false"` 는 검증 유지 쪽) · 시험 `test/test_remaining_items.py:48-51` | (이 표) |
| `app/core/security.py:28-38` | D21-③ | kid 로 현재·유예 키 선택 — 정확 | [keys.py.md](app/core/keys.py.md) |
| `app/core/config.py:34-35` | D21-③ | 이전 키 env — L2 | [keys.py.md](app/core/keys.py.md) |
| `app/api/ops.py:858` | D21-③ | 격리 복구 시험의 보관 키 검증 — L4 | [admin.py.md](app/api/admin.py.md) |
| `app/models.py:753` · `migrations/versions/0020_usage_client_key.py` | D20-① | 정확 — L31(대형 PG 잠금) | [llm.py.md](app/services/llm.py.md) |
| `app/api/a2a.py:254`·`:557`·`:562`·`:568` | D21-①②⑤ | 카드 URL·push_verify·issuer 정확, `drop_tokens` L14 | [a2a_gateway.py.md](app/services/a2a_gateway.py.md) |
| `app/services/a2a_server.py:182` | D21-⑤ | 정확(원문은 로그, 클라이언트엔 참조 id) | [a2a_gateway.py.md](app/services/a2a_gateway.py.md) |
| `app/services/governance.py:25`·`:71` · `app/services/review.py:29` | D20-④·D21-⑤ | M2·L26 | [registry.py.md](app/services/registry.py.md) |
| `app/api/catalog.py:533` | D20-② | M6(반대 방향)·L25 | [registry.py.md](app/services/registry.py.md) |

---

## 7. 종합 분석 / Synthesis (Step 7)

### 7.1 반복 패턴 (3+ 파일)
1. **검사 기준과 사용 기준이 다르다.** TOTP 검증 정규화 vs 1회 키(M1, `app/api/idp.py:149` ↔ `app/services/totp.py:27`), 토큰 캐시 키 vs 폐기 조건(L14, `app/services/a2a_gateway.py:260` ↔ `:278`), 게시 메타데이터 판정 vs 등록 후 발견 AS(M5), 서명 검증 태그 vs 실행 이미지(M11), 번들 검증 키의 출처(H3), `any` 예산의 버킷 vs 집계 범위(L9), 역할 이름 vs 권한 집합(M8·L32). 공통 처방: 같은 함수·같은 키를 검사와 사용에 함께 쓰고, 시험은 **실제 경로가 만든 값**으로.
2. **정산·반납을 취소에 안전하지 않은 위치에 둔다.** `_sse` 의 `_final` 이 `finally` 밖(H2), A2A 사용량 기록이 `finally` 안의 `await`(`app/services/a2a_gateway.py:622-624`, `:765-766` — 취소 범위에서 재취소 가능). 같은 코드베이스의 정답: `app/services/mcp_modern.py:504-507`(동기 반납) + 고정 만료(D20-④).
3. **상한을 "다 읽은 뒤" 검사한다.** OTLP(`app/api/observe.py:102-104`), 원격 SSE 줄(`app/services/a2a_gateway.py:724-728`, M3), 푸시 수신(`app/api/a2a.py:300`), 압축 해제 조각(`app/services/a2a_gateway.py:358`). 바이트 스트림 누적 상한 헬퍼 하나를 `app/core/http.py` 에 두고 모두 그것을 쓰게.
4. **시험이 실제 경로 대신 합성 입력·소스 문자열을 본다.** 합성 캐시 키(`test/test_remaining_items.py:55-61`), 소스 문자열 단언(`:27-28`, `test/test_components.py:42-47`), 번들 안 공개키 전제(`test/test_deploy_hardening.py:84`), 커밋 → 정산 순서(`test/test_model_gateway2.py:96-99` — 실제는 정산 → 커밋). 이번 [높음]·[중간] 중 4건이 이 공백에서 살아남았다.
5. **같은 차수 안의 교차 계약 누락.** D21-③(재암호화)이 D21-⑤(새 암호문)를 모름(M4), D21-② 의 작업 재배치가 orchestrator 의 "재개는 이 프로세스" 가정을 바꿈(M7), D20-① 예약이 2차에 지적된 `_sse` 정산 누락 위에 얹힘(H2).

### 7.2 계층·경계 위반
- 서비스 → API import: `app/services/scheduler.py:46`, `:120`(2차 §4.2 이월).
- 구성요소 경계: gateway-only 프로세스가 Host 실행 재개(M7, `app/main.py:100` 이 구성요소와 무관하게 `orchestrator.LOOP` 설정).

### 7.3 위험 핫스팟 top-5
§3 과 같다: ① 신원 경계(H1·M1·M8·M12) ② 모델 비용 통제(H2·M9·M13) ③ 공급망(H3·M11) ④ 카탈로그 신뢰(M5·M6·M2) ⑤ 키 회전 운영(M4·L7·L27).

### 7.4 삭제 후보
- `app/services/a2a_gateway.py:278` 의 두 번째 조건(`k[1] == f"a2a:{agent_id}"`) — 절대 참이 되지 않는 죽은 조건(L14 수정과 함께 교체).
- `app/services/governance.py:72` 의 `"trusted"` — 60th r2 가 "쓰는 곳 없음, 해 없음" 으로 남긴 값. 데이터에 `trust: trusted` 가 없음을 확인한 뒤 제거 후보.

### 7.5 테스트 공백 (우선순위순)
1. 스트림 중간 단절 → 사용 기록·예약 반환(H2). 2. 비관리자의 admin 매핑 IdP `jwks_uri` 변경 403(H1). 3. 공백 넣은 TOTP 재사용 거부(M1). 4. 바꿔치기 번들(외부 키 필수, H3). 5. 재암호화가 모든 암호문 컬럼을 덮는지(M4). 6. 줄바꿈 없는 거대 SSE(M3). 7. `type: oauth2` 만 있는 auto_approve 항목 보류(M5). 8. 미러가 로컬 항목에 버전 추가 거부(M6). 9. gateway-only 프로세스의 승인 만료가 실행을 재개하지 않음(M7). 10. 출력 토큰 포함 예약(M9), 폴백 대상 부재 429(M13). 11. 소문자 결합·활용형 도구 이름 분류(M2).

### 7.6 다음 단계 (영향순)
1. **High 3건**: H1(IdP 패치 권한 + 시험) → H3(번들 외부 키) → H2(`_sse` `finally` 동기 정산 + 예산 예약 고정 만료). H2 는 두 파일(`app/api/llm.py`, `app/services/ratelimit.py`)을 함께 고쳐야 효과가 있다.
2. **신원·카탈로그 Medium**: M1(TOTP 단계 기준) · M8(권한 부분집합 — 사용자 결정 확인) · M12(대상 토큰 스코프) · M5·M6(카탈로그 신뢰).
3. **운영·비용 Medium**: M4(재암호화 목록 등록부) · M9·M13(예약·폴백) · M7(재개 위치) · M3(스트림 상한 헬퍼) · M10(스팬 집계) · M11(다이제스트 고정) · M2(분류 보강).
4. **절차 문서**: 키 회전(env 2단계·롤아웃 재시작·existingSecret·GitOps), 이전 키 비우기 시점, `drop_previous` 전 확인 항목 — L6·L7·L27.
5. Low 는 §5.3 순서대로 묶어서(같은 파일끼리).

---

## 부록 / Appendix (Step 8)
- **A. 대상·스냅샷**: 표시 검색 명령은 범위 고지 참고(35개 파일 64개 표시 — 결정 항목별 D20-① 8 · ② 8 · ③ 12 · ④ 9, D21-① 4 · ② 2 · ③ 10 · ⑤ 11). 스냅샷 해시·시각·대상 목록은 세션 scratchpad `r3/snapshot.sha`·`r3/snapshot.time`·`r3/files.txt`(저장소 밖). 인용 코드 블록은 스냅샷 원문에서 줄 범위로 기계적으로 복사했다.
- **B. 근거 문서**: `PRD.md:101-102`(D20·D21), `PRD.md:256-258`(결정 이력), `docs/implementation-plan.md:59-67`, `test/impl/{52nd,53rd,54th,55th,56th,57th,58th,60th}/{scenario,result}.md`, 2차 [INDEX](../../round2/CODE-REVIEW-Lv2/INDEX.md)·[VERIFY](../../round2/VERIFY.md). 잠금 버전: starlette 1.7.0·fastapi 0.142.2·httpx 0.28.1·anyio 4.15.1(`uv.lock:87-88`, `:557-558`, `:811-812`, `:2187-2188`).
- **C. 용어(Level 2 선택)**: 예약(reservation — 호출 전에 예상 사용량을 한도에 미리 더해 두는 것, 끝나면 정산·반환) · 미끄러지는 TTL(sliding TTL — 값을 바꿀 때마다 만료가 다시 연장되는 카운터) · 보유자 슬롯(holder slot — 멤버마다 고정 만료를 가진 집합으로 동시 수를 세는 방식) · TOFU(trust on first use — 처음 받은 값을 검증 없이 믿고 고정) · PRM(Protected Resource Metadata, RFC 9728 — MCP 서버가 자기 인가 서버를 알려 주는 문서) · env 모드/파일 모드(키를 환경 변수로 주는지, `data_dir/keys` 파일로 두는지) · 블록 모드(파일 전체가 아니라 변경 블록과 경계만 읽는 리뷰 방식).
- **D. 위임 리뷰 대조**: 이 작업 중 `.review/round3/delegated/{code-review,security-review}.md`(12:32·12:36 생성, 이 보고서 작성자가 쓴 것 아님)가 생겼다. 병합하지 않고 겹치는 항목만 대조한다.

| 위임 항목 | 위임 심각도 | 이 보고서 | 차이와 근거 |
|---|---|---|---|
| CR3-01 · SEC3-01 `drop_tokens` 교환 토큰 키 불일치 | HIGH · MEDIUM | L14 (Low) | 결함 자체는 같다. 다만 교환 경로는 에이전트 `auth` 를 쓰지 않고(`app/services/a2a_gateway.py:267` 스코프 `None`), 교환 비활성화·scheme 변경은 캐시 조회 **전에** 분기하며(`:247-259`), 교환 정책은 PATCH 대상이 아니다 — 남은 토큰이 "바뀐 인증과 다른 자격" 이 되는 경로를 찾지 못해 Low 로 두었다 |
| CR3-02 스트림 단절 시 예약 누수 | MEDIUM | H2 (High) | 위임은 "300초 뒤 자동 만료" 를 전제했으나 예약 카운터는 매 요청 TTL 이 연장되어(`app/services/ratelimit.py:340`, `:61`, `:168`) 활동 중에는 만료되지 않는다. 사용 기록 누락(예산 우회, 2차부터)도 같은 원인이라 합쳐서 High |
| CR3-03 재암호화 단일 트랜잭션 | MEDIUM | L5 (Low) | 같은 지점. 관리자 수동 작업이고 실패해도 이전 키로 복호화가 유지되어 Low. 이 보고서는 같은 함수의 **범위 누락**(M4)을 더 큰 위험으로 본다 |
| CR3-04 헬스 varint 1바이트 | LOW | L18 (Low) | 같음 |
| SEC3-02 이전 암호화 키 파일 umask | MEDIUM | L3 (Low) | 같은 지점. 컨테이너 단일 사용자 기준 Low — 호스트 바인드 마운트·다중 사용자 호스트면 Medium 이 맞다 |
| SEC3-03 `decode_long_lived` issuer 미검증 | LOW | L4 (Low) | 같음 |
| SEC3-04 보관 공개키 umask | LOW | (지적 안 함) | 공개키라 기밀성 문제가 아니다. 무결성(교체) 관점은 같은 디렉터리의 개인키 파일과 같은 수준 |
| (없음) | — | H1·H3·M1~M13 | 위임 리뷰에 없는 항목 — 특히 H1(IdP 검증 설정 우회)·H3(번들 검증 키 위치)·M1(TOTP 공백 우회)·M5(자동 승인 + PRM TOFU) |
