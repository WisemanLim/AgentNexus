> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 a2a_gateway.py.md](../../../../round2/CODE-REVIEW-Lv2/app/services/a2a_gateway.py.md)

### 06. app/services/a2a_gateway.py (+ app/api/a2a.py 의 카드·푸시·PATCH hunk, app/services/a2a_server.py:182)
**파일 요약 / File Summary**
- 역할 / Role: A2A 게이트웨이 — 원격 에이전트 카드 조회·검증, 업스트림 인증(토큰 교환·client_credentials·API 키), JSON-RPC·REST·gRPC 바인딩 중계, Task 영속, 푸시 수신·서명 검증·클라이언트 웹훅 중계, 감시기 폴링. 12차 D21-① 로 **원격 경계**가 조여졌다 — 원격 응답 8 MiB 상한·비 JSON 처리·SSE 이벤트 상한·history 200 개 상한·푸시 JWT 의 `iss`·`jti`·본문 해시 필수. D21-⑤ 로 토큰 캐시 폐기·사용량 오류 상태·웹훅 토큰 암호화.
- 진입점 거리 / Distance: 1 — `POST /a2a/{agent_id}`·REST 바인딩(`app/api/a2a.py`) → `proxy`/`_proxy`. 푸시 `POST /a2a/push/{token}`(`app/api/a2a.py:298-310`) → `receive_push`. 감시기 `watcher`(`app/main.py:111-113`).
- LOC: 956 (2차 스냅샷 886 → +70)
- 주요 export(범위 안): `drop_tokens`, `MAX_REMOTE_BYTES`·`HISTORY_MAX`, `_fetch`·`_json_body`, `record`, `_save_push`, `_stream`, `verify_push_jwt`, `receive_push`, `_relay_push`
- 의존(내부): `app.core.keys`(웹훅 토큰 암호화), `app.services.ratelimit`(jti 1회), `identity`(교환), `secrets`, `a2a_proto`·`a2a_rest`·`a2a_grpc`, `app.core.http`
- 외부 패키지: httpx(스트리밍), PyJWT(`PyJWK`), starlette
- 부수효과 / Side effects: 아웃바운드 HTTP·gRPC, `a2a_tasks` 쓰기, 감사, 프로세스 캐시 `_TOKENS`·`_JWKS_CACHE`, 백그라운드 Task(`_PUSH_RELAYS`).
- 동시성 / Concurrency: 이벤트 루프 + 스레드풀(DB·복호화). `_TOKENS` 는 이벤트 루프와 동기 PATCH 핸들러(스레드풀) 양쪽에서 바뀐다.
- 테스트 커버리지 흔적: `test/test_a2a_boundaries.py:30-65`(C56-01 푸시 JWT), `:67-85`(C56-02 상한·비 JSON), `:87-105`(C56-03 깨진 이벤트), `:107-116`(C56-04 history), `:118-127`(C56-05 카드 URL), `test/test_identity_boundaries.py:98-105`(C53-06 push_verify 권한), `test/test_remaining_items.py:55-61`(C60-04 토큰 캐시), `:104-115`(C60-08 사용량), `:117-131`(C60-09 웹훅 토큰), `:133-150`(C60-10 실행기 오류).
- 3차 변경 요약: D20-② push_verify 변경 통제(`app/api/a2a.py:556-558`). D21-① L351-372·L393·L434·L442·L724-732·L814-845, 카드 URL(`app/api/a2a.py:254`), issuer 필수(`app/api/a2a.py:562-563`). D21-⑤ L276-281·L619-621·L698-700·L899-902, `app/api/a2a.py:568`, `app/services/a2a_server.py:182-186`.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_gateway.py lines=235-281
_TOKENS: dict[tuple, tuple[float, str]] = {}


def _cache_token(key: tuple, expires: float, token: str) -> None:
    if len(_TOKENS) >= 1024:  # 만료분 정리 — 한 번 왔다 다시 오지 않는 주체·대상 조합이 쌓이지 않게
        now = time.time()
        for k in [k for k, (exp, _) in _TOKENS.items() if exp <= now]:
            _TOKENS.pop(k, None)
    _TOKENS[key] = (expires, token)


async def upstream_headers(agent: AgentRef, p: Principal) -> dict:
    scheme = agent.auth.get("scheme", "none")
    if scheme in ("none", None, ""):
        return {}
    if scheme == "apiKey":
        value = await run_in_threadpool(_secret, agent.auth.get("secret_ref"))
        if not value:
            raise A2AError(-32603, f"{agent.id}: API 키 미설정")
        return {agent.auth.get("header", "X-API-Key"): value}
    if scheme == "oauth2":  # OAuth2 client_credentials (SCR-G04 ②) — 게이트웨이가 발급·캐시(만료 30초 전 갱신)
        return {"Authorization": f"Bearer {await _client_credentials(agent)}"}
    if agent.auth.get("secret_ref") and not agent.auth.get("token_exchange", True):
        value = await run_in_threadpool(_secret, agent.auth["secret_ref"])
        return {"Authorization": f"Bearer {value}"}
    key = (p.sub, agent.id)
    hit = _TOKENS.get(key)
    if hit and hit[0] - 30 > time.time():
        return {"Authorization": f"Bearer {hit[1]}"}

    def _exchange():
        with SessionLocal() as db:
            return identity.exchange(db, p, f"a2a:{agent.id}", None, actor="gateway")
    try:
        tok = await run_in_threadpool(_exchange)
    except Exception as exc:  # noqa: BLE001 — token_exchange_failed
        raise A2AError(-32003, getattr(exc, "detail", str(exc)), {"code": "token_exchange_failed"}) from exc
    _cache_token(key, time.time() + tok["expires_in"], tok["access_token"])
    return {"Authorization": f"Bearer {tok['access_token']}"}


def drop_tokens(agent_id: str) -> int:
    """에이전트 인증 설정이 바뀌면 이 워커의 캐시 토큰(client_credentials·교환)을 버린다 (D21-⑤) — 다른 워커는 만료까지."""
    gone = [k for k in list(_TOKENS) if k == ("cc", agent_id) or (len(k) > 1 and k[1] == f"a2a:{agent_id}")]
    for k in gone:
        _TOKENS.pop(k, None)
    return len(gone)
```
**분석 / Analysis (Level 2)**
- L235-243: 업스트림 토큰 캐시(워커 로컬 dict). 1024개를 넘으면 만료분만 정리.
- L255-256: `scheme == "oauth2"` → client_credentials 토큰(키 `("cc", 에이전트 id)`, L285).
- L260-273: 기본(교환) — 호출 주체 토큰을 `a2a:{id}` 대상 토큰으로 RFC 8693 교환해 캐시. **키는 `(p.sub, agent.id)`**(L260).
- L276-281 `drop_tokens`(D21-⑤): 에이전트 인증 설정이 바뀌면 이 워커의 캐시 토큰을 버린다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L14 — 교환 토큰 키를 지우지 못한다.** L278 은 `k[1] == f"a2a:{agent_id}"` 를 찾지만, 실제 교환 토큰 키는 `(p.sub, agent.id)`(L260) — 두 번째 요소가 `"a2a:"` 접두 없는 id 다. 그래서 L278 의 두 번째 조건은 **절대 참이 되지 않고**, client_credentials 토큰만 지워진다. 시험은 코드가 만들지 않는 3원소 키(`("usr_user", f"a2a:{AG}", ())`)를 직접 넣어 통과한다(`test/test_remaining_items.py:55-61`) — MCP 게이트웨이의 키 모양(`app/services/mcp_gateway.py:140`)을 옮겨 쓴 것으로 보인다. 지금 교환 토큰은 에이전트 `auth` 를 쓰지 않아(L267 은 스코프 `None`) 실해는 작지만, 의도(C60-04)와 시험이 모두 어긋나 있다. 최소 수정: L278 을 `k == ("cc", agent_id) or (len(k) == 2 and k[1] == agent_id)` 로, 시험은 `upstream_headers` 를 실제로 불러 채운 키로.
- [위험:낮음] 같은 dict 에서 client_credentials 키 `("cc", id)` 와 교환 키 `(sub, id)` 가 **이름 공간을 공유**한다. 주체 sub 가 문자열 `"cc"` 면 두 키가 같아진다. Access Token 의 `subject` 는 자유 문자열이라(`app/api/identity.py:458-463`, 사용자 sub 만 막음 `:496-499`) `tokens:issue` 보유자가 만들 수 있다. 에이전트의 scheme 이 바뀌는 순간(다른 워커는 L276 이 안 지움) 한 주체의 교환 토큰이 모든 사용자의 client_credentials 토큰으로 쓰일 수 있는 조합이다. 가능성은 낮다. 최소 수정: 키에 종류 접두(`("x", sub, id)`).
- [위험:낮음] `drop_tokens` 는 PATCH 핸들러(동기, 스레드풀)에서 **커밋 전**에 불린다(`app/api/a2a.py:568` → `:575`). 그 사이 같은 워커의 다른 요청이 옛 설정(아직 커밋 안 됨)으로 토큰을 받아 다시 캐시할 수 있다. 커밋 뒤로 옮기면 된다.

```python path=app/services/a2a_gateway.py lines=351-397
MAX_REMOTE_BYTES = 8 * 1024 * 1024  # 원격 응답·이벤트 상한 (D21-①) — 큰 응답으로 게이트웨이 메모리를 소모시키지 못하게
HISTORY_MAX = 200  # 저장하는 Task history 메시지 수 (최근 것부터)


async def _fetch(http, method: str, url: str, **kw) -> tuple[int, bytes]:
    async with http.stream(method, url, **kw) as res:
        buf = bytearray()
        async for chunk in res.aiter_bytes():
            buf += chunk
            if len(buf) > MAX_REMOTE_BYTES:
                raise A2AError(-32603, f"원격 응답이 상한({MAX_REMOTE_BYTES // 1048576} MiB)을 넘습니다.", http_status=502)
        return res.status_code, bytes(buf)


def _json_body(raw: bytes) -> dict:
    try:
        out = json.loads(raw) if raw else {}
    except ValueError:
        raise A2AError(-32603, "원격 응답이 JSON 이 아닙니다.", http_status=502) from None
    if not isinstance(out, dict):
        raise A2AError(-32603, "원격 응답 형식 오류 (객체가 아님)", http_status=502)
    return out


async def _rpc(url: str, method: str, params: dict, headers: dict, version: str, tls_kw: dict | None = None,
               binding: str = "JSONRPC", op: str | None = None) -> dict:
    if binding == "HTTP+JSON":
        return await _rest_rpc(url, op or ap.op_of(method), params, headers, tls_kw)
    if binding == "GRPC":
        from app.services import a2a_grpc
        return await a2a_grpc.call(url, op or ap.op_of(method), params, headers, (tls_kw or {}).get("grpc_credentials"))
    hdr = {"Content-Type": "application/json", "A2A-Version": version, **headers}
    try:
        async with async_client(timeout=settings.upstream_timeout, **(tls_kw or {})) as http:
            status, raw = await _fetch(http, "POST", url, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
                                                               "params": params}, headers=hdr)
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"원격 에이전트 연결 실패: {type(exc).__name__}", http_status=502) from exc
    if status in (401, 403):
        raise A2AError(-32003, f"원격 에이전트 인증 실패 ({status})", http_status=502)
    if status >= 400:
        raise A2AError(-32603, f"원격 에이전트 오류 ({status})", http_status=502)
    body = _json_body(raw)
    if "error" in body:
        e = body["error"]
        raise A2AError(e.get("code", -32603), e.get("message", "remote error"), e.get("data"))
    return body.get("result") or {}
```
**분석 / Analysis (Level 2)**
- L355-362 `_fetch`(D21-①): 응답을 스트림으로 읽으며 8 MiB 를 넘는 순간 502. 전에는 `res.json()` 이 응답 전체를 먼저 메모리에 올렸다.
- L365-372 `_json_body`: 200 인데 JSON 이 아니거나(HTML 오류 페이지 등) 객체가 아니면 502 — 전에는 `ValueError` 가 그대로 500 이 됐다.
- L389-392: 401/403 은 인증 실패, 그 밖의 4xx/5xx 는 원격 오류. 본문은 버린다(원격 오류 문구를 클라이언트에 흘리지 않음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L358 `aiter_bytes()` 는 **압축을 푼 뒤의** 조각을 준다. gzip/deflate 응답은 압축된 64 KiB 한 조각이 수십 MB 로 풀릴 수 있어, 상한 검사가 그 조각을 다 받은 뒤에야 걸린다(상한 초과량이 조각 하나로 묶이므로 무한은 아님). 엄격히 하려면 `Accept-Encoding: identity` 를 보내거나 `aiter_raw()` 로 압축 크기와 해제 크기를 함께 센다.

```python path=app/services/a2a_gateway.py lines=416-445
def record(agent_id: str, obj: dict, ctx: CallContext | None = None, push_token: str | None = None,
           source: str = "send", protocol: tuple | None = None) -> None:
    """정준형 Task 또는 이벤트를 A2ATask 로 반영 — 수신 경로(send·sse·push·poll)와 상태 전이를 이벤트로 남긴다."""
    kind = obj.get("kind")
    remote = obj.get("id") if kind == "task" else obj.get("taskId")
    if not remote:
        return
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, remote))
        if row is None:
            row = A2ATask(id=_tid(agent_id, remote), remote_task_id=remote, agent_id=agent_id, direction="outbound",
                          context_id=obj.get("contextId"), principal_sub=ctx.principal.sub if ctx else None,
                          session_id=ctx.session_id if ctx else None, run_id=ctx.run_id if ctx else None,
                          trace_id=current_trace_id(), history=[], artifacts=[])
            db.add(row)
        before = row.state  # 새 행은 None — 첫 상태도 전이로 기록
        if kind == "task":
            row.state = obj["status"]["state"]
            row.history = (obj.get("history") or row.history or [])[-HISTORY_MAX:]
            row.artifacts = obj.get("artifacts") or row.artifacts
            row.status_message = ap.text_of({"kind": "task", "status": obj["status"]})[:2000] or row.status_message
        elif kind == "status-update":
            row.state = obj["status"]["state"]
            text = ap.text_of({"kind": "task", "status": obj["status"]})
            if text:
                row.status_message = text[:2000]
                row.history = [*(row.history or []), obj["status"]["message"]][-HISTORY_MAX:]
        elif kind == "artifact-update":
            arts = [a for a in (row.artifacts or []) if a.get("artifactId") != obj["artifact"].get("artifactId")]
            row.artifacts = [*arts, obj["artifact"]]
```
**분석 / Analysis (Level 2)**
- L434·L442(D21-①): 저장 history 를 최근 200 개로 자른다(`[-HISTORY_MAX:]`). L459 이벤트 로그는 100 개.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L15 — artifacts 는 상한이 없다.** L443-445 는 `artifactId` 가 새로우면 계속 붙인다. 원격 에이전트가 서로 다른 id 의 `artifact-update` 를 스트림으로 계속 보내면(이벤트당 최대 8 MiB) 한 Task 행의 JSON 이 끝없이 커져 DB 쓰기·조회가 무거워진다. history 를 자른 이유(D21-①)와 같은 이유로 개수·총 크기 상한이 필요하다.

```python path=app/services/a2a_gateway.py lines=614-624
    t0 = time.perf_counter()
    status = "ok"
    try:
        result = await _rpc(url, up_method, up_params, headers, up_v,
                            await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url), binding, op)
    except BaseException:  # A2AError 밖의 실패(gRPC·형식 오류 등)도 사용량에는 error 로 (D21-⑤)
        status = "error"
        raise
    finally:
        if op in ("send", "cancel"):
            await run_in_threadpool(_usage, ctx, agent_id, int((time.perf_counter() - t0) * 1000), status)
```
**분석 / Analysis (Level 2)**
- L619-621(D21-⑤): `A2AError` 밖의 실패(gRPC 오류·형식 오류 등)도 사용량에 `error` 로 남긴다. `except BaseException` 이라 `CancelledError` 도 포함된다 — 클라이언트 단절로 취소된 호출도 error 로 집계하려는 의도로 읽힌다.
- 흔한 실수: `except Exception` 으로 바꾸면 취소는 잡히지 않고, `finally` 의 사용량 기록은 status 가 "ok" 인 채로 남는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 취소된 경우 L624 `finally` 안의 `await run_in_threadpool(...)` 이 anyio 취소 범위 안이면 다시 취소되어 기록 자체가 빠질 수 있다(`app/services/mcp_modern.py:506` 주석이 같은 현상을 기록). 비스트리밍 핸들러가 단절로 취소되는 경우는 드물어 영향은 작다.

```python path=app/services/a2a_gateway.py lines=691-701
def _save_push(agent_id: str, tid: str, token: str, client_cfg: dict, ctx: CallContext) -> None:
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, tid))
        if row is None:  # 게이트웨이가 중계한 Task 만 — 아무 id 로 행을 만들면 감시기가 영원히 폴링한다 (D19-④)
            raise A2AError(ap.TASK_NOT_FOUND, f"Task not found: {tid}")
        row.push_token = token
        row.push_config = {"url": client_cfg["url"]} if client_cfg.get("url") else {}
        if client_cfg.get("token"):  # 클라이언트 웹훅 토큰은 암호화 저장 (D21-⑤) — 전에는 평문
            from app.core import keys
            row.push_config["token_enc"] = keys.fernet().encrypt(str(client_cfg["token"]).encode()).decode()
        db.commit()
```
**분석 / Analysis (Level 2)**
- L694-695(D19-④): 게이트웨이가 중계한 적 없는 taskId 면 거부 — 2차 M41(아무 taskId 로 쓰레기 행) 해결.
- L698-700(D21-⑤): 클라이언트 웹훅 토큰을 Fernet 으로 암호화해 `token_enc` 로 저장. 전에는 평문 `token`. 이전 평문 행은 `_relay_push` 가 그대로 읽는다(L899).
- JSON 컬럼 주의: L697 에서 새 dict 를 대입(변경 감지됨)한 뒤 L700 에서 그 dict 를 고친다 — 같은 객체가 flush 때 직렬화되므로 반영된다. 반대로 **기존** JSON 값을 제자리에서 고치면 SQLAlchemy 가 변경을 모른다(`MutableDict` 가 아니면). 흔한 실수.
**짚어볼 점 / Watch-outs**
- [위험:중간] (교차) **M4** — 이 새 암호문은 키 회전 재암호화 목록에 없다(`app/api/admin.py:546`). 이전 키를 지우면 L900-902 복호화가 `InvalidToken` 을 내고, 백그라운드 Task 라 로그(L903-904)도 없이 중계가 멈춘다. 상세는 [admin.py.md](../api/admin.py.md).

```python path=app/services/a2a_gateway.py lines=704-767
def _stream(agent: AgentRef, ctx: CallContext, rid, url: str, method: str, params: dict, headers: dict,
            up_v: str, client_v: str, binding: str = "JSONRPC", op: str = "stream") -> StreamingResponse:
    async def upstream_events(http, grpc_credentials=None):
        """업스트림 스트림 → (JSON-RPC 형태 메시지) — REST 는 data 가 StreamResponse 그대로, gRPC 는 서버 스트림."""
        if binding == "GRPC":
            from app.services import a2a_grpc
            async for ev in a2a_grpc.stream(url, op, params, headers, grpc_credentials):
                yield ev
            return
        if binding == "HTTP+JSON":
            m, path, query, body = a2a_rest.rest_request(op, params)
            hdr = {"Content-Type": a2a_rest.MEDIA, "Accept": "text/event-stream", "A2A-Version": up_v, **headers}
            req = http.stream(m, f"{url.rstrip('/')}/{path}", params=query, headers=hdr, **({"json": body} if body is not None else {}))
        else:
            hdr = {"Content-Type": "application/json", "Accept": "text/event-stream", "A2A-Version": up_v, **headers}
            req = http.stream("POST", url, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
                                                 "params": params}, headers=hdr)
        async with req as res:
            if res.status_code >= 400:
                raise A2AError(-32603, f"원격 에이전트 오류 ({res.status_code})")
            async for line in res.aiter_lines():
                if not line.startswith("data:"):
                    continue
                if len(line) > MAX_REMOTE_BYTES:
                    raise A2AError(-32603, "원격 이벤트가 상한을 넘습니다.")
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:  # 오류 이벤트로 알리고 끝낸다 (전에는 스트림이 말없이 끊김)
                    raise A2AError(-32603, "원격 이벤트가 JSON 이 아닙니다.") from None
                if binding == "HTTP+JSON":
                    if isinstance(msg.get("error"), dict) and "status" in msg["error"]:
                        code, text = a2a_rest.code_from_status(int(msg["error"].get("code") or 500), msg)
                        yield {"error": {"code": code, "message": text}}
                    else:
                        yield {"result": msg}
                else:
                    yield msg

    async def gen():
        t0 = time.perf_counter()
        status = "ok"
        try:
            tls_kw = await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url)
            creds = tls_kw.pop("grpc_credentials", None)
            async with async_client(timeout=settings.upstream_timeout, **tls_kw) as http:
                async for msg in upstream_events(http, creds):
                    if "error" in msg:
                        yield _sse(rid, error=msg["error"])
                        status = "error"
                        break
                    canon = await run_in_threadpool(_sanitize_in, agent.id, ctx,
                                                    ap.event_from(msg.get("result") or {}))
                    await run_in_threadpool(record, agent.id, canon, ctx, None, "sse",
                                            (client_v, up_v, ctx.binding, binding))
                    yield _sse(rid, ap.event_to(canon, client_v))
        except A2AError as exc:
            status = "error"
            yield _sse(rid, error={"code": exc.code, "message": exc.message})
        except httpx.HTTPError as exc:
            status = "error"
            yield _sse(rid, error={"code": -32603, "message": f"연결 실패: {type(exc).__name__}"})
        finally:
            await run_in_threadpool(_usage, ctx, agent.id, int((time.perf_counter() - t0) * 1000), status)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
```
**분석 / Analysis (Level 2)**
- 원격 SSE 를 받아 정준형으로 바꾸고(L754-755), Task 영속(L756-757) 뒤 클라이언트에 다시 SSE 로 보낸다.
- L727-728(D21-①): 이벤트 한 줄이 8 MiB 를 넘으면 오류. L729-732: JSON 이 아닌 이벤트는 오류 이벤트로 알리고 끝낸다(전에는 스트림이 말없이 끊겼다 — C56-03).
- L765-766: 사용량은 `finally` 에서. (스트림 단절 시 이 `await` 가 다시 취소될 수 있다는 점은 위 L619 와 같다.)
**짚어볼 점 / Watch-outs**
- [위험:중간] **M3(INDEX §5) — 이벤트 상한이 줄을 다 모은 뒤에 걸린다.** L724 `res.aiter_lines()`(httpx 0.28.1, `uv.lock:811-812`)는 줄바꿈이 올 때까지 내용을 내부 버퍼에 쌓았다가 한 줄을 통째로 준다 — 줄 길이 상한이 없다. L727 의 검사는 **이미 메모리에 다 들어온 줄**에 대해서만 돈다. 실패 시나리오: 원격 에이전트(등록·승인된 외부 에이전트, 또는 탈취된 에이전트)가 `message/stream` 응답으로 `data:` 뒤에 줄바꿈 없이 수 GB 를 흘리면 게이트웨이 워커가 그 줄을 모으다 메모리 부족(OOM)으로 죽는다 — 같은 워커의 다른 모든 요청이 함께 끊긴다. D21-① 이 막으려던 "큰 응답으로 게이트웨이 메모리 소모"(L351 주석)가 스트리밍 경로에서는 막히지 않는다. 최소 수정: `aiter_bytes()` 를 직접 받아 `b"\n"` 으로 자르는 작은 줄 리더를 두고, 아직 줄바꿈이 없는 누적 버퍼가 `MAX_REMOTE_BYTES` 를 넘으면 즉시 오류. 스트림 총량·총 시간 상한도 함께.

```python path=app/services/a2a_gateway.py lines=800-845
_JWKS_CACHE: dict[str, tuple[float, dict]] = {}


def _push_jwks(url: str) -> dict:
    hit = _JWKS_CACHE.get(url)
    if hit and hit[0] > time.time():
        return hit[1]
    from app.core.http import sync_client
    with sync_client(timeout=5) as http:
        doc = http.get(url).raise_for_status().json()
    _JWKS_CACHE[url] = (time.time() + 300, doc)
    return doc


def verify_push_jwt(cfg: dict, authorization: str | None, raw: bytes) -> dict:
    """원격 에이전트가 서명한 푸시 JWT 검증 — JWKS 서명·발급자(필수)·발급 시각(±5분)·본문 해시(필수)·jti 1회 (D21-①)."""
    import hashlib

    import jwt as pyjwt
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PushRejected("서명 토큰 없음")
    tok = authorization.split(" ", 1)[1]
    try:
        kid = pyjwt.get_unverified_header(tok).get("kid")
        keys = _push_jwks(cfg["jwks_url"]).get("keys") or []
        jwk = next((k for k in keys if not kid or k.get("kid") == kid), None)
        if jwk is None:
            raise PushRejected("서명 키(kid)를 JWKS 에서 찾지 못함")
        key = pyjwt.PyJWK(jwk).key
        if not cfg.get("issuer"):
            raise PushRejected("push_verify.issuer 미설정 — 발급자를 확인할 수 없다")
        claims = pyjwt.decode(tok, key, algorithms=["RS256", "ES256", "EdDSA"], options={"verify_aud": False,
                              "require": ["iss", "iat", "jti"]}, issuer=cfg["issuer"])
    except PushRejected:
        raise
    except Exception as exc:  # noqa: BLE001 — 서명·형식 오류는 모두 거부
        raise PushRejected(f"서명 검증 실패: {type(exc).__name__}") from exc
    if abs(time.time() - float(claims.get("iat") or 0)) > 300:
        raise PushRejected("발급 시각이 5분 범위를 벗어남 (재전송 의심)")
    want = claims.get("request_body_sha256")
    if not want or want != hashlib.sha256(raw).hexdigest():  # 해시가 없으면 서명한 토큰을 다른 본문에 다시 쓸 수 있다
        raise PushRejected("본문 해시 없음·불일치")
    from app.services import ratelimit  # 같은 jti 는 한 번만 (iat 허용 창 ±5분 + 여유) — 재전송 거부, 워커 공유
    if ratelimit.backend().counter_add(f"push-jti:{cfg['issuer']}:{claims['jti']}", 1, 900) > 1:
        raise PushRejected("이미 쓴 jti (재전송)")
    return claims
```
**분석 / Analysis (Level 2)**
- L803-811: 푸시 서명용 JWKS 를 5분 캐시. URL 은 운영자(시크릿 관리 권한)가 설정한다(D20-②, `app/api/a2a.py:556-558`).
- L814-845 `verify_push_jwt`(D21-①): ① Bearer 토큰 ② kid 로 JWKS 키 선택 ③ issuer 설정 필수(L829-830) ④ 비대칭 알고리즘만(L831 — `HS256` 이 목록에 없어 알고리즘 혼동 차단) ⑤ `iss`·`iat`·`jti` 필수 ⑥ 발급 시각 ±5분 ⑦ 본문 SHA-256 필수·일치 ⑧ `jti` 1회(900초 기억, 워커 공유).
- L840 의 주석이 핵심: 본문 해시가 없으면 서명된 토큰을 **다른 본문에 다시 붙여** 쓸 수 있다. L843 은 `INCR` 결과가 1 일 때만 통과 — 원자적이라 같은 jti 동시 2건도 하나만 통과.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L17** ① D21 이전에 `issuer` 없이 설정한 에이전트는 이제 모든 푸시가 401(L829-830, 감사 `a2a.push.reject`)이 된다 — 안전한 쪽(fail-closed)이지만 이행 안내·데이터 점검이 없다. ② 모르는 kid 면 JWKS 를 다시 받지 않는다(L825-827 — 캐시 5분). 원격이 키를 회전하면 최대 5분간 푸시가 거부된다. 모르는 kid 일 때 한 번 강제 갱신(최소 간격 두고)이 일반적이다. ③ jti 1회 보장은 공유 백엔드(Valkey) 전제 — Valkey 장애 5초 동안은 워커 로컬로 떨어져(`app/services/ratelimit.py:129-139`) 다른 워커에서 같은 jti 를 다시 받을 수 있다. ④ `jwks_url` 은 `http://` 도 허용한다(`app/api/a2a.py:560`) — 운영 모드에서 평문 JWKS 는 중간자가 키를 바꿔 끼울 수 있다. prod 는 https 만.

```python path=app/services/a2a_gateway.py lines=894-904
async def _relay_push(client: dict, canon: dict, agent_id: str, remote_id: str) -> None:
    from app.core.http import post_with_retry
    if reason := await run_in_threadpool(push_url_problem, client["url"]):  # 등록 뒤 이름이 사설 주소로 바뀌었을 수 있다 — 전달 직전 다시 (D19-④)
        logs.event("warning", "gateway", f"클라이언트 푸시 중계 거부: {reason}", agent=agent_id, task=remote_id)
        return
    token = client.get("token", "")  # 이전 행은 평문
    if client.get("token_enc"):
        from app.core import keys
        token = await run_in_threadpool(lambda: keys.fernet().decrypt(client["token_enc"].encode()).decode())
    if not await post_with_retry(client["url"], json=canon, headers={"X-A2A-Notification-Token": token}):
        logs.event("warning", "gateway", "클라이언트 푸시 중계 실패(재시도 후)", agent=agent_id, task=remote_id)
```
**분석 / Analysis (Level 2)**
- 전달 직전 URL 재검사(D19-④) → 토큰 복호화(D21-⑤, 이전 평문 행 호환) → 재시도 포함 POST → 실패 경고.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L902 복호화 실패(`InvalidToken`)를 잡지 않는다. 이 함수는 `asyncio.create_task` 로 돈다(L885) — 예외는 "Task exception was never retrieved" 로만 남고 L903-904 의 운영 로그도 없다. M4 와 겹치면 장애 원인을 찾기 어렵다. `try/except InvalidToken` → `logs.event("warning", …)`.

```python path=app/api/a2a.py lines=252-254
@router.get("/.well-known/agent-card.json")
def platform_well_known(request: Request):
    return platform_card()  # 주소는 PUBLIC_BASE_URL — Host 헤더로 카드 URL 을 바꾸지 못하게 (D21-①)
```
```python path=app/api/a2a.py lines=298-310
@router.post("/a2a/push/{token}")
async def push_webhook(token: str, request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw)
    except ValueError:
        return Response(status_code=400)
    try:
        ok = await receive_push(token, body, request.headers.get("x-a2a-notification-token"),
                                request.headers.get("authorization"), raw)
    except PushRejected as exc:  # 서명 검증 실패 (SCR-G07 ⑧)
        return JSONResponse({"error": "push_signature_invalid", "message": str(exc)}, status_code=401)
    return Response(status_code=200 if ok else 404)
```
**분석 / Analysis (Level 2)**
- L254(D21-①): 플랫폼 카드의 주소는 `PUBLIC_BASE_URL` 고정 — 요청 `Host` 헤더로 광고 주소를 바꾸지 못한다(2차 M52 의 "Host 기반 카드 URL" 해결, C56-05).
- L298-310: 푸시 수신. 본문을 읽고 JSON 파싱 → `receive_push`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L16** L300 `await request.body()` 는 **인증 전에**(push 토큰은 경로에 있지만 DB 확인은 `receive_push` 안) 본문 전체를 읽는다. 상한이 없어 인증 없는 요청이 큰 본문으로 워커 메모리를 쓸 수 있다 — D21-① 이 원격 **응답**에는 8 MiB 상한을 두었지만 원격 **푸시 요청**에는 두지 않았다. Ingress 가 본문 크기를 막는 배포(nginx 기본 1 MiB)에서는 완화된다. 최소 수정: `Content-Length` 확인 + 스트림 누적 상한(예: `MAX_REMOTE_BYTES`).

```python path=app/api/a2a.py lines=552-568
    if body.auth is not None:
        secrets.check_refs(p, body.auth, f"a2a/{a.id}/")
        secrets.check_destination(p, {**(a.auth or {}), **body.auth}, secrets.destination_changed(a.auth, body.auth),
                                  bool(body.secret))
        if "push_verify" in body.auth and body.auth["push_verify"] != (a.auth or {}).get("push_verify") \
                and not p.has(secrets.SECRET_MANAGE):  # 푸시 서명 검증을 끄거나 바꾸면 위조 푸시를 받는다 (D20-②)
            raise ApiError(403, "push_verify_forbidden", f"푸시 서명 검증 설정 변경은 시크릿 관리 권한({secrets.SECRET_MANAGE})이 필요합니다.")
        pv = (body.auth.get("push_verify") or {}) if isinstance(body.auth.get("push_verify"), dict) else None
        if pv is not None and pv.get("jwks_url") and not str(pv["jwks_url"]).startswith(("https://", "http://")):
            raise bad_request("push_verify.jwks_url 은 http(s) URL (MSG-004).", code="invalid_jwks_url")
        if pv is not None and pv.get("jwks_url") and not pv.get("issuer"):  # 발급자 없이는 다른 발급자의 서명도 통과 (D21-①)
            raise bad_request("push_verify.issuer 가 필요합니다 (JWKS 를 지정하면 발급자도 지정).", code="push_verify_issuer_required")
        if body.auth.get("scheme") == "oauth2" and not str(body.auth.get("token_url") or (a.auth or {}).get("token_url") or "").startswith("https://") \
                and settings.app_env == "prod":
            raise bad_request("OAuth2 token_url 은 https 여야 합니다 (MSG-004).", code="invalid_token_url")
        a.auth = {**(a.auth or {}), **body.auth}
        a2a_gateway.drop_tokens(a.id)  # 바뀐 인증으로 다시 받게 — 이전 자격으로 받은 캐시 토큰을 쓰지 않는다 (D21-⑤)
```
**분석 / Analysis (Level 2)**
- L556-558(D20-②): `push_verify` 를 바꾸거나 끄려면(`null` 포함 — 값이 다르면) 시크릿 관리 권한. 푸시 서명 검증을 끄면 위조 푸시를 받기 때문(C53-06).
- L562-563(D21-①): `jwks_url` 을 주면 `issuer` 도 필수 — 발급자를 모르면 같은 JWKS 를 쓰는 다른 발급자의 서명도 통과한다.
- L568(D21-⑤): 인증이 바뀌면 이 워커의 캐시 토큰 폐기(위 L14 참고).

```python path=app/services/a2a_server.py lines=175-187
        async def run():
            try:
                await self.executor(ctx)
                if task["status"]["state"] not in ap.TERMINAL:
                    await ctx.update("completed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 원문(내부 경로·질의)은 로그에만, 원격에는 상관 id (D21-⑤)
                from app.core import logs
                ref = uuid.uuid4().hex[:12]
                logs.event("error", "a2a", f"플랫폼 실행기 오류 {ref}: {type(exc).__name__}: {exc}", task_id=task["id"])
                await ctx.update("failed", f"에이전트 내부 오류 (참조 {ref})")
        self.runners[task["id"]] = asyncio.create_task(run())
```
**분석 / Analysis (Level 2)**
- L182-186(D21-⑤): 실행기 예외의 원문(내부 경로·질의 등)은 로그에만, 원격 클라이언트에는 참조 id 만 — 2차 a2a_server 보고서의 "예외 원문 노출" 해결(C60-10). 로그 메시지는 `logs.event` 의 PII 마스킹을 거치고, 마스킹으로 바뀐 경우 원문은 암호문(`raw_enc`)으로만 남는다(`app/core/logs.py:85-91`).

## 파일 평가 / File Assessment
- 강점: (1) 원격 응답을 스트림으로 읽으며 상한을 적용하고, 비 JSON·비 객체를 502 로 정리했다(`app/services/a2a_gateway.py:355-372`). (2) 푸시 JWT 검증이 issuer·iat·jti 1회·본문 해시까지 갖춰 재전송·본문 바꿔치기를 막는다(`app/services/a2a_gateway.py:814-845`, 시험 `test/test_a2a_boundaries.py:30-65`). (3) 클라이언트 웹훅 토큰 암호화·실행기 오류 원문 은닉 등 기록 항목을 작게 정확히 처리(`app/services/a2a_gateway.py:698-700`, `app/services/a2a_server.py:182-186`).
- 위험: (1) [위험:중간] SSE 이벤트 상한이 줄 전체를 메모리에 모은 뒤 적용 — 단일 거대 줄로 워커 OOM(`app/services/a2a_gateway.py:724-728`). (2) [위험:중간] (교차) 웹훅 토큰 암호문이 재암호화 대상에서 빠짐(`app/services/a2a_gateway.py:698-700` ↔ `app/api/admin.py:546`). (3) [위험:낮음] `drop_tokens` 가 교환 토큰 키와 맞지 않고 시험이 이를 가림(`app/services/a2a_gateway.py:260`, `:278`).
- 개선 권고(영향순): (1) 바이트 단위 줄 리더로 SSE 상한·총량 상한. (2) 재암호화 목록에 `push_config.token_enc` + 중계 복호화 실패 로그. (3) 토큰 캐시 키를 종류 접두 튜플로 통일하고 `drop_tokens`·시험을 실제 키로. (4) 푸시 수신 본문 상한·artifacts 상한·모르는 kid 시 JWKS 재조회.
- 파일 위험 등급 / Risk grade: 중간 — 원격(신뢰 경계 밖) 입력 처리 모듈에 [위험:중간] 2건(1건은 교차).
- 재읽기 권고 레벨: 2 — 원격 응답 세 바인딩(JSON-RPC·REST·SSE·gRPC)마다 상한 위치가 달라 줄 단위로 비교해야 한다.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M41 `_save_push` 가 아무 taskId 로 행 생성 (`a2a_gateway.py:573`) | 해결(D19-④) | `app/services/a2a_gateway.py:694-695` |
| M47 A2A auth 병합 잔여 — `scheme`·`tls.verify`·`scopes`·`push_verify` 변경, 객체 수준 인가 부재 (`a2a.py:534-544`) | 부분 해결 | `push_verify` 는 시크릿 관리 권한 `app/api/a2a.py:556-558`, issuer 필수 `:562-563`. `scheme`·`tls` 는 전송처 키로 `check_destination` 경유(`app/services/secrets.py:31-32`, `app/api/a2a.py:554-555`). 객체 수준 인가(소유 팀)는 이번 범위 밖 |
| M52 Host 기반 카드 URL | 해결(카드) | `app/api/a2a.py:254`, `app/services/a2a_gateway.py:224` 기본 `public_base_url` · 시험 `test/test_a2a_boundaries.py:118-127` |
| (2차 보고서 L427) 200 비 JSON → 500, 응답 크기 상한 없음 | 해결(JSON-RPC·REST) | `:355-372`, `:393` · 시험 `test/test_a2a_boundaries.py:67-85` |
| (2차 보고서 L869) SSE JSON 오류 시 조용히 끊김, 이벤트 크기·개수 상한 없음 | 부분 해결 | 오류 이벤트 `:729-732`, 줄 상한 `:727-728`(줄을 다 모은 뒤 — M3). 개수·총량 상한 없음 |
| (2차 보고서 L523) `history` 상한 없음 | 해결 | `:434`, `:442` · 시험 `test/test_a2a_boundaries.py:107-116`. artifacts 상한 없음(L15) |
| (2차 보고서 L961) 모르는 kid 여도 JWKS 캐시 갱신 안 함 | 미해결 | `:803-811`, `:825-827` |
| (2차 보고서 L301) `patch_agent` 가 토큰 캐시를 비우지 않음 | 부분 해결 | client_credentials 는 폐기 `:278`, 교환 토큰 키 불일치(L14), 다른 워커는 만료까지(설계 기록 `:277`) |
| 1차 이월 — 웹훅 토큰 평문 저장 | 해결 · 신규 교차 위험 | `:698-700`. 재암호화 누락(M4) |
| (2차 a2a_server.py.md L264) 실행기 예외 원문을 클라이언트에 | 해결 | `app/services/a2a_server.py:182-186` |
