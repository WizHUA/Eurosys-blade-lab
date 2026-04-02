"""agent/audit.py — Audit Agent (audit_graph).

三节点 StateGraph: GATE_THINK → GATE_ACT → GATE_OBSERVE.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from agent.config import AgentConfig
from agent.llm_client import LLMClient, _extract_json_from_text
from agent.prompt_utils import render_prompt
from agent.schema import (
    AuditDecision,
    AuditEvidence,
    AuditState,
    AuditStep,
    ConclusionProposal,
    FocusContext,
    OrchestratorState,
    ToolCall,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


# =====================================================================
# Helper: format tool descriptions for audit (limited tools)
# =====================================================================

def _format_audit_tool_descriptions(tools: dict) -> str:
    """Format only MetricQueryTool and KBRetrievalTool descriptions."""
    allowed = {"MetricQueryTool", "KBRetrievalTool"}
    parts = []
    for name, tool in tools.items():
        if name not in allowed:
            continue
        schema = tool.get_schema()
        params = schema.get("parameters", {}).get("properties", {})
        param_lines = []
        for pname, pinfo in params.items():
            ptype = pinfo.get("type", "any")
            pdesc = pinfo.get("description", "")
            param_lines.append(f"  - {pname} ({ptype}): {pdesc}")
        parts.append(f"### {name}\n{schema.get('description', '')}\n参数:\n" + "\n".join(param_lines))
    return "\n\n".join(parts)


# =====================================================================
# Helper: format data for audit prompt
# =====================================================================

def _format_proposed_root_causes(proposal: ConclusionProposal) -> str:
    lines = []
    for rc in proposal.proposed_root_causes:
        lines.append(
            f"- {rc.fault_type}: {rc.cause} (confidence={rc.confidence:.2f}, "
            f"evidence={rc.evidence_ids})"
        )
    return "\n".join(lines) if lines else "无确认根因"


def _format_evidence_for_audit(proposal: ConclusionProposal) -> str:
    lines = []
    for e in proposal.evidence:
        lines.append(
            f"- [{e.id}] ({e.type}) via {e.source_tool}: {e.result_digest}"
        )
    return "\n".join(lines) if lines else "无证据"


def _format_audit_evidence(audit_evidence: list[AuditEvidence]) -> str:
    if not audit_evidence:
        return ""
    lines = ["## 已收集的审查证据"]
    for ae in audit_evidence:
        lines.append(
            f"- [{ae.id}] via {ae.source_tool}: {ae.result_digest} (purpose: {ae.purpose})"
        )
    return "\n".join(lines)


# =====================================================================
# Audit state builder (information isolation)
# =====================================================================

def build_audit_state(
    orchestrator_state: OrchestratorState,
    config: AgentConfig,
) -> AuditState:
    """Build AuditState from OrchestratorState with information isolation."""
    # Extract previous hint if available
    prev_hint = None
    prev_decision = orchestrator_state.get("audit_decision")
    if prev_decision and prev_decision.decision == "continue":
        prev_hint = prev_decision.hint

    return AuditState(
        run_id=orchestrator_state["run_id"],
        focus_context=orchestrator_state["focus_context"],
        proposal=orchestrator_state["current_proposal"],
        audit_evidence=[],
        audit_trace=[],
        audit_budget={
            "tool_calls_used": 0,
            "tool_calls_limit": config.budget.audit_tool_calls_limit,
            "max_rounds": config.budget.audit_max_rounds,
        },
        previous_hint=prev_hint,
    )


# =====================================================================
# Validate audit decision
# =====================================================================

def _validate_audit_decision(decision: AuditDecision) -> AuditDecision:
    """Validate and normalize audit decision."""
    if decision.decision == "continue" and not decision.hint:
        # Force a default hint if missing
        decision = decision.model_copy(update={
            "hint": "请补充更多证据以验证诊断结论。"
        })
    if decision.decision != "continue":
        decision = decision.model_copy(update={"hint": None})
    return decision


# =====================================================================
# GATE_THINK node
# =====================================================================

def gate_think_node(
    state: AuditState,
    llm_client: LLMClient,
    tools: dict,
    config: AgentConfig,
) -> dict:
    """Audit Agent core: independently review ConclusionProposal."""
    proposal = state["proposal"]
    fc = state["focus_context"]
    audit_evidence = list(state.get("audit_evidence", []))
    audit_trace = list(state.get("audit_trace", []))
    audit_budget = dict(state.get("audit_budget", {}))

    # Budget check
    force_decision = (
        audit_budget.get("tool_calls_used", 0) >= audit_budget.get("tool_calls_limit", 3)
        or len(audit_trace) >= audit_budget.get("max_rounds", 2)
    )

    # Build prompt
    previous_hint_section = ""
    if state.get("previous_hint"):
        previous_hint_section = f"## 上一轮审查反馈\n{state['previous_hint']}"

    audit_evidence_section = _format_audit_evidence(audit_evidence)

    top_metrics_brief = ", ".join(
        f"{tm.metric}({tm.direction}{tm.score:.1f})" for tm in fc.top_metrics[:5]
    )

    variables = {
        "diagnosis_type": proposal.proposed_diagnosis_type,
        "proposed_root_causes": _format_proposed_root_causes(proposal),
        "evidence_formatted": _format_evidence_for_audit(proposal),
        "leading_subsystem": fc.leading_subsystem,
        "top_metrics_brief": top_metrics_brief,
        "causal_order": " → ".join(fc.causal_order) if fc.causal_order else "未确定",
        "previous_hint_section": previous_hint_section,
        "audit_evidence_section": audit_evidence_section,
        "audit_tool_calls_limit": str(audit_budget.get("tool_calls_limit", 3)),
        "tool_descriptions": _format_audit_tool_descriptions(tools),
    }
    prompt = render_prompt(PROMPTS_DIR / "audit_system.md", variables)

    if force_decision:
        prompt += (
            "\n\n**[系统强制指令]** 审查预算已耗尽。"
            "你必须在本轮输出中将 action_type 设为 \"decision\"。"
        )

    messages = [
        {"role": "system", "content": "你是 HPC 故障诊断证据审查员。"},
        {"role": "user", "content": prompt},
    ]
    content, usage = llm_client.call(messages, temperature=0.2)
    llm_client.tracker.record("audit_think", usage)

    # Parse response
    try:
        json_str = _extract_json_from_text(content)
        data = json.loads(json_str)
    except Exception as e:
        logger.error("Failed to parse GATE_THINK response: %s", e)
        data = {
            "thought": "Parse error fallback",
            "action_type": "decision",
            "decision": {
                "decision": "pass",
                "reason": "Parse error, defaulting to pass",
            },
        }

    thought = data.get("thought", "")
    action_type = data.get("action_type", "decision")

    # Force to decision if budget exhausted
    if force_decision and action_type != "decision":
        action_type = "decision"
        data["decision"] = data.get("decision", {
            "decision": "pass",
            "reason": "Budget exhausted, defaulting to pass",
        })

    # Build AuditStep
    step_id = len(audit_trace) + 1
    step_tc = None
    if action_type == "gate_tool_call" and "tool_call" in data:
        tc_data = data["tool_call"]
        # Validate: only MetricQueryTool and KBRetrievalTool allowed
        tool_name = tc_data.get("tool", "")
        if tool_name not in ("MetricQueryTool", "KBRetrievalTool"):
            logger.warning("Audit tried to use forbidden tool %s, forcing decision", tool_name)
            action_type = "decision"
            data["decision"] = {
                "decision": "pass",
                "reason": f"Attempted to use forbidden tool {tool_name}",
            }
        else:
            step_tc = ToolCall(
                tool=tool_name,
                args=tc_data.get("args", {}),
                call_id=f"ac{step_id}",
            )

    audit_step = AuditStep(
        step_id=step_id,
        thought=thought,
        action_type=action_type,
        tool_call=step_tc,
        timestamp=datetime.now(),
        llm_raw_response=content,
    )
    new_trace = audit_trace + [audit_step]

    result: dict[str, Any] = {"audit_trace": new_trace}

    # If decision, parse and validate AuditDecision
    if action_type == "decision":
        dec_data = data.get("decision", {})
        try:
            decision = AuditDecision(
                decision=dec_data.get("decision", "pass"),
                reason=dec_data.get("reason", "No reason provided"),
                hint=dec_data.get("hint"),
                diagnosis_type=dec_data.get("diagnosis_type"),
            )
        except Exception as e:
            logger.error("Failed to parse AuditDecision: %s", e)
            decision = AuditDecision(
                decision="pass",
                reason=f"Parse error: {e}",
            )
        decision = _validate_audit_decision(decision)
        result["audit_decision"] = decision

    return result


# =====================================================================
# GATE_ACT node
# =====================================================================

def gate_act_node(
    state: AuditState,
    tools: dict,
    config: AgentConfig,
) -> dict:
    """Execute restricted tool call (only MetricQueryTool, KBRetrievalTool)."""
    audit_trace = state.get("audit_trace", [])
    audit_budget = dict(state.get("audit_budget", {}))

    if not audit_trace:
        return {"audit_budget": audit_budget, "_audit_tool_result": {"error": "No audit trace"}}

    latest = audit_trace[-1]
    if not latest.tool_call:
        return {"audit_budget": audit_budget, "_audit_tool_result": {"error": "No tool call"}}

    tc = latest.tool_call
    # Only allow MetricQueryTool and KBRetrievalTool
    if tc.tool not in ("MetricQueryTool", "KBRetrievalTool"):
        result = {"error": f"Tool {tc.tool} not allowed in audit"}
    else:
        tool = tools.get(tc.tool)
        if not tool:
            result = {"error": f"Tool {tc.tool} not available"}
        else:
            try:
                result = tool.execute(tc.args)
            except Exception as e:
                result = {"error": str(e)}

    audit_budget["tool_calls_used"] = audit_budget.get("tool_calls_used", 0) + 1

    return {"audit_budget": audit_budget, "_audit_tool_result": result}


# =====================================================================
# GATE_OBSERVE node
# =====================================================================

def gate_observe_node(state: AuditState) -> dict:
    """Generate AuditEvidence from tool result."""
    audit_trace = list(state.get("audit_trace", []))
    audit_evidence = list(state.get("audit_evidence", []))
    tool_result = state.get("_audit_tool_result", {})

    if not audit_trace:
        return {"audit_evidence": audit_evidence}

    latest = audit_trace[-1]
    if not latest.tool_call:
        return {"audit_evidence": audit_evidence}

    tc = latest.tool_call

    # Generate digest
    if "error" in tool_result:
        digest = f"Error: {tool_result['error']}"
    elif tc.tool == "MetricQueryTool":
        items = tool_result.get("results", [])
        parts = [f"{r.get('metric', '?')}: {r.get('aggregation', '?')}={r.get('value', '?')}" for r in items]
        digest = "; ".join(parts) if parts else "No results"
    elif tc.tool == "KBRetrievalTool":
        entries = tool_result.get("metric_entries", [])
        hits = tool_result.get("pattern_hits", [])
        if entries:
            parts = [f"{e.get('name', '?')}: {e.get('subsystem', '?')}" for e in entries]
            digest = "; ".join(parts)
        elif hits:
            parts = [f"{h.get('pattern_id', '?')}: {h.get('fault_type', '?')}" for h in hits]
            digest = "; ".join(parts)
        else:
            digest = "No KB results"
    else:
        digest = str(tool_result)[:200]

    # Build AuditEvidence
    ae_id = f"ae{len(audit_evidence) + 1}"
    ae = AuditEvidence(
        id=ae_id,
        source_tool=tc.tool,
        query_summary=f"{tc.tool}({json.dumps(tc.args, ensure_ascii=False)[:80]})",
        result_digest=digest,
        raw_stats={k: v for k, v in tool_result.items() if k not in ("error",)} if "error" not in tool_result else {},
        target_hypothesis_ids=[],
        purpose=latest.thought[:100] if latest.thought else "Audit verification",
    )

    # Update step observation
    latest = latest.model_copy(update={
        "observation": digest,
        "audit_evidence_generated": ae_id,
    })
    audit_trace[-1] = latest

    return {
        "audit_evidence": audit_evidence + [ae],
        "audit_trace": audit_trace,
    }


# =====================================================================
# Router
# =====================================================================

def gate_think_router(state: AuditState) -> str:
    """Route based on latest AuditStep action_type."""
    trace = state.get("audit_trace", [])
    if not trace:
        return "decision"
    latest = trace[-1]
    return latest.action_type


# =====================================================================
# Graph builder
# =====================================================================

def build_audit_graph(
    tools: dict,
    llm_client: LLMClient,
    config: AgentConfig,
):
    """Build and compile the Audit StateGraph."""
    graph = StateGraph(AuditState)

    def _gate_think(state: AuditState) -> dict:
        return gate_think_node(state, llm_client, tools, config)

    def _gate_act(state: AuditState) -> dict:
        return gate_act_node(state, tools, config)

    def _gate_observe(state: AuditState) -> dict:
        return gate_observe_node(state)

    graph.add_node("GATE_THINK", _gate_think)
    graph.add_node("GATE_ACT", _gate_act)
    graph.add_node("GATE_OBSERVE", _gate_observe)

    graph.set_entry_point("GATE_THINK")
    graph.add_conditional_edges("GATE_THINK", gate_think_router, {
        "gate_tool_call": "GATE_ACT",
        "decision": END,
    })
    graph.add_edge("GATE_ACT", "GATE_OBSERVE")
    graph.add_edge("GATE_OBSERVE", "GATE_THINK")

    return graph.compile()
