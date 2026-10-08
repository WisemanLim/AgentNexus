"""에이전트 프로파일 그래프 정의 (FR-A02·W02, SCR-D02~D07) — YAML/JSON 스키마·참조 검증(오류 줄 번호).

```yaml
version: 1
planner: {model: auto, mode: llm|template, max_steps: 6}
workers:
  retriever: {tools: [jira.search_issues, confluence.search_pages], max_results: 5}
  summarize: {model: domain-local, cite: true}
  executor:  {tools: [confluence.create_page]}
  remote:    {agents: [observability-analyst]}
fanout: {parallel: true, join: all}          # join: all | any
retry: {max_attempts: 2, backoff_ms: 200}
# 재계획 (FR-A02.1): 실패 단계가 있으면 Planner 가 실패 사유·성공 결과로 대체 계획을 만든다(최대 max_rounds).
# LLM 계획이 검증을 못 넘거나 template 모드면 fallback_plan 을 쓴다. 단계 id 는 r<N>_ 접두사로 붙는다.
#   planner: {..., replan: {max_rounds: 1, fallback_plan: [{id: s1b, agent: retriever, tool: confluence.search_pages}]}}
on_failure: partial                           # partial(부분 결과) | fail
# 노드 설정 (SCR-D03~D06):
#   planner: {fallback_model, prompt("{request}·{tools}·{agents}"), max_depth, assign: [retriever, …, <원격 id>],
#             replan: {max_rounds, triggers: [failed, empty, rejected]}}   # 'on' 은 YAML 1.1 에서 true 로 읽혀 쓰지 않는다
#   workers.retriever: {top_k, timeout_s} · workers.summarize: {max_tokens, cite, citation_format: n|n_link}
#   workers.executor: {approval: {approver: requester|approvers, ttl_s, on_deny: partial|replan|fail, medium: [도구]}}
#   fanout: {parallel, max_concurrency, join: all|any|required|n_of_m, required: [id], n, node_timeout_s, total_timeout_s}
#   retry: {max_attempts, backoff_ms, backoff: fixed|exponential} · fallbacks: {tool_429: wait|skip}
#   단계별: {retry: {max_attempts}, timeout_s}
data_class: internal
default_plan:                                 # template 모드 · LLM 계획 실패 시 폴백
  - {id: s1, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}"}}
  - {id: s2, agent: summarize, depends_on: [s1]}
  - {id: s3, agent: retriever, kb: runbooks, top_k: 5}            # RAG — workers.retriever.knowledge_bases
  - {id: s4, agent: executor, tool: jira.create_issue, depends_on: [s1], when: "s1.count == 0"}   # 조건 분기
```
변수: `{{request}}` 사용자 요청 · `{{sN.text}}` 단계 출력 텍스트 · `{{sources}}` 근거 목록.
조건 `when` (FR-W02): `<참조> <연산> <값>` — 참조 request · sN.text · sN.count · sN.state · sN.<출력 필드>,
연산 == != > < >= <= contains "not contains" exists, 값은 숫자·'문자열'·true·false. 거짓이면 단계는 건너뛴다.
"""
import fnmatch
import re

import yaml

AGENTS = ("retriever", "summarize", "executor", "remote")
DEFAULTS = {"version": 1, "planner": {"model": "auto", "mode": "llm", "max_steps": 6},
            "workers": {}, "fanout": {"parallel": True, "join": "all"},
            "retry": {"max_attempts": 2, "backoff_ms": 200}, "on_failure": "partial", "data_class": "internal",
            "fallbacks": {"tool_429": "wait"}, "default_plan": []}
JOINS = ("all", "any", "required", "n_of_m")
REPLAN_ON = ("failed", "empty", "rejected")


def parse(text_or_obj) -> tuple[dict, dict]:
    """(graph, line_map). YAML 문자열이면 경로→줄 번호 맵을 함께 만든다."""
    if isinstance(text_or_obj, dict):
        return text_or_obj, {}
    text = str(text_or_obj)
    obj = yaml.safe_load(text) or {}
    if not isinstance(obj, dict):
        raise ValueError("그래프 정의는 객체(매핑)여야 합니다.")
    return obj, _lines(yaml.compose(text))


def _lines(node, path: str = "", out: dict | None = None) -> dict:
    out = {} if out is None else out
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            p = f"{path}.{k.value}" if path else str(k.value)
            out[p] = k.start_mark.line + 1
            _lines(v, p, out)
    elif isinstance(node, yaml.SequenceNode):
        for i, v in enumerate(node.value):
            p = f"{path}[{i}]"
            out[p] = v.start_mark.line + 1
            _lines(v, p, out)
    return out


def normalize(g: dict) -> dict:
    out = {**DEFAULTS, **(g or {})}
    for k in ("planner", "fanout", "retry", "fallbacks"):
        out[k] = {**DEFAULTS[k], **((g or {}).get(k) or {})}
    out["workers"] = dict((g or {}).get("workers") or {})
    out["default_plan"] = list((g or {}).get("default_plan") or [])
    return out


def tools_of(g: dict) -> list[str]:
    w = g.get("workers") or {}
    return sorted(set((w.get("retriever") or {}).get("tools", []) + (w.get("executor") or {}).get("tools", [])))


def knowledge_bases_of(g: dict) -> list[str]:
    return sorted(set(((g.get("workers") or {}).get("retriever") or {}).get("knowledge_bases", [])))


def remote_agents_of(g: dict) -> list[str]:
    return sorted(set(((g.get("workers") or {}).get("remote") or {}).get("agents", [])))


def models_of(g: dict) -> list[str]:
    w = g.get("workers") or {}
    return sorted({m for m in ((g.get("planner") or {}).get("model"), (w.get("summarize") or {}).get("model")) if m})


_COND = re.compile(r"^\s*(request|s?[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?)\s+(==|!=|>=|<=|>|<|not contains|contains|exists)"
                   r"(?:\s+(.+?))?\s*$")


def parse_condition(expr: str) -> tuple[str, str, object]:
    m = _COND.match(str(expr or ""))
    if not m or (m.group(2) != "exists" and m.group(3) is None):
        raise ValueError(f"조건 형식 오류: {expr!r} — 예: \"s1.count > 0\", \"request contains '장애'\"")
    ref, op, raw = m.group(1), m.group(2), m.group(3)
    if raw is None:
        return ref, op, None
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        val: object = raw[1:-1]
    elif raw in ("true", "false"):
        val = raw == "true"
    else:
        try:
            val = float(raw) if "." in raw else int(raw)
        except ValueError:
            val = raw
    return ref, op, val


def eval_condition(expr: str, request: str, outputs: dict) -> bool:
    ref, op, val = parse_condition(expr)
    if ref == "request":
        cur: object = request
    else:
        sid, _, attr = ref.partition(".")
        out = outputs.get(sid)
        cur = None if out is None else (out.get(attr or "text") if isinstance(out, dict) else None)
    if op == "exists":
        return cur not in (None, "", [], {})
    if op in ("contains", "not contains"):
        hit = str(val) in (cur if isinstance(cur, str) else str(cur or ""))
        return hit if op == "contains" else not hit
    if op in ("==", "!="):
        same = str(cur) == str(val) if not isinstance(val, (int, float)) or isinstance(val, bool) else _num(cur) == val
        return same if op == "==" else not same
    a, b = _num(cur), _num(val)
    if a is None or b is None:
        return False
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_plan(g: dict, steps: list, prefix: str = "default_plan", known: set[str] | None = None) -> list[dict]:
    errors: list[dict] = []
    w = g.get("workers") or {}
    seen: set[str] = set(known or ())
    for i, st in enumerate(steps):
        p = f"{prefix}[{i}]"
        if not isinstance(st, dict):
            errors.append({"path": p, "message": "단계는 객체여야 합니다."})
            continue
        sid, agent = st.get("id"), st.get("agent")
        if not sid or sid in seen:
            errors.append({"path": f"{p}.id", "message": f"id 누락 또는 중복: {sid}"})
        if agent not in AGENTS:
            errors.append({"path": f"{p}.agent", "message": f"agent 는 {AGENTS} 중 하나: {agent}"})
        elif agent not in w:
            errors.append({"path": f"{p}.agent", "message": f"workers.{agent} 가 정의되지 않았습니다."})
        elif prefix != "default_plan" and (g.get("planner") or {}).get("assign") and \
                agent != "remote" and agent not in g["planner"]["assign"]:
            errors.append({"path": f"{p}.agent", "message": f"Planner 배정 대상이 아닌 노드: {agent}"})
        if agent == "remote" and prefix != "default_plan" and (g.get("planner") or {}).get("assign") and \
                st.get("remote_agent") not in g["planner"]["assign"]:
            errors.append({"path": f"{p}.remote_agent", "message": f"Planner 배정 대상이 아닌 원격 에이전트: {st.get('remote_agent')}"})
        if agent == "retriever" and st.get("kb"):
            if st["kb"] not in (w.get("retriever") or {}).get("knowledge_bases", []):
                errors.append({"path": f"{p}.kb", "message": f"workers.retriever.knowledge_bases 에 없는 지식 베이스: {st['kb']}"})
        elif agent in ("retriever", "executor"):
            tool = st.get("tool")
            allowed = (w.get(agent) or {}).get("tools", [])
            if not tool or "." not in str(tool):
                errors.append({"path": f"{p}.tool", "message": "도구는 '<서버>.<도구>' 형식이어야 합니다."})
            elif not any(fnmatch.fnmatchcase(tool, pat) for pat in allowed):
                errors.append({"path": f"{p}.tool", "message": f"workers.{agent}.tools 에 없는 도구: {tool}"})
        if agent == "remote":
            ra = st.get("remote_agent")
            if ra not in remote_agents_of(g):
                errors.append({"path": f"{p}.remote_agent", "message": f"workers.remote.agents 에 없는 에이전트: {ra}"})
        deps = st.get("depends_on") or []
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            errors.append({"path": f"{p}.depends_on", "message": "depends_on 은 단계 id(문자열) 목록이어야 합니다."})
            deps = []
        for d in deps:
            if d not in seen:
                errors.append({"path": f"{p}.depends_on", "message": f"앞 단계가 아닌 의존성(순환·미정의): {d}"})
        if st.get("when"):
            try:
                ref = parse_condition(st["when"])[0]
                if ref != "request" and ref.split(".")[0] not in seen:
                    errors.append({"path": f"{p}.when", "message": f"조건이 앞 단계가 아닌 출력을 참조: {ref}"})
            except ValueError as exc:
                errors.append({"path": f"{p}.when", "message": str(exc)})
        if sid:
            seen.add(sid)
    return errors


def depth(steps: list) -> int:
    """기본 계획 DAG 의 최대 깊이 (Planner 분해 규칙 max_depth)."""
    d: dict[str, int] = {}
    for st in steps:
        if isinstance(st, dict) and st.get("id"):
            d[st["id"]] = 1 + max((d.get(x, 0) for x in st.get("depends_on") or []), default=0)
    return max(d.values(), default=0)


def replan_triggers(rp: dict):
    """재계획 조건 목록 — 키 'triggers'. YAML 1.1 이 'on' 을 true 로 읽는 경우도 받아 준다."""
    for k in ("triggers", "on", True):
        if k in rp:
            return rp[k]
    return None


def _int_in(v, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _node_settings(g: dict) -> list[dict]:
    """노드·팬아웃 설정 검증 (SCR-D03~D06)."""
    errors: list[dict] = []
    pl, w, fo = g["planner"], g["workers"], g["fanout"]
    add = lambda path, msg: errors.append({"path": path, "message": msg})  # noqa: E731
    if pl.get("max_depth") is not None and not _int_in(pl["max_depth"], 1, 10):
        add("planner.max_depth", "1~10 정수")
    elif pl.get("max_depth") and depth(g["default_plan"]) > pl["max_depth"]:
        add("planner.max_depth", f"기본 계획 깊이 {depth(g['default_plan'])} 이 최대 깊이 {pl['max_depth']} 를 넘습니다.")
    if pl.get("assign") is not None:
        if not isinstance(pl["assign"], list) or not pl["assign"]:
            add("planner.assign", "배정 대상 목록이 필요합니다.")
        else:
            bad = [a for a in pl["assign"] if a not in w and a not in remote_agents_of(g)]
            if bad:
                add("planner.assign", f"정의되지 않은 배정 대상: {bad}")
    on = replan_triggers(pl.get("replan") or {})
    if on is not None and (not isinstance(on, list) or any(x not in REPLAN_ON for x in on)):
        add("planner.replan.triggers", f"재계획 조건은 {REPLAN_ON} 중에서")
    rt = w.get("retriever") or {}
    if rt.get("top_k") is not None and not _int_in(rt["top_k"], 1, 50):
        add("workers.retriever.top_k", "1~50 정수")
    if rt.get("timeout_s") is not None and not _int_in(rt["timeout_s"], 1, 300):
        add("workers.retriever.timeout_s", "1~300 초")
    sm = w.get("summarize") or {}
    if sm.get("max_tokens") is not None and not _int_in(sm["max_tokens"], 64, 8000):
        add("workers.summarize.max_tokens", "64~8000 토큰")
    if sm.get("citation_format") not in (None, "n", "n_link"):
        add("workers.summarize.citation_format", "n | n_link")
    ap = (w.get("executor") or {}).get("approval") or {}
    if ap:
        if ap.get("approver") not in (None, "requester", "approvers"):
            add("workers.executor.approval.approver", "requester | approvers")
        if ap.get("ttl_s") is not None and not _int_in(ap["ttl_s"], 60, 86400):
            add("workers.executor.approval.ttl_s", "60~86400 초")
        if ap.get("on_deny") not in (None, "partial", "replan", "fail"):
            add("workers.executor.approval.on_deny", "partial | replan | fail")
        extra = [t for t in ap.get("medium") or [] if not any(fnmatch.fnmatchcase(t, x)
                                                               for x in (w.get("executor") or {}).get("tools", []))]
        if extra:
            add("workers.executor.approval.medium", f"workers.executor.tools 에 없는 도구: {extra}")
    if fo.get("join") not in JOINS:
        add("fanout.join", f"join 은 {' | '.join(JOINS)}")
    if fo.get("join") == "required":
        ids = {st.get("id") for st in g["default_plan"] if isinstance(st, dict)}
        req = fo.get("required") or []
        if not req or any(r not in ids for r in req):
            add("fanout.required", "필수 노드는 기본 계획의 단계 id 목록이어야 합니다.")
    if fo.get("join") == "n_of_m" and not _int_in(fo.get("n"), 1, 20):
        add("fanout.n", "N 중 M 의 N(1~20)이 필요합니다.")
    for k, hi in (("max_concurrency", 20), ("node_timeout_s", 600), ("total_timeout_s", 3600)):
        if fo.get(k) is not None and not _int_in(fo[k], 1, hi):
            add(f"fanout.{k}", f"1~{hi} 정수")
    if g["retry"].get("backoff") not in (None, "fixed", "exponential"):
        add("retry.backoff", "fixed | exponential")
    if g["fallbacks"].get("tool_429") not in ("wait", "skip"):
        add("fallbacks.tool_429", "wait | skip")
    for i, st in enumerate(g["default_plan"]):
        if not isinstance(st, dict):
            continue
        if st.get("timeout_s") is not None and not _int_in(st["timeout_s"], 1, 600):
            add(f"default_plan[{i}].timeout_s", "1~600 초")
        if (st.get("retry") or {}).get("max_attempts") is not None and not _int_in(st["retry"]["max_attempts"], 1, 5):
            add(f"default_plan[{i}].retry.max_attempts", "1~5 정수")
    return errors


def validate(g: dict) -> list[dict]:
    errors: list[dict] = []
    g = normalize(g)
    if g["planner"].get("mode") not in ("llm", "template"):
        errors.append({"path": "planner.mode", "message": "mode 는 llm | template"})
    if not isinstance(g["planner"].get("max_steps"), int) or not 1 <= g["planner"]["max_steps"] <= 20:
        errors.append({"path": "planner.max_steps", "message": "1~20 정수"})
    for k in g["workers"]:
        if k not in AGENTS:
            errors.append({"path": f"workers.{k}", "message": f"알 수 없는 worker: {k}"})
    for k in ("retriever", "executor"):
        tools = (g["workers"].get(k) or {}).get("tools")
        kbs = (g["workers"].get(k) or {}).get("knowledge_bases") if k == "retriever" else None
        if k in g["workers"] and (not isinstance(tools, list) or not tools) and not kbs:
            errors.append({"path": f"workers.{k}.tools", "message": "도구 목록이 필요합니다."})
    rp = g["planner"].get("replan") or {}
    if rp:
        if not isinstance(rp.get("max_rounds", 1), int) or not 0 <= rp.get("max_rounds", 1) <= 3:
            errors.append({"path": "planner.replan.max_rounds", "message": "0~3 정수"})
        errors += validate_plan(g, rp.get("fallback_plan") or [], "planner.replan.fallback_plan",
                                known={st.get("id") for st in g["default_plan"] if isinstance(st, dict)})
    if "remote" in g["workers"] and not remote_agents_of(g):
        errors.append({"path": "workers.remote.agents", "message": "원격 에이전트 목록이 필요합니다."})
    errors += _node_settings(g)
    if g["on_failure"] not in ("partial", "fail"):
        errors.append({"path": "on_failure", "message": "partial | fail"})
    if g["data_class"] not in ("public", "internal", "confidential"):
        errors.append({"path": "data_class", "message": "public | internal | confidential"})
    if not isinstance(g["retry"].get("max_attempts"), int) or not 1 <= g["retry"]["max_attempts"] <= 5:
        errors.append({"path": "retry.max_attempts", "message": "1~5 정수"})
    if g["planner"]["mode"] == "template" and not g["default_plan"]:
        errors.append({"path": "default_plan", "message": "template 모드에는 default_plan 이 필요합니다."})
    errors += validate_plan(g, g["default_plan"])
    return errors


def with_lines(errors: list[dict], lines: dict) -> list[dict]:
    out = []
    for e in errors:
        path, line = e["path"], None
        while path and line is None:
            line = lines.get(path)
            path = path.rsplit(".", 1)[0] if "." in path else (path.rsplit("[", 1)[0] if "[" in path else "")
        out.append({**e, "line": line})
    return out


def to_yaml(g: dict) -> str:
    return yaml.safe_dump(g, allow_unicode=True, sort_keys=False)


def to_canvas(g: dict) -> dict:
    """그래프 편집기(SCR-D02) 용 노드·엣지 — 기본 계획을 시각화."""
    g = normalize(g)
    nodes = [{"id": "planner", "type": "planner", "label": f"Planner ({g['planner']['model']})"}]
    edges = []
    for st in g["default_plan"]:
        label = st.get("tool") or (f"RAG {st['kb']}" if st.get("kb") else None) or st.get("remote_agent") or st.get("agent")
        deps = st.get("depends_on") or ["planner"]
        retry = (st.get("retry") or {}).get("max_attempts")
        nodes.append({"id": st["id"], "type": st["agent"], "label": label, **({"when": st["when"]} if st.get("when") else {}),
                      **({"join": g["fanout"]["join"]} if len(deps) > 1 else {}),
                      **({"retry": retry} if retry else {}), **({"timeout_s": st["timeout_s"]} if st.get("timeout_s") else {}),
                      **({"required": True} if st["id"] in (g["fanout"].get("required") or []) else {})})
        edges += [{"from": d, "to": st["id"], **({"when": st["when"]} if st.get("when") else {})} for d in deps]
    return {"nodes": nodes, "edges": edges}


def diff(a: dict, b: dict) -> list[dict]:
    """두 그래프 비교 (SCR-D07 ⑦) — 단계 추가(+)·삭제(−)·변경(~)과 설정 변경."""
    a, b = normalize(a), normalize(b)
    out: list[dict] = []
    sa = {st["id"]: st for st in a["default_plan"] if isinstance(st, dict) and st.get("id")}
    sb = {st["id"]: st for st in b["default_plan"] if isinstance(st, dict) and st.get("id")}
    label = lambda st: st.get("tool") or st.get("remote_agent") or (f"RAG {st['kb']}" if st.get("kb") else st.get("agent"))  # noqa: E731
    out += [{"op": "+", "path": f"default_plan.{k}", "value": f"{sb[k].get('agent')} · {label(sb[k])}"} for k in sb if k not in sa]
    out += [{"op": "-", "path": f"default_plan.{k}", "value": f"{sa[k].get('agent')} · {label(sa[k])}"} for k in sa if k not in sb]
    for k in sa:
        if k in sb and sa[k] != sb[k]:
            for f in sorted(set(sa[k]) | set(sb[k])):
                if sa[k].get(f) != sb[k].get(f):
                    out.append({"op": "~", "path": f"default_plan.{k}.{f}", "from": sa[k].get(f), "to": sb[k].get(f)})
    for sec in ("planner", "fanout", "retry", "fallbacks"):
        for f in sorted(set(a[sec]) | set(b[sec])):
            if a[sec].get(f) != b[sec].get(f):
                out.append({"op": "~", "path": f"{sec}.{f}", "from": a[sec].get(f), "to": b[sec].get(f)})
    for wk in sorted(set(a["workers"]) | set(b["workers"])):
        if a["workers"].get(wk) != b["workers"].get(wk):
            op = "+" if wk not in a["workers"] else "-" if wk not in b["workers"] else "~"
            out.append({"op": op, "path": f"workers.{wk}", "from": a["workers"].get(wk), "to": b["workers"].get(wk)})
    for f in ("on_failure", "data_class"):
        if a.get(f) != b.get(f):
            out.append({"op": "~", "path": f, "from": a.get(f), "to": b.get(f)})
    return out


def to_adk(g: dict, name: str = "profile") -> tuple[str, list[str]]:
    """ADK(Python) 보기 (SCR-D07 ①) — 같은 그래프를 Google ADK 에이전트 코드로 변환. 읽기 전용, 변환 경고를 함께 준다."""
    g = normalize(g)
    ident = re.sub(r"\W", "_", name).strip("_") or "profile"
    warn: list[str] = []
    lines = ["# 자동 생성 — AgentNexus 그래프 정의의 ADK 보기 (읽기 전용, 편집은 YAML)",
             "from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent",
             "from google.adk.agents.remote_a2a_agent import RemoteA2aAgent",
             "from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams", "",
             'GATEWAY = "https://agentnexus/mcp/_all"  # 모든 도구 호출은 AI 게이트웨이 경유', ""]
    names = []
    for st in g["default_plan"]:
        if not isinstance(st, dict) or not st.get("id"):
            continue
        sid = re.sub(r"\W", "_", st["id"])
        names.append(sid)
        agent = st.get("agent")
        if st.get("when"):
            warn.append(f"{st['id']}: 조건 분기(when: {st['when']})는 ADK 에 직접 대응이 없어 주석으로 남깁니다.")
            lines.append(f"# when: {st['when']}")
        if agent == "remote":
            lines.append(f'{sid} = RemoteA2aAgent(name="{sid}", agent_card="https://agentnexus/a2a/{st.get("remote_agent")}'
                         '/.well-known/agent-card.json")')
        elif agent in ("retriever", "executor"):
            tool = st.get("tool") or ""
            if st.get("kb"):
                warn.append(f"{st['id']}: RAG 지식 베이스 {st['kb']} 는 ADK 도구로 노출되지 않습니다 (게이트웨이 API).")
            lines.append(f'{sid} = LlmAgent(name="{sid}", model="auto", instruction="{agent} — {tool or st.get("kb", "")}",')
            lines.append(f'              tools=[McpToolset(connection_params=StreamableHTTPConnectionParams(url=GATEWAY), '
                         f'tool_filter=["{tool.replace(".", "_", 1)}"])])')
        else:
            model = ((g["workers"].get("summarize") or {}).get("model")) or "auto"
            lines.append(f'{sid} = LlmAgent(name="{sid}", model="{model}", instruction="근거를 [n] 으로 인용해 요약")')
    seq = "ParallelAgent" if g["fanout"].get("parallel") and not any(
        (st.get("depends_on") for st in g["default_plan"] if isinstance(st, dict))) else "SequentialAgent"
    if g["fanout"].get("join") not in ("all",):
        warn.append(f"합류 조건 {g['fanout']['join']} 는 ADK 기본 에이전트에 없어 SequentialAgent 로 근사합니다.")
    lines += ["", f'root_agent = {seq}(name="{ident}", sub_agents=[{", ".join(names)}])']
    return "\n".join(lines) + "\n", warn
