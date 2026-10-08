"""배포 프로파일·에어갭 (FR-W01·P02, SCR-L01·A01, ST-09·CMP-16).

프로파일(full·gateway-only·registry-only·mcp-servers-only·a2a-only)은 기동 구성(컨테이너·COMPONENTS)이라 콘솔이 직접 프로세스를
바꾸지 않는다 — 원하는 상태를 저장하고 변경 미리보기·명령(compose·helm)을 준다(GitOps 와 같은 결과).
에어갭은 런타임에 바로 적용된다: 상용 공급자·상용 분류 모델 제외(로컬 폴백), 외부 레지스트리 동기화 거부, 외부 IdP 로그인 버튼 숨김.
"""
import time
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import ALL_COMPONENTS, settings
from app.db import utcnow
from app.models import ChatSession, Run, Setting
from app.services import audit

KEY = "deploy.state"
COMMERCIAL_KINDS = ("openai", "anthropic", "gemini")
# 프로파일 → 켜는 구성요소 (의존성 identity 는 자동 포함) · compose 프로파일
PROFILES = {
    "full": (ALL_COMPONENTS, "demo"),
    "gateway-only": (("gateway", "identity"), ""),
    "registry-only": (("registry", "identity"), ""),
    "mcp-servers-only": (("identity",), "demo"),
    "a2a-only": (("host", "gateway", "identity"), ""),
}
AIRGAP_EFFECTS = ["상용 LLM 공급자(OpenAI·Anthropic·Gemini)·상용 분류 모델 비활성 → 로컬 모델로 폴백",
                  "외부 MCP 레지스트리 동기화(official·docker) 끔", "외부 IdP 로그인 버튼 숨김 (내장 IdP 만)",
                  "로컬 모델 서비스 ollama 포함 (COMPOSE_PROFILES=demo,ollama)"]
_CACHE: dict = {"at": 0.0, "value": None}


def state(db: Session) -> dict:
    row = db.get(Setting, KEY)
    cur = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {"profile": cur.get("profile", "full"), "mode": cur.get("mode", "compose"), "airgap": bool(cur.get("airgap")),
            "applied_at": cur.get("applied_at"), "applied_by": cur.get("applied_by")}


def airgap(db: Session) -> bool:
    """에어갭 여부 — 호출마다 DB 를 읽지 않도록 5초 캐시 (적용 시 즉시 무효화)."""
    if _CACHE["value"] is None or time.monotonic() - _CACHE["at"] > 5:
        _CACHE.update(value=state(db)["airgap"], at=time.monotonic())
    return _CACHE["value"]


def invalidate() -> None:
    _CACHE.update(value=None, at=0.0)


def commands(profile: str, airgap_on: bool, mode: str) -> dict:
    comps, cp = PROFILES[profile]
    cprof = ",".join(x for x in (cp, "ollama" if airgap_on else "") if x)
    env = "" if profile == "full" else f"AGENTNEXUS_COMPONENTS={','.join(comps)} "
    return {"compose": f"{env}COMPOSE_PROFILES={cprof} make prod-all",
            "helm": f"helm upgrade anx deploy/helm/agentnexus --reuse-values --set profile={profile}"
                    f" --set config.components={','.join(comps) if profile != 'full' else 'all'}"
                    f"{' --set airgap.enabled=true' if airgap_on else ''}",
            "preferred": "helm" if mode == "helm" else "compose"}


def plan(db: Session, profile: str, airgap_on: bool) -> dict:
    if profile not in PROFILES:
        raise ValueError(f"알 수 없는 프로파일: {profile}")
    cur = state(db)
    now_on = set(PROFILES[cur["profile"]][0])
    next_on = set(PROFILES[profile][0])
    active = db.scalar(select(func.count()).select_from(Run).where(Run.status.in_(("running", "queued"))))
    sessions = db.scalar(select(func.count()).select_from(ChatSession).where(
        ChatSession.status == "active", ChatSession.updated_at > utcnow() - timedelta(hours=1)))
    return {"from": cur, "to": {"profile": profile, "airgap": airgap_on},
            "start": sorted(next_on - now_on), "stop": sorted(now_on - next_on), "keep": sorted(now_on & next_on),
            "data": "보존 — 카탈로그·세션·감사 데이터는 지우지 않습니다 (DB 공유)",
            "airgap_effects": AIRGAP_EFFECTS if airgap_on and not cur["airgap"] else
            (["상용 공급자·외부 레지스트리·외부 IdP 로그인 다시 허용"] if cur["airgap"] and not airgap_on else []),
            "active_runs": active or 0, "active_sessions": sessions or 0,
            "warning": "실행 중 작업은 끝까지 진행되고, 새 프로파일에서 빠지는 구성요소의 요청은 거부됩니다." if (active or 0) else None,
            "commands": commands(profile, airgap_on, cur["mode"])}


def apply(db: Session, actor: str, profile: str, airgap_on: bool, mode: str | None = None) -> dict:
    """원하는 상태 저장 + 에어갭 런타임 적용. 저장이 실패하면 이전 상태로 되돌린다 (ST-03)."""
    p = plan(db, profile, airgap_on)
    row = db.get(Setting, KEY)
    before = dict(row.value) if row and isinstance(row.value, dict) else None
    new = {"profile": profile, "airgap": airgap_on, "mode": mode or p["from"]["mode"], "applied_at": utcnow().isoformat(),
           "applied_by": actor}
    try:
        if row is None:
            db.add(Setting(key=KEY, value=new, updated_by=actor))
        else:
            row.value, row.updated_by = new, actor
        db.commit()
    except Exception:
        db.rollback()
        if before is not None:
            db.get(Setting, KEY).value = before
            db.commit()
        raise
    invalidate()
    audit.append("deploy.profile.apply", actor=actor, target=profile, data={"airgap": airgap_on, "stop": p["stop"],
                                                                            "start": p["start"]})
    return {**p, "applied": new}


def banners(db: Session, perms: set[str]) -> list[dict]:
    """전역 배너 (CMP-16) — 에어갭(info)·첫 실행 미완료(warn, 관리자)·감사 체인 손상(danger, MSG-022)."""
    out = []
    if airgap(db):
        out.append({"id": "airgap", "tone": "info", "text": "에어갭 모드 — 외부 SaaS·상용 공급자·외부 레지스트리·외부 IdP 를 쓰지 않습니다 (ST-09)",
                    "link": "/console/admin/profiles"})
    admin = "*" in perms or "admin:write" in perms
    done = db.get(Setting, "setup.completed")
    if admin and not (done and done.value):
        out.append({"id": "setup", "tone": "warn", "text": "첫 실행 설정이 끝나지 않았습니다 — 관리자·IdP·모델·레지스트리·관측을 확인하세요",
                    "link": "/setup"})
    chain = db.get(Setting, "audit.last_verify")
    if chain and isinstance(chain.value, dict) and chain.value.get("valid") is False and ("audit:read" in perms or "*" in perms):
        out.append({"id": "audit", "tone": "danger", "text": "감사 해시 체인 손상이 감지되었습니다 (MSG-022) — 즉시 점검하세요",
                    "link": "/console/audit"})
    return out


def is_commercial(provider_kind: str | None, model_category: str | None) -> bool:
    return provider_kind in COMMERCIAL_KINDS or model_category == "commercial"


def components_for(profile: str) -> tuple[str, ...]:
    return PROFILES[profile][0]


def env_components() -> list[str]:
    return [c for c in ALL_COMPONENTS if settings.enabled(c)]
