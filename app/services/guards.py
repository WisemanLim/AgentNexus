"""Security 가드 (FR-G02·G07, NFR-PRV1) — 입력·출력 PII 마스킹, 프롬프트 인젝션 탐지, 도구 응답 소독(간접 인젝션).

설정(Setting 'gateway.security'): pii_input mask|block|allow · pii_output mask|allow · injection_input block|flag|allow ·
tool_response_sanitize true|false. 소독 규칙(SanitizeRule)은 정규식 strip|mask|block.
"""
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SanitizeRule, Setting

DEFAULT_SECURITY = {"pii_input": "mask", "pii_output": "mask", "injection_input": "flag",
                    "tool_response_sanitize": True, "mtls": {"upstream": "optional", "client": "disabled"}}


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("rrn", re.compile(r"(?<!\d)\d{6}-?[1-8]\d{6}(?!\d)")),  # 주민·외국인등록번호
    ("phone", re.compile(r"(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("card", re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)")),
]
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("agentnexus_token", re.compile(r"anx_(?:pat|key|rt|ws)_[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
INJECTION_PATTERNS: list[re.Pattern] = [re.compile(p, re.I) for p in (
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|messages)",
    r"disregard (all |the )?(previous|prior|above) (instructions|rules)",
    r"(reveal|print|show|leak) (your |the )?(system prompt|hidden instructions|api key)",
    r"you are now (in )?(developer|dan|jailbreak) mode",
    r"<\|im_start\|>|<\|system\|>|\[/?INST\]|BEGIN SYSTEM PROMPT",
    r"(이전|위의|앞의) (모든 )?(지시|지침|명령)(을|를|사항을)? (모두 )?무시",
    r"시스템 프롬프트(를|을)? (보여|출력|알려)",
    r"(call|invoke|execute) the (\w+ )?tool .{0,40}(without|no) (approval|asking)",
)]


@dataclass
class Finding:
    kind: str  # pii | secret | injection | rule
    type: str
    count: int = 1

    def as_dict(self) -> dict:
        return {"kind": self.kind, "type": self.type, "count": self.count}


@dataclass
class ScanResult:
    text: str
    findings: list[Finding] = field(default_factory=list)
    blocked: bool = False
    reason: str | None = None

    @property
    def types(self) -> list[str]:
        return sorted({f"{f.kind}:{f.type}" for f in self.findings})


def security_settings(db: Session) -> dict:
    row = db.get(Setting, "gateway.security")
    return {**DEFAULT_SECURITY, **(row.value if row and isinstance(row.value, dict) else {})}


_CUSTOM: list = [0.0, []]  # (적재 시각, [(name, pattern)]) — 운영자 PII 규칙 5초 캐시 (SCR-L06)


def custom_pii_patterns() -> list[tuple[str, re.Pattern]]:
    import time
    if time.monotonic() - _CUSTOM[0] < 5:
        return _CUSTOM[1]
    out: list[tuple[str, re.Pattern]] = []
    try:
        from app.db import SessionLocal
        with SessionLocal() as db:
            row = db.get(Setting, "guards.pii_rules")
            for r in (row.value if row and isinstance(row.value, list) else []):
                if r.get("enabled", True) and r.get("pattern"):
                    out.append((f"custom:{r.get('name') or 'rule'}", re.compile(r["pattern"])))
    except Exception:  # noqa: BLE001 — 규칙을 못 읽어도 기본 규칙으로 마스킹한다
        out = _CUSTOM[1]
    _CUSTOM[0], _CUSTOM[1] = time.monotonic(), out
    return out


def reset_custom_cache() -> None:
    _CUSTOM[0] = 0.0


def mask_pii(text: str, include_secrets: bool = True, extra: list | None = None) -> tuple[str, list[Finding]]:
    findings: list[Finding] = []
    patterns = (SECRET_PATTERNS if include_secrets else []) + PII_PATTERNS + \
        (custom_pii_patterns() if extra is None else extra)
    for name, pat in patterns:
        hits = 0

        def repl(m: re.Match, _name=name) -> str:
            nonlocal hits
            if _name == "card" and not _luhn(re.sub(r"\D", "", m.group(0))):
                return m.group(0)
            hits += 1
            return f"[MASKED:{_name}]"
        text = pat.sub(repl, text)
        if hits:
            kind = "secret" if any(name == s for s, _ in SECRET_PATTERNS) else "pii"
            findings.append(Finding(kind, name, hits))
    return text, findings


def detect_injection(text: str) -> list[Finding]:
    hits = [p.pattern for p in INJECTION_PATTERNS if p.search(text)]
    return [Finding("injection", "prompt_injection", len(hits))] if hits else []


def scan_input(db: Session, text: str) -> ScanResult:
    cfg = security_settings(db)
    out = ScanResult(text)
    inj = detect_injection(text)
    if inj:
        out.findings += inj
        if cfg["injection_input"] == "block":
            return ScanResult(text, out.findings, True, "프롬프트 인젝션 의심 입력이 차단되었습니다.")
    if cfg["pii_input"] != "allow":
        masked, f = mask_pii(text)
        if f:
            out.findings += f
            if cfg["pii_input"] == "block":
                return ScanResult(text, out.findings, True, "개인정보·시크릿이 포함된 입력이 차단되었습니다.")
            out.text = masked
    return out


def scan_output(db: Session, text: str) -> ScanResult:
    cfg = security_settings(db)
    if cfg["pii_output"] == "allow":
        return ScanResult(text)
    masked, f = mask_pii(text)
    return ScanResult(masked, f)


def _rules(db: Session, applies_to: str) -> list[SanitizeRule]:
    return list(db.scalars(select(SanitizeRule).where(SanitizeRule.enabled.is_(True),
                                                      SanitizeRule.applies_to == applies_to)))


_REMOVED = "[REMOVED: 도구 응답 내 지시문]"


def _strip_lines(text: str) -> tuple[str, int]:
    out, removed = [], 0
    for line in text.splitlines(keepends=True):
        if any(p.search(line) for p in INJECTION_PATTERNS):
            removed += 1
            out.append(_REMOVED + ("\n" if line.endswith("\n") else ""))
        else:
            out.append(line)
    return "".join(out), removed


def _clean_str(db: Session, text: str, rules: list[SanitizeRule], findings: list[Finding]) -> tuple[str, bool]:
    t, removed = _strip_lines(text)
    if removed:
        findings.append(Finding("injection", "indirect_injection", removed))
    t, f = mask_pii(t)
    findings += f
    for rule in rules:
        pat = re.compile(rule.pattern)
        if not pat.search(t):
            continue
        findings.append(Finding("rule", rule.name))
        if rule.action == "block":
            return t, True
        t = pat.sub("[MASKED]" if rule.action == "mask" else "", t)
    return t, False


def _walk(db: Session, value, rules, findings: list[Finding]):
    """JSON 값을 순회하며 문자열만 소독 — 구조(따옴표·키)는 보존. (값, 차단여부)"""
    if isinstance(value, str):
        return _clean_str(db, value, rules, findings)
    if isinstance(value, list):
        out, blocked = [], False
        for v in value:
            nv, b = _walk(db, v, rules, findings)
            out.append(nv)
            blocked = blocked or b
        return out, blocked
    if isinstance(value, dict):
        out, blocked = {}, False
        for k, v in value.items():
            nv, b = _walk(db, v, rules, findings)
            out[k] = nv
            blocked = blocked or b
        return out, blocked
    return value, False


def _merge(findings: list[Finding]) -> list[Finding]:
    acc: dict[tuple, Finding] = {}
    for f in findings:
        key = (f.kind, f.type)
        if key in acc:
            acc[key].count += f.count
        else:
            acc[key] = Finding(f.kind, f.type, f.count)
    return list(acc.values())


_BLOCKED = "[BLOCKED: 소독 규칙에 의해 도구 응답이 차단되었습니다]"


def sanitize_tool_text(db: Session, text: str) -> ScanResult:
    """도구 응답(간접 인젝션 경로) 소독 — 지시문 줄 제거, 시크릿·PII 마스킹, 사용자 규칙. JSON 텍스트는 구조 보존."""
    rules = _rules(db, "tool_response")
    findings: list[Finding] = []
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if obj is not None:
            clean, blocked = _walk(db, obj, rules, findings)
            if blocked:
                return ScanResult(_BLOCKED, _merge(findings), True, "sanitize_rule")
            return ScanResult(json.dumps(clean, ensure_ascii=False) if findings else text, _merge(findings))
    clean, blocked = _clean_str(db, text, rules, findings)
    if blocked:
        return ScanResult(_BLOCKED, _merge(findings), True, "sanitize_rule")
    return ScanResult(clean, _merge(findings))


def sanitize_structured(db: Session, value) -> tuple[object, list[Finding], bool]:
    findings: list[Finding] = []
    clean, blocked = _walk(db, value, _rules(db, "tool_response"), findings)
    return clean, _merge(findings), blocked


def sanitize_tool_result(db: Session, result: dict) -> tuple[dict, list[Finding]]:
    """MCP tools/call result 의 text content·structuredContent 를 소독."""
    if not security_settings(db).get("tool_response_sanitize", True):
        return result, []
    findings: list[Finding] = []
    content = []
    for part in result.get("content") or []:
        if part.get("type") == "text":
            r = sanitize_tool_text(db, part.get("text", ""))
            findings += r.findings
            part = {**part, "text": r.text}
        content.append(part)
    out = {**result, "content": content}
    if result.get("structuredContent") is not None:
        clean, f, blocked = sanitize_structured(db, result["structuredContent"])
        if blocked:
            out.pop("structuredContent")
        elif f:
            out["structuredContent"] = clean
    findings = _merge(findings)
    if findings:
        out.setdefault("_meta", {})["io.agentnexus/sanitized"] = [f.as_dict() for f in findings]
    return out, findings
