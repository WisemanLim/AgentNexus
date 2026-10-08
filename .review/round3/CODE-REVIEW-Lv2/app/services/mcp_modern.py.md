> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정 — listen 슬롯·Inspector·OpenAPI 시크릿) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 mcp_modern.py.md](../../../../round2/CODE-REVIEW-Lv2/app/services/mcp_modern.py.md)(L1-133 까지 미완) · [round2 mcp.py.md](../../../../round2/CODE-REVIEW-Lv2/app/api/mcp.py.md)

### 08. app/services/mcp_modern.py (listen 슬롯) + app/api/mcp.py (listen·Inspector·OpenAPI 시크릿)
**파일 요약 / File Summary**
- 역할 / Role: `mcp_modern` 은 MCP 최신 사양(요청 상태 토큰·`subscriptions/listen` 등) 지원. `app/api/mcp.py` 는 MCP 게이트웨이 HTTP 진입(`/mcp/{server}`)과 관리 API. 이번 범위: D20-④ listen 동시 구독 수를 **보유자 슬롯**으로(`mcp_modern.py:57-75`, `mcp.py:233`), D21-⑤ OpenAPI 서버 생성 시 시크릿 덮어쓰기 방지(`mcp.py:646-647`), 그리고 요청받은 Inspector 블록(`mcp.py:459-522`, D19-④ 이후 변경 없음 — 맥락 확인).
- 진입점 거리 / Distance: 0 — `POST /mcp/{server_id}`(listen), `POST /api/v1/mcp-servers/{id}/inspect`, `POST /api/v1/mcp-servers/openapi`.
- LOC: `mcp_modern.py` 507 (2차 스냅샷 500 → +7), `mcp.py` 791 (2차 스냅샷 778 → +13)
- 주요 export(범위 안): `LISTEN_*`·`MAX_LISTENS`, `listen_acquire`·`listen_release`·`listen_count`, `listen_events`; `_listen`·`_listen_open`, `InspectIn`·`_test_principal`·`inspect`, `create_from_openapi`
- 의존(내부): `app.services.ratelimit`(보유자 슬롯 — [ratelimit.py.md](ratelimit.py.md)), `app.services.scheduler.WORKER`(보유자 id 접두), `mcp_gateway`, `governance`, `policy`, `secrets`, `registry`
- 외부 패키지: starlette(`StreamingResponse`), pydantic
- 부수효과 / Side effects: Valkey ZSET `anx:h:listen:<sub>`, 감사(`mcp.subscribe`·`mcp.inspect.test_principal`), 시크릿 쓰기(`mcp/<ns>/backend`).
- 동시성 / Concurrency: listen 은 장기 SSE(최대 900초) — 워커·파드 간 구독 수 합을 Valkey 로 센다.
- 테스트 커버리지 흔적: `test/test_observe_perf2.py:106-124`(C55-03 죽은 워커 보유자 960초 뒤 해제), `test/test_remaining_items.py:65-78`(C60-05 namespace 충돌 시 시크릿 보존). Inspector 는 2차 이후 변경 없음(기존 시험 유지).
- 3차 변경 요약: D20-④ `mcp_modern.py:57-75`(보유자 id·고정 만료), `mcp.py:233`·`:237-241`. D21-⑤ `mcp.py:646-647`.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/mcp_modern.py lines=46-75
LISTEN_POLL_S = 5.0    # 목록 지문 비교 주기 — 정책(도구 규칙)·업스트림 변경을 같이 잡는다
LISTEN_PING_S = 15.0   # 프록시 유휴 종료 방지 SSE 주석
LISTEN_MAX_S = 900.0   # 이후 정상 종료(resultType complete) → 클라이언트가 다시 listen
MAX_LISTENS = 5        # 주체별 동시 구독 — 워커 전체 합(ratelimit 백엔드 카운터, prod 는 Valkey 공유 · D18-②)
LISTENS: dict[str, int] = {}  # 이 워커의 구독 수 (상태 확인용)


def _listen_key(sub: str) -> str:
    return f"listen:{sub}"


def listen_acquire(sub: str) -> str | None:
    """구독 1개 확보 → 보유자 id (한도 초과면 None). 워커 전체 합은 보유자 집합(prod 는 Valkey) — 만료(최대 구독 시간 + 여유)는
    넣을 때 정해져, 보유 워커가 반납 없이 죽어도 그 주체가 계속 구독하는 동안에도 정해진 시간 뒤 풀린다 (D20-④)."""
    import uuid

    from app.services import ratelimit, scheduler
    holder = f"{scheduler.WORKER}:{uuid.uuid4().hex[:12]}"
    return holder if ratelimit.backend().holder_acquire(_listen_key(sub), holder, MAX_LISTENS, int(LISTEN_MAX_S) + 60) else None


def listen_release(sub: str, holder: str | None) -> None:
    from app.services import ratelimit
    if holder:
        ratelimit.backend().holder_release(_listen_key(sub), holder)


def listen_count(sub: str) -> int:
    from app.services import ratelimit
    return ratelimit.backend().holder_count(_listen_key(sub))
```
**분석 / Analysis (Level 2)**
- L48-49: 구독은 최대 900초 뒤 정상 종료(클라이언트가 다시 listen), 주체별 동시 5개.
- L57-64 `listen_acquire`(D20-④): 보유자 id = `호스트:pid:무작위12`. 만료 = `LISTEN_MAX_S + 60` = 960초 — **스트림 최대 수명보다 60초 길게** 잡아, 살아 있는 구독의 슬롯이 먼저 만료되지 않게 했다. 반납 없이 워커가 죽어도 960초 뒤 저절로 풀린다(2차 M31 해결).
- 흔한 실수: 만료를 스트림 수명과 같게(900초) 두면, 마지막 폴링이 조금 늦어지는 순간 살아 있는 구독이 슬롯을 잃고 한도가 느슨해진다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 60초 여유는 "마지막 루프 한 바퀴(5초 대기 + 지문 조회)" 가 60초 안에 끝난다는 가정이다(`listen_events` L485-491). 업스트림 지문 조회(`_fingerprints`)가 업스트림 타임아웃(설정에 따라 수십 초)까지 걸리면 스트림이 만료 뒤에도 잠깐 살아 있을 수 있다 — 한도가 순간적으로 1 넘는 정도라 실해는 작다.

```python path=app/services/mcp_modern.py lines=474-507
async def listen_events(server_id: str, ctx, rid, honored: dict, alive, holder: str | None = None):
    """승인 알림 → (변경 시) 목록 변경 알림 … → LISTEN_MAX_S 뒤 정상 종료 응답. 모든 메시지에 subscriptionId(=요청 id)."""
    from app.services import mcp_gateway as gw
    tag = {K_SUB: rid}
    sub = ctx.principal.sub
    LISTENS[sub] = LISTENS.get(sub, 0) + 1
    try:
        yield sse({"jsonrpc": "2.0", "method": "notifications/subscriptions/acknowledged",
                   "params": {"_meta": tag, "notifications": honored}})
        last = await _fingerprints(server_id, ctx, honored)
        until, quiet = time.monotonic() + LISTEN_MAX_S, 0.0
        while time.monotonic() < until:
            await asyncio.sleep(LISTEN_POLL_S)
            if not await alive():  # 토큰 만료·폐기 → 구독도 끝
                yield sse(gw.RpcError(gw.FORBIDDEN, "인증이 만료되었거나 폐기되어 구독을 종료합니다.").body(rid))
                return
            try:
                now = await _fingerprints(server_id, ctx, honored)
            except gw.RpcError as exc:
                yield sse(exc.body(rid))
                return
            changed = [k for k in now if k in last and now[k][0] != last[k][0]]
            last.update(now)
            for k in changed:
                yield sse({"jsonrpc": "2.0", "method": _notification(k), "params": {**now[k][1], "_meta": tag}})
            quiet = 0.0 if changed else quiet + LISTEN_POLL_S
            if quiet >= LISTEN_PING_S:
                quiet = 0.0
                yield ": ping\n\n"
        yield sse({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "_meta": tag}})
    finally:
        LISTENS[sub] = max(LISTENS.get(sub, 1) - 1, 0)
        # 동기 호출 — 연결이 끊기면 anyio 취소 범위가 finally 안의 await 도 다시 취소해 반납이 빠진다
        listen_release(sub, holder)
```
**분석 / Analysis (Level 2)**
- 승인 알림 → 5초마다 인증 생존 확인·목록 지문 비교 → 변경 알림 → 15초 조용하면 SSE 주석 핑 → 900초 뒤 `complete`.
- L504-507: `finally` 에서 **동기** 반납. 주석이 이유를 정확히 적었다 — 연결이 끊기면 anyio 취소 범위가 `finally` 안의 `await` 도 다시 취소하므로, `await` 로 반납하면 빠진다. 이 교훈이 [llm.py.md](llm.py.md) H2 의 최소 수정 근거다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L507 동기 반납은 Valkey 왕복(소켓 타임아웃 0.5초, `app/services/ratelimit.py:122`)을 **이벤트 루프 위에서** 한다. Valkey 가 느리면 끊길 때마다 루프가 최대 0.5초 멈춘다(그 뒤 5초 동안은 로컬 백엔드로 즉시 처리). 지금 구조에서 동기 반납은 의도된 선택이고, 대안은 `asyncio.get_running_loop().run_in_executor(None, …)` 를 `await` 없이 던지는 것(실행기 Task 는 취소 범위 밖).
- 제너레이터가 **한 번도 시작되지 않고** 응답이 끝나면(스트리밍 시작 전 단절) `finally` 도 돌지 않는다 — 이 경우가 바로 고정 만료 960초가 필요한 이유다(설계가 맞다).

```python path=app/api/mcp.py lines=227-241
async def _listen(server_id: str, request: Request, p: Principal, rid, params: dict, hdr: dict):
    """subscriptions/listen — 승인(acknowledged) 후 목록 변경 알림을 흘린다. 스트림을 닫으면 구독 취소."""
    if "text/event-stream" not in request.headers.get("accept", ""):
        return Response(status_code=406)  # listen 응답은 언제나 알림 스트림
    honored, uris, task_ids = mcp_modern.listen_filter(params)
    await run_in_threadpool(gw._load_targets, server_id, p)
    holder = await run_in_threadpool(mcp_modern.listen_acquire, p.sub)  # 워커 전체 합 (D18-② · 보유자 단위 D20-④)
    if holder is None:
        raise RpcError(gw.RATE_LIMITED, f"동시 구독 한도({mcp_modern.MAX_LISTENS}) 초과", {"limit": mcp_modern.MAX_LISTENS},
                       http_status=429)
    try:
        return await _listen_open(server_id, p, rid, params, hdr, request, honored, uris, task_ids, holder)
    except BaseException:  # 스트림을 열기 전에 실패하면 확보한 구독을 돌려준다(열린 뒤에는 listen_events 가 돌려준다)
        mcp_modern.listen_release(p.sub, holder)  # 동기 — 취소 중에도 반납
        raise
```
**분석 / Analysis (Level 2)**
- L233: 슬롯 확보는 동기 Valkey 호출이라 스레드풀로. 없으면 429(JSON-RPC `RATE_LIMITED`).
- L237-241: 스트림을 열기 **전** 실패(권한·리소스 확인 등)면 확보한 슬롯을 돌려준다. `except BaseException` 이라 취소(`CancelledError`)에도 반납 — 반납이 동기라 취소 중에도 실행된다. 스트림이 열린 뒤에는 `listen_events` 의 `finally` 가 돌려준다.

```python path=app/api/mcp.py lines=259-275
    def _check():
        with SessionLocal() as db:
            authenticate_token(db, p.raw_token)

    async def alive() -> bool:
        if not p.raw_token:
            return True
        try:
            await run_in_threadpool(_check)
            return True
        except ApiError:
            return False
    await run_in_threadpool(lambda: audit.append("mcp.subscribe", actor=p.sub, actor_kind=p.kind, target=server_id,
                                                 data={"subscription_id": rid, "notifications": sorted(honored),
                                                       "resources": len(readable)}))
    return StreamingResponse(mcp_modern.listen_events(server_id, ctx, rid, honored, alive, holder),
                             media_type="text/event-stream", headers={**hdr, **SSE_HEADERS})
```
**분석 / Analysis (Level 2)**
- L263-270 `alive`: 원래 토큰을 5초마다 다시 인증해 만료·폐기된 토큰의 구독을 끝낸다(D18). 키 회전(D21-③) 뒤에도 유예 중 이전 키로 서명된 토큰은 계속 통과한다([keys.py.md](../core/keys.py.md) L134-144).

```python path=app/api/mcp.py lines=459-522
class InspectIn(BaseModel):
    method: str = "tools/list"
    params: dict = {}
    data_class: str = "internal"
    approval_id: str | None = None
    test_principal: TestPrincipalIn | None = Field(None, description="시험 전용 가상 사용자 (SCR-F06 ⑤) — 역할·팀만, 실제 사용자 대리 없음")


def _test_principal(p: Principal, t: TestPrincipalIn) -> Principal:
    """가상 시험 주체 — 호출자보다 넓은 역할은 줄 수 없다(관리자 제외). 감사 actor 는 실제 호출자."""
    unknown = [r for r in t.roles if r not in ROLE_PERMS]
    if unknown:
        raise bad_request(f"알 수 없는 역할: {', '.join(unknown)}", code="unknown_role")
    if "*" not in p.permissions():
        mine = p.permissions()
        wider = [r for r in t.roles if not role_perms(r) <= mine]
        if wider:
            raise ApiError(403, "test_roles_exceed", f"내 권한보다 넓은 역할로 시험할 수 없습니다: {', '.join(wider)}")
    return Principal(sub=f"test:{p.sub}", kind="test", name="시험 사용자", roles=list(t.roles), team=t.team, actor=p.sub)


def _write_tool(server_id: str, tool: str) -> bool:
    with SessionLocal() as db:
        r = db.scalars(select(ToolRule).where(ToolRule.server_id == server_id, ToolRule.tool == tool)).first()
        return bool(r and (r.risk != "low" or r.approval_required))


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
            def _judge():
                from app.services import governance
                with SessionLocal() as db:
                    d = governance.check_tool(db, tp, server_id, tool, (body.params or {}).get("arguments") or {},
                                              data_class=body.data_class, create_pending=False)
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
- Inspector: 콘솔에서 MCP 메서드를 게이트웨이 경로 그대로 시험. `test_principal` 이면 가상 주체(호출자보다 넓은 역할 금지, L472-476)로 판정하고, 쓰기·위험 도구는 실제 호출 없이 정책 판정만(드라이런, L493-508).
- L511-513(D19-④): 데이터 등급은 클라이언트 값을 그대로 쓰지 않고 서버 판정(`declared_data_class`) — 2차 M23 해결.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L21** 드라이런 분기(L502-503)는 아직 클라이언트가 보낸 `body.data_class` 를 그대로 `check_tool` 에 넘긴다 — 실제 호출 경로(L511)와 다르게 판정한다. 모르는 등급 문자열이면 등급 조건 정책에 걸리지 않아(2차 M10 과 같은 원리) 드라이런이 "허용" 을 보여 줄 수 있다. 호출은 하지 않으므로 오해를 주는 정도다. L511 의 판정을 드라이런 앞으로 옮기면 된다.
- [위험:중간] (2차 이월, VERIFY:71 기록) L456·L477 가상 주체의 `team` 은 자유 문자열 — 비관리자가 다른 팀 이름으로 읽기 도구(드라이런이 아닌 L509-517 실제 호출)를 부를 수 있다(2차 M22). 이번 범위에서 바뀌지 않았다.

```python path=app/api/mcp.py lines=643-651
    if body.secret and body.identity == "service":
        def _sec():
            with SessionLocal() as db:
                if db.get(McpServer, body.namespace) is not None:  # 다른 서버의 백엔드 시크릿을 덮지 않게 — 충돌은 쓰기 전에 (D21-⑤)
                    raise conflict(f"namespace '{body.namespace}' 는 이미 사용 중입니다.", code="namespace_taken")
                secrets.put(db, f"mcp/{body.namespace}/backend", body.secret, kind="token")
                db.commit()
        await run_in_threadpool(_sec)
        hosted["secret_ref"] = f"mcp/{body.namespace}/backend"
```
**분석 / Analysis (Level 2)**
- L646-647(D21-⑤): 같은 namespace 의 MCP 서버가 이미 있으면 **시크릿을 쓰기 전에** 409 — 전에는 시크릿을 먼저 덮어써 다른 서버의 백엔드 자격이 바뀌었다(C60-05).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L20** ① 검사 대상이 `McpServer` 행뿐이다. 같은 이름의 `Secret`(`mcp/<ns>/backend`)이 서버 행 없이 남아 있으면(서버 삭제 후 시크릿 잔존, 또는 운영자가 다른 서버의 `secret_ref` 로 그 이름을 쓰는 경우) `secrets.put` 은 **조용히 덮어쓴다**(`app/services/secrets.py:57-71` — 있으면 갱신). ② 확인과 쓰기 사이(L646 → L648)와 서버 생성(L669-672) 사이에 잠금이 없어 같은 namespace 동시 생성이면 둘째 요청의 시크릿이 첫째 서버에 묶인다. ③ 시크릿을 쓴 뒤 게시(L669)가 `version_exists` 등으로 실패하면 시크릿만 남는다. 권한(`catalog:approve`+`mcp:write`)이 높아 실해는 작다. 최소 수정: 같은 이름의 `Secret` 존재도 409, 서버 생성과 시크릿 쓰기를 한 트랜잭션으로.

## 파일 평가 / File Assessment
- 강점: (1) listen 슬롯을 보유자 + 고정 만료로 바꾸면서 만료를 스트림 최대 수명보다 길게 잡아 "살아 있는 구독 보호" 와 "죽은 워커 몫 해제" 를 함께 만족(`app/services/mcp_modern.py:57-64`). (2) 취소 중에도 반납되도록 동기 반납을 의도적으로 썼고 이유를 주석으로 남겼다(`app/services/mcp_modern.py:504-507`, `app/api/mcp.py:237-241`). (3) OpenAPI 생성의 시크릿 덮어쓰기를 쓰기 전 충돌 검사로 막았다(`app/api/mcp.py:646-647`).
- 위험: (1) [위험:중간] (2차 이월) Inspector 가상 주체 팀 자유 지정(`app/api/mcp.py:456`, `:477`). (2) [위험:낮음] 드라이런 판정이 실제 경로와 다른 등급 사용(`app/api/mcp.py:502-503`). (3) [위험:낮음] 같은 이름 시크릿 덮어쓰기·생성 경합(`app/api/mcp.py:643-651`).
- 개선 권고(영향순): (1) 비관리자 가상 주체는 자기 팀만. (2) 드라이런도 서버 판정 등급. (3) 시크릿 이름 충돌 409 + 트랜잭션.
- 파일 위험 등급 / Risk grade: 낮음 — D20·D21 hunk 자체는 [위험:낮음]만. [위험:중간] 1건은 2차 기록 항목 이월.
- 재읽기 권고 레벨: 2 — 취소·단절 시 반납 순서(동기 vs `await`)가 이 파일의 핵심 교훈이라 줄 단위로.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M31 listen 증감 카운터 TTL 연장 → 429 고착 | 해결 | `app/services/mcp_modern.py:57-75`, `app/services/ratelimit.py:64-83`·`:107-113` · 시험 `test/test_observe_perf2.py:106-124` |
| M22 Inspector 가상 주체 `team` 무제한 + 드라이런 규칙 원문 키 | 미해결(VERIFY:71 기록) | `app/api/mcp.py:456`, `:477`, `:480-483` |
| M23 Inspector `data_class` 를 서버 판정 없이 | 부분 해결 | 실제 호출 `:511-513`(D19-④), 드라이런 `:502-503` 은 원문 |
| M25 `operationId` 없는 operation 노출 | 해결(D19-④) | `app/api/mcp.py:604` |
| (2차 secrets.py.md L100) `secrets.put` 이 이름 upsert — `create_from_openapi` 가 같은 namespace 의 다른 서버 시크릿을 조용히 덮어씀 | 부분 해결 | 서버 행 충돌은 쓰기 전 409 `:646-647`. `put` 의 upsert 자체(`app/services/secrets.py:57-71`)·서버 행 없는 같은 이름 시크릿·경합은 남음(L20) |
| 2차 `mcp_modern.py.md` 미완(L1-133) | 이번 범위 hunk 만 보완 | `:46-75`, `:474-507` |
