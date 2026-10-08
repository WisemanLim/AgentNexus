# wise-agentnexus — python-fastapi (wise-vibe wds-scaffold 생성 / generated)
# 모드: local(SQLite) | prod(postgres) — 실행은 docker compose 전용. make help
PROJECT     := wise-agentnexus
APP_SVC     := api
TEST        ?= pytest -q
MIGRATE     ?= alembic upgrade head
SEED        ?= python -m app.seed
SQLITE_PATH := /app/data/app.db
DB_ENGINE   := postgres
DB_PING     := pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"
DB_SHELL    := psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"

## ===== wds 공통 타겟 / common targets (docker compose 전용 · 모드 = local | prod) =====
# local : docker-compose.yml + docker-compose.local.yml + .env.local  (SQLite, 소스 바인드/핫리로드, dev 스테이지)
# prod  : docker-compose.yml + docker-compose.prod.yml  + .env.prod   (DB_ENGINE 의 RDBMS 컨테이너, runtime 스테이지)
# compose/*.yml (scaffold --extras) 은 두 모드에 자동 포함된다.
ENV   ?= local
MODES := local prod
SVC   ?=
EXTRA_FILES := $(foreach f,$(sort $(wildcard compose/*.yml)),-f $(f))
DC_local := docker compose -p $(PROJECT)-local -f docker-compose.yml -f docker-compose.local.yml $(EXTRA_FILES) --env-file .env.local
DC_prod  := docker compose -p $(PROJECT)-prod  -f docker-compose.yml -f docker-compose.prod.yml  $(EXTRA_FILES) --env-file .env.prod
DC = $(DC_$(ENV))

.PHONY: help preflight lint audit test test-pg web-build web-test web-lock web-e2e e2e-lock licenses helm-check helm-package shell deploy db-ping db-shell db-migrate db-seed db-reset db-fresh \
        $(foreach m,$(MODES),$(m)-all $(m)-build $(m)-logs $(m)-stop $(m)-restart $(m)-ps)

help:  ## 타겟 목록 / list targets
	@grep -hE '^[a-z][a-zA-Z%_-]*:.*## ' $(MAKEFILE_LIST) | sed -E 's/:[^#]*##/\t/' | awk -F'\t' '{printf "  %-14s %s\n", $$1, $$2}'
	@echo "  <mode>-all     [local|prod] 전체 스택 기동 (이미지 없으면 빌드)"
	@echo "  <mode>-build   [local|prod] 이미지 재빌드 후 기동"
	@echo "  <mode>-logs    [local|prod] 로그 추적 (SVC=<service>)"
	@echo "  <mode>-stop    [local|prod] 컨테이너 정리 (볼륨 유지)"
	@echo "  <mode>-restart [local|prod] 재기동 (SVC=<service>)"
	@echo "  <mode>-ps      [local|prod] 컨테이너 상태"
	@echo "  db-* 는 ENV=local|prod (기본 local). 예) make db-migrate ENV=prod"

preflight:  ## Docker·Compose v2·환경 파일 점검 / check docker, compose v2, env files
	@command -v docker >/dev/null 2>&1 || { echo "FAIL: Docker 미설치 / not installed"; exit 1; }
	@docker compose version >/dev/null 2>&1 || { echo "FAIL: docker compose v2 필요 / required"; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "FAIL: Docker 데몬 미기동 / daemon not running"; exit 1; }
	@for f in .env.local .env.prod; do [ -f $$f ] || { echo "FAIL: $$f 없음 — cp .env.example $$f"; exit 1; }; done
	@grep -q 'CHANGE_ME' .env.prod && echo "WARN: .env.prod 에 CHANGE_ME placeholder — 실제 배포 전 Secret Manager 로 주입" || true
	@$(DC_local) config -q && $(DC_prod) config -q && echo "=== Preflight OK (DB_ENGINE local=sqlite, prod=$(DB_ENGINE)) ==="

## --- 모드별 수명주기 / lifecycle per mode ---
$(MODES:%=%-all): %-all:
	$(DC_$*) up -d
$(MODES:%=%-build): %-build:
	$(DC_$*) up -d --build
$(MODES:%=%-logs): %-logs:
	$(DC_$*) logs -f $(SVC)
$(MODES:%=%-stop): %-stop:
	$(DC_$*) down --remove-orphans
$(MODES:%=%-restart): %-restart:
	$(DC_$*) restart $(SVC)
$(MODES:%=%-ps): %-ps:
	$(DC_$*) ps

## --- 공통 / common ---
lint:   ## ruff (F·E9 — 정의되지 않은 이름·문법 오류) + 주요 모듈 import
	$(DC_local) run --rm --no-deps --entrypoint sh $(APP_SVC) -c 'ruff check --select F,E9 --output-format concise app migrations test && python -c "import app.main, app.grpc_server, app.builtin.a2a_agent, app.builtin.mcp_servers"'
audit:  ## 의존성 취약점 감사 — pip-audit(런타임 잠금) · pnpm audit(콘솔 prod). 네트워크 필요(CI 에서 실행)
	$(DC_local) run --rm --no-deps --entrypoint sh $(APP_SVC) -c 'uv export --locked --no-dev --no-hashes --format requirements-txt -o /tmp/req.txt && uvx --from pip-audit pip-audit -r /tmp/req.txt'
	$(DC_local) --profile web run --rm --no-deps --entrypoint sh web -c 'pnpm audit --prod --audit-level high'
test:   ## local dev 컨테이너에서 테스트 실행
	$(DC_local) run --rm --no-deps --entrypoint sh $(APP_SVC) -c '$(TEST)'
test-pg:  ## PostgreSQL 회귀 테스트 (prod db 컨테이너에 app_test 재생성 → dev 이미지로 pytest)
	$(DC_prod) up -d --wait db
	$(DC_prod) exec -T db sh -c 'psql -q -U "$$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS app_test" -c "CREATE DATABASE app_test"'
	docker run --rm --network $(PROJECT)-prod_backend -v "$(CURDIR)":/app --entrypoint sh \
	  -e TEST_DATABASE_URL="postgresql+psycopg://$$(grep '^DB_USER=' .env.prod | cut -d= -f2):$$(grep '^DB_PASSWORD=' .env.prod | cut -d= -f2)@db:5432/app_test" \
	  $(PROJECT)-api:local -c '$(TEST)'
web-build:  ## 콘솔 SPA 빌드 → web/dist (local api 가 서빙, 의존성은 web-dev 이미지 안)
	$(DC_local) --profile web build web
	$(DC_local) --profile web run --rm --no-deps web pnpm build
web-test:   ## 콘솔 단위 시험 (Vitest · jsdom)
	$(DC_local) --profile web build web
	$(DC_local) --profile web run --rm --no-deps web pnpm test
web-lock:   ## web/pnpm-lock.yaml 갱신 (package.json 변경 후 — 이미지 빌드 중 설치한 잠금 파일을 추출)
	$(DC_local) --profile web build --build-arg PNPM_FROZEN=0 web
	$(DC_local) --profile web run --rm --no-deps -T web cat /tmp/pnpm-lock.resolved.yaml > web/pnpm-lock.yaml
e2e-lock:   ## web/e2e/package-lock.json 갱신 (package.json 변경 후 — e2e 이미지 빌드 중 설치한 잠금 파일을 추출)
	$(DC_local) --profile e2e build --build-arg NPM_LOCKED=0 e2e
	$(DC_local) --profile e2e run --rm --no-deps -T e2e cat /tmp/package-lock.resolved.json > web/e2e/package-lock.json
licenses:   ## THIRD_PARTY_LICENSES.md 재생성 — prod 이미지 파이썬 런타임 + 콘솔 prod JS 메타데이터(오프라인, make prod-build 선행)
	$(DC_local) --profile web run --rm --no-deps -T --entrypoint sh web -c 'pnpm licenses list --prod --json' > data/licenses-js.json
	$(DC_prod) run --rm --no-deps -T -v "$(CURDIR)":/src -w /src --entrypoint python api scripts/third_party.py data/licenses-js.json > THIRD_PARTY_LICENSES.md
	rm -f data/licenses-js.json
web-e2e:    ## 콘솔 브라우저 스모크 (Playwright 컨테이너 · local 스택 + make web-build 선행)
	$(DC_local) --profile e2e build e2e
	$(DC_local) --profile e2e run --rm --no-deps e2e
helm-check: ## Helm 차트 lint(--strict) + 시나리오별 template 오프라인 정합성 검사 (호스트 helm 필요)
	@command -v helm >/dev/null || { echo "helm 미설치 — https://helm.sh"; exit 1; }
	helm lint deploy/helm/agentnexus --strict
	@for v in "" ci/airgap-values.yaml ci/ha-values.yaml ci/gateway-only-values.yaml; do \
	  printf '%-30s ' "$${v:-default}"; \
	  helm template anx deploy/helm/agentnexus $${v:+-f deploy/helm/agentnexus/$$v} | python3 deploy/helm/check-rendered.py || exit 1; \
	done
helm-package: ## Helm 차트 패키지 → dist/agentnexus-<ver>.tgz (에어갭 번들: nexus bundle export --include)
	helm package deploy/helm/agentnexus -d dist
shell:  ## 앱 컨테이너 셸 (ENV=local|prod)
	$(DC) exec $(APP_SVC) sh
deploy: ## prod 모드 롤아웃 (대상 호스트에서 실행 — 이미지 재빌드 + 재기동)
	@grep -q 'CHANGE_ME' .env.prod && { echo "FAIL: .env.prod 에 CHANGE_ME — 시크릿 주입 후 배포"; exit 1; } || true
	$(DC_prod) up -d --build --remove-orphans

## --- DB (ENV=local|prod, 기본 local) ---
db-ping:     ## DB 연결 확인 (local=SQLite 경로 · prod=DB 컨테이너 헬스)
ifeq ($(ENV),prod)
ifeq ($(DB_ENGINE),sqlite)
	$(DC_prod) run --rm --no-deps --entrypoint sh $(APP_SVC) -c 'mkdir -p $(dir $(SQLITE_PATH)) && echo "sqlite OK: $(SQLITE_PATH)"'
else
	$(DC_prod) exec -T db sh -c '$(DB_PING)' && echo "$(DB_ENGINE) OK"
endif
else
	$(DC_local) run --rm --no-deps --entrypoint sh $(APP_SVC) -c 'mkdir -p $(dir $(SQLITE_PATH)) && echo "sqlite OK: $(SQLITE_PATH)"'
endif
db-shell:    ## DB 클라이언트 접속 (prod RDBMS 전용)
	@[ "$(ENV)" = "prod" ] && [ "$(DB_ENGINE)" != "sqlite" ] || { echo "db-shell 은 ENV=prod + RDBMS 전용 (local SQLite: $(SQLITE_PATH))"; exit 1; }
	$(DC_prod) exec db sh -c '$(DB_SHELL)'
db-migrate:  ## 마이그레이션 적용 (MIGRATE 변수)
	$(DC) run --rm --entrypoint sh $(APP_SVC) -c '$(MIGRATE)'
db-seed:     ## 시드 적재 (SEED 변수)
	$(DC) run --rm --entrypoint sh $(APP_SVC) -c '$(SEED)'
db-reset:    ## [local 전용] SQLite 삭제 + 마이그레이션 — prod 거부
ifeq ($(ENV),prod)
	@echo "prod DB reset 금지 / refused"; exit 1
else
	$(DC_local) run --rm --no-deps --entrypoint sh $(APP_SVC) -c 'rm -f $(SQLITE_PATH)'
	$(MAKE) db-migrate ENV=local
endif
db-fresh: db-reset  ## [local 전용] db-reset + db-seed
	$(MAKE) db-seed ENV=local
