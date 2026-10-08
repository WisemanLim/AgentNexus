> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 29. app/services/slo.py
**파일 요약 / File Summary**
- 역할 / Role: SLO·오류 예산 — 데이터 경로 요청을 워커 메모리에서 (분, 분류)별로 세고(`observe`), 주기적으로 DB 의 워커별 분 단위 행에 더한다(`flush`). 이동 창 SLI·예산 잔량·다중 창 소진율·경보 상태를 계산(`report`)하고 상태 변화 시 알림(`evaluate`).
- 진입점 거리 / Distance: 1 (`app/main.py:115-117`) · 2 (`app/core/metrics.py:34-35`, `app/services/scheduler.py:105`)
- LOC: 156
- 주요 export: `observe`, `flush`, `seed`, `report`, `evaluate`, `classify`, `BOUNDS`, `CLASSES`, `WINDOWS`, `PAGE_BURN`·`TICKET_BURN`, `MIN_EVENTS`, `DEFAULTS`
- 의존(내부): `app.db`, `app.models.{SliMinute,Slo}`, (지연) `app.services.notify`
- 외부 패키지: sqlalchemy, 표준 `threading`
- 부수효과 / Side effects: 워커 메모리 버퍼, DB 쓰기(sli_minutes·slos), 오래된 행 삭제, 알림
- 동시성 / Concurrency: `threading.Lock` 으로 버퍼 보호(요청은 이벤트 루프·스레드풀 양쪽에서 기록)
- 테스트 커버리지 흔적: `test/test_slo.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/slo.py lines=1-33
"""SLO · 오류 예산 (D15-④, NFR-A1·P1) — 데이터 경로(모델 API·MCP·A2A) 요청을 분 단위로 세어 이동 창 SLI 를 낸다.

- 수집: 트레이싱 미들웨어가 요청마다 `observe()` → 워커 메모리에서 (분, 분류) 별 total·5xx·첫 응답 지연 초과 수를 모으고,
  예약 작업 주기마다 `flush()` 로 `sli_minutes` 에 워커별 행으로 더한다(재시작·다중 워커에도 30일 창 유지).
- 가용성 SLI = 1 − 5xx/전체 (4xx·429 는 정책 결과라 오류 아님). 지연 SLI = 1 − (첫 응답 > threshold_ms)/전체 —
  SSE 는 스트림 길이가 아니라 첫 응답(헤더)까지로 잰다.
- 오류 예산 = (1 − 목표) × 창 안의 전체 요청, 잔량 = 1 − 나쁜 요청/예산. 소진율(burn rate) = 창 안 오류율 / (1 − 목표).
- 경보(다중 창·다중 소진율, Google SRE workbook): page = 1h·5m 모두 ≥ 14.4 · ticket = 6h·30m 모두 ≥ 6 · 예산 소진 = 잔량 ≤ 0.
"""
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, utcnow
from app.models import SliMinute, Slo

BOUNDS = (100, 250, 500, 1000, 2000, 5000)
CLASSES = ("model", "mcp", "a2a")
_MODEL_PREFIXES = ("/v1/chat", "/v1/completions", "/v1/embeddings", "/v1/models")
WINDOWS = {"5m": timedelta(minutes=5), "30m": timedelta(minutes=30), "1h": timedelta(hours=1), "6h": timedelta(hours=6)}
PAGE_BURN, TICKET_BURN = 14.4, 6.0
MIN_EVENTS = 10  # 짧은 창에 요청이 이보다 적으면 소진율 경보를 내지 않는다 (저트래픽 오경보 방지)
RETAIN_DAYS = 95
DEFAULTS = [
    {"id": "gateway-availability", "name": "데이터 경로 가용성 (NFR-A1)", "kind": "availability", "scope": "all",
     "target": 0.9995, "threshold_ms": None, "window_days": 30},
    {"id": "gateway-latency", "name": "첫 응답 2초 이내 (NFR-P1 첫 토큰)", "kind": "latency", "scope": "all",
     "target": 0.99, "threshold_ms": 2000, "window_days": 30},
]
_LOCK = threading.Lock()
_BUF: dict[tuple[datetime, str], list[int]] = {}  # (분, 분류) → [total, errors, gt100 … gt5000]
```
**분석 / Analysis (Level 2)**
- L1-8: 정의 — 가용성 SLI = 1 − 5xx/전체(4xx·429 는 정책 결과라 오류 아님), 지연 SLI = 1 − (첫 응답 > 기준)/전체(SSE 는 첫 응답까지). 경보는 Google SRE workbook 의 다중 창·다중 소진율(page: 1h·5m ≥ 14.4, ticket: 6h·30m ≥ 6).
- L19-25: 지연 구간, 분류, 모델 경로 접두, 창 4종, 소진율 임계, 저트래픽 오경보 방지 최소 10건, 보존 95일.
- L26-31 `DEFAULTS`: 가용성 99.95%·첫 응답 2초 99%(30일).
- L32-33: 버퍼 락과 (분, 분류) → [total, errors, gt100…gt5000].

```python path=app/services/slo.py lines=36-57
def classify(path: str) -> str | None:
    if path.startswith("/mcp/"):
        return "mcp"
    if path.startswith("/a2a/"):
        return "a2a"
    if path.startswith(_MODEL_PREFIXES):
        return "model"
    return None  # 콘솔·관리 API·정적 파일은 데이터 경로 SLO 밖


def observe(path: str, status: int, first_byte_s: float) -> None:
    cls = classify(path)
    if cls is None:
        return
    minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ms = first_byte_s * 1000
    with _LOCK:
        row = _BUF.setdefault((minute, cls), [0] * (2 + len(BOUNDS)))
        row[0] += 1
        row[1] += status >= 500
        for i, b in enumerate(BOUNDS):
            row[2 + i] += ms > b
```
**분석 / Analysis (Level 2)**
- L36-43 `classify`: `/mcp/`·`/a2a/`·모델 API 접두만 — 콘솔·관리 API 는 SLO 밖.
- L46-57 `observe`: 분 단위 키, 락 안에서 카운터 증가(불리언을 정수로 더하는 관용구 — `True == 1`).
**짚어볼 점 / Watch-outs**
- L50 분 경계는 **요청 종료 시각** 기준(미들웨어가 끝에서 호출) — 긴 SSE 는 시작과 다른 분에 기록된다(지연 SLI 의 의미는 첫 바이트라 영향 작음).
- L52 `threading.Lock` 을 이벤트 루프 스레드에서 잡는다 — 임계 구역이 매우 짧아 루프 차단은 무시할 수준.

```python path=app/services/slo.py lines=60-87
def flush(worker: str) -> int:
    """워커 메모리의 카운터를 DB 에 더한다 (같은 분 행은 누적). 반환: 반영한 (분, 분류) 수."""
    with _LOCK:
        buf = dict(_BUF)
        _BUF.clear()
    if not buf:
        return 0
    with SessionLocal() as db:
        for (minute, cls), c in buf.items():
            rid = f"{minute:%Y%m%dT%H%M}:{cls}:{worker}"[:120]
            row = db.get(SliMinute, rid)
            if row is None:
                row = SliMinute(id=rid, minute=minute, day=f"{minute:%Y-%m-%d}", cls=cls, worker=worker[:80],
                                total=0, errors=0, **{f"gt{b}": 0 for b in BOUNDS})
                db.add(row)
            row.total += c[0]
            row.errors += c[1]
            for i, b in enumerate(BOUNDS):
                setattr(row, f"gt{b}", getattr(row, f"gt{b}") + c[2 + i])
        db.query(SliMinute).filter(SliMinute.minute < utcnow() - timedelta(days=RETAIN_DAYS)).delete()
        db.commit()
    return len(buf)


def seed(db: Session) -> None:
    for d in DEFAULTS:
        if db.get(Slo, d["id"]) is None:
            db.add(Slo(**d, enabled=True, updated_by="system:seed", updated_at=utcnow()))
```
**분석 / Analysis (Level 2)**
- L60-81 `flush`: 락 안에서 버퍼를 복사·비우고(짧은 임계 구역), 락 밖에서 DB 에 워커별 행으로 누적(같은 분 행은 더하기), 보존 기간 지난 행 삭제 후 커밋.
- L84-87 `seed`: 기본 SLO 2개(없을 때만 — 멱등).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 데이터 손실] L62-64 버퍼를 **먼저 비우고** DB 에 쓴다 — DB 쓰기가 실패하면(잠금·연결 오류) 그 주기의 카운터가 사라진다. 실패 시 버퍼로 되돌리거나(merge back) 쓰기 성공 후 비워야 한다.
- L69 행 id 에 워커 이름(`호스트:pid`)이 들어가 워커·재시작마다 행이 늘어난다(설계상 경합 없는 쓰기를 위한 선택 — 읽을 때 합산).
- L79 삭제 쿼리를 **모든 워커가 매 주기** 실행한다 — 인덱스(`minute`)가 있어 비용은 작다.
- 프로세스가 강제 종료(SIGKILL·OOM)되면 마지막 flush 이후 최대 1주기 분량이 유실된다(lifespan 종료 flush 는 정상 종료에만).

```python path=app/services/slo.py lines=90-134
# ───────────────────────── 계산 ─────────────────────────
def _bad_col(slo: Slo):
    return SliMinute.errors if slo.kind == "availability" else getattr(SliMinute, f"gt{slo.threshold_ms}")


def _scoped(stmt, slo: Slo):
    return stmt.where(SliMinute.cls == slo.scope) if slo.scope != "all" else stmt


def _sum(db: Session, slo: Slo, since: datetime) -> tuple[int, int]:
    total, bad = db.execute(_scoped(select(func.coalesce(func.sum(SliMinute.total), 0), func.coalesce(func.sum(_bad_col(slo)), 0))
                                    .where(SliMinute.minute >= since), slo)).one()
    return int(total), int(bad)


def report(db: Session, slo: Slo, now: datetime | None = None) -> dict:
    now = now or utcnow()
    allowed_rate = 1 - slo.target
    total, bad = _sum(db, slo, now - timedelta(days=slo.window_days))
    budget = allowed_rate * total
    burns = {}
    for name, w in WINDOWS.items():
        t, b = _sum(db, slo, now - w)
        burns[name] = {"total": t, "bad": b, "burn_rate": round((b / t) / allowed_rate, 2) if t else 0.0}

    def hot(w: str, rate: float) -> bool:
        return burns[w]["total"] >= MIN_EVENTS and burns[w]["burn_rate"] >= rate
    remaining = 1 - bad / budget if budget else (1.0 if not bad else 0.0)
    state = ("page" if hot("1h", PAGE_BURN) and hot("5m", PAGE_BURN) else
             "ticket" if hot("6h", TICKET_BURN) and hot("30m", TICKET_BURN) else
             "exhausted" if total and remaining <= 0 else "ok")
    days = {d: (int(t), int(b)) for d, t, b in db.execute(
        _scoped(select(SliMinute.day, func.sum(SliMinute.total), func.sum(_bad_col(slo)))
                .where(SliMinute.minute >= now - timedelta(days=slo.window_days)), slo).group_by(SliMinute.day))}
    series = []
    for i in range(slo.window_days - 1, -1, -1):
        d = f"{now - timedelta(days=i):%Y-%m-%d}"
        t, b = days.get(d, (0, 0))
        series.append({"day": d, "total": t, "bad": b, "sli": round(1 - b / t, 6) if t else None})
    return {"id": slo.id, "name": slo.name, "kind": slo.kind, "scope": slo.scope, "target": slo.target,
            "threshold_ms": slo.threshold_ms, "window_days": slo.window_days, "enabled": slo.enabled,
            "sli": round(1 - bad / total, 6) if total else None, "total": total, "bad": bad,
            "budget": {"allowed": round(budget, 2), "consumed": bad, "remaining": round(remaining, 4)},
            "burn": burns, "state": state, "alert_state": slo.alert_state, "alert_since": slo.alert_since,
            "series": series, "updated_by": slo.updated_by, "updated_at": slo.updated_at}
```
**분석 / Analysis (Level 2)**
- L91-92 `_bad_col`: 가용성은 `errors`, 지연은 `gt{threshold_ms}` 컬럼(동적 getattr).
- L95-96 `_scoped`: 범위가 all 이 아니면 분류 필터.
- L99-102 `_sum`: 기간 합계(NULL 은 0).
- L105-134 `report`: 창 전체 합계 → 예산 = (1−목표)×전체 → 창별 소진율 = (나쁨/전체)/(1−목표) → 잔량 → 상태(page > ticket > exhausted > ok) → 일별 시계열.
**짚어볼 점 / Watch-outs**
- L92 `getattr(SliMinute, f"gt{slo.threshold_ms}")` — `threshold_ms` 가 BOUNDS 밖이면 `AttributeError`. API 가 값을 제한하고(`app/api/observe.py:432-434`) 목표도 0.9~0.99999 로 제한해(L406 `SloPatch`) L113 의 0 나눗셈을 막는다 — 서비스 함수 자체에는 방어가 없다.
- L111-113 창 4개 × 쿼리 + 전체·일별 쿼리 = SLO 당 6쿼리, 30일 창 집계는 행 수(워커×분류×분)에 비례 — 워커가 많으면 비용이 커진다.
- L117 예산이 0(요청 0건)이면 나쁨이 없을 때 1.0 — 합리적 기본값.

```python path=app/services/slo.py lines=137-156
def evaluate(db: Session, now: datetime | None = None) -> list[dict]:
    """예약 작업(경보 주기) — 상태가 바뀌면 알림 1건(page=danger, ticket·exhausted=warn), 회복도 알린다."""
    from app.services.notify import notify
    out = []
    for slo in db.scalars(select(Slo).where(Slo.enabled.is_(True))):
        r = report(db, slo, now)
        if r["state"] != (slo.alert_state or "ok"):
            prev, slo.alert_state, slo.alert_since = slo.alert_state or "ok", r["state"], now or utcnow()
            if r["state"] != "ok":
                why = {"page": f"1시간·5분 소진율 {r['burn']['1h']['burn_rate']}·{r['burn']['5m']['burn_rate']} ≥ {PAGE_BURN}",
                       "ticket": f"6시간·30분 소진율 {r['burn']['6h']['burn_rate']}·{r['burn']['30m']['burn_rate']} ≥ {TICKET_BURN}",
                       "exhausted": f"{slo.window_days}일 오류 예산 소진 (잔량 {r['budget']['remaining']:.0%})"}[r["state"]]
                notify("slo", f"SLO {slo.name}: {r['state']}", why, severity="danger" if r["state"] == "page" else "warn",
                       link="/console/observe/slo", audience_role="operator", dedupe_key=f"slo:{slo.id}:{r['state']}")
            elif prev != "ok":
                notify("slo", f"SLO {slo.name}: 회복", "소진율이 경보 기준 아래로 내려왔습니다.", severity="info",
                       link="/console/observe/slo", audience_role="operator", dedupe_key=f"slo:{slo.id}:ok")
            out.append({"id": slo.id, "from": prev, "to": r["state"]})
    db.commit()
    return out
```
**분석 / Analysis (Level 2)**
- L137-156 `evaluate`: 활성 SLO 마다 보고서 → 상태가 바뀌면 이전 상태·시각 갱신, 나빠지면 사유와 함께 알림(page=danger), 회복도 알림, 마지막에 커밋.
**짚어볼 점 / Watch-outs**
- L149-153 `notify` 는 **별도 세션**(`session_scope`, `app/services/notify.py:13`)으로 즉시 커밋하고, SLO 상태 변경은 L155 에서 나중에 커밋한다. L155 커밋이 실패하면 알림은 이미 나갔는데 상태는 이전 값으로 남아 다음 주기에 같은 알림을 다시 시도한다(중복 억제 키로 1시간 내 중복은 막힘). 다른 곳의 "커밋 후 알림" 관례(`app/api/a2a.py:483-486` 주석)와 순서가 반대다.
- 스케줄러 리스(`alerts`) 아래에서만 실행되지만, `POST /api/v1/observe/slos/evaluate`(`observe:read` 권한)로도 실행된다 — 동시 실행 시 같은 상태 변화 알림이 중복될 수 있다(중복 억제 키로 완화).

## 파일 평가 / File Assessment
- 강점: (1) 워커별 행 + 읽을 때 합산으로 다중 워커 경합 없는 집계. (2) SRE workbook 다중 창 소진율·저트래픽 보호. (3) 짧은 락 임계 구역.
- 위험: (1) [낮음] flush 실패 시 카운터 유실. (2) SLO 수·워커 수에 비례하는 집계 비용. (3) 상태 커밋 전 알림(순서 역전).
- 개선 권고(영향순): (1) DB 쓰기 성공 후 버퍼 제거(실패 시 병합). (2) 분 단위 행을 주기적으로 시간 단위로 롤업. (3) 상태 커밋 후 알림.
- 재읽기 권고 레벨: 3.
