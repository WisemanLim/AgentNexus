"""시스템 정보 (SCR-B05·L04 기초) · 전역 배너 (CMP-16)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal
from app.core.config import A2A_PROTOCOL_VERSION, A2A_SUPPORTED_VERSIONS, ALL_COMPONENTS, APP_VERSION, MCP_PROTOCOL_VERSION, MCP_SUPPORTED_VERSIONS, settings
from app.db import get_db
from app.services import deploy

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/info")
def info(db: Session = Depends(get_db)):
    return {
        "name": "AgentNexus",
        "version": APP_VERSION,
        "env": settings.app_env,
        "db_engine": settings.db_engine,
        "components": [c for c in ALL_COMPONENTS if settings.enabled(c)],
        "protocols": {"mcp": MCP_PROTOCOL_VERSION, "mcp_supported": ["2026-07-28", *MCP_SUPPORTED_VERSIONS],  # dual-era (D13-④)
                      "a2a": A2A_PROTOCOL_VERSION, "a2a_supported": list(A2A_SUPPORTED_VERSIONS),
                      "openai": "chat.completions"},
        "airgap": deploy.airgap(db),
    }


@router.get("/banners")
def banners(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """상단 고정 배너 (CMP-16) — 에어갭(ST-09)·첫 실행 미완료·감사 체인 손상(MSG-022)."""
    return {"items": deploy.banners(db, p.permissions() if p.scope_perms() is None else p.scope_perms())}
