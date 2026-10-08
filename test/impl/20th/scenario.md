# impl 20th — 첫 실행·설치 (Epic 20 · FR-P02·P03·W01·W04·I00·L00·C00·O00 · SCR-A01·A02·A04·A05~A09·A11·L01·B01 · ST-09·ST-11·CMP-16 · PRD D12-④)

## 범위

| 항목 | 구현 |
|------|------|
| 일회용 설정 코드 | 첫 실행 미완료면 기동 시 생성 → **stdout 로그에만**(DB 로그·K04 에는 없음) · `nexus install code [--local]` · `POST /api/v1/setup/code`(관리자) · 해시 저장·30분·1회·5회 실패 잠금 → `POST /api/v1/setup/claim` 이 설정 전용 토큰(aud=agentnexus-setup, 관리 API 불가) |
| A05 ① 관리자·IdP | 비밀번호 정책(14자·3종·유출 목록·ID 포함 금지) · TOTP MFA(RFC 6238, 등록 필수) · 외부 IdP(디스커버리·PKCE 시험, 그룹→역할, 관리자 그룹 ≥1, 로그인 노출은 시험 로그인 후) · **로그인 폼·password grant 가 MFA 강제** |
| A06 ② LLM | 공급자 표(로컬 감지·상용 키 → 시크릿·연결 시험) · 별칭(chat-default·internal-data(로컬만)·summarize·planner → 라우팅 규칙) · 에어갭이면 상용 비활성 |
| A07 ③ 레지스트리 | 자체 호스팅 · Docker 미러(에어갭 비활성) · 승인 정책(official 자동/전부 대기) · 참조 서버 선택 |
| A08 ④ 관측 | 내장/기존 스택(OTLP 연결 시험) · 보존 기간(감사 ≥ 1년) · PII 마스킹(끄기는 보안 담당) · 계측 대상 · 라이선스 분리 안내 |
| A09 ⑤ 템플릿 | 준비 상태(필요 MCP·원격) · **즉시 사용 자가 시험** 4단계 소요 시간 · 60초 판정 · 요약 · 완료 |
| 진행 상태 | 서버 저장(setup.progress) — 재접속 시 이어서, 관리자 외 건너뛰기 |
| L01 배포 프로파일 | full·gateway-only·registry-only·mcp-servers-only·a2a-only 카드 · 변경 미리보기(시작·중지·유지·데이터 보존·실행 중 경고) · compose/helm 명령 · 드라이런 · 적용(확인 MSG-002, 실패 시 되돌림) |
| ST-09 에어갭 | 적용 즉시: 상용 공급자·상용 분류 모델 제외(로컬 폴백) · 외부 레지스트리 동기화 거부 · 외부 IdP 로그인 버튼·브로커 차단 · 워커 간 5초 캐시 |
| CMP-16·B01 | `GET /api/v1/system/banners` 상단 고정 배너(에어갭·첫 실행 미완료·감사 체인 손상 MSG-022) · 상단 바 환경·에어갭 배지 |
| ST-11 | 세션 만료 → 재로그인 모달 → 새 창 로그인 → 같은 출처 postMessage 로 토큰 → **입력 보존** |
| CLI | `install --check`(자원·디스크·런타임·포트·DNS·시간·외부 접근 → 보고서 JSON, ✓/✗/⚠, 실패 잔존 시 거부, 외부 차단이면 에어갭 권장) · `--interactive`/`--save` → install.yaml · `install compose`(install.yaml → .env 난수 생성 0600 · cosign 서명 게이트 · 기동 · 헬스 대기 · 설정 코드) · `install code` · `context add/use/list` · `audit list --since` · `bundle import --verify --registry --apply-airgap`(불일치 격리·라이선스 분리·부분 실패) |

마이그레이션 0009: `users.mfa_enabled·mfa_secret`(Fernet).

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C20-01 | 코드 없이 진입 불가 · 잘못된 코드 · 1회용 · 설정 토큰은 관리 API 불가 · 5회 잠금 · 관리자 단계 건너뛰기 불가 | `test_setup_code_gate` | pytest |
| C20-02 | 정책 위반·유출 비밀번호 · MFA 오답 · 관리자 그룹 없는 매핑 거부 · 생성 후 MFA 없이 로그인 불가·있으면 admin | `test_identity_step_password_policy_mfa_and_login` | pytest |
| C20-03 | ②~⑤ 저장·검증(internal-data 로컬만, 별칭 라우팅, 승인 정책, PII 끄기 거부, 감사 1년, OTLP 시험, 자가 시험 ≤60s) · 완료 후 토큰 무효 | `test_models_registry_observability_templates_and_complete` | pytest |
| C20-04 | 설정 코드로는 관리자 단계 전 완료 불가 · 건너뛰기 | `test_identity_required_before_complete_with_code` | pytest |
| C20-05 | 프로파일 미리보기·확인 필수·에어갭 적용 → 배너·로컬 폴백·상용 403·외부 동기화 거부·IdP 숨김 → 되돌림 | `test_deploy_profile_plan_apply_and_airgap` | pytest |
| C20-06~10 | CLI 사전 점검·보고서·install.yaml·대화형 · compose 계획·서명 게이트·env 생성 · install code → claim · 컨텍스트 전환·감사 조회 · 번들 격리·라이선스·사설 레지스트리·에어갭 | `test/test_cli_install.py` | pytest |
| E20-01~04 | 코드 게이트 → MFA 등록 → ⑤ 60초 자가 시험 · MFA 로그인 강제 · 프로파일 에어갭 적용 → 배너 → 되돌림 · 세션 만료 재로그인 입력 보존 | `web/e2e/setup.spec.ts` | Playwright |
| P20-01~16 | prod: 0009 · 코드 stdout 만(DB 로그 0) · CLI 코드·claim · 토큰 격리 · 정책·MFA 실제 로그인 폼 · 자가 시험 · 에어갭 배너·모든 워커 라우팅 · 되돌림 | `smoke-prod.sh` | live |
| R | 전체 회귀 | logs | make |

## 환경 가정

- WebAuthn 은 다음 릴리스(라디오 비활성 표시). TOTP 등록은 QR 대신 키·otpauth URI 를 보여 준다(외부 라이브러리 없이).
- `install compose` 의 이미지 서명은 cosign 공개키가 있을 때만 검증한다 — 키가 없으면 기본은 중단, PoC 만 `--allow-unsigned`.
- 배포 프로파일 적용은 원하는 상태 저장 + 에어갭 런타임 적용이며, 컨테이너 재구성은 출력 명령(compose·helm)으로 반영한다(콘솔이 호스트 프로세스를 조작하지 않음).
