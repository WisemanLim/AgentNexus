"""41st gRPC 시험 클라이언트 — env: ADDR · TOKEN(없으면 익명) · TENANT · CA_PEM(있으면 TLS) · SNI(TLS 이름) · MODE(send|anon).

출력: `send <STATE> <taskId>` · `get <id>` · `anon <CODE>` · 실패 시 `error <CODE>` (stdout 한 줄씩).
"""
import asyncio
import os
import uuid

import grpc
from a2a.types import a2a_pb2 as pb
from a2a.types import a2a_pb2_grpc as pbg


async def main() -> None:
    addr, tenant, ca = os.environ["ADDR"], os.environ.get("TENANT", "observability-analyst"), os.environ.get("CA_PEM")
    md = [("authorization", f"Bearer {os.environ['TOKEN']}")] if os.environ.get("TOKEN") else []
    if ca:
        ch = grpc.aio.secure_channel(addr, grpc.ssl_channel_credentials(root_certificates=ca.encode()),
                                     options=[("grpc.ssl_target_name_override", os.environ.get("SNI", ""))])
    else:
        ch = grpc.aio.insecure_channel(addr)
    msg = pb.SendMessageRequest(tenant=tenant, message=pb.Message(message_id=uuid.uuid4().hex, role=pb.ROLE_USER,
                                                                  parts=[pb.Part(text="orders-api 장애 분석")]))
    async with ch:
        stub = pbg.A2AServiceStub(ch)
        try:
            if os.environ.get("MODE") == "anon":
                await stub.SendMessage(msg, timeout=20)
                print("anon OK")
                return
            sent = await stub.SendMessage(msg, metadata=md, timeout=60)
            print("send", pb.TaskState.Name(sent.task.status.state), sent.task.id)
            got = await stub.GetTask(pb.GetTaskRequest(tenant=tenant, id=sent.task.id), metadata=md, timeout=20)
            print("get", got.id)
        except grpc.aio.AioRpcError as exc:
            print("anon" if os.environ.get("MODE") == "anon" else "error", exc.code().name, exc.details())


asyncio.run(main())
