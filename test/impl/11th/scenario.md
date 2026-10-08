# impl 11th — CLI `nexus` (Epic 11 · FR-P03·S03 · SCR-A01·A04·A11·E04·F05)

## 범위

Typer CLI(`python -m app.cli`, 컨테이너 `/usr/local/bin/nexus`, 저장소 `bin/nexus`). 서버 API 를 그대로 호출하며
권한은 서버 RBAC 가 판정한다(CLI 는 토큰만 전달).

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C11-01 | 미로그인 시 명령 거부(종료코드 2) | `test_requires_login` | pytest |
| C11-02 | Device Authorization Grant(RFC 8628) 로그인 → 토큰 0600 저장 → whoami | `test_device_login_flow` · S11-01~03 | pytest + live |
| C11-03 | 거부된 디바이스 코드는 실패 처리 | `test_denied_device_login` | pytest |
| C11-04 | `mcp scaffold` 템플릿 생성(비어 있지 않으면 거부) → `registry publish` → pending | `test_scaffold_publish_and_pending` · S11-04~06 | pytest + live |
| C11-05 | publish 권한 없는 사용자는 403 | 위 + S11-07 | pytest + live |
| C11-06 | `catalog list` 타입 필터 | `test_catalog_list_and_register` · S11-08 | pytest + live |
| C11-07 | `mcp inspect` 고위험 도구 → 승인 요청 → `approvals approve` → `--approval` 재호출 성공 | `test_inspect_approval_and_approve` · S11-09~11 | pytest + live |
| C11-08 | `chat` 실행 이벤트 스트리밍(SSE) | `test_chat_streams_run` · S11-12 | pytest + live |
| C11-09 | `usage` · `audit-verify` | `test_usage_and_audit_verify` · S11-13 | pytest + live |
| C11-10 | `install --check` Docker·Compose·env·포트·(helm) 점검, 실패 시 종료코드 1 | `test_install_check` · S11-14 | pytest + live |
| C11-11 | `bundle export/import` — docker save/load·서명 구성 백업·SHA-256 매니페스트, 변조 탐지 | `test_bundle_export_import_roundtrip` · S11-15~17 | pytest + live |
| C11-12 | `python -m app.cli --help` 진입점 | `test_module_entrypoint_help` · S11-18 | pytest + live |

## 실행

```sh
make test                       # 전체 회귀
sh test/impl/11th/smoke.sh      # local 스택 기동 상태에서 (api 컨테이너 안 nexus 실행)
```

## 환경 가정

- live 스모크의 CLI 는 api 컨테이너 안에서 `NEXUS_URL=http://127.0.0.1:8000` 로 실행. 컨테이너에는 Docker CLI 가 없으므로
  `install --check` 는 docker=✘ 로 **정확히 실패를 보고**하는지 확인하고, 이미지 포함 번들은 dry-run 으로 계획만 확인한다
  (실제 docker save/load 경로는 pytest 에서 subprocess 대역으로 검증).
- 브라우저 승인은 `/idp/device` 폼 POST(curl)로 대신한다.
