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
