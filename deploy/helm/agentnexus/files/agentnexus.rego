# AgentNexus 게이트웨이 정책 (OPA) — PRD 9.2 예시를 POLICY_ENGINE=opa 입력 형식에 맞춘 기준 정책.
# input: {kind, target, model|tool|agent, data_class, args, agent, attrs{category,risk}, subject{sub,kind,roles,groups,team}}
# 결과: data.agentnexus.gateway → {allow, require_approval, reason, reroute}
package agentnexus.gateway

import rego.v1

default allow := false

local_models := {"domain-local", "vllm/domain-7b", "ollama/qwen"}

# 내부·기밀 데이터는 로컬(도메인 특화) 모델만
allow if {
	input.kind == "model"
	input.data_class in {"internal", "confidential"}
	input.model in local_models
}

# 공개 데이터는 모든 모델 허용
allow if {
	input.kind == "model"
	input.data_class == "public"
}

reroute := "domain-local" if {
	input.kind == "model"
	input.data_class in {"internal", "confidential"}
	not input.model in local_models
}

# 도구: 에이전트 허용목록 + 고위험 아님
allow if {
	input.kind == "tool"
	input.tool in data.agents[input.agent].allowed_tools
	not high_risk
}

# 고위험 도구는 승인 조건부 허용
allow if {
	input.kind == "tool"
	input.tool in data.agents[input.agent].allowed_tools
	high_risk
}

require_approval if {
	input.kind == "tool"
	input.tool in {"confluence.create_page", "jira.transition_issue"}
}

require_approval if {
	input.kind == "tool"
	input.attrs.risk == "high"
}

high_risk if require_approval

# 원격 에이전트: 차단 등급 제외
allow if {
	input.kind == "agent"
	input.attrs.trust_tier in {"verified", "trusted"}
}

reason := "allow" if allow

reason := "deny: no matching rule" if not allow
