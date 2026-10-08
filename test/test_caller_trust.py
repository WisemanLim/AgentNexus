"""Epic 40 — 인증 경계 강화 (D17-①): 토큰 교환 grant 워크로드 인증 · 호출자 신고 헤더의 서버 판정.

agent 는 기계·위임 주체만 · 데이터 등급 하향 신고는 `data:declare` 권한(역할 기본 부여, RBAC 로 회수) · run 은 그 주체의 실행만.
"""
import contextlib

from app.core import auth
from app.core.auth import Principal
from app.db import SessionLocal
from app.models import AuditEvent, Run, Setting
from app.services import policy

H_AGENT = {"x-agentnexus-agent": "a2a:observability-analyst"}


def _p(kind: str, roles=("user",), sub="usr_user") -> Principal:
    return Principal(sub=sub, kind=kind, roles=list(roles))


# ── C40-01 토큰 교환 — 신고한 client_id 만으로는 거부, 틀린 비밀 거부, 워크로드 인증이면 act=워크로드 ──
def test_token_exchange_requires_workload(client, H, tokens):
    client.put("/api/v1/identity/token-exchange/policies", json={"audience": "mcp:e40", "allowed_scopes": ["r"],
                                                                 "default_scopes": ["r"], "ttl_seconds": 300},
               headers=H["security"])
    wl = client.post("/api/v1/identity/workloads", json={"name": "e40-tx", "kind": "service", "scopes": []},
                     headers=H["security"]).json()
    form = {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": tokens["user"],
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token", "audience": "mcp:e40"}
    for extra in ({"client_id": "gateway"}, {"client_id": wl["client_id"], "client_secret": "wrong"}):
        res = client.post("/idp/token", data={**form, **extra})
        assert res.status_code == 401 and res.json()["error"] == "invalid_client", extra
    ok = client.post("/idp/token", data={**form, "client_id": wl["client_id"], "client_secret": wl["client_secret"]}).json()
    from app.core import security
    assert security.verify_local(ok["access_token"], audience="mcp:e40")["act"] == {"sub": "workload:e40-tx"}


# ── C40-02 agent 신고 — 사람 주체(user·pat·client_key)는 무시, 워크로드·Access Token·위임 토큰은 받는다 ──
def test_agent_header_only_for_machine_principals():
    with SessionLocal() as db:
        for kind in ("user", "pat", "client_key"):
            assert policy.caller_context(db, _p(kind), H_AGENT)["agent"] is None, kind
        for kind in ("workload", "access_token", "delegation"):
            assert policy.caller_context(db, _p(kind), H_AGENT)["agent"] == "a2a:observability-analyst", kind


@contextlib.contextmanager
def _without_declare():
    """역할 user 에서 data:declare 회수 (RBAC 재정의) — 끝나면 원래 값으로."""
    with SessionLocal() as db:
        row = db.get(Setting, "rbac.overrides")
        before = dict(row.value) if row and isinstance(row.value, dict) else None
        if row is None:
            row = Setting(key="rbac.overrides", value={})
            db.add(row)
        row.value = {**(before or {}), "user": {"remove": ["data:declare"]}}
        db.commit()
    auth.reset_rbac_cache()
    try:
        yield
    finally:
        with SessionLocal() as db:
            row = db.get(Setting, "rbac.overrides")
            if before is None:
                db.delete(row)
            else:
                row.value = before
            db.commit()
        auth.reset_rbac_cache()


# ── C40-03 데이터 등급 — 하향 신고는 data:declare 가 있어야, 없으면 기본값으로 올리고 감사 · 상향·같은 등급은 그대로 ──
def test_lower_data_class_needs_permission():
    user = _p("user")
    with SessionLocal() as db:
        assert policy.declared_data_class(db, user, "public") == "public"  # 역할 기본 부여 — 기존 동작 유지
        assert policy.declared_data_class(db, user, None) == "internal"
    with _without_declare(), SessionLocal() as db:
        assert policy.declared_data_class(db, user, "public") == "internal"
        assert policy.declared_data_class(db, user, "confidential") == "confidential"  # 상향은 언제나
        ev = db.query(AuditEvent).filter(AuditEvent.action == "gateway.data_class.raise").order_by(AuditEvent.seq.desc()).first()
        assert ev is not None and ev.data == {"declared": "public", "effective": "internal"}
    with SessionLocal() as db:  # 스코프 토큰 — gateway:invoke 묶음에는 포함, 다른 스코프만 가진 토큰은 하향 불가
        scoped = Principal(sub="workload:x", kind="workload", roles=["user"], scopes=["chat:use"])
        bundle = Principal(sub="workload:y", kind="workload", roles=["user"], scopes=["gateway:invoke"])
        assert policy.declared_data_class(db, scoped, "public") == "internal"
        assert policy.declared_data_class(db, bundle, "public") == "public"


# ── C40-04 run 신고 — 그 주체의 실행만 (남의 실행 스트림에 동의 이벤트 주입 방지), step 은 run 이 있을 때만 ──
def test_run_header_bound_to_owner():
    with SessionLocal() as db:
        db.add(Run(id="run_e40", session_id=None, profile_id="tpl-qa", user_sub="usr_user", status="succeeded",
                   request="x", principal={}))
        db.commit()
        try:
            hdr = {"x-agentnexus-run": "run_e40", "x-agentnexus-step": "s1"}
            mine = policy.caller_context(db, _p("delegation"), hdr)
            other = policy.caller_context(db, _p("user", sub="usr_user2"), hdr)
            assert (mine["run_id"], mine["step_id"]) == ("run_e40", "s1")
            assert (other["run_id"], other["step_id"]) == (None, None)
            assert policy.caller_context(db, _p("user"), {"x-agentnexus-run": "no-such"})["run_id"] is None
        finally:
            db.delete(db.get(Run, "run_e40"))
            db.commit()


# ── C40-05 경로 연결 — Unified Model API 도 같은 판정: 권한 회수 시 public 신고가 internal 로 올라가 상용 모델 대신 내부 모델로 ──
def test_model_api_uses_server_data_class(client, H):
    body = {"model": "fast-commercial", "messages": [{"role": "user", "content": "e40 등급 확인"}]}
    hdr = {**H["user"], "X-AgentNexus-Data-Class": "public"}
    assert client.post("/v1/chat/completions", headers=hdr, json=body).json()["model"] == "fast-commercial"
    with _without_declare():
        raised = client.post("/v1/chat/completions", headers=hdr, json=body)
        assert raised.status_code == 200 and raised.json()["model"] != "fast-commercial", raised.text
