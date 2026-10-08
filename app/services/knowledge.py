"""지식 베이스 · 내장 벡터 색인 (FR-A02.2 RAG, PRD D12) — 문서 → 청크 → 임베딩(llm.embed_texts, 게이트웨이와 같은
가드·데이터 등급 라우팅·사용량) → JSON 벡터 저장. 검색은 L2 정규화 벡터 내적(=코사인)을 앱 안에서 계산한다.

SQLite·PostgreSQL 이 같은 방식으로 동작한다. 청크 수만 개 규모까지를 전제로 하며, 그 이상은 pgvector 등 외부 색인으로 바꾼다.
"""
import hashlib
import re

from sqlalchemy import delete, func, select
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal
from app.core.ids import new_id
from app.db import SessionLocal, utcnow
from app.models import KbChunk, KbDocument, KnowledgeBase
from app.services import audit, guards, policy

CHUNK, OVERLAP, BATCH = 800, 120, 64


class KnowledgeError(ValueError):
    pass


def chunks(text: str) -> list[str]:
    """문단 경계를 우선해 ~800자 청크, 앞 청크 끝 120자를 겹친다(경계에 걸친 문장 보존)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    out, cur = [], ""
    for p in paras:
        while len(p) > CHUNK:  # 문단 하나가 크면 자른다
            head, p = p[:CHUNK], p[CHUNK - OVERLAP:]
            if cur:
                out.append(cur)
                cur = ""
            out.append(head)
        if cur and len(cur) + len(p) + 2 > CHUNK:
            out.append(cur)
            cur = cur[-OVERLAP:] + "\n\n" + p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def visible(kb: KnowledgeBase, p: Principal) -> bool:
    return kb.team is None or kb.team == p.team or "*" in p.permissions()


def stricter(a: str, b: str) -> str:
    order = list(policy.DATA_CLASSES)
    return a if order.index(a) >= order.index(b) else b


async def _embed(p: Principal, data_class: str, model: str, texts: list[str], agent: str) -> tuple[list[list[float]], str]:
    from app.services import llm
    ctx = llm.CallContext(p, data_class, agent)
    vecs: list[list[float]] = []
    used = model
    for i in range(0, len(texts), BATCH):
        res, used = await llm.embed_texts(ctx, model, texts[i:i + BATCH])
        vecs += [d["embedding"] for d in sorted(res["data"], key=lambda d: d["index"])]
    return vecs, used


def kb_out(kb: KnowledgeBase, docs: int | None = None, chunks_n: int | None = None) -> dict:
    return {"id": kb.id, "name": kb.name, "description": kb.description, "team": kb.team, "data_class": kb.data_class,
            "embedding_model": kb.embedding_model, "dims": kb.dims, "owner": kb.owner, "documents": docs,
            "chunks": chunks_n, "created_at": kb.created_at, "updated_at": kb.updated_at}


def doc_out(d: KbDocument) -> dict:
    return {"id": d.id, "kb_id": d.kb_id, "title": d.title, "source": d.source, "sha256": d.sha256, "chunks": d.chunks,
            "created_by": d.created_by, "created_at": d.created_at}


async def add_document(kb_id: str, p: Principal, title: str, text: str, source: str | None = None) -> dict:
    def _kb():
        with SessionLocal() as db:
            kb = db.get(KnowledgeBase, kb_id)
            if kb is None or not visible(kb, p):
                raise KnowledgeError("knowledge base not found")
            return kb.data_class, kb.embedding_model, kb.dims
    dc, model, dims = await run_in_threadpool(_kb)
    raw_sha = hashlib.sha256(text.encode()).hexdigest()

    def _clean():  # 색인 입력 소독 (OWASP Agentic T1 Memory Poisoning) — 지시문 줄 제거·시크릿·PII 마스킹 후 저장·임베딩
        with SessionLocal() as db:
            return guards.sanitize_tool_text(db, text)
    scan = await run_in_threadpool(_clean)
    if scan.blocked:
        raise KnowledgeError(f"문서가 소독 규칙으로 차단되었습니다: {scan.reason}")
    text = scan.text
    parts = chunks(text)
    if not parts:
        raise KnowledgeError("문서 본문이 비어 있습니다.")
    vecs, used = await _embed(p, dc, model, parts, f"knowledge/{kb_id}")
    if dims and len(vecs[0]) != dims:
        raise KnowledgeError(f"임베딩 차원 불일치 ({len(vecs[0])} ≠ {dims}) — 모델을 바꿨다면 reindex 하세요.")

    def _store():
        with SessionLocal() as db:
            kb = db.get(KnowledgeBase, kb_id)
            doc = KbDocument(id=new_id("kbd"), kb_id=kb_id, title=title[:255], source=source,
                             sha256=raw_sha, chunks=len(parts), created_by=p.sub)
            db.add(doc)
            db.flush()
            for i, (t, v) in enumerate(zip(parts, vecs)):
                db.add(KbChunk(id=new_id("kbc"), kb_id=kb_id, doc_id=doc.id, seq=i, text=t, embedding=v))
            kb.dims, kb.updated_at = kb.dims or len(vecs[0]), utcnow()
            db.commit()
            audit.append("knowledge.document.add", actor=p.sub, target=f"{kb_id}/{doc.id}",
                         data={"title": doc.title, "chunks": len(parts), "model": used, "sanitized": len(scan.findings)})
            return doc_out(doc) | {"sanitized": [f.kind for f in scan.findings]}
    return await run_in_threadpool(_store)


async def search(kb_id: str, p: Principal, query: str, top_k: int = 5, data_class: str = "internal") -> dict:
    def _kb():
        with SessionLocal() as db:
            kb = db.get(KnowledgeBase, kb_id)
            if kb is None or not visible(kb, p):
                raise KnowledgeError("knowledge base not found")
            return kb.data_class, kb.embedding_model
    kdc, model = await run_in_threadpool(_kb)
    (qv,), used = await _embed(p, stricter(data_class, kdc), model, [query], f"knowledge/{kb_id}")

    def _rank():
        with SessionLocal() as db:
            rows = db.execute(select(KbChunk.id, KbChunk.doc_id, KbChunk.text, KbChunk.embedding)
                              .where(KbChunk.kb_id == kb_id)).all()
            scored = sorted(((sum(a * b for a, b in zip(qv, r.embedding)), r) for r in rows if r.embedding),
                            key=lambda x: -x[0])[:max(1, min(top_k, 20))]
            docs = {d.id: d for d in db.scalars(select(KbDocument).where(
                KbDocument.id.in_([r.doc_id for _, r in scored])))}
            return [{"chunk_id": r.id, "doc_id": r.doc_id, "title": docs[r.doc_id].title,
                     "source": docs[r.doc_id].source, "text": r.text, "score": round(s, 4)} for s, r in scored]
    items = await run_in_threadpool(_rank)
    return {"kb_id": kb_id, "model": used, "items": items}


async def reindex(kb_id: str, p: Principal) -> dict:
    """임베딩 모델 교체 후 전체 재색인."""
    def _rows():
        with SessionLocal() as db:
            kb = db.get(KnowledgeBase, kb_id)
            if kb is None or not visible(kb, p):
                raise KnowledgeError("knowledge base not found")
            return kb.data_class, kb.embedding_model, list(db.execute(select(KbChunk.id, KbChunk.text)
                                                                      .where(KbChunk.kb_id == kb_id)).all())
    dc, model, rows = await run_in_threadpool(_rows)
    vecs, used = await _embed(p, dc, model, [r.text for r in rows], f"knowledge/{kb_id}") if rows else ([], model)

    def _store():
        with SessionLocal() as db:
            for r, v in zip(rows, vecs):
                db.get(KbChunk, r.id).embedding = v
            kb = db.get(KnowledgeBase, kb_id)
            kb.dims, kb.updated_at = (len(vecs[0]) if vecs else kb.dims), utcnow()
            db.commit()
        audit.append("knowledge.reindex", actor=p.sub, target=kb_id, data={"chunks": len(rows), "model": used})
        return {"kb_id": kb_id, "chunks": len(rows), "model": used}
    return await run_in_threadpool(_store)


def counts(db, kb_ids: list[str]) -> dict[str, tuple[int, int]]:
    docs = dict(db.execute(select(KbDocument.kb_id, func.count()).where(KbDocument.kb_id.in_(kb_ids))
                           .group_by(KbDocument.kb_id)).all())
    ch = dict(db.execute(select(KbChunk.kb_id, func.count()).where(KbChunk.kb_id.in_(kb_ids))
                         .group_by(KbChunk.kb_id)).all())
    return {k: (docs.get(k, 0), ch.get(k, 0)) for k in kb_ids}


def delete_document(db, kb_id: str, doc_id: str) -> None:
    db.execute(delete(KbChunk).where(KbChunk.doc_id == doc_id))
    db.execute(delete(KbDocument).where(KbDocument.id == doc_id, KbDocument.kb_id == kb_id))
