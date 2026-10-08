"""Epic 13 — 배포 산출물 정합성 (Helm 차트 ↔ 앱 · 정책 사본). 렌더 검증은 make helm-check (helm 필요)."""
from pathlib import Path

import yaml

from app.core.config import APP_VERSION

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "agentnexus"


def test_chart_version_matches_app():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert chart["appVersion"] == APP_VERSION and chart["name"] == "agentnexus"


def test_chart_policy_copy_in_sync():
    # Helm 은 차트 밖 파일을 읽을 수 없어 OPA 정책 사본을 둔다 — 원본과 같아야 한다
    assert (CHART / "files" / "agentnexus.rego").read_text() == (ROOT / "policies" / "agentnexus.rego").read_text()


def test_chart_config_covers_compose_env():
    """compose 의 api 서비스가 받는 비밀 아닌 설정 키를 차트 ConfigMap 도 모두 정의한다."""
    tpl = (CHART / "templates" / "configmap.yaml").read_text()
    env_keys = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text().splitlines()
                if line and not line.startswith("#") and "=" in line}
    host_only = {"BIND_HOST", "API_PORT", "WEB_PORT", "A2A_GRPC_PORT", "COMPOSE_PROFILES", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER",
                 "AGENTNEXUS_COMPONENTS", "OTLP_INGEST_TOKEN", "METRICS_TOKEN", "DB_PASSWORD", "DATABASE_URL",
                 "BOOTSTRAP_ADMIN_PASSWORD", "SIGNING_KEY_PEM", "ENCRYPTION_KEY", "A2A_DEMO_CLIENT_SECRET",
                 "SIGNING_KEY_PEM_PREVIOUS", "ENCRYPTION_KEY_PREVIOUS", "SIGNING_KEY_PEM_NEXT", "ENCRYPTION_KEY_NEXT"}
    secret_tpl = (CHART / "templates" / "secret.yaml").read_text()
    missing = sorted(k for k in env_keys - host_only if f"{k}:" not in tpl)
    assert not missing, f"차트 ConfigMap 에 없는 설정: {missing}"
    for k in ("SIGNING_KEY_PEM", "ENCRYPTION_KEY", "DATABASE_URL", "A2A_DEMO_CLIENT_SECRET", "OTLP_INGEST_TOKEN",
              "SIGNING_KEY_PEM_PREVIOUS", "ENCRYPTION_KEY_PREVIOUS", "SIGNING_KEY_PEM_NEXT", "ENCRYPTION_KEY_NEXT"):
        assert k in secret_tpl, k
