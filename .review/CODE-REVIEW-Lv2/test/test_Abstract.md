# test/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../INDEX.md)

pytest 통합 시험(Epic별 38개 파일) + `conftest.py`(in-process 멀티서비스 토폴로지)·`contract/`(OpenAPI 계약). `test/impl/`(구현 차수 로그)·`test/dev-env/logs` 는 리뷰 제외. 시험은 실제 마이그레이션+시드로 스키마를 만들고 TestClient 로 전체 앱을 구동한다.

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `conftest.py` | 106 | 공통 fixture·in-process 마운트·역할 토큰 | [conftest.py.md](conftest.py.md) |
| `test_mcp.py` | 296 | Epic 6 MCP 경로 통합 시험 | [test_mcp.py.md](test_mcp.py.md) |
| `test_a2a.py` | 306 | Epic 7 A2A 경로 통합 시험 | [test_a2a.py.md](test_a2a.py.md) |

## 미선택(부록, 35개)
`test_ops.py`(547)·`test_chat_editor.py`(433)·`test_review_fixes.py`(410)·`test_reliability.py`(381)·`test_review.py`(359)·`test_orchestration.py`(339)·`test_gateway_policy.py`(321)·`test_llm.py`(318)· 그 밖 보안·관측·identity·mcp 확장·a2a 바인딩 시험들은 부록 A.

## 구조 요약
모든 시험이 `conftest.client`(세션 범위) 를 공유한다 — 실제 네트워크 없이 api·mcp-servers·a2a-demo·데모 IdP 를 같은 이벤트 루프에 마운트. SSE 역채널(sampling)은 `StreamingASGITransport` 로 교착을 피한다. `test_review_fixes.py` 는 리뷰(.review)에서 확인된 결함의 회귀 시험 — 동시 작업으로 이미 다수 수정됨(INDEX §1.6·§4).

## 위험 신호
- 세션 범위 공유 DB·시드로 일부 시험이 상태를 바꾸고 복원하지 않아 순서 의존 가능성.
- 폴링 기반 시험(감시기·푸시)의 flaky 가능성.
- 커버리지 공백(종합 §4.5): 동시 승인 이중 사용·푸시 task id 위조·refresh 재사용 등 경합·음성 케이스.
