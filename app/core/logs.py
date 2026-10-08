"""구조화 로그 (FR-O02, NFR-PRV1) — JSON 출력, trace_id 연계, PII·시크릿 마스킹, 주요 이벤트 DB 영속(SCR-K04).

event(level, component, message, **data) 는 stdout(JSON) + log_entries 에 남긴다. 일반 logging 은 stdout 만.
"""
import json
import logging
import queue
import sys
import threading
from datetime import datetime, timezone

from app.core.config import settings
from app.core.context import current_trace_id

_q: "queue.Queue[dict]" = queue.Queue(maxsize=10000)
_worker: threading.Thread | None = None


def _mask(value):
    from app.services.guards import mask_pii
    if isinstance(value, str):
        return mask_pii(value)[0]
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body = {"ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(), "level": record.levelname,
                "logger": record.name, "msg": _mask(record.getMessage())}
        tid = getattr(record, "trace_id", None) or current_trace_id()
        if tid:
            body["trace_id"] = tid
        for k in ("component", "data"):
            if hasattr(record, k):
                body[k] = _mask(getattr(record, k))
        if record.exc_info:
            body["exc"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(body, ensure_ascii=False, default=str)


def setup() -> None:
    root = logging.getLogger("agentnexus")
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(settings.log_level.upper())
    root.propagate = False


def _drain() -> None:
    from app.db import SessionLocal
    from app.models import LogEntry
    while True:
        batch = [_q.get()]
        while not _q.empty() and len(batch) < 200:
            batch.append(_q.get_nowait())
        try:
            with SessionLocal() as db:
                db.add_all([LogEntry(**b) for b in batch])
                db.commit()
        except Exception:  # noqa: BLE001 — 로그 영속 실패는 서비스에 영향 주지 않는다
            pass


def _ensure_worker() -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_drain, name="anx-log-writer", daemon=True)
        _worker.start()


def event(level: str, component: str, message: str, sync: bool = False, **data) -> None:
    """주요 이벤트 로그 — 정책 결정·오류·폴백 등. 메시지·데이터는 PII 마스킹 후 저장."""
    lvl = getattr(logging, level.upper(), logging.INFO)
    msg, clean = _mask(message), _mask(data)
    logging.getLogger(f"agentnexus.{component}").log(lvl, msg, extra={"component": component, "data": clean})
    row = {"ts": datetime.now(timezone.utc), "level": level.upper(), "logger": f"agentnexus.{component}",
           "message": msg[:4000], "component": component, "trace_id": current_trace_id(), "data": clean}
    if msg != message or clean != data:  # 마스킹된 원문은 암호문으로만 — 사유·30분 마스킹 해제(SCR-K04)에서 PII 만 보인다
        try:
            from app.core.keys import fernet
            row["raw_enc"] = fernet().encrypt(json.dumps({"message": message[:4000], "data": data}, ensure_ascii=False,
                                                         default=str).encode()).decode()
        except Exception:  # noqa: BLE001
            pass
    if sync:
        from app.db import SessionLocal
        from app.models import LogEntry
        with SessionLocal() as db:
            db.add(LogEntry(**row))
            db.commit()
        return
    try:
        _q.put_nowait(row)
        _ensure_worker()
    except queue.Full:
        pass


def flush(timeout: float = 2.0) -> None:
    """시험용 — 큐가 빌 때까지 대기."""
    import time
    t0 = time.time()
    while not _q.empty() and time.time() - t0 < timeout:
        time.sleep(0.02)
    time.sleep(0.05)
