> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../INDEX.md) · 디렉터리 개요: [app_Abstract.md](app_Abstract.md)

### 02. app/grpc_server.py
**파일 요약 / File Summary**
- 역할 / Role: A2A gRPC 바인딩 수신 프로세스 진입점. 상태 없이 요청을 게이트웨이(api)의 HTTP+JSON 바인딩으로 위임한다.
- 진입점 거리 / Distance: 0 (`python -m app.grpc_server` — docker-compose 서비스 `a2a-grpc`)
- LOC: 23
- 주요 export: `main()`
- 의존(내부): `app.core.logs`, `app.core.config.settings`, `app.services.a2a_grpc`
- 외부 패키지: (간접) grpcio — `a2a_grpc.serve()` 내부
- 부수효과 / Side effects: 0.0.0.0:50051(`A2A_GRPC_LISTEN`) 포트 바인딩, stdout JSON 로그 설정
- 동시성 / Concurrency: asyncio (`grpc.aio` 서버)
- 테스트 커버리지 흔적: `test/test_a2a_grpc.py`(`a2a_grpc.serve` 를 0번 포트로 직접 기동)

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/grpc_server.py lines=1-12
"""A2A gRPC 바인딩 수신 서버 (D16-②) — compose 서비스 a2a-grpc: `python -m app.grpc_server`.

요청을 게이트웨이(GATEWAY_URL)의 HTTP+JSON 바인딩으로 위임하므로 상태가 없다 — 인증·정책·감사는 게이트웨이가 한다.
"""
import asyncio
import logging

from app.core import logs
from app.core.config import settings
from app.services import a2a_grpc

log = logging.getLogger("agentnexus.a2a-grpc")
```
**분석 / Analysis (Level 2)**
- L1-4: docstring 이 설계 결정을 요약 — 인증·정책·감사는 게이트웨이 한 곳에서만 하고 이 프로세스는 프로토콜 변환만 한다(단일 집행 지점 원칙).
- L8-10: 로그 설정·설정 싱글턴·실제 구현 모듈(`app/services/a2a_grpc.py`).
- L12: 표준 `logging` 로거. 이름 `agentnexus.a2a-grpc` 는 `logs.setup()` 이 붙이는 `agentnexus` 루트 핸들러 아래로 들어간다(`app/core/logs.py:45-53`).

```python path=app/grpc_server.py lines=15-23
async def main() -> None:
    server = await a2a_grpc.serve()
    log.info("A2A gRPC 수신 %s → %s", settings.a2a_grpc_listen, settings.gateway_url)
    await server.wait_for_termination()


if __name__ == "__main__":
    logs.setup()
    asyncio.run(main())
```
**분석 / Analysis (Level 2)**
- L15-18: `serve()` 가 `grpc.aio.server()` 를 만들고 시작한 뒤 반환(`app/services/a2a_grpc.py:248-254`). `wait_for_termination()` 으로 프로세스 수명 동안 블록.
- L21-23: 스크립트로 실행될 때만 로그 설정 후 `asyncio.run` — 이벤트 루프를 새로 만들고 끝나면 닫는 표준 진입 패턴.
**짚어볼 점 / Watch-outs**
- [위험:중간] 수신 포트는 `add_insecure_port`(평문, `app/services/a2a_grpc.py:251`)이며 compose 가 호스트에 게시한다(`docker-compose.yml` `a2a-grpc` ports). metadata `authorization` 의 Bearer 토큰이 평문으로 오가므로, 운영에서는 TLS 종단(Ingress/프록시) 뒤에 두거나 `add_secure_port` 를 써야 한다.
- 종료 신호(SIGTERM) 처리 코드가 없다 — `asyncio.run` 이 KeyboardInterrupt 에만 반응하므로 컨테이너 중지 시 진행 중 RPC 가 정리(`server.stop(grace)`)되지 않고 끊긴다.
- `logs.setup()` 만 호출하고 `tracing.setup(...)` 은 호출하지 않는다 — 이 프로세스의 스팬은 기록되지 않고, 게이트웨이로 가는 `traceparent` 는 `async_client` 의 전파 훅(`app/core/http.py:109-114`)이 비어 있는 컨텍스트로 주입한다.

## 파일 평가 / File Assessment
- 강점: (1) 23줄로 책임이 명확하다(진입점 = 기동만). (2) 정책 집행을 게이트웨이에 위임해 중복 구현을 피했다.
- 위험: (1) 평문 gRPC 수신. (2) graceful shutdown 부재. (3) 트레이싱 미설정으로 gRPC 경로의 trace 가 끊긴다.
- 개선 권고(영향순): (1) TLS 자격 증명 옵션 추가(`grpc.ssl_server_credentials`). (2) `loop.add_signal_handler(SIGTERM, ...)` 로 `await server.stop(5)`. (3) `tracing.setup_for_service("agentnexus-a2a-grpc")` 호출.
- 재읽기 권고 레벨: 3 — 실제 로직은 `app/services/a2a_grpc.py` 에 있다.
