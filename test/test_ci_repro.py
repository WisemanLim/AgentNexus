"""Epic 50 — CI·재현성 (D19-③): 액션 커밋 SHA 고정 · 성능 시험 재측정 판정 · 시간 의존 시험 경합 제거."""
import re
from pathlib import Path

import yaml

from test import test_perf

ROOT = Path(__file__).resolve().parents[1]


# ── C50-01 GitHub 액션 — 모든 uses 가 40자리 커밋 SHA + 태그 주석 (태그 이동·탈취에도 같은 코드) ──
def test_actions_pinned_to_sha():
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    uses = re.findall(r"uses:\s*(\S+)(.*)", text)
    assert uses
    for ref, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert re.search(r"#\s*v\d+\.\d+\.\d+", rest), ref  # Dependabot 이 SHA 와 함께 갱신하는 버전 주석
    ci = yaml.safe_load(text)
    assert all("@" in s["uses"] for j in ci["jobs"].values() for s in j["steps"] if "uses" in s)


# ── C50-02 성능 시험 — 한도 안이면 1회, 넘으면 다시 재고 가장 좋은 회차로 판정 ──
def test_perf_best_of(monkeypatch):
    monkeypatch.setattr(test_perf, "TRIES", 3)
    seq = iter([{"p95": 10.0}, {"p95": 99.0}])
    assert test_perf._best_of(lambda: next(seq), 30) == {"p95": 10.0, "tries": 1}
    seq = iter([{"p95": 80.0}, {"p95": 45.0}, {"p95": 70.0}])
    assert test_perf._best_of(lambda: next(seq), 30) == {"p95": 45.0, "tries": 2}  # 3회 모두 넘으면 최솟값으로 실패 판정
    seq = iter([{"p95": 80.0}, {"p95": 20.0}, {"p95": 1.0}])
    assert test_perf._best_of(lambda: next(seq), 30) == {"p95": 20.0, "tries": 2}  # 한도 안에 들면 멈춘다


# ── C50-03 시간 의존 시험 — 승인 뒤 대기는 종료 상태까지 · 느린 노드만 단계 타임아웃 ──
def test_time_dependent_tests_hardened():
    src = (ROOT / "test/test_chat_editor.py").read_text()
    body = src.split("def test_approval_context_remember_and_node_policy", 1)[1].split("\ndef ", 1)[0]
    assert "until=done" in body.split("/approve", 1)[1].split("rid2", 1)[0]
    cont = src.split("def test_continue_with_partial_results", 1)[1].split("\ndef ", 1)[0]
    assert "node_timeout_s" not in cont and "tool: e22slow.lookup, timeout_s: 1" in cont
