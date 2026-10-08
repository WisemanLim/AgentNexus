"""Epic 20 — 첫 실행 마법사·설치 (SCR-A05~A09·L01, ST-09·CMP-16, FR-P02·W01·W04)."""
import httpx
import pytest

from app.core.http import register_mount
from app.db import SessionLocal
from app.models import McpServer, Provider, RegistrySource, RoutingRule, Setting, User
from app.services import setup as S
from app.services import totp

PW = "Orion-Setup-2026!x"


def _set(key, value):
    with SessionLocal() as db:
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
        db.commit()


@pytest.fixture(scope="module", autouse=True)
def ready(client, H):
    for sid in ("jira", "confluence", "dynatrace"):  # 기동 시 refresh 가 만드는 도구 규칙·토큰 교환 정책
        client.post(f"/api/v1/mcp-servers/{sid}/refresh", headers=H["operator"])


@pytest.fixture
def fresh():
    """첫 실행 전 상태 — 끝나면 완료 상태와 진행을 되돌린다."""
    with SessionLocal() as db:
        before = {k: (db.get(Setting, k).value if db.get(Setting, k) else None) for k in ("setup.completed", "setup.progress")}
    _set("setup.completed", False)
    _set("setup.progress", {"steps": {}})
    yield
    for k, v in before.items():
        _set(k, v)


def claim(client) -> dict:
    with SessionLocal() as db:
        code, _ = S.new_code(db)
    tok = client.post("/api/v1/setup/claim", json={"code": code}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_setup_code_gate(client, fresh):
    with SessionLocal() as db:
        code, exp = S.new_code(db)
    assert client.get("/api/v1/setup/state").json()["completed"] is False
    assert client.get("/api/v1/setup/progress").status_code == 401  # 코드 없이 진입 불가
    bad = client.post("/api/v1/setup/claim", json={"code": "AAAA-BBBB-CCCC"})
    assert bad.status_code == 400 and bad.json()["code"] == "code_invalid"
    ok = client.post("/api/v1/setup/claim", json={"code": code.lower()})  # 대소문자·하이픈 무관
    assert ok.status_code == 200 and ok.json()["expires_in"] == 3600
    again = client.post("/api/v1/setup/claim", json={"code": code})
    assert again.json()["code"] == "code_expired"  # 1회용
    hdr = {"Authorization": f"Bearer {ok.json()['token']}"}
    assert client.get("/api/v1/setup/progress", headers=hdr).json()["current"] == "identity"
    assert client.get("/api/v1/me", headers=hdr).status_code == 401  # 설정 토큰은 관리 API 에 못 쓴다
    with SessionLocal() as db:
        S.new_code(db)
    for _ in range(5):
        client.post("/api/v1/setup/claim", json={"code": "ZZZZ-ZZZZ-ZZZZ"})
    assert client.post("/api/v1/setup/claim", json={"code": "ZZZZ-ZZZZ-ZZZZ"}).status_code == 429  # 5회 실패 잠금
    assert client.post("/api/v1/setup/identity/skip", headers=hdr).json()["code"] == "not_skippable"


def test_identity_step_password_policy_mfa_and_login(client, fresh):
    hdr = claim(client)
    enroll = client.post("/api/v1/setup/mfa", params={"account": "nexus-admin"}, headers=hdr).json()
    assert enroll["otpauth"].startswith("otpauth://totp/AgentNexus:nexus-admin?secret=")
    base = {"admin": {"username": "nexus-admin", "email": "admin@agentnexus.local", "password": "short"},
            "mfa": {"secret": enroll["secret"], "code": totp.code_at(enroll["secret"])}}
    weak = client.post("/api/v1/setup/identity", json=base, headers=hdr).json()
    assert weak["code"] == "weak_password" and "14자 이상" in weak["problems"]
    breached = client.post("/api/v1/setup/identity", json={**base, "admin": {**base["admin"], "password": "Password1234!"}},
                           headers=hdr).json()
    assert "유출·흔한 비밀번호 목록과 일치" in breached["problems"]
    bad_mfa = client.post("/api/v1/setup/identity", json={"admin": {**base["admin"], "password": PW},
                                                          "mfa": {"secret": enroll["secret"], "code": "000000"}}, headers=hdr)
    assert bad_mfa.json()["code"] == "mfa_invalid"
    no_admin_group = client.post("/api/v1/setup/identity", json={
        "admin": {**base["admin"], "password": PW}, "mfa": base["mfa"],
        "idp": {"kind": "entra", "name": "사내 Entra", "issuer": "https://login.example.test/t/v2.0", "client_id": "c",
                "mapping": {"all-staff": "user"}}}, headers=hdr)
    assert no_admin_group.json()["code"] == "admin_group_required"
    ok = client.post("/api/v1/setup/identity", json={
        "admin": {**base["admin"], "password": PW}, "mfa": base["mfa"],
        "idp": {"kind": "entra", "name": "사내 Entra", "issuer": "https://login.example.test/t/v2.0", "client_id": "c",
                "client_secret": "s", "mapping": {"nexus-admins": "operator", "agent-dev": "developer"}}}, headers=hdr)
    assert ok.status_code == 200, ok.text
    assert ok.json()["steps"]["identity"]["status"] == "done" and ok.json()["current"] == "models"
    idps = [i for i in client.get("/api/v1/identity/idps", headers={"Authorization": hdr["Authorization"]}).json().get("items", [])]
    assert idps == []  # 설정 토큰으로는 관리 API 불가 (위와 같음)
    # 로그인 — MFA 필수
    form = {"grant_type": "password", "username": "nexus-admin", "password": PW, "client_id": "demo"}
    assert client.post("/idp/token", data=form).json()["error"] == "invalid_grant"
    with SessionLocal() as db:
        secret = enroll["secret"]
        assert db.query(User).filter(User.username == "nexus-admin").first().mfa_enabled is True
    tok = client.post("/idp/token", data={**form, "otp": totp.code_at(secret)}).json()
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
    assert me["roles"] == ["admin"]


def test_models_registry_observability_templates_and_complete(client, H, fresh):
    hdr = claim(client)
    m = client.get("/api/v1/setup/models", headers=hdr).json()
    rows = {r["id"]: r for r in m["providers"]}
    assert rows["mock"]["detected"]["ok"] and rows["ollama"]["category"] == "local" and rows["openai"]["category"] == "commercial"
    assert m["aliases"]["internal-data"] == "domain-local"
    bad = client.post("/api/v1/setup/models", json={"aliases": {"internal-data": "fast-commercial"}}, headers=hdr)
    assert bad.json()["code"] == "internal_data_local_only"
    done = client.post("/api/v1/setup/models", json={"providers": [{"id": "openai", "enabled": False, "api_key": "sk-test-not-real"}],
                                                       "aliases": {"chat-default": "smart-commercial", "internal-data": "domain-local"}},
                       headers=hdr).json()
    assert done["steps"]["models"]["status"] == "done"
    with SessionLocal() as db:
        assert db.get(Provider, "openai").secret_ref == "provider/openai/api_key"
    res = client.post("/v1/chat/completions", json={"model": "chat-default", "messages": [{"role": "user", "content": "hi"}]},
                      headers={**H["user"], "X-AgentNexus-Data-Class": "public"})
    assert res.headers["x-agentnexus-model"] == "smart-commercial"  # 별칭 → 대상 (클라이언트 코드 변경 없이 교체)
    reg = client.get("/api/v1/setup/registry", headers=hdr).json()
    assert {s["id"] for s in reg["servers"]} >= {"jira", "confluence", "dynatrace"}
    client.post("/api/v1/setup/registry", json={"mirror_docker": True, "approval": "all_pending",
                                               "servers": {"jira": True, "confluence": True, "dynatrace": True}}, headers=hdr)
    with SessionLocal() as db:
        assert next(x for x in db.query(RegistrySource) if x.kind == "official").auto_approve is False
    pii = client.post("/api/v1/setup/observability", json={"pii_masking": False}, headers=hdr)
    assert pii.json()["code"] == "security_approval_required"
    short = client.post("/api/v1/setup/observability", json={"retention": {"audit_days": 30}}, headers=hdr)
    assert short.status_code == 422  # 감사 최소 1년
    register_mount("http://otel.test", sync_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert client.post("/api/v1/setup/observability/test", json={"otlp_endpoint": "http://otel.test:4318"}, headers=hdr).json()["ok"]
    assert client.post("/api/v1/setup/observability/test", json={"otlp_endpoint": "http://127.0.0.1:9"}, headers=hdr).json()["ok"] is False
    client.post("/api/v1/setup/observability", json={"retention": {"spans_days": 14, "logs_days": 30, "audit_days": 365}},
                headers=hdr)
    ready = {t["id"]: t for t in client.get("/api/v1/setup/templates", headers=hdr).json()["items"]}
    assert ready["tpl-qa"]["ready"] is True
    st = client.post("/api/v1/setup/templates/test", json={"template": "tpl-qa"}, headers=hdr).json()
    assert [s["stage"] for s in st["stages"]] == ["카탈로그 선택", "인증 설정(토큰 교환)", "정책 기본값 적용", "오케스트레이션·콘솔 노출"]
    assert st["passed"] is True and st["total_seconds"] <= 60
    client.post("/api/v1/setup/templates", json={"templates": ["tpl-qa", "tpl-issue-report"], "self_test": st}, headers=hdr)
    with SessionLocal() as db:  # ① 관리자 단계는 test_identity_step_* 에서 — 여기서는 끝난 것으로 둔다
        S.mark(db, "identity", "done", {"admin": "nexus-admin"}, "test")
    fin = client.post("/api/v1/setup/complete", headers=hdr).json()
    assert fin["completed"] is True and fin["summary"]["steps"]["templates"]["status"] == "done"
    assert client.get("/api/v1/setup/progress", headers=hdr).json()["code"] == "setup_completed"  # 완료 후 설정 토큰 무효
    assert client.post("/api/v1/setup/claim", json={"code": "AAAA-BBBB-CCCC"}).json()["code"] == "setup_completed"
    with SessionLocal() as db:  # 다른 시험을 위해 되돌린다
        for r in db.query(RoutingRule).filter(RoutingRule.name.like("alias:%")):
            db.delete(r)
        for x in db.query(RegistrySource):
            if x.kind == "official":
                x.auto_approve = True
            if x.kind == "docker":
                x.enabled = False
        db.commit()


def test_identity_required_before_complete_with_code(client, fresh):
    hdr = claim(client)
    assert client.post("/api/v1/setup/complete", headers=hdr).json()["code"] == "identity_required"
    assert client.post("/api/v1/setup/models/skip", headers=hdr).json()["steps"]["models"]["status"] == "skipped"


def test_deploy_profile_plan_apply_and_airgap(client, H):
    plan = client.post("/api/v1/admin/profiles/plan", json={"profile": "gateway-only"}, headers=H["admin"]).json()
    assert set(plan["stop"]) >= {"host", "registry"} and "gateway" in plan["keep"] and "삭제" in plan["data"] or "지우지" in plan["data"]
    assert "AGENTNEXUS_COMPONENTS=gateway,identity" in plan["commands"]["compose"]
    assert client.post("/api/v1/admin/profiles/apply", json={"profile": "full", "airgap": True},
                       headers=H["admin"]).json()["code"] == "confirm_required"
    assert client.post("/api/v1/admin/profiles/apply", json={"profile": "full", "airgap": True, "confirm": True},
                       headers=H["operator"]).status_code in (200, 403)
    ap = client.post("/api/v1/admin/profiles/apply", json={"profile": "full", "airgap": True, "confirm": True}, headers=H["admin"])
    assert ap.status_code == 200 and ap.json()["applied"]["airgap"] is True
    try:
        banners = {b["id"] for b in client.get("/api/v1/system/banners", headers=H["user"]).json()["items"]}
        assert "airgap" in banners
        assert client.get("/api/v1/admin/profiles", headers=H["admin"]).json()["current"]["airgap"] is True
        res = client.post("/v1/chat/completions", json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                          headers={**H["user"], "X-AgentNexus-Data-Class": "public"})
        assert res.headers["x-agentnexus-model"] == "domain-local"  # 상용 제외 → 로컬 폴백
        assert client.post("/v1/chat/completions", json={"model": "fast-commercial", "messages": [{"role": "user", "content": "hi"}]},
                           headers={**H["user"], "X-AgentNexus-Data-Class": "public"}).status_code == 403
        with SessionLocal() as db:
            off = next(x for x in db.query(RegistrySource) if x.kind == "official").id
        sync = client.post(f"/api/v1/registry/sources/{off}/sync", headers=H["operator"])
        assert sync.status_code == 400 and sync.json()["code"] == "airgap"
        assert client.get("/idp/providers").json()["items"] == []  # 외부 IdP 로그인 숨김
    finally:
        client.post("/api/v1/admin/profiles/apply", json={"profile": "full", "airgap": False, "confirm": True}, headers=H["admin"])
    assert "airgap" not in {b["id"] for b in client.get("/api/v1/system/banners", headers=H["user"]).json()["items"]}
    with SessionLocal() as db:
        assert db.get(McpServer, "jira").status == "active"
