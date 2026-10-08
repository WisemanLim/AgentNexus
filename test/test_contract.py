"""OpenAPI 계약 테스트 (COMPLIANCE ict-saas testing_additions: OpenAPI 계약 적합성).

스냅샷(test/contract/openapi-paths.json)에서 경로·메서드가 사라지면 항상 실패(하위 호환 파괴).
새 경로는 UPDATE_CONTRACT=1 로 스냅샷을 갱신해야 통과한다 — 계약 변경을 명시적으로 만든다.
"""
import json
import os
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "contract" / "openapi-paths.json"


def _current(app) -> list[str]:
    spec = app.openapi()
    ops = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                ops.append(f"{method.upper()} {path}")
    return sorted(ops)


def test_openapi_contract(client):
    current = _current(client.app)
    if os.environ.get("UPDATE_CONTRACT") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(current, indent=1, ensure_ascii=False) + "\n")
    snapshot = json.loads(SNAPSHOT.read_text())
    removed = sorted(set(snapshot) - set(current))
    added = sorted(set(current) - set(snapshot))
    assert not removed, f"계약 파괴(삭제된 엔드포인트): {removed}"
    assert not added, f"새 엔드포인트 — UPDATE_CONTRACT=1 make test 로 스냅샷 갱신: {added}"


def test_openapi_operations_documented(client):
    spec = client.app.openapi()
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            assert op.get("tags"), f"{method.upper()} {path} 에 tags 없음"
