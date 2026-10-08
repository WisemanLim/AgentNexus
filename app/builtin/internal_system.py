"""데모 '내부 시스템' REST API (FR-S02 대상) — Internal API MCP Server 가 OpenAPI 로 도구화한다."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.builtin.demo_data import ORDERS

app = FastAPI(title="Hangyeol Internal System", version="1.0.0",
              description="주문·티켓 내부 API (가상 데이터) — OpenAPI → MCP 도구 자동 노출 시연용")
_TICKETS: list[dict] = []


@app.get("/orders", operation_id="orders.search", summary="주문 검색")
def orders_search(customer: str | None = None, status: str | None = None, limit: int = 20) -> list[dict]:
    rows = [o for o in ORDERS if (not customer or o["customer"] == customer) and (not status or o["status"] == status)]
    return rows[:limit]


@app.get("/orders/{order_id}", operation_id="orders.get", summary="주문 상세")
def orders_get(order_id: str) -> dict:
    for o in ORDERS:
        if o["id"] == order_id:
            return o
    raise HTTPException(404, "order not found")


class TicketIn(BaseModel):
    title: str
    order_id: str | None = None
    priority: str = "normal"


@app.post("/tickets", operation_id="tickets.create", summary="고객센터 티켓 생성", status_code=201)
def tickets_create(body: TicketIn) -> dict:
    ticket = {"id": f"T-{len(_TICKETS) + 1:04d}", **body.model_dump(), "status": "open"}
    _TICKETS.append(ticket)
    return ticket


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}
