"""공식 fastmcp Client 를 하류 MCP 클라이언트로 — sampling·elicitation 능력을 선언하고 게이트웨이 중계를 받는다 (api 컨테이너에서 실행)."""
import asyncio
import json
import sys

import mcp.types as mt
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

TOKEN = sys.argv[1]
asked: list[str] = []


async def sampling(messages, params, context):
    asked.append("sampling")
    return mt.CreateMessageResult(role="assistant", content=mt.TextContent(type="text", text="외부 클라이언트 모델이 분류"),
                                  model="client-side-model", stopReason="endTurn")


async def elicitation(message, response_type, params, context):
    asked.append("elicitation")
    return {"reason": "오배송", "quantity": 1}  # fastmcp 규약: 수락 데이터만 반환 (거절·취소는 ElicitResult)


async def main():
    transport = StreamableHttpTransport("http://127.0.0.1:8000/mcp/internal-orders",
                                        headers={"Authorization": f"Bearer {TOKEN}"})
    async with Client(transport, sampling_handler=sampling, elicitation_handler=elicitation) as c:
        a = await c.call_tool("classify_orders", {})
        b = await c.call_tool("request_return", {"order_id": "O-2402"})
    print(json.dumps({"asked": asked, "classify": a.structured_content, "return": b.structured_content},
                     ensure_ascii=False))

asyncio.run(main())
