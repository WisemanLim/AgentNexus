> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 a2a_gateway.py.md](../../../../round3/CODE-REVIEW-Lv2/app/services/a2a_gateway.py.md)

### 05. app/services/a2a_gateway.py (+ `app/api/a2a.py` 등록·PATCH·목록)
**파일 요약 / File Summary**
- 역할 / Role: 원격 A2A 에이전트 게이트웨이 — 원격 호출(상한·압축 거부), Task 저장(`record`), 푸시 수신 서명 검증(`verify_push_jwt`), 클라이언트 웹훅 중계.
- 진입점 거리 / Distance: 1 — `/a2a/{agent}`·푸시 수신(`app/api/a2a.py`), 감시기(`watch`).
- LOC: 998 (3차 956 → 998) · `app/api/a2a.py` 759 (3차 751 → 759)
- 주요 export: `record`·`verify_push_jwt`·`receive_push`·`_cap_artifacts`(신규)·`ARTIFACTS_MAX`·`ARTIFACTS_MAX_BYTES`(신규)
- 의존(내부): `app.services.{a2a_proto as ap, ratelimit, audit, secrets}`, `app.core.http.sync_client`, `app.db.SessionLocal`
- 외부 패키지: httpx, PyJWT(`PyJWK`), SQLAlchemy
- 부수효과 / Side effects: `a2a_tasks` 행 갱신(history·artifacts·events), JWKS 프로세스 캐시 `_JWKS_CACHE`·`_JWKS_FORCED`(신규), Valkey jti 카운터.
- 동시성 / Concurrency: `verify_push_jwt`·`record` 는 동기 — `receive_push` 가 스레드풀로 부른다(`app/services/a2a_gateway.py:907`, `:925`). 캐시 dict 는 스레드 사이 공유(원자적 dict 연산만 — 경합 시 추가 조회 정도).
- 테스트 커버리지 흔적: `test/test_boundaries4.py:57`(C64-02 kid 회전·30초·prod https·issuer 플래그), `:101`(C64-03 artifacts 상한).
- 4차 변경 요약 / Changes in D22: D22-④ — Task artifacts 최근 50개·직렬화 4 MiB(3차 L15), 푸시 JWKS 모르는 kid 1회 재조회(같은 URL 30초에 1회)·prod 는 `jwks_url` https 만·issuer 없는 설정을 목록에 표시(3차 L17).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_gateway.py lines=351-364
MAX_REMOTE_BYTES = 8 * 1024 * 1024  # 원격 응답·이벤트 상한 (D21-①) — 큰 응답으로 게이트웨이 메모리를 소모시키지 못하게
HISTORY_MAX = 200  # 저장하는 Task history 메시지 수 (최근 것부터)
ARTIFACTS_MAX, ARTIFACTS_MAX_BYTES = 50, 4 * 1024 * 1024  # 저장하는 Task artifacts — 개수·총량 (최근 것부터, D22-④)


def _cap_artifacts(arts: list) -> list:
    """원격 에이전트가 artifact-update 를 끝없이 보내도 행 하나가 커지지 않게 — 최근 것부터 개수·직렬화 총량 안에서만."""
    out, size = [], 0
    for a in reversed(arts[-ARTIFACTS_MAX:]):
        size += len(json.dumps(a, ensure_ascii=False, default=str))
        if size > ARTIFACTS_MAX_BYTES:
            break
        out.append(a)
    return out[::-1]
```
**분석 / Analysis (Level 2)**
- L353(D22-④): artifacts 상한 — 개수 50, 직렬화 총량 4 MiB. 원격 응답 상한(8 MiB, L351)·history(200, L352)와 같은 "행 하나가 끝없이 커지지 않게" 계열.
- L356-364 `_cap_artifacts`: 뒤(최신)에서부터 하나씩 직렬화 길이를 더하다가 4 MiB 를 넘으면 멈춘다(L361-362) → 다시 원래 순서로(L364).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L10** ① 넘는 순간 `break` 라, **가장 최근 artifact 하나가 4 MiB 보다 크면 결과가 빈 목록**이다 — 새 결과도, 이전 결과도 모두 사라진다. 원격 응답 상한이 8 MiB(L351)라 4~8 MiB 결과물은 정상 응답으로 들어와 조용히 버려진다. 이벤트 기록(`events`)에도 잘림 표시가 없다(L483-494). ② 매 `artifact-update` 마다 남은 artifact 전체(최대 4 MiB)를 다시 `json.dumps` 한다 — 스트리밍 artifact 이벤트가 잦으면 이벤트당 수 MB 직렬화 + 같은 크기 행 쓰기. 최소 수정: 큰 항목은 건너뛰고(`continue`) 잘린 개수를 `ev["artifacts_truncated"]` 로 남기기, 크기는 항목별로 한 번만 계산.

```python path=app/services/a2a_gateway.py lines=467-480
        if kind == "task":
            row.state = obj["status"]["state"]
            row.history = (obj.get("history") or row.history or [])[-HISTORY_MAX:]
            row.artifacts = _cap_artifacts(obj.get("artifacts") or row.artifacts or [])
            row.status_message = ap.text_of({"kind": "task", "status": obj["status"]})[:2000] or row.status_message
        elif kind == "status-update":
            row.state = obj["status"]["state"]
            text = ap.text_of({"kind": "task", "status": obj["status"]})
            if text:
                row.status_message = text[:2000]
                row.history = [*(row.history or []), obj["status"]["message"]][-HISTORY_MAX:]
        elif kind == "artifact-update":
            arts = [a for a in (row.artifacts or []) if a.get("artifactId") != obj["artifact"].get("artifactId")]
            row.artifacts = _cap_artifacts([*arts, obj["artifact"]])
```
**분석 / Analysis (Level 2)**
- L470(task), L480(artifact-update): 두 경로 모두 `_cap_artifacts` 를 거친다.
- L479: 같은 `artifactId` 는 새 것으로 **교체**한다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L10(인접)** A2A `artifact-update` 의 `append: true`(같은 artifact 에 조각을 이어 붙임)를 보지 않는다 — 이벤트 변환은 `append`·`lastChunk` 를 나르지만(`app/services/a2a_proto.py:205-206`) 저장은 늘 교체라, 조각으로 흘린 큰 결과는 **마지막 조각만** 남는다. D22-④ 상한과 같은 줄이라 함께 고칠 대상: `append` 면 `parts` 를 이어 붙인 뒤 상한 적용.

```python path=app/services/a2a_gateway.py lines=835-887
_JWKS_CACHE: dict[str, tuple[float, dict]] = {}
_JWKS_FORCED: dict[str, float] = {}  # 모르는 kid 로 강제 갱신한 마지막 시각 — 위조 kid 로 JWKS 를 계속 부르지 못하게


def _push_jwks(url: str, refresh: bool = False) -> dict:
    hit = _JWKS_CACHE.get(url)
    if hit and hit[0] > time.time() and not (refresh and time.time() - _JWKS_FORCED.get(url, 0) > 30):
        return hit[1]
    if refresh:
        _JWKS_FORCED[url] = time.time()
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
    if not cfg.get("issuer"):  # D21 이전 설정 — 에이전트 목록에 push_verify_issuer=false 로 보인다
        raise PushRejected("push_verify.issuer 미설정 — 발급자를 확인할 수 없다 (에이전트 auth.push_verify.issuer 지정)")
    try:
        kid = pyjwt.get_unverified_header(tok).get("kid")
        jwk = None
        for refresh in (False, True):  # 모르는 kid — 원격 에이전트가 키를 회전했을 수 있어 한 번 다시 받는다(30초에 1회, D22-④)
            keys = _push_jwks(cfg["jwks_url"], refresh).get("keys") or []
            jwk = next((k for k in keys if not kid or k.get("kid") == kid), None)
            if jwk is not None:
                break
        if jwk is None:
            raise PushRejected("서명 키(kid)를 JWKS 에서 찾지 못함")
        key = pyjwt.PyJWK(jwk).key
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
- L835-836: JWKS 캐시(5분)와 "강제 갱신 마지막 시각" 을 URL 별로.
- L839-849 `_push_jwks`(D22-④): 캐시가 살아 있고 강제 갱신이 아니면 캐시. `refresh=True` 라도 **30초 안에 강제 갱신한 적이 있으면 캐시**(L841) — 위조 kid 를 계속 보내 JWKS 서버를 두드리게 하지 못한다. 강제 갱신 시각은 조회 **전에** 기록(L843-844) — 조회가 실패해도 30초 동안은 다시 두드리지 않는다.
- L860-861: issuer 확인이 JWKS 조회 **앞**으로 왔다 — issuer 없는 D21 이전 설정은 네트워크를 쓰지 않고 바로 거부.
- L865-871(D22-④): 첫 시도는 캐시, kid 가 없으면 한 번 강제 갱신. 원격 에이전트가 키를 회전한 직후의 푸시도 받는다(3차 L17).
- L873-874: 알고리즘 허용 목록이 명시돼 있다(`none`·HS256 혼동 공격 차단) — 좋은 습관.
- 흔한 실수: `kid` 가 없는 토큰이면 L867 `not kid` 로 JWKS 첫 키를 고른다. JWKS 에 키가 여러 개면 서명 키가 아닐 수 있다 → 서명 실패로 거부될 뿐(안전한 쪽).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L9** "운영은 https 만" 은 PATCH 입력 검사(`app/api/a2a.py:564-568`)에만 있고 **사용 시점**(L839-847)에는 없다. 그래서 ① 등록 API 로 넣은 `push_verify`(아래 `register_agent`), ② 13차 전에 저장된 `http://` JWKS 설정은 prod 에서도 평문으로 JWKS 를 받아 검증한다 — 경로상 공격자가 JWKS 를 바꿔치면 위조 푸시가 서명 검증을 통과한다. 최소 수정: `_push_jwks` 에서 `settings.app_env == "prod"` 이고 `https://` 가 아니면 `PushRejected`, 등록 경로에도 PATCH 와 같은 검사(https·issuer 필수·`SECRET_MANAGE`).

```python path=app/api/a2a.py lines=388-400
def agent_out(a: A2AAgent, detail: bool = False) -> dict:
    info = ap.card_info(a.card) if a.card else {}
    out = {"id": a.id, "name": a.name, "url": a.url, "card_url": a.card_url, "version": a.version,
           "trust_tier": a.trust_tier, "status": a.status, "skills": a.skills, "card_changed": a.card_changed,
           "last_fetched_at": a.last_fetched_at, "auth": {k: v for k, v in (a.auth or {}).items() if k != "secret_ref"},
           "credential_configured": bool((a.auth or {}).get("secret_ref")), "gateway_path": f"/a2a/{a.id}",
           "protocol_versions": info.get("protocol_versions", []), "bindings": info.get("bindings", []),
           "streaming": info.get("streaming"),
           "push": info.get("push"), "catalog_item_id": a.catalog_item_id, "owner_team": a.owner_team,
           "framework": a.framework or framework_hint(a.card), "framework_source": "operator" if a.framework else
           ("card" if framework_hint(a.card) else None), "push_verify": bool(((a.auth or {}).get("push_verify") or {}).get("jwks_url")),
           # JWKS 는 있는데 발급자가 없는 D21 이전 설정 — 모든 서명 푸시가 401 이므로 목록에서 보이게 (D22-④)
           "push_verify_issuer": bool(((a.auth or {}).get("push_verify") or {}).get("issuer")),
```
**분석 / Analysis (Level 2)**
- L399-400(D22-④): 목록·상세에 `push_verify_issuer` — JWKS 는 있는데 issuer 가 없는(D21 이전) 설정을 운영자가 찾게. 그 설정은 모든 서명 푸시가 401(`app/services/a2a_gateway.py:860-861`)이므로 "왜 푸시가 안 오나" 를 화면에서 바로 알 수 있다.

```python path=app/api/a2a.py lines=557-574
    if body.auth is not None:
        secrets.check_refs(p, body.auth, f"a2a/{a.id}/")
        secrets.check_destination(p, {**(a.auth or {}), **body.auth}, secrets.destination_changed(a.auth, body.auth),
                                  bool(body.secret))
        if "push_verify" in body.auth and body.auth["push_verify"] != (a.auth or {}).get("push_verify") \
                and not p.has(secrets.SECRET_MANAGE):  # 푸시 서명 검증을 끄거나 바꾸면 위조 푸시를 받는다 (D20-②)
            raise ApiError(403, "push_verify_forbidden", f"푸시 서명 검증 설정 변경은 시크릿 관리 권한({secrets.SECRET_MANAGE})이 필요합니다.")
        pv = (body.auth.get("push_verify") or {}) if isinstance(body.auth.get("push_verify"), dict) else None
        if pv is not None and pv.get("jwks_url") and not str(pv["jwks_url"]).startswith(
                ("https://",) if settings.app_env == "prod" else ("https://", "http://")):  # 운영은 https 만 (D22-④)
            raise bad_request("push_verify.jwks_url 은 https URL 이어야 합니다 (local·test 는 http 허용, MSG-004).",
                              code="invalid_jwks_url")
        if pv is not None and pv.get("jwks_url") and not pv.get("issuer"):  # 발급자 없이는 다른 발급자의 서명도 통과 (D21-①)
            raise bad_request("push_verify.issuer 가 필요합니다 (JWKS 를 지정하면 발급자도 지정).", code="push_verify_issuer_required")
        if body.auth.get("scheme") == "oauth2" and not str(body.auth.get("token_url") or (a.auth or {}).get("token_url") or "").startswith("https://") \
                and settings.app_env == "prod":
            raise bad_request("OAuth2 token_url 은 https 여야 합니다 (MSG-004).", code="invalid_token_url")
        a.auth = {**(a.auth or {}), **body.auth}
```
**분석 / Analysis (Level 2)**
- L561-563: `push_verify` 변경은 `SECRET_MANAGE` 권한(D20-②).
- L564-568(D22-④): prod 면 `https://` 만, local·test 는 `http://` 도. 문자열 접두 비교라 대문자 `HTTPS://` 는 거부된다(안전한 쪽).
- L569-570: JWKS 를 주면 issuer 도 필수(D21-①).

```python path=app/api/a2a.py lines=473-497
@router.post("/api/v1/a2a/agents", status_code=201)
async def register_agent(body: AgentIn, p: Principal = Depends(require("a2a:write"))):
    """원격 에이전트 등록 (SCR-G02) — Agent Card 조회(게이트웨이 egress)·검증 → 승인 권한이 없으면 등록 요청(pending,
    E05 승인 후 활성), 있으면 바로 활성. dry_run 이면 조회·점검 결과만."""
    import time as _t
    secrets.check_refs(p, body.auth, f"a2a/{body.id}/" if body.id else "\0")
    cu = card_url_for(body.url)
    t0 = _t.perf_counter()
    try:
        card = await fetch_card(cu, (body.auth or {}).get("tls"))
    except A2AError as exc:
        raise ApiError(exc.http_status if exc.http_status >= 400 else 502, "agent_card_invalid", exc.message,
                       extra=exc.data) from exc
    rv = card_review(card, cu, round((_t.perf_counter() - t0) * 1000, 1))
    if body.dry_run:
        return rv
    if body.trust_tier in ("verified", "trusted") and not p.has("policy:write"):
        raise ApiError(403, "forbidden", "신뢰 등급 지정은 보안 담당 권한이 필요합니다.")
    review_needed = not p.has("catalog:approve")

    def _do():
        with SessionLocal() as db:
            auth = dict(body.auth)
            a = create_agent_row(db, card, cu, p.sub, agent_id=body.id, trust_tier=body.trust_tier, auth=auth)
            a.owner_team = body.owner_team or p.team
```
**분석 / Analysis (Level 2)**
- 등록은 `body.auth` 를 그대로 행에 저장한다(L495-496). `secrets.check_refs`(L478)는 시크릿 참조만 본다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L9** PATCH 의 세 검사(`SECRET_MANAGE`·https·issuer 필수, `app/api/a2a.py:561-570`)가 등록에는 없다. `a2a:write` 보유자가 `POST /api/v1/a2a/agents` 에 `"auth": {"push_verify": {"jwks_url": "http://…"}}` 를 넣으면 prod 에서도 평문 JWKS 설정이 저장된다(issuer 를 빼면 모든 푸시가 401 로 끝나 안전 쪽이지만, issuer 를 넣으면 평문 JWKS 로 검증). 콘솔은 PATCH 로만 설정하므로(`web/src/pages/a2a/A2A.tsx:328`) API 직접 호출 경로다. 위 사용 시점 검사 하나로 두 경로가 함께 막힌다.

## 파일 평가 / File Assessment
- 강점: (1) 모르는 kid 재조회에 URL 별 30초 상한 — 회전 대응과 증폭 방지를 함께(`app/services/a2a_gateway.py:839-849`, `:865-871`). (2) issuer 확인을 네트워크 앞으로(`:860-861`). (3) artifacts 상한으로 행 크기 상한이 생겼다(`:353-364`).
- 위험: (1) [위험:낮음] L9 — https·issuer 검사가 PATCH 입력에만, 등록·기존 행·사용 시점에 없음. (2) [위험:낮음] L10 — 큰 artifact 하나가 목록 전체를 비움 · `append` 무시(인접).
- 개선 권고(영향순): (1) `_push_jwks` 에서 prod https 강제 + 등록 경로 검사. (2) `_cap_artifacts` 를 건너뛰기 + 잘림 표시로. (3) `append` 조각 이어 붙이기.
- 파일 위험 등급 / Risk grade: **낮음** — [위험:낮음]만.
- 재읽기 권고 레벨: 3 — D22 hunk 는 블록 단위로 충분. 푸시 검증 전체(`:852-887`)는 보안 경계라 처음 맡으면 2 로 한 번.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| L15 artifacts 상한 없음 | 해결 · 큰 항목 처리(L10) | `app/services/a2a_gateway.py:353-364` |
| L17 issuer 없는 설정·모르는 kid·https | 해결(PATCH·표시·재조회) · 등록·사용 시점 공백(L9) | `:839-871`, `app/api/a2a.py:399-400`, `:564-568` |
| M3 SSE 줄 버퍼(59th 수정) | 유지 | `app/services/a2a_gateway.py:385-397` |
| L14 `drop_tokens` 키(59th 수정) | 유지 | `app/api/a2a.py:582-583` |
