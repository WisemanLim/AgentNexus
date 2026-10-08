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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        sys.exit(probe(sys.argv[2] if len(sys.argv) > 2 else ""))
    logs.setup()
    asyncio.run(main())
