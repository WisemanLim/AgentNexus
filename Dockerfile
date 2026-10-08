# wise-agentnexus — python-fastapi (multi-stage: dev = local 모드, runtime = prod 모드)
# 베이스 이미지는 digest 고정 — 갱신은 Dependabot(docker) PR (D17-③)
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONPATH=/app
RUN pip install --no-cache-dir uv==0.12.23
WORKDIR /app
RUN printf '#!/bin/sh\nPYTHONPATH=/app exec python -m app.cli "$@"\n' > /usr/local/bin/nexus && chmod +x /usr/local/bin/nexus
ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]

# dev: 테스트/개발 의존성 포함, 소스는 local 모드에서 바인드 마운트
FROM base AS dev
COPY pyproject.toml uv.lock* ./
RUN uv sync --locked --no-install-project
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# web-dev: 콘솔 SPA(React + Vite) 개발 서버 — 의존성 설치는 이미지 빌드 안에서만 (local 은 src 만 바인드)
FROM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS web-dev
RUN npm install -g pnpm@10.12.1
WORKDIR /web
ARG PNPM_FROZEN=1
COPY web/package.json web/pnpm-lock.yaml* ./
# 잠금 파일이 없으면 실패 — 고정 안 된 설치로 조용히 넘어가지 않는다 (D20-③). 잠금 갱신은 make web-lock(PNPM_FROZEN=0)
RUN if [ "$PNPM_FROZEN" = 1 ]; then test -f pnpm-lock.yaml || { echo "web/pnpm-lock.yaml 없음 — make web-lock"; exit 1; }; \
      pnpm install --frozen-lockfile; else pnpm install --no-frozen-lockfile; fi \
    && cp pnpm-lock.yaml /tmp/pnpm-lock.resolved.yaml
COPY web/ ./
EXPOSE 5173
CMD ["pnpm", "dev"]

# web-build: 정적 번들(dist) — runtime 이 /app/web/dist 로 서빙 (폰트 로컬 번들, 외부 CDN 없음)
FROM web-dev AS web-build
RUN pnpm build

# e2e: 콘솔 브라우저 스모크 (Playwright · Chromium) — compose e2e 프로파일에서만 사용
FROM mcr.microsoft.com/playwright:v1.55.0-noble@sha256:b27e719ecbfef153e13fd24e8341736733bf2658b229677eb21ff57ff5d7fb29 AS e2e
WORKDIR /e2e
ARG NPM_LOCKED=1
COPY web/e2e/package.json web/e2e/package-lock.json* ./
RUN if [ "$NPM_LOCKED" = 1 ]; then test -f package-lock.json || { echo "web/e2e/package-lock.json 없음 — make e2e-lock"; exit 1; }; \
      npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi \
    && cp package-lock.json /tmp/package-lock.resolved.json
COPY web/e2e/ ./
CMD ["npx", "playwright", "test"]

# runtime: 운영 의존성만 (uvicorn 다중 워커 → Prometheus multiprocess 집계)
FROM base AS runtime
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus UVICORN_WORKERS=2
COPY pyproject.toml uv.lock* ./
RUN uv sync --locked --no-install-project --no-dev
COPY . .
COPY --from=web-build /web/dist /app/web/dist
RUN useradd -r -u 10001 app && mkdir -p /app/data && chown -R app /app/data
USER app
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers \"${UVICORN_WORKERS:-2}\""]
