"""첫 실행 마법사 (SCR-A05~A09) — 일회용 설정 코드 · 진행 상태(서버 저장) · 비밀번호 정책 · 템플릿 60초 자가 시험.

설정 코드: 첫 실행을 끝내기 전까지 기동할 때마다(또는 `nexus install code`) 새로 만들고 로그·CLI 로만 보여 준다.
해시만 저장하고 30분 뒤 만료, 한 번 교환(claim)하면 폐기된다. 교환하면 설정 전용 토큰(aud=agentnexus-setup, 1시간)을 준다.
"""
import hashlib
import hmac
import logging
import re
import secrets as pysecrets
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.db import utcnow
from app.models import Setting

log = logging.getLogger("agentnexus.setup")
STEPS = ("identity", "models", "registry", "observability", "templates")
SKIPPABLE = set(STEPS) - {"identity"}  # 관리자 단계는 건너뛸 수 없다
CODE_TTL = timedelta(minutes=30)
AUDIENCE = "agentnexus-setup"
_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 0·O·1·I 제외
# 유출·흔한 비밀번호(오프라인 대조용, 소문자) — 에어갭에서도 동작하도록 내장
BREACHED = {
    "password", "password1", "password123", "password1234", "p@ssw0rd", "passw0rd!", "qwerty", "qwerty123", "qwertyuiop",
    "123456", "12345678", "123456789", "1234567890", "1q2w3e4r", "1q2w3e4r5t", "1qaz2wsx", "zaq12wsx", "abc123",
    "admin", "admin123", "admin1234", "administrator", "letmein", "welcome", "welcome1", "welcome123", "iloveyou",
    "monkey", "dragon", "master", "sunshine", "princess", "football", "baseball", "superman", "trustno1", "changeme",
    "change_me", "changeit", "secret", "secret123", "default", "root", "toor", "test1234", "guest", "hello123",
    "q1w2e3r4", "asdf1234", "asdfghjkl", "zxcvbnm", "11111111", "00000000", "a1b2c3d4", "agentnexus", "nexus-admin",
    "korea123", "sarang", "saranghae", "dlrjtwkd", "qwer1234", "q1w2e3r4t5", "summer2026", "winter2026", "spring2026",
}


# ── 진행 상태 ──
def _get(db: Session, key: str, default=None):
    row = db.get(Setting, key)
    return row.value if row is not None else default


def _put(db: Session, key: str, value, actor: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, updated_by=actor))
    else:
        row.value, row.updated_by = value, actor


def completed(db: Session) -> bool:
    return bool(_get(db, "setup.completed"))


def progress(db: Session) -> dict:
    p = _get(db, "setup.progress") or {}
    steps = {s: (p.get("steps") or {}).get(s, {"status": "todo"}) for s in STEPS}
    current = next((s for s in STEPS if steps[s]["status"] == "todo"), "templates")
    return {"current": current, "steps": steps}


def mark(db: Session, step: str, status: str, summary: dict, actor: str) -> dict:
    p = _get(db, "setup.progress") or {"steps": {}}
    p = {**p, "steps": {**(p.get("steps") or {}), step: {"status": status, "summary": summary, "at": utcnow().isoformat(),
                                                          "by": actor}}}
    _put(db, "setup.progress", p, actor)
    db.commit()
    return progress(db)


# ── 설정 코드 ──
def _h(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").upper().encode()).hexdigest()


def new_code(db: Session, actor: str = "system") -> tuple[str, str]:
    raw = "".join(pysecrets.choice(_ALPHA) for _ in range(12))
    code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    exp = (utcnow() + CODE_TTL).isoformat()
    _put(db, "setup.code", {"hash": _h(code), "expires_at": exp, "used": False, "fails": 0}, actor)
    db.commit()
    return code, exp


def ensure_code(db: Session) -> str | None:
    """기동 시 — 첫 실행 미완료이고 유효한 코드가 없으면 새로 만들어 로그에만 남긴다(평문은 저장하지 않음)."""
    if completed(db):
        return None
    cur = _get(db, "setup.code") or {}
    if cur and not cur.get("used") and cur.get("expires_at", "") > utcnow().isoformat():
        return None
    code, exp = new_code(db)
    log.warning("[setup] 첫 실행 일회용 설정 코드: %s (만료 %s, 재발급: nexus install code) → %s/setup", code, exp[:19],
                security.issuer().rsplit("/idp", 1)[0])
    return code


class SetupError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def claim(db: Session, code: str) -> dict:
    if completed(db):
        raise SetupError("setup_completed", "첫 실행이 이미 끝났습니다 — 관리자로 로그인하세요.")
    cur = _get(db, "setup.code") or {}
    if not cur or cur.get("used") or cur.get("expires_at", "") < utcnow().isoformat():
        raise SetupError("code_expired", "설정 코드가 만료되었거나 이미 사용되었습니다 — nexus install code 로 재발급하세요.")
    if cur.get("fails", 0) >= 5:
        raise SetupError("code_locked", "설정 코드 입력을 5회 틀렸습니다 — 코드를 재발급하세요.")
    if not hmac.compare_digest(cur["hash"], _h(code or "")):
        _put(db, "setup.code", {**cur, "fails": cur.get("fails", 0) + 1}, "setup")
        db.commit()
        raise SetupError("code_invalid", "설정 코드가 올바르지 않습니다.")
    _put(db, "setup.code", {**cur, "used": True}, "setup")
    db.commit()
    token, claims = security.sign({"sub": "setup", "aud": AUDIENCE, "anx_kind": "setup"}, 3600)
    return {"token": token, "expires_in": 3600}


def verify_token(token: str) -> bool:
    try:
        claims = security.verify_local(token, audience=AUDIENCE)
    except Exception:  # noqa: BLE001
        return False
    return claims.get("anx_kind") == "setup"


# ── 비밀번호 정책 (SCR-A05 ②) ──
def password_problems(password: str, username: str = "") -> list[str]:
    out = []
    if len(password) < 14:
        out.append("14자 이상")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        out.append("대문자·소문자·숫자·기호 중 3종 이상")
    low = password.lower()
    if low in BREACHED or re.sub(r"[^a-z0-9]", "", low) in BREACHED or re.sub(r"\d+$", "", low) in BREACHED:
        out.append("유출·흔한 비밀번호 목록과 일치")
    if username and username.lower() in low:
        out.append("사용자 이름 포함 금지")
    return out


# ── 템플릿 즉시 사용 자가 시험 (SCR-A09 ⑤⑥, FR-W04 ≤ 60s) ──
TEMPLATE_NEEDS = {"tpl-qa": {"mcp": ["confluence"], "remote": []},
                  "tpl-issue-report": {"mcp": ["jira", "confluence"], "remote": []},
                  "tpl-incident": {"mcp": ["dynatrace"], "remote": ["observability-analyst"]}}
SAMPLE = {"tpl-qa": "장애 대응 런북 알려줘", "tpl-issue-report": "ORION 이번 주 이슈 요약",
          "tpl-incident": "orders-api 장애 원인 분석"}


def readiness(db: Session) -> list[dict]:
    from app.models import A2AAgent, AgentProfile, McpServer
    out = []
    for pid, need in TEMPLATE_NEEDS.items():
        pr = db.get(AgentProfile, pid)
        missing = [f"MCP {s}" for s in need["mcp"] if (m := db.get(McpServer, s)) is None or m.status != "active"]
        missing += [f"원격 {a}" for a in need["remote"] if (a2 := db.get(A2AAgent, a)) is None or a2.status != "active"
                    or not a2.card]
        out.append({"id": pid, "name": pr.name if pr else pid, "published": bool(pr and pr.status == "published"),
                    "needs": need, "missing": missing, "ready": bool(pr) and not missing,
                    "policy_default": "쓰기 도구 사람 승인 필수"})
    return out


async def self_test(principal, template: str) -> dict:
    """선택 → 인증(토큰 교환 정책) → 정책 기본값 → 오케스트레이션·노출 단계별 소요 시간, 합계 ≤ 60초면 통과."""
    from starlette.concurrency import run_in_threadpool

    from app.models import AgentProfile, CatalogItem, TokenExchangePolicy, ToolRule
    from app.services import orchestrator as orch
    stages = []

    def timed(name, fn):
        t0 = time.perf_counter()
        ok, note = fn()
        stages.append({"stage": name, "ok": ok, "note": note, "seconds": round(time.perf_counter() - t0, 2)})
        return ok

    def _select():
        from app.db import SessionLocal
        with SessionLocal() as db:
            item = db.scalars(select(CatalogItem).where(CatalogItem.type == "agent_profile",
                                                       CatalogItem.name == template)).first()
            return bool(item or db.get(AgentProfile, template)), "카탈로그 '프로파일' 항목"

    def _auth():
        from app.db import SessionLocal
        with SessionLocal() as db:
            need = TEMPLATE_NEEDS.get(template, {"mcp": []})["mcp"]
            have = {p.audience for p in db.scalars(select(TokenExchangePolicy))}
            miss = [s for s in need if f"mcp:{s}" not in have]
            return not miss, "토큰 교환 정책 " + ("충족" if not miss else f"없음: {', '.join(miss)}")

    def _policy():
        from app.db import SessionLocal
        with SessionLocal() as db:
            prof = db.get(AgentProfile, template)
            tools = ((prof.graph or {}).get("workers") or {}).get("executor", {}).get("tools", []) if prof else []
            rules = {f"{r.server_id}.{r.tool}": r for r in db.scalars(select(ToolRule))}
            weak = [t for t in tools if t in rules and not rules[t].approval_required]
            return not weak, "쓰기 도구 승인 필수" if not weak else f"승인 없음: {', '.join(weak)}"
    for name, fn in (("카탈로그 선택", _select), ("인증 설정(토큰 교환)", _auth), ("정책 기본값 적용", _policy)):
        await run_in_threadpool(timed, name, fn)
    t0 = time.perf_counter()
    rid = await run_in_threadpool(lambda: orch.start_run(principal, profile_id=template, request=SAMPLE.get(template, "시험"),
                                                         test=True))
    orch.schedule(rid)
    status = await orch.wait(rid, timeout=60)
    stages.append({"stage": "오케스트레이션·콘솔 노출", "ok": status in ("succeeded", "partial", "waiting_approval"),
                   "note": f"run {rid} {status}", "seconds": round(time.perf_counter() - t0, 2), "run_id": rid})
    total = round(sum(s["seconds"] for s in stages), 2)
    slow = max(stages, key=lambda s: s["seconds"])["stage"]
    return {"template": template, "stages": stages, "total_seconds": total, "passed": total <= 60 and all(s["ok"] for s in stages),
            "slowest": slow, "target_seconds": 60}
