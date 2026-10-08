> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 mcp_modern.py.md](../../../../round3/CODE-REVIEW-Lv2/app/services/mcp_modern.py.md)(mcp.py 의 Inspector·OpenAPI 절 포함)

### 06. app/api/mcp.py (Inspector 드라이런 · OpenAPI 서버 생성) (+ `app/services/secrets.py:57-71` · `app/services/registry.py:276-287`)
**파일 요약 / File Summary**
- 역할 / Role: MCP 서버 관리 API와 게이트웨이 진입(`/mcp/{server}`). 이번 범위는 두 절 — Inspector(SCR-F06, 도구 시험)와 OpenAPI → MCP 서버 생성(FR-S02, SCR-F04).
- 진입점 거리 / Distance: 0 — HTTP 라우터.
- LOC: 813 (3차 791 → 813)
- 주요 export: `router`·`inspect`·`create_from_openapi`·`select_operations`
- 의존(내부): `app.services.{policy, governance, registry, secrets, audit, mcp_gateway as gw}`, `app.db.SessionLocal`
- 외부 패키지: FastAPI, Starlette(`run_in_threadpool`), SQLAlchemy
- 부수효과 / Side effects: `secrets`·`token_exchange_policies`·`catalog_items`·`mcp_servers`·`tool_rules` 쓰기, 감사.
- 동시성 / Concurrency: async 핸들러 + DB 작업은 단계마다 별도 세션·별도 커밋(스레드풀).
- 테스트 커버리지 흔적: `test/test_boundaries4.py:117`(C64-04 기존 시크릿 409·실패 뒤 정리), `:142`(C64-05 드라이런 서버 판정 등급).
- 4차 변경 요약 / Changes in D22: D22-④ — Inspector 드라이런도 서버 판정 데이터 등급(3차 L21), OpenAPI 생성 전 같은 이름 시크릿 409·게시/등록 실패 시 이번에 쓴 시크릿 삭제(3차 L20).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/mcp.py lines=486-527
@router.post("/api/v1/mcp-servers/{server_id}/inspect")
async def inspect(server_id: str, body: InspectIn, p: Principal = Depends(require("mcp:read"))):
    """MCP 도구 시험 (SCR-F06 Inspector) — 게이트웨이 경로 그대로(정책·승인·소독 적용) 호출.
    test_principal 이면 가상 시험 주체로 판정·호출하고, 쓰기·위험 도구는 호출하지 않는 드라이런(정책 판정만)."""
    if body.test_principal is not None:
        tp = _test_principal(p, body.test_principal)
        tool = (body.params or {}).get("name")
        dry = body.method == "tools/call" and await run_in_threadpool(_write_tool, server_id, tool or "")

        def _audit(extra: dict):
            audit.append("mcp.inspect.test_principal", actor=p.sub, target=f"{server_id}.{tool or body.method}",
                         data={"roles": tp.roles, "team": tp.team, "method": body.method, **extra})
        if dry:
            # 드라이런도 실제 호출과 같은 서버 판정 등급으로 — 클라이언트 신고 원문을 그대로 쓰지 않는다 (D22-④)
            dry_dc = await run_in_threadpool(policy.declared_data_class, None, tp, body.data_class)
            if dry_dc not in policy.DATA_CLASSES:
                raise bad_request(f"data_class 는 {policy.DATA_CLASSES} 중 하나여야 합니다.", code="invalid_data_class")

            def _judge():
                from app.services import governance
                with SessionLocal() as db:
                    d = governance.check_tool(db, tp, server_id, tool, (body.params or {}).get("arguments") or {},
                                              data_class=dry_dc, create_pending=False)
                    return {"allow": d.allow, "code": d.code, "reason": d.reason, "risk": d.risk}
            dec = await run_in_threadpool(_judge)
            await run_in_threadpool(_audit, {"dry_run": True, "decision": dec["code"]})
            return {"request": {"method": body.method, "params": body.params}, "test_principal": {"sub": tp.sub, "roles": tp.roles, "team": tp.team},
                    "dry_run": True, "decision": dec, "result": None, "error": None, "latency_ms": 0.0, "trace_id": current_trace_id()}
        await run_in_threadpool(_audit, {"dry_run": False})
        p = tp
    dc = await run_in_threadpool(policy.declared_data_class, None, p, body.data_class)  # 신고 등급은 서버 판정 (D19-④)
    if dc not in policy.DATA_CLASSES:
        raise bad_request(f"data_class 는 {policy.DATA_CLASSES} 중 하나여야 합니다.", code="invalid_data_class")
    ctx = RpcContext(p, data_class=dc, approval_id=body.approval_id)
    t0 = time.perf_counter()
    try:
        result, error = await dispatch(server_id, ctx, body.method, body.params), None
    except RpcError as exc:
        result, error = None, {"code": exc.code, "message": exc.message, "data": exc.data}
    return {"request": {"method": body.method, "params": body.params}, "result": result, "error": error,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "trace_id": current_trace_id(),
            **({"test_principal": {"sub": p.sub, "roles": p.roles, "team": p.team}, "dry_run": False} if p.kind == "test" else {})}
```
**분석 / Analysis (Level 2)**
- L490-493: 가상 시험 주체(`test:<sub>`)이고 `tools/call` 이며 쓰기·위험 도구면 **드라이런**(호출 없이 판정만).
- L499-502(D22-④): 드라이런도 `policy.declared_data_class(None, tp, body.data_class)` — 실제 호출 경로(L516)와 **같은 함수·같은 주체**로 등급을 정한다. 전에는 클라이언트가 보낸 `data_class` 원문을 그대로 판정에 썼다(3차 L21 — `public` 이라 우겨 `confidential` 규칙을 피한 "허용" 판정을 볼 수 있었다).
- L507-508: `create_pending=False` — 드라이런이 승인 대기 행을 만들지 않게.
- 흔한 실수: 판정 함수와 실행 함수가 입력 정규화를 따로 하면 "시험은 통과, 실제는 거부"(또는 반대)가 생긴다. 이 hunk 가 정확히 그 불일치를 없앴다. 64th r1 에서 시험이 실제 호출로 빠졌던 것(도구 규칙 새로고침 전)은 시험 순서 문제였고 코드 결함이 아니다(`test/impl/64th/result.md` r1 행).

```python path=app/api/mcp.py lines=629-665
@router.post("/api/v1/mcp-servers/openapi", status_code=201)
async def create_from_openapi(body: OpenApiIn, p: Principal = Depends(require("catalog:approve", "mcp:write"))):
    """Internal API MCP Server 생성 (FR-S02, SCR-F04) — OpenAPI 의 각 operation 을 MCP 도구로 노출(FastMCP)."""
    if not str(body.openapi.get("openapi", "")).startswith("3"):
        raise bad_request("OpenAPI 3.x 문서가 필요합니다.")
    spec, risks = select_operations(body.openapi, body.operations) if body.operations else (body.openapi, {})
    ref = f"mcp/{body.namespace}/backend"

    def _free():  # 쓰기 전에 — 남의 namespace 의 교환 정책·시크릿을 만들거나 덮지 않게 (D21-⑤·D22-④)
        with SessionLocal() as db:
            if db.get(McpServer, body.namespace) is not None:
                raise conflict(f"namespace '{body.namespace}' 는 이미 사용 중입니다.", code="namespace_taken")
            if body.secret and body.identity == "service" and db.scalars(select(Secret).where(Secret.name == ref)).first():
                raise conflict(f"시크릿 '{ref}' 가 이미 있습니다 — 다른 namespace 를 쓰거나 시크릿을 먼저 정리하세요.",
                               code="secret_exists")
    await run_in_threadpool(_free)
    hosted = {"openapi": spec, "base_url": body.base_url, "auth_header": body.auth_header, "identity": body.identity}
    if body.identity == "obo":  # 사용자 위임 — 내부 API 는 사용자 신원(sub·팀·역할)을 받는다. 서비스 시크릿은 쓰지 않는다
        hosted["scopes"] = body.scopes

        def _pol():
            with SessionLocal() as db:
                aud = f"api:{body.namespace}"
                pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == aud)).first()
                if pol is None:
                    db.add(TokenExchangePolicy(id=new_id("txp"), audience=aud, audience_type="api", allowed_scopes=body.scopes,
                                               default_scopes=body.scopes, ttl_seconds=300, method="rfc8693"))
                    db.commit()
        await run_in_threadpool(_pol)
    if body.secret and body.identity == "service":
        def _sec():
            with SessionLocal() as db:
                secrets.put(db, ref, body.secret, kind="token")
                db.commit()
        await run_in_threadpool(_sec)
        hosted["secret_ref"] = ref
    ops = [op.get("operationId") for path in spec.get("paths", {}).values() for op in path.values()
```
**분석 / Analysis (Level 2)**
- L637-644 `_free`(D22-④): 쓰기 **전에** namespace(서버 행)와 같은 이름 시크릿 `mcp/<ns>/backend` 를 확인해 409. 전에는 남의 namespace 시크릿을 조용히 덮었다(3차 L20).
- L646-657: `obo` 면 `api:<ns>` 교환 정책을 만든다(이미 있으면 두기).
- L658-664: `service` 면 시크릿 저장 — 별도 세션·별도 커밋.

```python path=app/api/mcp.py lines=666-709
           if isinstance(op, dict) and op.get("operationId")]
    doc = {"name": f"io.agentnexus.internal/{body.namespace}", "title": body.title[:100],
           "description": body.description[:100], "version": body.version,
           "packages": [{"registryType": "oci", "identifier": "agentnexus/hosted-openapi",
                         "transport": {"type": "streamable-http"}}],
           "_meta": {"hosted": hosted, "tools": ops, "license": "internal"}}

    def _mark():
        with SessionLocal() as db:
            s = db.get(McpServer, body.namespace)
            s.transport, s.endpoint = "hosted-openapi", None
            db.commit()
    t0 = time.perf_counter()

    def _do():
        with SessionLocal() as db:
            item, _, _ = registry.publish(db, "mcp_server", doc["name"], doc["version"], doc, p.sub, source="manual")
            if item.status == "pending":
                registry.approve(db, item, None, p.sub, "OpenAPI 서버 생성")
            return registry.register(db, item, p.sub, {"namespace": body.namespace})
    try:
        inst = await run_in_threadpool(_do)
    except Exception:
        if hosted.get("secret_ref"):  # 게시·등록 실패 — 이번에 쓴 시크릿을 남기지 않는다 (D22-④)
            def _drop():
                with SessionLocal() as db:
                    db.query(Secret).filter(Secret.name == ref).delete()
                    db.commit()
            await run_in_threadpool(_drop)
        raise
    await run_in_threadpool(_mark)
    inst["transport"] = "hosted-openapi"
    inst["refresh"] = await gw.refresh_tools(body.namespace, p)
    if risks:  # 운영자가 정한 위험도 (고위험 = 사람 승인)
        def _risk():
            with SessionLocal() as db:
                for r in db.scalars(select(ToolRule).where(ToolRule.server_id == body.namespace)):
                    if r.tool in risks:
                        r.risk, r.approval_required = risks[r.tool], risks[r.tool] == "high"
                db.commit()
        await run_in_threadpool(_risk)
        inst["risks"] = risks
    inst["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return inst
```
**분석 / Analysis (Level 2)**
- L680-685 `_do`: 게시 → (대기면) 승인 → 등록. L686-695(D22-④): 이 셋 중 하나가 예외면 이번에 쓴 시크릿을 지우고 다시 던진다.
- L696-708: 전송 표시·도구 새로고침·위험도 반영 — `_do` 뒤 단계의 실패는 시크릿을 남긴다(서버가 이미 등록됐으므로 맞다).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L11** PRD 의 "한 트랜잭션"(`PRD.md:103` D22-④ "OpenAPI 시크릿 같은 이름 409·한 트랜잭션")과 달리 **확인 → 정책 커밋 → 시크릿 커밋 → 게시·승인·등록 커밋** 이 각각 다른 세션이다. 그래서 ① 같은 namespace 로 동시에 두 요청이 오면 둘 다 `_free` 를 통과하고, `secrets.put` 은 **있으면 덮는**(upsert) 함수라(`app/services/secrets.py:58-69`) 늦은 쪽 값으로 바뀐 뒤, 등록에서 진 쪽의 정리(L689-694)가 **이긴 쪽 서버가 쓰는 시크릿을 지운다**. ② `registry.register` 는 커밋한 **뒤** 감사를 남기므로(`app/services/registry.py:285-286`) 감사 기록이 실패하면 서버는 등록됐는데 예외가 올라와 정리가 그 서버의 시크릿을 지운다. 둘 다 관리자 작업·드문 실패지만 결과는 "등록된 서버가 백엔드 자격 없이 동작". 최소 수정: 시크릿은 "없을 때만 삽입"(이름 유니크 제약 → `IntegrityError` 를 409)으로 만들고 이번 요청이 **만든 경우에만** 정리, 감사 실패는 정리 조건에서 제외(등록 성공 여부로 판단).
- [위험:낮음] **L11(관찰)** obo 경로의 교환 정책(L649-657)은 실패해도 정리하지 않는다 — 같은 namespace 로 다시 만들 때 `_pol` 이 "이미 있음" 으로 넘어가 **새 요청의 scopes 가 반영되지 않는다**.

```python path=app/services/secrets.py lines=57-71
def put(db: Session, name: str, value: str, kind: str = "api_key", description: str | None = None) -> Secret:
    row = db.scalars(select(Secret).where(Secret.name == name)).first()
    cipher = keys.fernet().encrypt(value.encode()).decode()
    if row is None:
        row = Secret(id=new_id("sec"), name=name, kind=kind, ciphertext=cipher, last4=value[-4:] if len(value) >= 8 else None,
                     description=description)
        db.add(row)
    else:
        row.ciphertext = cipher
        row.last4 = value[-4:] if len(value) >= 8 else None
        row.rotated_at = utcnow()
        if description is not None:
            row.description = description
    db.flush()
    return row
```
**분석 / Analysis (Level 2)**
- `put` 은 이름으로 찾아 있으면 암호문을 바꾸고 `rotated_at` 을 찍는다(upsert). OpenAPI 경로가 "새로 만든 시크릿" 이라고 가정하는 것과 맞지 않는 지점 — L11 의 근거.

```python path=app/services/registry.py lines=276-287
def register(db: Session, item: CatalogItem, actor: str, options: dict | None = None) -> dict:
    if item.status not in ("approved", "active"):
        raise bad_request(f"승인된 항목만 등록할 수 있습니다 (현재 {item.status}).", code="not_approved")
    handler = REGISTER_HANDLERS.get(item.type)
    if handler is None:
        raise bad_request(f"{item.type} 등록 핸들러가 없습니다.", code="no_handler")
    instance = handler(db, item, actor, options or {})
    item.status = "active"
    item.ref_id = instance["id"]
    db.commit()
    audit.append("catalog.register", actor=actor, target=f"{item.type}:{item.name}", data={"instance": instance["id"]})
    return instance
```
**분석 / Analysis (Level 2)**
- L285 커밋 → L286 감사. 감사 함수는 자체 세션·트랜잭션(`app/services/audit.py:52-56`)이라 여기서 실패하면 등록은 이미 확정된 상태로 예외만 올라간다 — L11 ② 의 근거.

## 파일 평가 / File Assessment
- 강점: (1) 드라이런·실제 호출이 같은 등급 판정 함수를 쓴다(`app/api/mcp.py:500`, `:516`). (2) 쓰기 전 409 로 남의 namespace 시크릿 덮어쓰기를 막았다(`:637-644`). (3) 실패 정리 경로가 생겼다(`:686-695`).
- 위험: (1) [위험:낮음] L11 — 단계별 커밋 + upsert 시크릿 + 정리 조건 때문에 동시 요청·감사 실패에서 등록된 서버의 시크릿이 지워질 수 있음(`:637-695`).
- 개선 권고(영향순): (1) 시크릿 "없을 때만 삽입" + 만든 경우에만 정리. (2) 정책·시크릿·게시·등록을 한 세션 한 커밋으로(게시·승인·등록 함수가 커밋하지 않는 변형 필요). (3) obo 정책도 실패 시 정리 또는 scopes 갱신.
- 파일 위험 등급 / Risk grade: **낮음** — [위험:낮음]만.
- 재읽기 권고 레벨: 3 — 두 절 모두 블록 단위로 충분하다. 등록 함수의 커밋 위치만 함께 볼 것.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| L20 서버 없는 같은 이름 시크릿 덮어쓰기·게시 실패 시 시크릿 잔존 | 해결(순차 경로) · 동시·감사 실패 경로(L11) | `app/api/mcp.py:637-695` |
| L21 Inspector 드라이런 `data_class` 원문 | 해결 | `:499-502` |
| M22 Inspector 가상 주체 팀(2차 이월) | 미해결(기록 유지) | `:477` |
