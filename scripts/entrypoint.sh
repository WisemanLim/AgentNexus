#!/bin/sh
# AgentNexus 컨테이너 진입점 — api 역할이면 마이그레이션·시드 후 본 프로세스 실행 (AUTO_MIGRATE=false 로 비활성)
set -e
if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then  # 워커 간 메트릭 공유 디렉터리 — 기동마다 초기화
  mkdir -p "$PROMETHEUS_MULTIPROC_DIR" && find "$PROMETHEUS_MULTIPROC_DIR" -name '*.db' -delete
fi
if [ "${AGENTNEXUS_ROLE:-api}" = "api" ] && [ "${AUTO_MIGRATE:-true}" = "true" ]; then
  alembic upgrade head
  python -m app.seed
fi
exec "$@"
