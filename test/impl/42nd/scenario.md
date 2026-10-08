# impl 42nd — 공급망·라이선스·CI (Epic 42 · D17-③)

## 범위

| 항목 | 구현 |
|------|------|
| LICENSE | Apache-2.0 원문(공식 텍스트 md5 `3b83ef96…` — 이미지 안 두 패키지의 동일 사본에서 복사) · `pyproject.toml` `license`·`license-files` · `web/package.json`·`web/e2e/package.json` `license` · Chart `artifacthub.io/license` |
| NOTICE | 저장소 고지 + 번들 서드파티(Pretendard OFL-1.1·psycopg LGPL-3.0·certifi MPL-2.0)·베이스 이미지 OS 패키지 안내 |
| THIRD_PARTY_LICENSES.md | `make licenses` — prod 이미지 파이썬 런타임 메타데이터 + 콘솔 prod JS(`pnpm licenses list --prod --json`) + 폰트, 카피레프트·소스 공개형은 검토 표(판정 사유) — `scripts/third_party.py` |
| 콘솔 번들 | Vite `esbuild.legalComments: 'eof'`(의존성 라이선스 주석 보존) · Pretendard OFL 원문 `web/public/licenses/` → api 가 `/licenses/*` 로 서빙 |
| SECURITY.md | 비공개 취약점 보고(GitHub Security Advisories)·범위·운영 점검표 |
| Dependabot | uv · npm(web·web/e2e) · docker · github-actions — 주 1회 |
| CI | `permissions: contents: read` · concurrency · lint(`make lint`)·SQLite·PG·Vitest·helm-check·prod 이미지 빌드 + 별도 audit 잡(`make audit`: pip-audit·pnpm audit) |
| 고정 | Dockerfile `python:3.12-slim@sha256:dddf…`·`node:22-alpine@sha256:0a71…` · `uv==0.12.23` · `uv sync --locked`(39th) · e2e `package-lock.json` + `npm ci`(`make e2e-lock`) |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C42-01 | LICENSE = Apache-2.0 공식 텍스트 · NOTICE·SECURITY.md·THIRD_PARTY 존재 · 메타데이터 license | `test_supply_chain.py::test_license_files` | pytest |
| C42-02 | THIRD_PARTY 검토 표에 판정 없는(검토 필요) 항목 0 · UNKNOWN 0 | `test_third_party_reviewed` | pytest |
| C42-03 | Dockerfile 베이스 digest 고정·uv 버전 고정·`--locked` · e2e `npm ci` + 잠금 파일 단일 playwright-core | `test_build_pinned` | pytest |
| C42-04 | CI permissions read · lint·test-pg·web-test·helm-check·audit 단계 · Dependabot 5 생태계 | `test_ci_and_dependabot` | pytest |
| C42-05 | 콘솔 번들에 라이선스 주석 · `/licenses/Pretendard-OFL-1.1.txt` 200 | `make web-build` + curl · prod P42 | live |
| P42-01~04 | prod 이미지 LICENSE·NOTICE · `/licenses/` 서빙 · 번들 법적 주석 | `smoke-prod.sh` | live |

## 환경 가정

- 저작권자 표기 `AgentNexus contributors` 는 가정 — 법인명이 정해지면 NOTICE 한 줄을 바꾼다.
- `make audit` 은 취약점 DB 가 필요해 네트워크가 있는 CI 에서만 돈다(로컬 미실행). GitHub 액션은 메이저 태그 + Dependabot 갱신(SHA 고정은 오프라인 확인 불가).
- e2e 베이스(`mcr.microsoft.com/playwright`)는 시험 전용이라 태그만 — 로컬에 digest 가 없다.
