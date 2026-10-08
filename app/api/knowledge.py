"""지식 베이스 API (FR-A02.2 RAG) — 관리(agents:write)·검색(chat:use, 팀 범위). Retriever 의 `kb` 단계가 search 를 부른다."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require
from app.core.errors import bad_request, conflict, not_found
from app.db import SessionLocal, get_db
from app.models import Attachment, KbDocument, KnowledgeBase, Model
from app.services import audit, knowledge, policy

router = APIRouter(tags=["agents"])
K = "/api/v1/knowledge-bases"


def _kb(db: Session, kid: str, p: Principal) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kid)
    if kb is None or not knowledge.visible(kb, p):
        raise not_found("knowledge base")
    return kb


class KbIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    team: str | None = None
    data_class: str = Field("internal", pattern="^(public|internal|confidential)$")
    embedding_model: str = "embed-local"


@router.get(K)
def list_kbs(p: Principal = Depends(require("agents:read")), db: Session = Depends(get_db)):
    rows = [kb for kb in db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.id)) if knowledge.visible(kb, p)]
    n = knowledge.counts(db, [kb.id for kb in rows])
    return {"items": [knowledge.kb_out(kb, *n[kb.id]) for kb in rows]}


@router.post(K, status_code=201)
def create_kb(body: KbIn, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    if db.get(KnowledgeBase, body.id):
        raise conflict("같은 id 의 지식 베이스가 있습니다.")
    m = db.get(Model, body.embedding_model)
    if m is None or "embeddings" not in (m.capabilities or []):
        raise bad_request(f"임베딩 모델이 아닙니다: {body.embedding_model}", code="invalid_embedding_model")
    kb = KnowledgeBase(**body.model_dump(), owner=p.sub)
    db.add(kb)
    db.commit()
    audit.append("knowledge.create", actor=p.sub, target=kb.id, data={"team": kb.team, "data_class": kb.data_class})
    return knowledge.kb_out(kb, 0, 0)


@router.get(K + "/{kid}")
def get_kb(kid: str, p: Principal = Depends(require("agents:read")), db: Session = Depends(get_db)):
    kb = _kb(db, kid, p)
    docs = db.scalars(select(KbDocument).where(KbDocument.kb_id == kid).order_by(KbDocument.created_at.desc())).all()
    return {**knowledge.kb_out(kb, *knowledge.counts(db, [kid])[kid]), "documents": [knowledge.doc_out(d) for d in docs]}


@router.delete(K + "/{kid}")
def delete_kb(kid: str, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    kb = _kb(db, kid, p)
    for d in db.scalars(select(KbDocument.id).where(KbDocument.kb_id == kid)).all():
        knowledge.delete_document(db, kid, d)
    db.delete(kb)
    db.commit()
    audit.append("knowledge.delete", actor=p.sub, target=kid)
    return {"deleted": kid}


class DocIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    text: str | None = Field(None, max_length=2_000_000)
    attachment_id: str | None = None
    source: str | None = None


@router.post(K + "/{kid}/documents", status_code=201)
async def add_document(kid: str, body: DocIn, p: Principal = Depends(require("agents:write"))):
    def _text():
        with SessionLocal() as db:
            _kb(db, kid, p)
            if body.attachment_id:
                a = db.get(Attachment, body.attachment_id)
                if a is None or a.owner_sub != p.sub:
                    raise not_found("attachment")
                if not a.text:
                    raise bad_request("텍스트를 추출할 수 없는 첨부입니다 (이미지·스캔 PDF).", code="no_text")
                return a.text, body.source or f"attachment:{a.name}"
            if not body.text:
                raise bad_request("text 또는 attachment_id 가 필요합니다.")
            return body.text, body.source
    text, source = await run_in_threadpool(_text)
    try:
        return await knowledge.add_document(kid, p, body.title, text, source)
    except knowledge.KnowledgeError as exc:
        raise bad_request(str(exc), code="knowledge_error") from exc


@router.delete(K + "/{kid}/documents/{doc_id}")
def delete_document(kid: str, doc_id: str, p: Principal = Depends(require("agents:write")),
                    db: Session = Depends(get_db)):
    _kb(db, kid, p)
    if db.get(KbDocument, doc_id) is None:
        raise not_found("document")
    knowledge.delete_document(db, kid, doc_id)
    db.commit()
    audit.append("knowledge.document.delete", actor=p.sub, target=f"{kid}/{doc_id}")
    return {"deleted": doc_id}


@router.post(K + "/{kid}/reindex")
async def reindex(kid: str, p: Principal = Depends(require("agents:write"))):
    try:
        return await knowledge.reindex(kid, p)
    except knowledge.KnowledgeError as exc:
        raise not_found("knowledge base") from exc


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(5, ge=1, le=20)


@router.post(K + "/{kid}/search")
async def search(kid: str, body: SearchIn, request: Request, p: Principal = Depends(require("chat:use"))):
    """검색 — 질의 임베딩은 요청 데이터 등급과 지식 베이스 등급 중 엄격한 쪽으로 라우팅된다."""
    dc = policy.declared_data_class(None, p, request.headers.get("x-agentnexus-data-class"))
    if dc not in policy.DATA_CLASSES:
        raise bad_request("data_class 오류", code="invalid_data_class")
    try:
        return await knowledge.search(kid, p, body.query, body.top_k, dc)
    except knowledge.KnowledgeError as exc:
        raise not_found("knowledge base") from exc
