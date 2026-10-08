"""Epic 42 — 공급망·라이선스·CI (D17-③): 라이선스 파일·서드파티 목록·빌드 고정·CI·Dependabot 정합 (파일 수준, 오프라인)."""
import hashlib
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APACHE_MD5 = "3b83ef96387f14655fc854ddc3c6bd57"  # apache.org LICENSE-2.0.txt


# ── C42-01 라이선스 파일·메타데이터 ──
def test_license_files():
    assert hashlib.md5((ROOT / "LICENSE").read_bytes()).hexdigest() == APACHE_MD5  # noqa: S324 — 원문 동일성 확인용
    notice = (ROOT / "NOTICE").read_text()
    assert "Apache License, Version 2.0" in notice and "Pretendard" in notice and "psycopg" in notice
    assert "Report a vulnerability" in (ROOT / "SECURITY.md").read_text()
    assert re.search(r'^license = "Apache-2.0"$', (ROOT / "pyproject.toml").read_text(), re.M)
    for p in ("web/package.json", "web/e2e/package.json"):
        assert json.loads((ROOT / p).read_text())["license"] == "Apache-2.0", p
    chart = yaml.safe_load((ROOT / "deploy/helm/agentnexus/Chart.yaml").read_text())
    assert chart["annotations"]["artifacthub.io/license"] == "Apache-2.0"
    assert "SIL Open Font License" in (ROOT / "web/public/licenses/Pretendard-OFL-1.1.txt").read_text()


# ── C42-02 서드파티 목록 — 검토 표의 모든 항목에 판정, 라이선스 미상 없음 ──
def test_third_party_reviewed():
    text = (ROOT / "THIRD_PARTY_LICENSES.md").read_text()
    review = text.split("## 검토 대상", 1)[1].split("\n## ", 1)[0]
    assert "검토 필요" not in review and "UNKNOWN" not in text
    assert "| psycopg |" in review and "| fastapi |" in text and "| react |" in text


# ── C42-03 빌드 고정 — 베이스 이미지 digest·uv 버전·잠금 설치 ──
def test_build_pinned():
    df = (ROOT / "Dockerfile").read_text()
    for base in ("python:3.12-slim", "node:22-alpine"):
        assert re.search(rf"^FROM {re.escape(base)}@sha256:[0-9a-f]{{64}} ", df, re.M), base
    assert re.search(r"pip install --no-cache-dir uv==\d+\.\d+\.\d+", df)
    assert df.count("uv sync --locked") == 2 and "npm ci" in df
    lock = json.loads((ROOT / "web/e2e/package-lock.json").read_text())
    cores = {k: v["version"] for k, v in lock["packages"].items() if k.endswith("node_modules/playwright-core")}
    assert list(cores.values()) == [json.loads((ROOT / "web/e2e/package.json").read_text())["devDependencies"]["playwright-core"]]
    assert "esbuild: { legalComments: 'eof' }" in (ROOT / "web/vite.config.ts").read_text()


# ── C42-04 CI·Dependabot ──
def test_ci_and_dependabot():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert ci["permissions"] == {"contents": "read"}
    steps = " ".join(s.get("run", "") for s in ci["jobs"]["test"]["steps"])
    for target in ("make lint", "make test", "make test-pg", "make web-test", "make helm-check"):
        assert target in steps, target
    assert any("make audit" in s.get("run", "") for s in ci["jobs"]["audit"]["steps"])
    mk = (ROOT / "Makefile").read_text()
    for t in ("lint:", "audit:", "licenses:", "e2e-lock:"):
        assert re.search(rf"^{t}", mk, re.M), t
    deps = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())["updates"]
    assert {(u["package-ecosystem"], u["directory"]) for u in deps} == {
        ("uv", "/"), ("npm", "/web"), ("npm", "/web/e2e"), ("docker", "/"), ("github-actions", "/")}
