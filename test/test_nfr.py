"""Epic 25 — NFR-A1 장애 시 동작: Valkey 가 끊겨도 한도 검사가 오류를 내지 않고(워커 로컬 버킷) 5초 뒤 다시 시도한다.
실측(동시 1,000 세션·장애 주입 가용성·복구 시간)은 prod 리허설 `test/impl/25th/chaos-prod.sh` + `deploy/k6/chaos.js`."""
import time

from app.services.ratelimit import RedisBackend


def test_rate_limiter_survives_valkey_outage():
    b = RedisBackend("redis://127.0.0.1:1/0")  # 닫힌 포트 — 연결 실패
    t0 = time.monotonic()
    ok, _ = b.take("e25:user", rate=1.0, burst=2)
    assert ok and b.degraded_events == 1
    ok2, _ = b.take("e25:user", rate=1.0, burst=2)  # 5초 창 안 — 다시 연결을 시도하지 않고 로컬 버킷
    over, retry = b.take("e25:user", rate=1.0, burst=2)
    assert ok2 and not over and retry > 0  # 로컬 버킷도 한도는 지킨다
    assert b.degraded_events == 1 and time.monotonic() - t0 < 2.0
    assert b.window_add("e25:tok", 5) == 5 and b.window_get("e25:tok") == 5
    b._down_until = 0  # 창이 지나면 다시 Valkey 를 시도 → 여전히 실패 → 다시 로컬
    assert b.take("e25:other", rate=1.0, burst=1)[0] and b.degraded_events == 2
