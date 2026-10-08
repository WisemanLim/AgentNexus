"""에이전트 프로파일 공용 — 그래프 적용(허용 도구·원격 에이전트·모델 동기화)·카탈로그 노출(FR-P01)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.models import AgentProfile, CatalogItem
from app.services import graph as G


def apply_graph(pr: AgentProfile, g: dict) -> None:
    g = G.normalize(g)
    pr.graph, pr.tools, pr.remote_agents, pr.models = g, G.tools_of(g), G.remote_agents_of(g), G.models_of(g)


def sync_catalog(db: Session, pr: AgentProfile, actor: str) -> None:
    item = db.scalars(select(CatalogItem).where(CatalogItem.type == "agent_profile", CatalogItem.name == pr.id)).first()
    meta = {"name": pr.name, "description": pr.description, "version": pr.version, "tools": pr.tools,
            "remote_agents": pr.remote_agents, "template": pr.template}
    status = "active" if pr.status == "published" else "approved"
    if item is None:
        db.add(CatalogItem(id=new_id("cat"), type="agent_profile", name=pr.id, display_name=pr.name,
                           description=pr.description, source="builtin" if pr.template else "manual", status=status,
                           version=str(pr.version), item_metadata=meta, owner=actor, ref_id=pr.id,
                           tags=["template"] if pr.template else []))
    else:
        keep = {k: v for k, v in (item.item_metadata or {}).items() if k.startswith("_anx_")}  # 심사 기록 유지
        item.item_metadata, item.status, item.version, item.display_name = {**meta, **keep}, status, str(pr.version), pr.name
