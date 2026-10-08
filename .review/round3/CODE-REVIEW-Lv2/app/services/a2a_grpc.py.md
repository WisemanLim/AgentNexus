> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 a2a_grpc.py.md](../../../../round2/CODE-REVIEW-Lv2/app/services/a2a_grpc.py.md)

### 07. app/services/a2a_grpc.py (헬스·수신 서버) + app/grpc_server.py
**파일 요약 / File Summary**
- 역할 / Role: A2A gRPC 바인딩. 수신 쪽(`GatewayServicer`)은 상태 없이 RPC 를 게이트웨이(api)의 HTTP+JSON 바인딩으로 위임한다. 11차 D20-③ 로 **gRPC 표준 헬스(`grpc.health.v1.Health/Check`)** 가 생성 코드·추가 의존성 없이 "바이트 직접 인코딩" 으로 들어왔고, 컨테이너 헬스 검사 명령(`python -m app.grpc_server --probe [readiness]`)이 생겼다.
- 진입점 거리 / Distance: 0 — `app/grpc_server.py`(`python -m app.grpc_server`) → `a2a_grpc.serve`. Helm 의 kubelet 네이티브 gRPC 프로브(`deploy/helm/agentnexus/templates/a2a-grpc.yaml:48-54`)와 compose healthcheck(`docker-compose.yml:72-76`)가 헬스를 부른다.
- LOC: `a2a_grpc.py` 309 (2차 스냅샷 277 → +32), `grpc_server.py` 46
- 주요 export(범위 안): `server_tls`, `HEALTH_SERVICE`·`_STATUS`·`health_request`·`_service_of`·`health_handler`, `serve`, `grpc_server.probe`·`main`
- 의존(내부): `app.core.config.settings`, `app.core.http.async_client`, `app.core.logs`
- 외부 패키지: grpcio(aio·generic handler), httpx, a2a-sdk(protobuf 스텁)
- 부수효과 / Side effects: 포트 수신(50051), readiness 요청마다 api `/health` 로 HTTP GET.
- 동시성 / Concurrency: grpc.aio 서버(이벤트 루프), 동시 RPC 상한 256(`MAX_CONCURRENT_RPCS`, L265). 프로브는 별도 프로세스(동기 채널).
- 테스트 커버리지 흔적: `test/test_deploy_hardening.py:21-39`(C54-01 기본 SERVING·readiness 200/503), `:41-50`(C54-02 compose 헬스 = probe readiness), live `test/impl/54th/logs/local-probe.log`(L54-01, 54th 결과 표).
- 3차 변경 요약: D20-③ — L268-295(헬스), L302(핸들러 등록), `grpc_server.py:21-44`(프로브).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_grpc.py lines=252-257
def server_tls() -> tuple[bytes, bytes] | None:
    """(key, cert) PEM — A2A_GRPC_TLS_CERT·A2A_GRPC_TLS_KEY 가 모두 있으면."""
    if settings.a2a_grpc_tls_cert and settings.a2a_grpc_tls_key:
        with open(settings.a2a_grpc_tls_key, "rb") as k, open(settings.a2a_grpc_tls_cert, "rb") as c:
            return k.read(), c.read()
    return None
```
**분석 / Analysis (Level 2)**
- 수신 TLS: 인증서·키 경로가 **둘 다** 있어야 TLS. 하나만 있으면 조용히 평문이다(설정 실수가 드러나지 않는다 — 2차 M44 "평문 기본값" 과 같은 계열, VERIFY:68 기록).

```python path=app/services/a2a_grpc.py lines=268-295
# gRPC 표준 헬스 (grpc.health.v1.Health/Check) — 생성 코드·추가 의존성 없이 바이트로 (D20-③).
# HealthCheckRequest{string service = 1} · HealthCheckResponse{ServingStatus status = 1} (1 SERVING · 2 NOT_SERVING)
HEALTH_SERVICE = "grpc.health.v1.Health"
_STATUS = {True: b"\x08\x01", False: b"\x08\x02"}


def health_request(service: str = "") -> bytes:
    raw = service.encode()
    return b"\x0a" + bytes([len(raw)]) + raw if raw else b""


def _service_of(req: bytes) -> str:
    return req[2:2 + req[1]].decode("utf-8", "replace") if len(req) >= 2 and req[0] == 0x0A else ""


def health_handler(gateway_url: str | None = None) -> grpc.GenericRpcHandler:
    """""(기본·liveness) = 프로세스가 응답하면 SERVING · "readiness" = 위임 대상 게이트웨이 /health 가 200 일 때만 SERVING."""
    base = (gateway_url or settings.gateway_url).rstrip("/")

    async def check(request: bytes, context) -> bytes:
        if _service_of(request) != "readiness":
            return _STATUS[True]
        try:
            async with async_client(timeout=2) as http:
                return _STATUS[(await http.get(f"{base}/health")).status_code == 200]
        except httpx.HTTPError:
            return _STATUS[False]
    return grpc.method_handlers_generic_handler(HEALTH_SERVICE, {"Check": grpc.unary_unary_rpc_method_handler(check)})
```
**분석 / Analysis (Level 2)**
- L271 `_STATUS`: `HealthCheckResponse{status}` 의 protobuf 인코딩을 손으로 — 필드 1·varint 타입이면 태그 바이트 `0x08`, 값 1(SERVING)·2(NOT_SERVING). 생성 코드 없이 grpc-health-checking 패키지 의존을 피했다.
- L274-276 `health_request`: `HealthCheckRequest{string service = 1}` — 태그 `0x0a`(필드 1·길이 구분 타입) + 길이 1바이트 + 바이트.
- L279-280 `_service_of`: 요청에서 `service` 를 꺼낸다(형식이 다르면 빈 문자열 = liveness).
- L283-295: `service == "readiness"` 면 위임 대상 api 의 `/health` 가 200 일 때만 SERVING, 그 밖(빈 문자열 포함)은 프로세스가 응답하면 SERVING. 직렬화 함수를 주지 않았으므로(`unary_unary_rpc_method_handler(check)`) 요청·응답이 **원시 바이트**다.
- 흔한 실수: liveness 에 readiness 와 같은 하위 의존(api·DB) 확인을 넣으면, DB 장애 때 kubelet 이 멀쩡한 a2a-grpc 파드를 계속 재시작한다. 여기서는 둘을 나눴다(맞는 설계).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L18** ① L276·L280 길이를 **1바이트**로 다룬다. protobuf 길이는 varint 라 128 이상이면 2바이트 이상이다 — `health_request` 는 256 이상에서 `ValueError`, `_service_of` 는 128 이상 이름을 잘못 읽는다(결과는 liveness 로 처리되어 SERVING). 지금 쓰는 이름은 `"readiness"` 뿐이라 실해는 없지만, 손 인코딩의 한계를 주석에 남기거나 `len(raw) < 128` 단언. ② 헬스 서비스는 a2a-grpc 와 **같은 공개 포트**에서 인증 없이 열려 있다(엣지 노출 — `docker-compose.yml:65`, Helm Ingress `a2a-grpc.yaml:79-105`). `service: readiness` 요청 하나가 api `/health` HTTP 요청 하나(+ DB `ping`, `app/main.py:228-237`)가 되고, 요청마다 새 HTTP 클라이언트(L291)를 만든다 — 외부에서 반복 호출하면 api·DB 로 증폭된다(동시 RPC 256 상한 안). readiness 결과를 1~2초 캐시하고, 클라이언트는 모듈 수준에서 재사용 권장.

```python path=app/services/a2a_grpc.py lines=298-309
async def serve(listen: str | None = None, gateway_url: str | None = None,
                tls: tuple[bytes, bytes] | None = None) -> grpc.aio.Server:
    server = grpc.aio.server(options=SERVER_OPTIONS, maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS)
    pbg.add_A2AServiceServicer_to_server(GatewayServicer(gateway_url), server)
    server.add_generic_rpc_handlers((health_handler(gateway_url),))
    tls = tls or server_tls()
    addr = listen or settings.a2a_grpc_listen
    port = server.add_secure_port(addr, grpc.ssl_server_credentials([tls])) if tls else server.add_insecure_port(addr)
    server.tls = bool(tls)
    await server.start()
    server.bound_port = port  # 시험에서 0 번 포트로 띄울 때
    return server
```
**분석 / Analysis (Level 2)**
- L302(D20-③): A2A 서비스와 같은 서버에 헬스 핸들러를 추가 등록. 수신 상한(L261-265, D19-②)은 헬스에도 똑같이 적용된다.

```python path=app/grpc_server.py lines=1-46
"""A2A gRPC 바인딩 수신 서버 (D16-②) — compose 서비스 a2a-grpc: `python -m app.grpc_server`.

요청을 게이트웨이(GATEWAY_URL)의 HTTP+JSON 바인딩으로 위임하므로 상태가 없다 — 인증·정책·감사는 게이트웨이가 한다.
"""
import asyncio
import logging

from app.core import logs
from app.core.config import settings
from app.services import a2a_grpc

log = logging.getLogger("agentnexus.a2a-grpc")


async def main() -> None:
    server = await a2a_grpc.serve()
    log.info("A2A gRPC 수신 %s (%s) → %s", settings.a2a_grpc_listen, "TLS" if server.tls else "평문", settings.gateway_url)
    await server.wait_for_termination()


def probe(service: str = "") -> int:
    """컨테이너 헬스 검사 — `python -m app.grpc_server --probe [readiness]` (D20-③). TLS 수신이면 TCP 연결만 본다."""
    import socket

    import grpc
    port = int(settings.a2a_grpc_listen.rsplit(":", 1)[1])
    if a2a_grpc.server_tls():
        try:
            socket.create_connection(("127.0.0.1", port), 2).close()
            return 0
        except OSError:
            return 1
    with grpc.insecure_channel(f"127.0.0.1:{port}") as ch:
        check = ch.unary_unary(f"/{a2a_grpc.HEALTH_SERVICE}/Check")
        try:
            return 0 if check(a2a_grpc.health_request(service), timeout=3) == a2a_grpc._STATUS[True] else 1
        except grpc.RpcError:
            return 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        sys.exit(probe(sys.argv[2] if len(sys.argv) > 2 else ""))
    logs.setup()
    asyncio.run(main())
```
**분석 / Analysis (Level 2)**
- L21-38 `probe`(D20-③): 컨테이너 헬스 검사용 별도 프로세스. TLS 수신이면 kubelet·이 프로브가 TLS 로 gRPC 를 말할 수 없어서(인증서 검증 대상 이름 문제) **TCP 연결만** 본다(L27-32). 평문이면 동기 채널로 `Health/Check` 를 직접 부른다(L33-38).
- L43-44: `--probe` 가 있으면 로깅 설정·서버 기동 없이 결과 코드로 종료 — 헬스 검사는 가볍고 부작용이 없어야 한다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L19** ① L26·L29·L33 프로브는 항상 `127.0.0.1:<포트>` 로 붙는다. `A2A_GRPC_LISTEN` 을 특정 인터페이스(예: `10.0.0.5:50051`)로 바꾸면 프로브가 항상 실패한다(기본 `0.0.0.0`/`[::]` 에서는 문제 없음). 리슨 주소의 호스트가 와일드카드가 아니면 그 호스트로 붙게. ② TLS 수신이면 liveness·readiness 가 모두 TCP 라 **readiness 의 의미(위임 대상 api 까지 확인)가 사라진다** — api 가 내려가도 TLS 파드는 Ready 로 남아 Ingress 가 요청을 계속 보낸다. 54th 결과 문서·차트 주석(`deploy/helm/agentnexus/templates/a2a-grpc.yaml:39`)에 한계로 적혀 있다. 프로브 프로세스가 TLS 채널(`grpc.ssl_channel_credentials(root_certificates=자기 인증서)` + `grpc.ssl_target_name_override`)로 readiness 를 부르면 의미를 살릴 수 있다. ③ compose healthcheck 는 10초마다 이 모듈을 새로 import 한다(a2a-sdk·protobuf·httpx 포함) — 부하가 큰 호스트에서는 import 시간 + readiness 2초가 `timeout: 3s`(`docker-compose.yml:75`)에 가까워 unhealthy 로 흔들릴 수 있다.

## 파일 평가 / File Assessment
- 강점: (1) liveness(프로세스)·readiness(위임 대상까지)를 분리했다(`app/services/a2a_grpc.py:287-294`). (2) 생성 코드·추가 의존성 없이 표준 헬스 프로토콜을 구현해 kubelet 네이티브 gRPC 프로브와 호환(`app/services/a2a_grpc.py:268-276`, `deploy/helm/agentnexus/templates/a2a-grpc.yaml:48-54`). (3) 프로브 경로가 서버 기동·로깅과 분리되어 가볍다(`app/grpc_server.py:41-44`).
- 위험: (1) [위험:낮음] 인증 없는 공개 readiness 가 api·DB 로 요청을 증폭(`app/services/a2a_grpc.py:287-294`). (2) [위험:낮음] TLS 수신이면 readiness 의미 상실(`app/grpc_server.py:27-32`). (3) [위험:낮음] 손 인코딩 길이 1바이트 가정(`app/services/a2a_grpc.py:274-280`).
- 개선 권고(영향순): (1) readiness 결과 짧은 캐시 + HTTP 클라이언트 재사용. (2) TLS 프로브. (3) 길이 단언·주석.
- 파일 위험 등급 / Risk grade: 낮음 — [위험:낮음]만.
- 재읽기 권고 레벨: 3 — 헬스는 블록 단위로 충분하다. 바이트 인코딩 부분만 protobuf 와이어 형식과 대조.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M44 `grpcs://` 아니면 평문 HTTP/2 로 authorization 전송 (`a2a_grpc.py:52`·`:59-60`) | 미해결(기록 유지 VERIFY:68) | `app/services/a2a_grpc.py:53`(`grpcs://` 판정)·`:60`(평문 채널 분기) 그대로 |
| (2차 INFRA.md L84) 준비성이 TCP 뿐 — api 에 못 닿아도 Ready · (L46) a2a-grpc 에 OTLP env 미전달 | 해결(평문 수신) · 해당 없음(OTLP) | 헬스 `:268-295`, 차트 프로브 `deploy/helm/agentnexus/templates/a2a-grpc.yaml:39-55`. OTLP 는 이 프로세스가 스팬을 만들지 않아 해당 없음(`test/impl/54th/result.md` 결과 요약 2번째 항목) |
