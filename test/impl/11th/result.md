# 시험 결과 / Test Result — impl 11th (Epic 11 CLI `nexus`)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 14, 통과 Pass: 14, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 130 passed · PostgreSQL 130 passed (CLI 11 케이스 추가)
- live: `smoke.sh` 18/18 (api 컨테이너 안 `nexus`)

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C11-01 ~ C11-12 | PASS (round 4) | `test/test_cli.py` (11 케이스) |
| C11-13 | PASS | `logs/make-test.log` · `logs/make-test-pg.log` (130/130) |
| C11-14 | PASS | `logs/smoke-run1.log` 18/18 — device login(폼 승인)·scaffold·publish·403·inspect→승인→재호출·chat SSE·usage·audit·install·bundle·변조 탐지 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | `mcp list` 403 | 시험이 `user`(mcp:read 없음)로 호출 — RBAC 정상 (시험 코드) | dev 로 호출, user 403 은 별도 단언으로 추가 | round 2 |
| 2 | `mcp inspect jira transition_issue` 사용법 오류 | Typer 가 `tool: str \| None = None` 을 `--tool` 옵션으로 해석 | `typer.Argument(None)` 선택 위치 인자로 | round 3 |
| 3 | 승인 후 재호출이 다시 approval_required | Inspector API 는 `approval_id` 를 본문으로 받아야 함 — CLI 에 전달 수단 없음 | `--approval <id>` 옵션 추가 | round 4 |
| 3 | 재호출 JSON 파싱 실패 | 시험 키 `ORI-1439` 미존재 + 서버 로그가 CliRunner 출력에 섞임 | 존재 키 `ORI-1440`, JSON 은 `result.stdout` 만 파싱 | round 4 PASS |

## 비고 / Notes

- 컨테이너에는 Docker CLI 가 없어 `install --check` 는 docker=✘ 로 실패를 정확히 보고(S11-14)하고, 이미지 포함 번들은 dry-run 계획만
  live 확인. 실제 `docker save/load` 인자·매니페스트 SHA-256·복원은 pytest(subprocess 대역)로 검증. 호스트 실행은 `uv run bin/nexus …`.

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-run1) · `smoke.sh`
