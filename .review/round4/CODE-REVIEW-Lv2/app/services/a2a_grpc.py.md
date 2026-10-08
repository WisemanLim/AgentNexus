> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 a2a_grpc.py.md](../../../../round3/CODE-REVIEW-Lv2/app/services/a2a_grpc.py.md)

### 08. app/services/a2a_grpc.py (gRPC 헬스) (+ `app/grpc_server.py`)
**파일 요약 / File Summary**
- 역할 / Role: A2A gRPC 바인딩 수신 서버 — 상태 없이 api 의 HTTP+JSON 바인딩으로 위임. 이번 범위는 표준 헬스(`grpc.health.v1.Health/Check`)와 컨테이너 프로브.
- 진입점 거리 / Distance: 0 — `python -m app.grpc_server`(compose `a2a-grpc`, Helm Deployment `-a2a-grpc`), `--probe [readiness]`.
- LOC: 337 (3차 309 → 337) · `app/grpc_server.py` 48 (3차 46 → 48)
- 주요 export: `health_handler`·`health_request`·`_service_of`·`_varint`(신규)·`READINESS_CACHE_S`(신규)·`serve`, `app.grpc_server.probe`
- 의존(내부): `app.core.config.settings`, `app.core.http.async_client`
- 외부 패키지: grpcio(`grpc.aio`), httpx
- 부수효과 / Side effects: readiness 때 api `/health` HTTP 호출(이제 2초 공유).
- 동시성 / Concurrency: `grpc.aio` 이벤트 루프 — 헬스 핸들러는 `asyncio.Lock` 하나로 직렬화. `maximum_concurrent_rpcs=256`(`app/services/a2a_grpc.py:267`).
- 테스트 커버리지 흔적: `test/test_boundaries4.py:193`(C64-08 readiness 5번 → `/health` 1번), `test/test_remaining_items2.py:26`(C66-01 리슨 호스트 프로브), `:131`(C66-07 varint 0·9·127·300 바이트).
- 4차 변경 요약 / Changes in D22: D22-④ — readiness 결과 2초 공유(3차 L18 증폭). D22-⑥ — 서비스 이름 길이 protobuf varint(위임 CR3-04), 프로브가 특정 리슨 주소면 그 주소로(3차 L19 ①).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_grpc.py lines=276-303
READINESS_CACHE_S = 2.0  # readiness 결과 공유 시간 — 프로브·외부 호출이 몰려도 api /health 는 2초에 1번


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def health_request(service: str = "") -> bytes:
    """HealthCheckRequest{service} — 필드 1(문자열) 길이는 protobuf varint (128 바이트 이상도, D22-⑥)."""
    raw = service.encode()
    return b"\x0a" + _varint(len(raw)) + raw if raw else b""


def _service_of(req: bytes) -> str:
    if len(req) < 2 or req[0] != 0x0A:
        return ""
    n, shift, i = 0, 0, 1
    while i < len(req) and i < 6:  # 길이 varint (최대 5 바이트)
        n |= (req[i] & 0x7F) << shift
        shift, i = shift + 7, i + 1
        if not req[i - 1] & 0x80:
            return req[i:i + n].decode("utf-8", "replace")
    return ""
```
**분석 / Analysis (Level 2)**
- L279-285 `_varint`: protobuf 가변 길이 정수 — 7비트씩 끊고 다음 바이트가 있으면 최상위 비트(0x80)를 켠다. 300 → `AC 02`(C66-07). 3차·위임 CR3-04 의 "길이 1바이트 가정"(128 이상이면 깨짐)을 고쳤다.
- L288-291: 필드 1(wire type 2 = 길이 구분) 태그 `0x0A` + varint 길이 + UTF-8. 빈 이름은 빈 메시지(기본값은 보내지 않는 proto3 규칙).
- L294-303 `_service_of`: 최대 5바이트까지 varint 를 읽고(L298 `i < 6`), 끝 비트가 꺼진 바이트에서 길이 확정 → 그만큼 잘라 디코드. 5바이트 안에 끝나지 않거나 비면 `""`(= liveness 로 취급, 안전한 쪽).
- 흔한 실수: `n |= (b & 0x7F) << shift` 에서 `shift` 를 늘리는 위치를 틀리면 두 번째 바이트부터 값이 어긋난다. 여기서는 OR 뒤에 늘린다(L299-300) — 맞다.

```python path=app/services/a2a_grpc.py lines=306-323
def health_handler(gateway_url: str | None = None) -> grpc.GenericRpcHandler:
    """""(기본·liveness) = 프로세스가 응답하면 SERVING · "readiness" = 위임 대상 게이트웨이 /health 가 200 일 때만 SERVING."""
    base = (gateway_url or settings.gateway_url).rstrip("/")
    last, lock = {"at": -READINESS_CACHE_S, "ok": False}, asyncio.Lock()

    async def check(request: bytes, context) -> bytes:
        if _service_of(request) != "readiness":
            return _STATUS[True]
        async with lock:  # 인증 없는 공개 readiness 가 api /health(DB)로 증폭되지 않게 — 결과를 잠깐 공유 (D22-④)
            if time.monotonic() - last["at"] >= READINESS_CACHE_S:
                try:
                    async with async_client(timeout=2) as http:
                        last["ok"] = (await http.get(f"{base}/health")).status_code == 200
                except httpx.HTTPError:
                    last["ok"] = False
                last["at"] = time.monotonic()
            return _STATUS[last["ok"]]
    return grpc.method_handlers_generic_handler(HEALTH_SERVICE, {"Check": grpc.unary_unary_rpc_method_handler(check)})
```
**분석 / Analysis (Level 2)**
- L309: 마지막 결과·시각을 클로저 dict 에 — 처음 `at = -2.0` 이라 첫 호출은 반드시 조회.
- L314-322(D22-④): 잠금 안에서 2초가 지났을 때만 api `/health` 를 부른다. 실패도 2초 공유(L319-321). 인증 없는 공개 readiness 를 외부에서 아무리 불러도 api·DB 로는 2초에 1번(3차 L18, C64-08).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L20** 잠금을 잡은 채 HTTP 호출(최대 2초, L317)을 기다린다. 그 2초 동안 들어온 readiness 요청은 모두 잠금 앞에 줄 서며 **RPC 동시 슬롯**(256, `:267`)을 차지한다. api 가 느릴 때(= 정확히 장애 순간) 인증 없는 readiness 호출을 쏟아 부으면 실제 A2A RPC 가 `RESOURCE_EXHAUSTED` 를 받을 수 있다. 증폭은 막았지만 대기 줄은 남은 셈. 최소 수정: 잠금 **밖에서** 캐시를 먼저 확인하고(이중 확인), 갱신 중이면 기다리지 말고 직전 값을 돌려주기.

```python path=app/grpc_server.py lines=21-40
def probe(service: str = "") -> int:
    """컨테이너 헬스 검사 — `python -m app.grpc_server --probe [readiness]` (D20-③). TLS 수신이면 TCP 연결만 본다."""
    import socket

    import grpc
    host, port = settings.a2a_grpc_listen.rsplit(":", 1)
    host = host.strip("[]")
    host = "127.0.0.1" if host in ("", "0.0.0.0", "*") else ("::1" if host == "::" else host)  # 특정 주소로만 수신하면 그 주소로 (D22-⑥)
    if a2a_grpc.server_tls():
        try:
            socket.create_connection((host, int(port)), 2).close()
            return 0
        except OSError:
            return 1
    with grpc.insecure_channel(f"[{host}]:{port}" if ":" in host else f"{host}:{port}") as ch:
        check = ch.unary_unary(f"/{a2a_grpc.HEALTH_SERVICE}/Check")
        try:
            return 0 if check(a2a_grpc.health_request(service), timeout=3) == a2a_grpc._STATUS[True] else 1
        except grpc.RpcError:
            return 1
```
**분석 / Analysis (Level 2)**
- L26-28(D22-⑥): 리슨 주소의 호스트가 와일드카드(`""`·`0.0.0.0`·`*`)면 `127.0.0.1`, `::` 면 `::1`, 특정 주소면 **그 주소**로 붙는다. 특정 인터페이스로만 수신할 때 프로브가 늘 실패하던 3차 L19 ① 해소(C66-01).
- L35: IPv6 주소는 `[addr]:port` 로 감싼다.
- L29-34: TLS 수신이면 TCP 연결만 — readiness 의 의미(위임 대상 api 까지)는 TLS 에서 여전히 사라진다(3차 L19 ②, 66th 범위 표 "기록 유지").

## 파일 평가 / File Assessment
- 강점: (1) protobuf varint 를 양방향으로 정확히 구현(`app/services/a2a_grpc.py:279-303`). (2) readiness 증폭을 2초 공유로 막음(`:314-322`). (3) 프로브가 리슨 주소를 따른다(`app/grpc_server.py:26-28`).
- 위험: (1) [위험:낮음] L20 — 잠금 안 HTTP 대기로 공개 readiness 가 RPC 슬롯을 점유.
- 개선 권고(영향순): (1) 잠금 밖 캐시 확인 + 갱신 중 직전 값 반환. (2) TLS readiness(3차 L19 ②, 기록 유지).
- 파일 위험 등급 / Risk grade: **낮음** — [위험:낮음]만.
- 재읽기 권고 레벨: 3 — 헬스는 블록 단위로 충분하다.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| L18 공개 readiness 증폭·길이 1바이트 | 해결 · 대기 줄(L20) | `app/services/a2a_grpc.py:279-322` |
| L19 프로브 127.0.0.1 고정 · TLS 면 TCP | ① 해결 · ② 기록 유지 | `app/grpc_server.py:26-34` |
