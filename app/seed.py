"""wise-agentnexus — 시드 (make db-seed · entrypoint 자동 실행). 모두 멱등(idempotent)."""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core import keys, security
from app.core.config import settings
from app.core.ids import new_id
from app.db import session_scope
from app.models import (A2AAgent, CatalogItem, CatalogVersion, GatewayPolicy, McpServer, Model, Provider,
                        RateLimit, RegistrySource, RoutingRule, TokenExchangePolicy, User, Workload)

# local/test 전용 개발 계정 — 비밀번호 = "<username>-pass" (prod 에서는 생성하지 않는다)
DEV_USERS = [
    ("admin", "관리자", ["admin"], "team-platform"),
    ("operator", "운영자", ["operator"], "team-platform"),
    ("dev", "개발자", ["developer"], "team-agents"),
    ("security", "보안담당", ["security"], "team-security"),
    ("finops", "FinOps담당", ["finops"], "team-finops"),
    ("user", "사용자", ["user"], "team-biz"),
    ("user2", "사용자2", ["user"], "team-agents"),
]


def _ensure_user(db: Session, username: str, name: str, roles: list[str], team: str | None, password: str) -> None:
    if db.scalars(select(User).where(User.username == username)).first():
        return
    uid = f"usr_{username}"
    db.add(User(id=uid, sub=uid, issuer=security.issuer(), username=username, name=name, email=f"{username}@agentnexus.local",
                password_hash=security.hash_password(password), roles=roles, team=team))


def seed_identity(db: Session) -> list[str]:
    done = []
    if settings.app_env in ("local", "test"):
        for username, name, roles, team in DEV_USERS:
            _ensure_user(db, username, name, roles, team, f"{username}-pass")
        done.append("dev-users")
    pw = settings.bootstrap_admin_password.strip()
    if pw and pw != "CHANGE_ME":
        _ensure_user(db, "admin", "관리자", ["admin"], "team-platform", pw)
        done.append("bootstrap-admin")
    return done


REGISTRY_SOURCES = [
    ("mcp-official", "official", "https://registry.modelcontextprotocol.io"),
    ("docker-mcp-catalog", "docker", "https://desktop.docker.com/mcp/catalog/v2/catalog.yaml"),
    ("airgap-bundle", "file", None),
]


def seed_registry(db: Session) -> list[str]:
    for name, kind, url in REGISTRY_SOURCES:
        if not db.scalars(select(RegistrySource).where(RegistrySource.name == name)).first():
            db.add(RegistrySource(id=new_id("src"), name=name, kind=kind, url=url))
    return ["registry-sources"]


# 기본 정책 (PRD 9.2 Rego 예시와 동등): 내부·기밀 데이터 → 로컬 모델, 나머지 기본 허용. 도구·에이전트는 규칙·신뢰등급이 추가로 거른다.
DEFAULT_POLICIES = [
    dict(name="내부·기밀 데이터는 로컬 모델만", target_type="model", target="category:commercial", effect="deny",
         conditions={"data_class": ["internal", "confidential"]},
         data_class_routing={"internal": "domain-local", "confidential": "domain-local"}, priority=10,
         description="FR-G03 데이터 분류별 모델 허용 — 상용 모델 차단 후 도메인 특화 로컬 모델로 재라우팅"),
    dict(name="기본 모델 허용", target_type="model", target="*", effect="allow", priority=1000),
    dict(name="등록 도구 허용(도구 규칙 적용)", target_type="tool", target="*", effect="allow", priority=1000),
    dict(name="신뢰 원격 에이전트 허용", target_type="agent", target="*", effect="allow", priority=1000),
]


def seed_gateway(db: Session) -> list[str]:
    for pol in DEFAULT_POLICIES:
        if not db.scalars(select(GatewayPolicy).where(GatewayPolicy.name == pol["name"])).first():
            db.add(GatewayPolicy(id=new_id("pol"), **pol))
    if not db.scalars(select(RateLimit).where(RateLimit.name == "주체별 기본 요청 한도")).first():
        db.add(RateLimit(id=new_id("lim"), name="주체별 기본 요청 한도", scope_type="any", scope="per-subject",
                         rps=50, burst=100))
    from app.services import slo
    slo.seed(db)  # 기본 SLO 2종 (D15-④)
    return ["gateway-policies"]


# 공급자: mock 은 오프라인·에어갭 시험용(활성). 상용·로컬 공급자는 키·엔드포인트 설정 후 활성화한다.
PROVIDERS = [
    ("mock", "내장 Mock (오프라인)", "mock", None, True),
    ("openai", "OpenAI", "openai", None, False),
    ("anthropic", "Anthropic", "anthropic", None, False),
    ("gemini", "Google Gemini", "gemini", None, False),
    ("ollama", "Ollama (로컬)", "ollama", "http://ollama:11434/v1", False),
    ("vllm", "vLLM (도메인 특화)", "vllm", "http://vllm:8000/v1", False),
]
# (별칭, 공급자, 업스트림, 분류, USD/1M in, out) — 별칭은 유지하고 공급자만 바꿔 교체(FR-G06)
MODELS = [
    ("domain-local", "mock", "domain-7b", "local", "0.05", "0.10"),
    ("fast-commercial", "mock", "mock-fast", "commercial", "0.15", "0.60"),
    ("smart-commercial", "mock", "mock-smart", "commercial", "2.50", "10.00"),
    ("gpt-4o", "openai", "gpt-4o", "commercial", "2.50", "10.00"),
    ("claude-sonnet", "anthropic", "claude-sonnet-4-5", "commercial", "3.00", "15.00"),
    ("gemini-flash", "gemini", "gemini-2.5-flash", "commercial", "0.30", "2.50"),
    ("ollama/qwen", "ollama", "qwen2.5:7b", "local", "0", "0"),
    ("vllm/domain-7b", "vllm", "domain-7b", "domain", "0", "0"),
    ("embed-local", "mock", "mock-embed", "local", "0.01", "0"),  # RAG 임베딩 (결정적 해시, 실공급자로 교체)
]
CAPS = {"embed-local": ["embeddings"]}
# 품질 점수(0~1) — 라우팅 전략 quality 의 기본값. 운영자가 I03·H02 에서 조정
QUALITY = {"smart-commercial": 0.9, "gpt-4o": 0.9, "claude-sonnet": 0.92, "gemini-flash": 0.75,
           "fast-commercial": 0.7, "domain-local": 0.6, "ollama/qwen": 0.65, "vllm/domain-7b": 0.7}


def seed_models(db: Session) -> list[str]:
    from app.services.llm import sync_model_catalog
    for pid, name, kind, base, enabled in PROVIDERS:
        if db.get(Provider, pid) is None:
            db.add(Provider(id=pid, name=name, kind=kind, base_url=base, enabled=enabled,
                            secret_ref=None if kind in ("mock", "ollama", "vllm") else f"provider/{pid}/api_key"))
    db.flush()
    for mid, pid, up, cat, pin, pout in MODELS:
        m = db.get(Model, mid)
        if m is None:
            m = Model(id=mid, provider_id=pid, upstream_model=up, category=cat, price_in=Decimal(pin),
                      price_out=Decimal(pout), capabilities=CAPS.get(mid, ["stream", "tools", "json_schema"]),
                      quality=QUALITY.get(mid), enabled=db.get(Provider, pid).enabled)
            db.add(m)
            db.flush()
            sync_model_catalog(db, m, "system:seed")
        elif m.quality is None and mid in QUALITY:  # 0007 이전 DB — 기본 품질 점수만 채운다
            m.quality = QUALITY[mid]
    if not db.scalars(select(RoutingRule).where(RoutingRule.name == "auto — 비용 우선")).first():
        db.add(RoutingRule(id=new_id("rr"), name="auto — 비용 우선", match_model="auto", strategy="cost",
                           targets=[{"model": "fast-commercial"}, {"model": "smart-commercial"}],
                           fallbacks=["domain-local"], retries=1, priority=100))
    return ["models"]


def seed_mcp(db: Session) -> list[str]:
    """참조 MCP 서버(데모) 카탈로그·인스턴스 — 도구 목록·기본 규칙·교환 정책은 기동 후 refresh 가 채운다."""
    if not settings.seed_demo:
        return []
    from app.builtin.mcp_servers import SCOPES, SERVERS, server_json
    for name in SERVERS:
        doc = server_json(name, settings.mcp_servers_url)
        item = db.scalars(select(CatalogItem).where(CatalogItem.type == "mcp_server",
                                                    CatalogItem.name == doc["name"])).first()
        if item is None:
            item = CatalogItem(id=new_id("cat"), type="mcp_server", name=doc["name"], display_name=doc["title"],
                               description=doc["description"], source="builtin", status="active",
                               version=doc["version"], item_metadata=doc, license="MIT", auth_scheme="oauth2",
                               owner="system:seed", ref_id=name, tags=["builtin", "reference"])
            db.add(item)
            db.flush()
            db.add(CatalogVersion(id=new_id("cver"), item_id=item.id, version=doc["version"], item_metadata=doc,
                                  status="approved", created_by="system:seed", decided_by="system:seed"))
        if db.get(McpServer, name) is None:
            db.add(McpServer(id=name, catalog_item_id=item.id, name=doc["title"], transport="streamable-http",
                             endpoint=doc["remotes"][0]["url"], server_json=doc,
                             auth={"type": "oauth2", "tool_scopes": SCOPES[name]}, builtin=True, owner="system:seed"))
    return ["mcp-reference-servers"]


def seed_a2a(db: Session) -> list[str]:
    """데모 원격 A2A 에이전트 + 그 워크로드 신원(client_credentials). 자격 증명은 공유 볼륨 파일로만 전달한다."""
    if not settings.seed_demo:
        return []
    import json
    from pathlib import Path

    from app.services import identity
    aid, url = "observability-analyst", settings.a2a_demo_url.rstrip("/") + "/"
    if db.get(A2AAgent, aid) is None:
        item = CatalogItem(id=new_id("cat"), type="a2a_agent", name=aid, display_name="관측 분석 에이전트 (demo)",
                           description="Dynatrace 문제·메트릭 장애 분석 (가상 데이터)", source="builtin",
                           status="active", version="1.0.0", item_metadata={}, owner="system:seed", ref_id=aid,
                           tags=["builtin", "sre", "incident"])
        db.add(item)
        db.flush()
        db.add(A2AAgent(id=aid, catalog_item_id=item.id, name="관측 분석 에이전트 (demo)", url=url,
                        card_url=url.rstrip("/") + "/.well-known/agent-card.json", trust_tier="trusted",
                        auth={"scheme": "bearer"}))
    if not db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == f"a2a:{aid}")).first():
        db.add(TokenExchangePolicy(id=new_id("txp"), audience=f"a2a:{aid}", audience_type="a2a_agent",
                                   allowed_scopes=["a2a:invoke"], default_scopes=["a2a:invoke"], ttl_seconds=600))
    cred = Path(settings.data_dir) / "workloads" / "a2a-demo.json"
    wl = db.scalars(select(Workload).where(Workload.name == "a2a-demo")).first()
    fixed = settings.a2a_demo_client_secret if settings.a2a_demo_client_secret not in ("", "CHANGE_ME") else ""
    if fixed:  # 다중 파드(Helm): 공유 볼륨 대신 Secret 으로 같은 값을 api·a2a-demo 에 주입
        if wl is None:
            wl, _ = identity.create_workload(db, "a2a-demo", "agent", ["gateway:invoke"])
        if not security.check_password(fixed, wl.secret_hash):
            wl.secret_hash = security.hash_password(fixed)
    elif wl is None or not cred.exists():
        if wl is None:
            wl, secret = identity.create_workload(db, "a2a-demo", "agent", ["gateway:invoke"])
        else:
            secret = identity.rotate_workload(db, wl)
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text(json.dumps({"client_id": wl.client_id, "client_secret": secret}))
        cred.chmod(0o640)
    return ["a2a-demo-agent"]


TEMPLATES = {
    "tpl-qa": ("사내 Q&A", "사내 문서(Confluence)·내부 시스템을 검색해 근거와 함께 답한다 (읽기 전용)", True, """
planner: {model: auto, mode: llm, max_steps: 4}
workers:
  retriever: {tools: [confluence.search_pages, confluence.get_page, internal-api.orders_search]}
  summarize: {model: domain-local, cite: true}
data_class: internal
default_plan:
  - {id: s1, agent: retriever, tool: confluence.search_pages, args: {query: "{{request}}"}}
  - {id: s2, agent: summarize, depends_on: [s1]}
"""),
    "tpl-issue-report": ("이슈 보고 자동화", "Jira 이슈를 검색·요약해 Confluence 주간 보고 페이지를 작성한다 (쓰기는 사람 승인)", False, """
planner: {model: auto, mode: llm, max_steps: 5}
workers:
  retriever: {tools: [jira.search_issues, confluence.search_pages]}
  summarize: {model: domain-local, cite: true}
  executor: {tools: [confluence.create_page]}
fanout: {parallel: true, join: all}
data_class: internal
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}", limit: 10}}
  - {id: s2, agent: retriever, tool: confluence.search_pages, args: {query: "주간 보고 템플릿"}}
  - {id: s3, agent: summarize, depends_on: [s1, s2]}
  - {id: s4, agent: executor, tool: confluence.create_page, depends_on: [s3],
     args: {space: ORION, title: "주간 이슈 보고", body: "{{s3.text}}"}}
"""),
    "tpl-incident": ("장애 분석 위임", "Dynatrace 문제를 조회하고 원격 관측 분석 에이전트(A2A)에 위임해 결과를 종합한다", False, """
planner: {model: auto, mode: llm, max_steps: 4}
workers:
  retriever: {tools: [dynatrace.list_problems, dynatrace.get_problem]}
  remote: {agents: [observability-analyst]}
  summarize: {model: domain-local, cite: true}
fanout: {parallel: true, join: all}
data_class: internal
default_plan:
  - {id: s1, agent: retriever, tool: dynatrace.list_problems, args: {status: OPEN}}
  - {id: s2, agent: remote, remote_agent: observability-analyst, args: {message: "{{request}}"}}
  - {id: s3, agent: summarize, depends_on: [s1, s2]}
"""),
}


# 데모 프로파일(템플릿 아님) — MCP 서버의 sampling·elicitation 요청을 Host 동의 카드로 받는 흐름 (SCR-C08)
DEMO_PROFILES = {
    "demo-orders": ("주문 이상 분류 (데모)", "주문 메모를 서버가 요청한 모델 호출(샘플링)로 분류하고, 반품 접수에 필요한 입력을 묻는다", """
planner: {model: auto, mode: template, max_steps: 4}
workers:
  retriever: {tools: [internal-orders.classify_orders]}
  executor: {tools: [internal-orders.request_return]}
  summarize: {model: domain-local, cite: true}
data_class: internal
default_plan:
  - {id: s1, agent: retriever, tool: internal-orders.classify_orders, args: {date: "2026-09-30"}}
  - {id: s2, agent: executor, tool: internal-orders.request_return, depends_on: [s1], args: {order_id: O-2404}}
  - {id: s3, agent: summarize, depends_on: [s1, s2]}
"""),
    "demo-rag": ("운영 런북 Q&A (RAG 데모)", "내장 벡터 색인(ops-runbooks)에서 근거를 찾아 요약 — 요청에 '이슈'가 있으면 Jira 도 검색", """
planner: {model: auto, mode: template, max_steps: 4}
workers:
  retriever: {tools: [jira.search_issues], knowledge_bases: [ops-runbooks]}
  summarize: {model: domain-local, cite: true}
data_class: internal
default_plan:
  - {id: s1, agent: retriever, kb: ops-runbooks, top_k: 3, args: {query: "{{request}}"}}
  - {id: s2, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}", limit: 3}, when: "request contains '이슈'"}
  - {id: s3, agent: summarize, depends_on: [s1, s2]}
"""),
}

# 데모 지식 베이스 (RAG) — 내부 운영 문서. 벡터는 embed-local(결정적 해시)과 같은 함수로 만든다
DEMO_KB = ("ops-runbooks", "운영 런북", [
    ("주문 API 지연 대응 런북", "runbook://orders-api/latency", """p95 지연 경보가 울리면 먼저 Dynatrace 문제 목록에서 orders-api 의 열린 문제를 확인한다.

최근 30분 안에 배포가 있었다면 롤백을 우선 검토한다. 롤백 기준은 오류율 2% 이상 또는 p95 800ms 이상이 10분 넘게 지속되는 경우다.

배포가 없었다면 DB 슬로우 쿼리와 커넥션 풀 고갈을 확인한다. 인덱스 누락이 원인이면 DBA 승인 후 온라인 인덱스를 만든다."""),
    ("결제 샌드박스 인증서 갱신 절차", "runbook://payments/cert-rotation", """결제 샌드박스 인증서는 분기마다 갱신한다. 만료 14일 전에 보안팀이 새 인증서를 발급한다.

새 인증서는 게이트웨이 시크릿 저장소에 certificate 종류로 등록하고, 결제 MCP 서버의 mTLS 설정에서 이름만 바꾼다.

교체 후 연결 시험이 성공하면 이전 인증서를 폐기한다. 실패하면 이전 이름으로 되돌린다."""),
    ("배포 롤백 체크리스트", "runbook://release/rollback", """롤백 전: 변경 관리 티켓 번호, 영향 서비스, 데이터 마이그레이션 여부를 확인한다.

데이터 마이그레이션이 포함된 배포는 롤백 대신 정방향 수정(fix-forward)을 검토한다.

롤백 후: 오류율·p95·주문 처리량이 배포 전 수준으로 돌아왔는지 15분간 관찰하고 결과를 장애 티켓에 남긴다."""),
])


def seed_demo_idp(db: Session) -> list[str]:
    """데모 외부 IdP 연동 (local·test 전용) — 콘솔 로그인 화면에 '데모 파트너 IdP' 버튼, 그룹 anx-developers → developer."""
    if not settings.seed_demo or settings.app_env not in ("local", "test"):
        return []
    from app.builtin import demo_idp
    from app.models import IdpConnection
    from app.services import secrets as sec
    iss = demo_idp.issuer()
    if db.scalars(select(IdpConnection).where(IdpConnection.issuer == iss)).first() is not None:
        return []
    idp = IdpConnection(id="idp-demo-partner", name="데모 파트너 IdP", kind="oidc", issuer=iss, client_id=demo_idp.CLIENT_ID,
                        client_secret_ref="idp/idp-demo-partner/client_secret", audience=demo_idp.CLIENT_ID,
                        internal_url=settings.idp_internal_url.rstrip("/") + "/_demo/oidc",
                        role_mapping={"anx-developers": "developer", "anx-operators": "operator"},
                        enabled=True, login_enabled=True, status="ok", last_test={"status": "ok", "note": "seed demo"})
    sec.put(db, idp.client_secret_ref, demo_idp.CLIENT_SECRET, kind="oauth_client", description="데모 파트너 IdP (local)")
    db.add(idp)
    return ["demo-idp"]


def seed_knowledge(db: Session) -> list[str]:
    if not settings.seed_demo:
        return []
    import hashlib

    from app.models import KbChunk, KbDocument, KnowledgeBase
    from app.services.knowledge import chunks
    from app.services.providers import MOCK_DIMS, hash_embedding
    kid, name, docs = DEMO_KB
    if db.get(KnowledgeBase, kid) is not None:
        return []
    db.add(KnowledgeBase(id=kid, name=name, description="주문·결제·배포 운영 런북 (데모)", data_class="internal",
                         embedding_model="embed-local", dims=MOCK_DIMS, owner="system:seed"))
    db.flush()
    for title, source, text in docs:
        parts = chunks(text)
        doc = KbDocument(id=new_id("kbd"), kb_id=kid, title=title, source=source,
                         sha256=hashlib.sha256(text.encode()).hexdigest(), chunks=len(parts), created_by="system:seed")
        db.add(doc)
        db.flush()
        for i, t in enumerate(parts):
            db.add(KbChunk(id=new_id("kbc"), kb_id=kid, doc_id=doc.id, seq=i, text=t, embedding=hash_embedding(t)))
    return ["knowledge-demo"]


def seed_profiles(db: Session) -> list[str]:
    """즉시 사용 템플릿 3종 (FR-W04): 사내 Q&A · 이슈 보고 자동화 · 장애 분석 위임."""
    import yaml

    from app.models import AgentProfile, AgentProfileVersion
    from app.services.profiles import apply_graph, sync_catalog
    for pid, (name, desc, exposed, text) in TEMPLATES.items():
        if db.get(AgentProfile, pid) is not None:
            continue
        pr = AgentProfile(id=pid, name=name, description=desc, status="published", version=1, template=True,
                          a2a_exposed=exposed, created_by="system:seed")
        apply_graph(pr, yaml.safe_load(text))
        db.add(pr)
        db.flush()
        db.add(AgentProfileVersion(id=new_id("apv"), profile_id=pid, version=1, graph=pr.graph, note="seed",
                                   created_by="system:seed"))
        sync_catalog(db, pr, "system:seed")
    if settings.seed_demo:
        for pid, (name, desc, text) in DEMO_PROFILES.items():
            if db.get(AgentProfile, pid) is not None:
                continue
            pr = AgentProfile(id=pid, name=name, description=desc, status="published", version=1, template=False,
                              created_by="system:seed")
            apply_graph(pr, yaml.safe_load(text))
            db.add(pr)
            db.flush()
            db.add(AgentProfileVersion(id=new_id("apv"), profile_id=pid, version=1, graph=pr.graph, note="seed demo",
                                       created_by="system:seed"))
            sync_catalog(db, pr, "system:seed")
    return ["agent-templates"]


def run() -> list[str]:
    keys.ensure_keys()
    from app.core import tracing
    tracing.ingest_token()  # 별도 컨테이너(mcp-servers·a2a-demo)가 공유 볼륨으로 읽는 OTLP 수신 토큰
    steps: list[str] = []
    with session_scope() as db:
        if db.get_bind().dialect.name == "postgresql":  # 동시 기동한 파드의 시드 직렬화(멱등 확인이 경합하지 않게)
            from app.db import unbounded
            unbounded(db)  # 다른 파드의 시드를 기다리는 동안 잠금 상한에 걸리지 않게 (65th)
            db.execute(sql_text("SELECT pg_advisory_xact_lock(727274102)"))
        steps += seed_identity(db)
        steps += seed_registry(db)
        steps += seed_gateway(db)
        steps += seed_models(db)
        steps += seed_mcp(db)
        steps += seed_a2a(db)
        steps += seed_knowledge(db)
        steps += seed_demo_idp(db)
        steps += seed_profiles(db)
    return steps


if __name__ == "__main__":
    print("seed:", ", ".join(run()) or "nothing to load")
