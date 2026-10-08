"""알림 센터 (SCR-B04) — 승인 대기·예산·카드 변경·장애 등. dedupe_key 로 중복 억제."""
from datetime import timedelta

from sqlalchemy import select

from app.core.ids import new_id
from app.db import session_scope, utcnow
from app.models import Notification


def notify(kind: str, title: str, body: str | None = None, *, severity: str = "info", link: str | None = None,
           audience_role: str | None = None, dedupe_key: str | None = None, dedupe_window_s: int = 3600) -> None:
    with session_scope() as db:
        if dedupe_key:
            recent = db.scalars(select(Notification).where(
                Notification.dedupe_key == dedupe_key,
                Notification.ts >= utcnow() - timedelta(seconds=dedupe_window_s))).first()
            if recent:
                return
        db.add(Notification(id=new_id("ntf"), kind=kind, severity=severity, title=title, body=body, link=link,
                            audience_role=audience_role, dedupe_key=dedupe_key))
        _deliver(db, kind, severity, title, body, link)


def _deliver(db, kind: str, severity: str, title: str, body: str | None, link: str | None) -> None:
    """외부 채널 (SCR-K07 ④·B04) — Slack 웹훅이 설정돼 있고 경고 이상이면 백그라운드로 보낸다(에어갭이면 보내지 않음)."""
    from app.models import Setting
    row = db.get(Setting, "notify.channels")
    hook = ((row.value if row else None) or {}).get("slack_webhook")
    if not hook or severity not in ("warn", "danger"):
        return
    from app.services import deploy
    if deploy.airgap(db):
        return
    import threading

    def _send():
        try:
            from app.core.http import sync_client
            with sync_client(timeout=3) as http:
                http.post(hook, json={"text": f"[AgentNexus · {severity}] {title}\n{body or ''}\n{link or ''}"})
        except Exception:  # noqa: BLE001 — 채널 실패는 알림 센터 기록에 영향 없음
            pass
    threading.Thread(target=_send, daemon=True).start()
