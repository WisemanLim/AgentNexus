> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [versions_Abstract.md](versions_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(신규 파일)

### 31. migrations/versions/0019_refresh_family.py
**파일 요약 / File Summary**
- 역할 / Role: Alembic 리비전 0019 — `oauth_refresh_tokens` 에 회전 계열(family) 컬럼 `family_id` 와 인덱스를 추가해 refresh token 재사용 탐지(Epic 43 · D17-④)의 저장 기반을 만든다.
- 진입점 거리 / Distance: 2 (`scripts/entrypoint.sh:8` `alembic upgrade head` → `migrations/env.py:25-32` `_run` → 이 리비전). 시험은 `test/conftest.py:1-36` 이 alembic 으로 스키마 생성.
- LOC: 26 (현재 `wc -l`) · 1차 리뷰 시점 없음(신규)
- 주요 export: `revision='0019'`, `down_revision='0018'`, `upgrade()`, `downgrade()`
- 의존(내부): 없음(앱 코드 import 없음 — `migrations/env.py:11-15` 의 `render_item` 이 앱 타입 의존을 끊는 규칙과 일치)
- 외부 패키지: alembic(`op.batch_alter_table`), sqlalchemy(`sa.Column`, `sa.String`)
- 부수효과 / Side effects: DDL — `ALTER TABLE oauth_refresh_tokens ADD COLUMN family_id VARCHAR(40) NULL` + `CREATE INDEX ix_oauth_refresh_tokens_family_id`. 기존 행 값은 채우지 않는다(backfill 없음).
- 동시성 / Concurrency: PostgreSQL 은 `migrations/env.py:29-30` 의 `pg_advisory_xact_lock(727274101)` 로 다중 파드 동시 기동 시 마이그레이션이 하나씩 실행된다. 같은 트랜잭션 안의 `CREATE INDEX`(비-CONCURRENTLY)는 실행 동안 테이블 쓰기를 막는다.
- 테스트 커버리지 흔적: `test/test_foundation.py:20-31`(SQLite 에서 `upgrade head → downgrade base → upgrade head` 왕복), `test/test_admin.py:69`(head == "0019"), `test/test_policy_hardening.py:93-115`(컬럼을 쓰는 재사용 탐지). PostgreSQL 왕복은 자동 시험 없음 — `test/impl/43rd/result.md:32` prod 리허설(PostgreSQL·2 workers)에서 upgrade 만 확인.
- 2차 변경 요약 / Changes since 1st review: 신규. `test/impl/43rd/result.md:7` 에 따르면 첫 작성 때 테이블 이름 오기(`refresh_tokens`)로 pytest r1 이 실패했고 `oauth_refresh_tokens` 로 고쳐 r2 16/16. `test/impl/43rd/result.md:37` — 로컬 SQLite 가 0019 로 올라가지 않은 상태에서 `/idp/token` 이 500(`no column named family_id`) — 핫 리로드는 마이그레이션을 하지 않는다(운영 절차 주의).

## 라인 리뷰 / Line Walk (Level 2)

```python path=migrations/versions/0019_refresh_family.py lines=1-14
"""refresh token family — rotation lineage for reuse detection (D17-④)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-07 07:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None
```
**분석 / Analysis (Level 2)**
- L1-6: 리비전 docstring — 목적(재사용 탐지용 회전 계열)과 체인(0018 → 0019). Alembic 은 docstring 이 아니라 L11-12 변수로 체인을 만든다. 흔한 실수: 파일을 복사해 새 리비전을 만들 때 `down_revision` 을 갱신하지 않아 head 가 둘로 갈라지는 것 — 여기서는 `migrations/versions/0018_slo.py:11` 이 `revision = '0018'` 이라 선형 체인이 맞다(확인).
- L7-8: 앱 모델을 import 하지 않고 `sa` 기본 타입만 쓴다 — 마이그레이션이 미래의 모델 변경에 끌려가지 않게 하는 정석.
- L11-14: 분기·의존 없음.

```python path=migrations/versions/0019_refresh_family.py lines=15-26


def upgrade() -> None:
    with op.batch_alter_table('oauth_refresh_tokens', schema=None) as b:
        b.add_column(sa.Column('family_id', sa.String(length=40), nullable=True))
        b.create_index('ix_oauth_refresh_tokens_family_id', ['family_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('oauth_refresh_tokens', schema=None) as b:
        b.drop_index('ix_oauth_refresh_tokens_family_id')
        b.drop_column('family_id')
```
**분석 / Analysis (Level 2)**
- L18 `batch_alter_table`: SQLite 는 `ALTER TABLE` 기능이 제한적이라 Alembic 이 "테이블 복사 후 교체(move and copy)" 를 할 수 있게 하는 컨텍스트. `migrations/env.py:27` 이 SQLite 에서만 `render_as_batch=True` 를 켜고, PostgreSQL 에서는 같은 코드가 일반 `ALTER TABLE` 로 실행된다(이식성 확보). `recreate` 기본값 `"auto"` 이므로 `add_column`·`create_index` 만 있는 upgrade 는 SQLite 에서도 테이블 재생성 없이 실행되고, `drop_column` 이 있는 downgrade 는 SQLite 3.35 미만에서 재생성 경로를 탄다.
- L19: `String(40)`, `nullable=True` — 모델 `app/models.py:201`(`family_id: Mapped[str | None] = mapped_column(String(40), index=True)`)과 길이·NULL 허용이 일치한다. `RefreshToken.id` 도 `String(40)`(`app/models.py:194`)이라 "계열 id = 첫 토큰 id" 규칙(`app/services/identity.py:69` `family_id=family or rt_id`)을 담을 수 있다. NULL 허용 + 기본값 없음이라 PostgreSQL 에서 기존 행을 다시 쓰지 않는 메타데이터 변경으로 끝난다(대형 테이블에도 빠름).
- L20: 인덱스 이름 `ix_oauth_refresh_tokens_family_id` 는 SQLAlchemy 가 `index=True` 에 붙이는 기본 명명 규칙(`ix_<table>_<column>`)과 같다 — 이후 autogenerate 가 "모델에는 있는데 DB 에 없음" 으로 중복 생성하지 않는다. 이 인덱스는 재사용 탐지의 계열 일괄 폐기 UPDATE(`app/services/identity.py:85-86` `family_id == fam OR id == fam`)에 쓰인다. `id` 는 PK 인덱스가 있으므로 OR 양쪽 모두 인덱스 경로가 있다(PostgreSQL BitmapOr, SQLite OR 최적화 — 실행 계획은 미확인).
- L23-26 downgrade: 인덱스 먼저, 컬럼 나중 — 역순 정리가 맞다(컬럼을 먼저 지우면 SQLite 재생성 단계에서 존재하지 않는 컬럼의 인덱스 재생성으로 실패할 수 있다). 계열 정보만 사라지고 토큰 행 자체는 보존된다. 이전 코드(0018 시점)는 `family_id` 를 읽지 않으므로 다운그레이드 후 롤백 배포와 호환된다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L19 기존 행 backfill 이 없다. 0019 이전에 회전된 사슬(A→B, 둘 다 `family_id` NULL)에서는 B 가 A 를 가리키는 정보가 없다. 업그레이드 뒤 A 가 재제시되면 `fam = row.family_id or row.id = A.id`(`app/services/identity.py:84`) 로 `family_id == A.id OR id == A.id` 만 폐기하므로 살아 있는 B(그리고 B 의 후손 — 후손은 `family=B.id` 로 묶임)는 폐기되지 않는다. 시나리오: 업그레이드 직전 탈취된 오래된 refresh token 을 공격자가 재제시 → 재사용은 탐지·감사되지만 피해자의 현재 세션 계열은 살아 있다. 창은 업그레이드 시점 살아 있던 세션의 수명(기본 14일 `app/services/identity.py:36`, 회전마다 연장)까지. 최소 수정: 과도기에는 계열을 모르는 행(`family_id IS NULL`)의 재사용 탐지 시 같은 `user_id`+`client_id` 의 살아 있는 토큰을 함께 폐기하거나, 업그레이드 때 `family_id = id` 로 채워 이후 회전부터는 계열이 이어지게(과거 사슬 연결은 복원 불가) 한다.
- [위험:낮음] L20 PostgreSQL 에서 `CREATE INDEX`(비-CONCURRENTLY)는 빌드 동안 테이블에 SHARE 잠금을 잡아 INSERT/UPDATE(로그인·refresh 회전)를 막는다. `oauth_refresh_tokens` 는 회전마다 행이 늘고 정리 작업이 없다(`app/` 전체에서 `RefreshToken` 삭제 경로 0건 — `grep -rn RefreshToken app/` 결과는 조회·폐기만) → 운영 기간이 길수록 인덱스 빌드가 길어진다. `migrations/env.py:28-31` 이 트랜잭션 + advisory lock 안에서 실행하므로 `CONCURRENTLY` 는 그대로는 못 쓴다. 최소 수정: 행 수가 큰 배포는 점검 창에서 실행하거나, 별도 리비전에서 `with op.get_context().autocommit_block(): op.create_index(..., postgresql_concurrently=True)` 패턴을 검토.
- L25-26 SQLite 의 batch 재생성은 원 테이블의 UNIQUE(`token_hash`)·인덱스(`user_id`)를 반영(reflect)해 다시 만든다 — `test/test_foundation.py:20-31` 왕복 시험이 SQLite 에서 이를 보증. PostgreSQL 다운그레이드는 시험 공백.

## 파일 평가 / File Assessment
- 강점: (1) 모델 정의(`app/models.py:201`)와 타입·길이·인덱스 이름이 정확히 일치해 autogenerate 차이가 생기지 않는다. (2) `batch_alter_table` + `render_as_batch`(SQLite 한정)로 SQLite/PostgreSQL 양쪽 이식성. (3) NULL 허용 추가라 기존 데이터 재작성 없이 빠르고, 다운그레이드가 역순·무손실(계열 정보 외)이다.
- 위험: (1) [위험:낮음] backfill 없음 → 0019 이전 회전 사슬은 재사용 탐지 시 계열 폐기 누락(`app/services/identity.py:84-86` 와 결합). (2) [위험:낮음] PostgreSQL 비동시 인덱스 생성의 쓰기 차단 — 정리 작업 없는 테이블이라 운영 기간에 비례(`migrations/env.py:28-31`). (3) PostgreSQL 왕복 시험 공백.
- 개선 권고(영향순): (1) 업그레이드 과도기 처리(위 Watch-out 1의 최소 수정 중 하나) 또는 운영 문서에 "0019 적용 전 발급 세션은 재사용 탐지 범위 밖" 명시. (2) `oauth_refresh_tokens`·`oauth_codes`·`oauth_device_codes` 보존 정리 작업 추가(재사용 탐지에 필요한 기간 — 예: 만료 후 N일 — 만 남기고 삭제). (3) PostgreSQL 대상 upgrade/downgrade 왕복을 CI prod 리허설에 포함.
- 파일 위험 등급 / Risk grade: 낮음 — 스키마 변경 자체는 정확하고 남은 것은 과도기·운영 비용 성격의 [위험:낮음] 뿐.
- 재읽기 권고 레벨: 1 — 짧고 정형적인 리비전. 재사용 탐지 의미는 `app/services/identity.py` 리뷰에서 읽을 것.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 `app/services/identity.py.md` L77-84 Watch-out — "재사용 탐지가 없다 — 폐기된 refresh token 재제시 시 계열 전체 폐기 필요(OAuth 2.0 Security BCP)" (저장 기반 부재) | 해결(저장 기반) — 계열 컬럼·인덱스 추가. 탐지 로직의 잔여 한계는 identity 보고서 참조 | `migrations/versions/0019_refresh_family.py:18-20`, `app/models.py:201`, `app/services/identity.py:82-90` |
| 1차 INDEX §4.5 "테스트 공백 — refresh token 재사용 탐지" | 해결 — 재사용 시 계열 폐기·감사 시험 추가(SQLite) | `test/test_policy_hardening.py:93-115`, `test/test_foundation.py:20-31` |
