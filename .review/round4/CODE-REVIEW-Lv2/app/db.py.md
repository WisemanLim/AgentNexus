> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../INDEX.md) · 3차 리뷰: [round3 observe.py.md](../../../round3/CODE-REVIEW-Lv2/app/api/observe.py.md)(M10·L13 출처)

### 09. app/db.py (+ `migrations/env.py` · `migrations/versions/0021_span_event_indexes.py` · `app/models.py:516`·`:827` · `app/api/observe.py:259-278`)
**파일 요약 / File Summary**
- 역할 / Role: DB 엔진·세션 팩토리(SQLite·PostgreSQL·MySQL/MariaDB 공용). 13차(D22-①, Epic 61)에서 **PostgreSQL 세션 상한**(문장 120초·잠금 대기 15초·트랜잭션 유휴 0=끔)을 연결 옵션으로 넣었고, 마이그레이션만 상한을 끈다. 개요 KPI 용 인덱스 2개(migration 0021)와 표본 정렬이 함께 들어왔다.
- 진입점 거리 / Distance: 1 — 모든 요청 세션(`get_db`), 백그라운드 작업(`session_scope`·`SessionLocal`), 마이그레이션(`migrations/env.py:6` 가 같은 `engine` 을 쓴다).
- LOC: 87 · `migrations/env.py` 49 · `0021` 23 · `app/api/observe.py` 492 (3차 489 → 492)
- 주요 export: `engine`·`SessionLocal`·`get_db`·`session_scope`·`UTCDateTime`·`utcnow`·`ping`
- 의존(내부): `app.core.config.settings`
- 외부 패키지: SQLAlchemy, psycopg(libpq 연결 인자 `options`·`keepalives`), Alembic
- 부수효과 / Side effects: 프로세스당 연결 풀(10 + overflow 20). 이제 **모든** PostgreSQL 연결이 `statement_timeout`·`lock_timeout` 을 가진다.
- 동시성 / Concurrency: 풀 공유, 다중 파드. 마이그레이션은 `pg_advisory_xact_lock(727274101)` 로 직렬화.
- 테스트 커버리지 흔적: `test/test_data_layer.py:18`(C61-01 SHOW 값), `:30`(C61-02 300ms 엔진에서 `pg_sleep(2)` 취소), `:43`(C61-03 마이그레이션 `SET LOCAL … = 0` 이 advisory lock 앞), `:61`(C61-04 인덱스), `:69`(C61-05 표본 정렬 SQL).
- 4차 변경 요약 / Changes in D22: D22-① — 세션 상한 env 3개(`app/core/config.py:20-24`), 마이그레이션 상한 해제(`migrations/env.py:30-32`), 인덱스 `spans(parent_id, trace_id)`·`run_events(type, ts)`(0021), 게이트웨이 추가 지연 표본 `ORDER BY start_time DESC LIMIT 500`(3차 M10·L13).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/db.py lines=42-58
def _make_engine():
    url = settings.database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    # 장애조치(HA primary 교체)·네트워크 단절 대비 (D18-③): 연결 시도 5초 제한 · TCP keepalive 로 죽은 연결을 빨리 감지 ·
    # 풀 연결은 30분마다 교체 · 체크아웃 때 pre_ping. (psycopg/libpq 연결 인자)
    # 문장·잠금 대기 상한(D22-①)은 세션 옵션으로 — 요청마다 SET 하지 않는다
    args = {"connect_timeout": 5, "keepalives": 1, "keepalives_idle": 10, "keepalives_interval": 5, "keepalives_count": 3,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms} -c lock_timeout={settings.db_lock_timeout_ms} "
                       f"-c idle_in_transaction_session_timeout={settings.db_idle_tx_timeout_ms}"} if url.startswith("postgresql") else {}
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, pool_recycle=1800, pool_timeout=10,
                         connect_args=args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
```
**분석 / Analysis (Level 2)**
- L44-46: SQLite 는 `timeout=30`(busy handler 30초) 그대로 — D22-① 의 "SQLite busy_timeout" 은 확인만(`test/impl/61st/scenario.md` 범위 표).
- L49-52(D22-①): libpq `options` 연결 인자로 `-c statement_timeout=… -c lock_timeout=… -c idle_in_transaction_session_timeout=…` — **서버 세션 파라미터**를 연결 시점에 정한다. 요청마다 `SET` 하지 않아 왕복이 없다. 값은 정수(`app/core/config.py:21-24` pydantic 이 검증)라 문자열 주입 여지가 없다.
- 단위: 세 값 모두 밀리초(PostgreSQL 기본 단위). 0 은 끔.
- MySQL/MariaDB 는 `args = {}`(L52 조건) — D22-① 상한은 PostgreSQL 전용이다. README 표는 "SQLite 는 쓰지 않음" 만 적는다(`README.md:252`).
**짚어볼 점 / Watch-outs**
- [위험:중간] **M2** 상한이 요청 세션뿐 아니라 **장시간 일괄 작업**에도 똑같이 걸린다. ① 보존 정리(`app/services/retention.py:29-35`)는 테이블마다 `DELETE … WHERE ts < cutoff` **한 문장**, 네 테이블을 **한 트랜잭션**으로 지운다. 보존 기간을 줄였거나 스케줄러가 며칠 멈춘 뒤처럼 지울 스팬이 수천만 행이면 첫 DELETE 가 120초를 넘겨 취소 → logs·usage·run_events 삭제까지 롤백 → 리스(`app/services/scheduler.py:129-130`, 1시간) 뒤 같은 일 반복 → 백로그는 늘기만 해 디스크가 찬다(실패는 `scheduler tick failed` 경고 로그뿐, `app/services/scheduler.py:144-145`). ② 감사 아카이브(`app/services/audit.py:126-157`)는 체인 머리 행을 `FOR UPDATE` 로 잡은 채(`:129`) 보관 기간 지난 감사 행 전체를 한 번에 읽고 지운다. 그 동안 다른 워커의 모든 `audit.append`(같은 머리 행 `FOR UPDATE`, `app/services/audit.py:40-43`, `:56-57`)가 **15초 뒤 `lock_timeout` 오류** — 감사를 남기는 요청(로그인·키 회전·정책 변경…)이 본 작업은 커밋된 뒤 500 으로 끝난다. 13차 전에는 기다렸다가 성공하던 경로다. 최소 수정: 보존 삭제를 PK 배치(예: 1만 행)·배치마다 커밋으로, 또는 보존·아카이브 트랜잭션 시작에 `SET LOCAL statement_timeout = 0`(마이그레이션과 같은 방식, `migrations/env.py:31`); 아카이브는 청크 단위로 머리 행 잠금을 짧게; `audit.append` 의 머리 행 잠금은 `SET LOCAL lock_timeout = 0` 검토.
- [위험:낮음] **L15** `options` 시작 파라미터는 PgBouncer 가 기본 설정에서 거부한다("unsupported startup parameter: options") — 앞단에 PgBouncer(또는 CNPG Pooler — README 장애 대응표가 CNPG 를 다룬다 `README.md:535`)를 두면 **연결 자체가 실패**하거나, `ignore_startup_parameters=options` 설정에서는 상한이 **조용히 적용되지 않는다**. 트랜잭션 풀링이면 세션 파라미터 자체가 의미가 없다. 최소 수정: 문서에 풀러 사용 시 주의 명시, 또는 `ALTER ROLE app SET statement_timeout = …`(DB 쪽 기본값) 안내.

```python path=migrations/env.py lines=25-34
def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite", render_item=render_item)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":  # 다중 파드(Helm)가 동시에 기동해도 마이그레이션은 하나씩
            # 앱 세션 상한(D22-①)은 마이그레이션에 쓰지 않는다 — 큰 테이블 인덱스·다른 파드의 잠금을 기다릴 수 있다
            connection.execute(text("SET LOCAL statement_timeout = 0"))
            connection.execute(text("SET LOCAL lock_timeout = 0"))
            connection.execute(text("SELECT pg_advisory_xact_lock(727274101)"))
        context.run_migrations()
```
**분석 / Analysis (Level 2)**
- L30-33(D22-①): 마이그레이션 트랜잭션 안에서 `SET LOCAL statement_timeout = 0`·`lock_timeout = 0` **다음에** advisory lock. 순서가 중요하다 — `lock_timeout` 은 advisory lock 대기에도 걸리므로, 잠금을 먼저 잡으면 다른 파드의 긴 마이그레이션을 15초만 기다리고 실패한다(C61-03 이 순서를 확인). `SET LOCAL` 은 트랜잭션이 끝나면 원래 값으로 돌아간다.
- `idle_in_transaction_session_timeout` 은 다시 끄지 않는다 — 기본 0 이라 문제없지만, 운영자가 켜면(README 권고 "켤 때는 최장 스트림보다 길게") 파이썬 처리 시간이 긴 데이터 마이그레이션이 끊길 수 있다. 같은 줄에 `SET LOCAL idle_in_transaction_session_timeout = 0` 을 더하는 것이 일관적이다(등급 없음).

```python path=app/seed.py lines=370-378
def run() -> list[str]:
    keys.ensure_keys()
    from app.core import tracing
    tracing.ingest_token()  # 별도 컨테이너(mcp-servers·a2a-demo)가 공유 볼륨으로 읽는 OTLP 수신 토큰
    steps: list[str] = []
    with session_scope() as db:
        if db.get_bind().dialect.name == "postgresql":  # 동시 기동한 파드의 시드 직렬화(멱등 확인이 경합하지 않게)
            db.execute(sql_text("SELECT pg_advisory_xact_lock(727274102)"))
        steps += seed_identity(db)
```
**분석 / Analysis (Level 2)**
- 시드도 advisory lock(727274102)으로 다중 파드를 직렬화한다. 마이그레이션과 달리 **상한을 끄지 않는다**.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L16** 처음 설치처럼 여러 파드가 동시에 뜨면, 두 번째 파드의 시드는 첫 파드의 시드 트랜잭션(데모 시드·지식 베이스 포함)을 기다리다 **15초 뒤 `lock_timeout`** 으로 실패 → initContainer 재시작(백오프) → 재시도에서 성공. 스스로 회복하지만 설치 시간이 늘고 오류 로그가 남는다. 최소 수정: 시드 잠금 앞에 `SET LOCAL lock_timeout = 0`(`migrations/env.py:31-32` 와 같게).

```python path=migrations/versions/0021_span_event_indexes.py lines=1-23
"""span · run event indexes — overview KPI aggregation without full scans (D22-①)

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-08 18:00:00
"""
from alembic import op


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_spans_parent_trace', 'spans', ['parent_id', 'trace_id'], unique=False)
    op.create_index('ix_run_events_type_ts', 'run_events', ['type', 'ts'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_run_events_type_ts', table_name='run_events')
    op.drop_index('ix_spans_parent_trace', table_name='spans')
```
```python path=app/models.py lines=513-516
class RunEvent(Base):
    """SSE 재생용 실행 이벤트 로그 (워커 간 공유)."""
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_type_ts", "type", "ts"),)  # 첫 토큰 KPI — 기간 안 실행 시작 이벤트 (D22-①)
```
```python path=app/models.py lines=824-827
class Span(Base):
    """내장 트레이스 저장소 (FR-O01 조회용, OTLP 내보내기와 병행)."""
    __tablename__ = "spans"
    __table_args__ = (Index("ix_spans_parent_trace", "parent_id", "trace_id"),)  # 자식 스팬 집계 (D22-①)
```
**분석 / Analysis (Level 2)**
- 0021 과 모델 `__table_args__` 가 같은 이름·같은 열 순서 — 다음 autogenerate 가 "인덱스 차이" 를 만들지 않는다(흔한 실수: 마이그레이션에만 만들고 모델에 빠뜨리면 autogenerate 가 drop 을 제안).
- `run_events(type, ts)`: 첫 토큰 KPI 의 바깥 질의 `type = 'run.started' AND ts >= since`(`app/api/observe.py:278`)에 정확히 맞는 순서(등호 열 먼저, 범위 열 뒤).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L14** `op.create_index` 는 일반 `CREATE INDEX` — 만드는 동안 그 테이블 **쓰기를 막는다**(`test/impl/61st/result.md` 결과 요약도 "업그레이드 창에서 실행" 이라고 적었다). 이번에는 D22-① 잠금 상한과 맞물린다: Helm 롤링 업그레이드에서 새 파드의 initContainer 가 큰 `spans`·`run_events` 에 인덱스를 만드는 동안, 아직 도는 옛 파드의 스팬·실행 이벤트 INSERT 가 **15초 뒤 `lock_timeout` 오류** — 실행 이벤트 기록이 실패하면 진행 중 오케스트레이션이 오류로 끝난다. 최소 수정: `postgresql_concurrently=True` + `with op.get_context().autocommit_block():`(advisory lock 이 트랜잭션 잠금이라 해제됨 — 맞바꿈 기록 필요), 또는 릴리스 노트에 "0021 은 유지보수 창" 명시.

```python path=app/api/observe.py lines=259-278
def _overview_extra(db: Session, since) -> dict:
    """운영 개요 KPI (SCR-B02 ②⑤) — 게이트웨이 추가 지연 p95(도구 호출 스팬 − 업스트림 자식 스팬)·첫 토큰 p95·팀 예산·등록 대기."""
    from app.models import CatalogVersion, RateLimit, RunEvent
    # 게이트웨이 추가 지연 = 도구 호출 스팬 − 가장 긴 자식 스팬 — 자식 최대값을 한 번의 집계로 (스팬마다 조회 없음, D20-④)
    kid = aliased(Span)
    kids = (select(kid.parent_id.label("pid"), kid.trace_id.label("tid"), func.max(kid.duration_ms).label("mx"))
            .where(kid.start_time >= since)  # 자식은 부모 시작 뒤에 시작 — 보존 기간 전체를 집계하지 않게 (59th)
            .group_by(kid.parent_id, kid.trace_id).subquery())
    over = [max(0.0, float(d) - float(mx)) for d, mx in db.execute(
        select(Span.duration_ms, kids.c.mx).join(kids, and_(kids.c.pid == Span.id, kids.c.tid == Span.trace_id))
        .where(Span.start_time >= since, Span.name.like("%tools/call%"))
        .order_by(Span.start_time.desc()).limit(500))]  # 최근 500 — 순서 없는 표본은 p95 가 흔들린다 (D22-①)
    # 첫 토큰 = 실행 시작 → 다음 시작 전 첫 delta — 이벤트를 모두 읽지 않고 시작마다 하위 질의 (D20-④)
    st, d, nx = aliased(RunEvent), aliased(RunEvent), aliased(RunEvent)
    next_start = (select(func.min(nx.id)).where(nx.run_id == st.run_id, nx.type == "run.started", nx.id > st.id)
                  .correlate(st).scalar_subquery())  # 두 단계 위(바깥 시작 이벤트)와 상관 — 자동 상관은 바로 위만 본다
    first_delta = (select(func.min(d.ts)).where(d.run_id == st.run_id, d.type == "delta", d.id > st.id, d.ts >= since,
                                                d.id < func.coalesce(next_start, 2 ** 62)).correlate(st).scalar_subquery())
    first = [(fd - s0).total_seconds() * 1000 for s0, fd in db.execute(
        select(st.ts, type_coerce(first_delta, UTCDateTime())).where(st.ts >= since, st.type == "run.started")) if fd is not None]
```
**분석 / Analysis (Level 2)**
- L263-266: 자식 스팬 최대 지속시간 = `GROUP BY parent_id, trace_id` 하위 질의(59th 가 기간 조건 추가).
- L267-270(D22-①): 바깥 질의에 `ORDER BY start_time DESC LIMIT 500` — 최근 500 표본. 전에는 순서 없는 LIMIT 이라 실행 계획에 따라 p95 가 흔들렸다(3차 L13).
- L272-278: 첫 토큰 — 기간 안 `run.started` 마다 상관 하위 질의 두 개. 바깥 질의가 새 `ix_run_events_type_ts` 를 탄다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L13** `ix_spans_parent_trace` 는 L264-266 의 **집계 하위 질의**를 빠르게 하지 못할 가능성이 높다. PostgreSQL 은 `GROUP BY` 하위 질의 안으로 바깥 조인 조건(`pid = Span.id`)을 밀어 넣지 못하므로, 기간 안 **모든** 스팬을 시간 인덱스로 읽어 해시 집계한 뒤 조인한다 — 새 인덱스는 쓰이지 않고 가장 쓰기가 많은 `spans` 의 INSERT 비용만 늘린다(실행 계획을 이 리뷰에서 돌려 보지는 못했다 — `EXPLAIN` 확인 필요). 최소 수정: 바깥 500행마다 상관 스칼라 하위 질의 `(SELECT max(k.duration_ms) FROM spans k WHERE k.parent_id = s.id AND k.trace_id = s.trace_id)` 로 바꾸면 500번의 인덱스 탐색이 되어 인덱스가 의도대로 쓰인다.

```python path=app/services/retention.py lines=25-39
def run(db: Session, actor: str, overrides: dict | None = None, now=None) -> dict:
    now = now or utcnow()
    pol = {**policy(db), **(overrides or {})}
    deleted = {}
    for key, model, col in (("spans_days", Span, Span.start_time), ("logs_days", LogEntry, LogEntry.ts),
                            ("usage_days", UsageRecord, UsageRecord.ts), ("run_events_days", RunEvent, RunEvent.ts)):
        cutoff = now - timedelta(days=pol[key])
        deleted[model.__tablename__] = db.execute(delete(model).where(col < cutoff)).rowcount
    for model in (McpRelay, McpTask):
        deleted[model.__tablename__] = db.execute(delete(model).where(model.created_at < now - timedelta(days=STATE_DAYS))).rowcount
    db.commit()
    archive = audit.archive_before(now - timedelta(days=pol["audit_days"]), actor)
    audit.append("retention.run", actor=actor, data={"policy": pol, "deleted": deleted,
                                                     "archive": archive["archive_id"] if archive else None})
    return {"policy": pol, "deleted": deleted, "archive": archive}
```
**분석 / Analysis (Level 2)**
- M2 ① 의 근거. L29-32: 테이블별 단일 DELETE, L35: 한 번에 커밋. 실패하면 네 테이블 모두 롤백.

```python path=app/services/audit.py lines=126-157
def archive_before(before: datetime, actor: str) -> dict | None:
    """before 이전 이벤트를 JSONL 로 내보내고 삭제한 뒤, 체인 앵커를 옮기고 'audit.archive' 이벤트로 증명을 남긴다."""
    with _lock, session_scope() as db:
        head = _head(db, lock=True)
        rows = list(db.scalars(select(AuditEvent).where(AuditEvent.seq > head.anchor_seq, AuditEvent.ts < before)
                               .order_by(AuditEvent.seq)))
        # 연속 구간만 (앵커 직후부터) — 중간에 보관기간 이내 이벤트가 끼면 거기서 멈춘다
        chain, prev = [], head.anchor_hash
        for ev in rows:
            if ev.prev_hash != prev:
                break
            chain.append(ev)
            prev = ev.hash
        if not chain:
            return None
        aid = new_id("arc")
        path = _archive_dir() / f"{aid}.jsonl"
        lines = [canonical_json({**payload(ev), "prev_hash": ev.prev_hash, "hash": ev.hash}) for ev in chain]
        content = "\n".join(lines) + "\n"
        path.write_text(content, encoding="utf-8")
        arc = AuditArchive(id=aid, from_seq=chain[0].seq, to_seq=chain[-1].seq, first_prev_hash=chain[0].prev_hash,
                           last_hash=chain[-1].hash, event_count=len(chain), file_path=str(path),
                           file_sha256=sha256_hex(content))
        db.add(arc)
        for ev in chain:
            db.delete(ev)
        head.anchor_seq, head.anchor_hash = chain[-1].seq, chain[-1].hash
        db.flush()
        out = {"archive_id": aid, "from_seq": arc.from_seq, "to_seq": arc.to_seq, "count": arc.event_count,
               "file_sha256": arc.file_sha256, "last_hash": arc.last_hash}
    append("audit.archive", actor=actor, target=aid, decision="archived", data=out)
    return out
```
**분석 / Analysis (Level 2)**
- M2 ② 의 근거. L128-129: 프로세스 잠금 + 머리 행 `FOR UPDATE`, L130-131: 보관 기간 지난 행 전체를 한 문장으로 읽기, L150-151: 행마다 삭제 — 이 모든 것이 머리 행을 잡은 한 트랜잭션이다(L128 `with` 블록이 L155 에서 끝나며 커밋).

## 파일 평가 / File Assessment
- 강점: (1) 세션 상한을 연결 옵션으로 — 요청마다 왕복 없음(`app/db.py:50-52`). (2) 마이그레이션은 `SET LOCAL` 로 상한 해제 후 잠금 — 순서까지 시험으로 고정(`migrations/env.py:30-33`, C61-03). (3) 모델·마이그레이션 인덱스 정의 일치(`app/models.py:516`, `:827`).
- 위험: (1) [위험:중간] M2 — 같은 상한이 보존 삭제·감사 아카이브 같은 장시간 일괄 작업을 영구 실패로, 감사 머리 행 대기를 15초 실패로 바꾼다. (2) [위험:낮음] L14·L16 — 0021 인덱스 생성·시드 잠금 대기가 잠금 상한과 맞물림. (3) [위험:낮음] L13·L15 — 새 인덱스 사용 여부 불확실, 풀러 호환.
- 개선 권고(영향순): (1) 일괄 작업(보존·아카이브)은 배치 + 커밋 또는 `SET LOCAL statement_timeout = 0`. (2) 시드·감사 머리 행 잠금 대기는 `lock_timeout` 예외. (3) `EXPLAIN` 으로 자식 스팬 질의 확인 후 상관 하위 질의로. (4) 풀러 주의 문서화.
- 파일 위험 등급 / Risk grade: **중간** — [위험:중간] 1(M2).
- 재읽기 권고 레벨: 2 — 87줄 파일이지만 "모든 연결" 에 걸리는 설정이라, 바뀐 3줄이 닿는 장시간 작업 목록(보존·아카이브·시드·마이그레이션)을 함께 읽어야 한다.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| M10 자식 스팬 전체 집계(59th 기간 조건) · 인덱스 권고 | 인덱스 추가 · 질의 모양상 미사용 가능(L13) | `app/api/observe.py:263-266`, `migrations/versions/0021_span_event_indexes.py:17` |
| L13 순서 없는 `limit(500)`·`run_events` 인덱스 | 해결 | `app/api/observe.py:270`, `app/models.py:516` |
| L31 0020 인덱스 잠금 | 같은 패턴 반복 + 잠금 상한과 맞물림(L14) | `migrations/versions/0021_span_event_indexes.py:17-18` |
| (D21-⑤ 기록) DB 세션 상한 미적용 | 적용 · 일괄 작업 부작용(M2) | `app/db.py:49-52` |
