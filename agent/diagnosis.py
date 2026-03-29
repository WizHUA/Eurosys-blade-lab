"""agent/diagnosis.py — Diagnosis Agent (diagnosis_graph).

四节点 StateGraph: HYPOTHESIZE → THINK → ACT → OBSERVE.
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
    ConclusionProposal,
    DiagnosisState,
    Evidence,
    Hypothesis,
    ProposedRootCause,
    ReActStep,
    ThinkAction,
    ThinkOutput,
    ToolCall,
    VerificationItem,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


# =====================================================================
# Helper: format tools for prompt injection
# =====================================================================

def format_tool_descriptions(tools: dict) -> str:
    """Generate unified tool descriptions for system prompt."""
    parts = []
    for name, tool in tools.items():
        schema = tool.get_schema()
        params = schema.get("parameters", {}).get("properties", {})
        param_lines = []
        for pname, pinfo in params.items():
            ptype = pinfo.get("type", "any")
            pdesc = pinfo.get("description", "")
            req = "required" if pname in schema.get("parameters", {}).get("required", []) else "optional"
            param_lines.append(f"  - {pname} ({ptype}, {req}): {pdesc}")
        parts.append(f"### {name}\n{schema.get('description', '')}\n参数:\n" + "\n".join(param_lines))
    return "\n\n".join(parts)


# =====================================================================
# Helper: ThinkOutput schema for prompt
# =====================================================================

_THINK_OUTPUT_SCHEMA = json.dumps({
    "thought": "string — 推理过程",
    "hypothesis_updates": [
        {"hypothesis_id": "string", "new_confidence": "float 0-1", "reason": "string"}
    ],
    "action": {
        "type": "tool_call | conclude",
        "tool": "工具名 (tool_call时必填)",
        "args": {},
        "reasoning": "string",
    },
}, indent=2, ensure_ascii=False)


# =====================================================================
# HYPOTHESIZE node
# =====================================================================

def hypothesize_node(
    state: DiagnosisState,
    llm_client: LLMClient,
    tools: dict,
    config: AgentConfig,
) -> dict:
    """Generate 2-4 candidate hypotheses from FocusContext + FPL hits."""
    fc = state["focus_context"]
    existing_hypotheses = list(state.get("hypotheses", []))
    rehyp_count = state.get("rehyp_count", 0)

    # 1. Query FPL for pattern matches
    fpl_hits_formatted = "无知识库命中"
    kb_tool = tools.get("KBRetrievalTool")
    if kb_tool:
        try:
            anomaly_metrics = [tm.metric for tm in fc.top_metrics]
            fpl_result = kb_tool.execute({
                "mode": "pattern_match",
                "subsystem": fc.leading_subsystem,
                "anomaly_metrics": anomaly_metrics,
            })
            hits = fpl_result.get("pattern_hits", [])
            if hits:
                lines = []
                for h in hits:
                    lines.append(
                        f"- {h.get('pattern_id', '?')}: {h.get('fault_type', '?')} "
                        f"(confidence={h.get('confidence', 0):.2f}, match_score={h.get('match_score', 0):.2f})"
                    )
                fpl_hits_formatted = "\n".join(lines)
        except Exception as e:
            logger.warning("FPL lookup failed: %s", e)

    # 2. Format top_metrics
    top_lines = []
    for tm in fc.top_metrics[:10]:
        top_lines.append(f"  - {tm.metric} ({tm.subsystem}): score={tm.score:.2f}, direction={tm.direction}")
    top_metrics_formatted = "\n".join(top_lines) if top_lines else "无异常指标"

    # 3. Rehyp section
    rehyp_section = ""
    if rehyp_count > 0:
        refuted = [h for h in existing_hypotheses if h.status == "refuted"]
        if refuted:
            ref_lines = [f"  - {h.id}: {h.fault_type} (confidence={h.current_confidence:.2f})" for h in refuted]
            rehyp_section = (
                "## 已驳回的假设（不要重复这些 fault_type）\n"
                + "\n".join(ref_lines)
            )
        gate_hint = state.get("gate_hint")
        if gate_hint:
            rehyp_section += f"\n\n## 审查反馈\n{gate_hint}"

    # 4. Render prompt
    variables = {
        "leading_subsystem": fc.leading_subsystem,
        "triage_confidence": f"{fc.triage_confidence:.2f}",
        "top_metrics_formatted": top_metrics_formatted,
        "causal_order": " → ".join(fc.causal_order) if fc.causal_order else "未确定",
        "subsystem_scores": json.dumps(fc.subsystem_scores, ensure_ascii=False),
        "fpl_hits_formatted": fpl_hits_formatted,
        "rehyp_section": rehyp_section,
    }
    prompt = render_prompt(PROMPTS_DIR / "diagnosis_hypothesize.md", variables)

    # 5. Call LLM
    messages = [
        {"role": "system", "content": "你是 HPC 故障诊断专家。"},
        {"role": "user", "content": prompt},
    ]
    content, usage = llm_client.call(messages, temperature=config.llm.temperature)
    llm_client.tracker.record("diagnosis_hypothesize", usage)

    # 6. Parse response
    try:
        json_str = _extract_json_from_text(content)
        raw_list = json.loads(json_str)
        if isinstance(raw_list, dict):
            raw_list = [raw_list]
    except Exception as e:
        logger.error("Failed to parse HYPOTHESIZE response: %s", e)
        raw_list = []

    # 7. Build Hypothesis objects
    # ID continuation: start from max existing ID + 1
    max_id = 0
    for h in existing_hypotheses:
        try:
            num = int(h.id.replace("h", ""))
            max_id = max(max_id, num)
        except ValueError:
            pass

    new_hypotheses = []
    for i, raw in enumerate(raw_list):
        hid = f"h{max_id + i + 1}"
        try:
            hyp = Hypothesis(
                id=raw.get("id", hid) if rehyp_count == 0 else hid,
                root_cause=raw.get("root_cause", "Unknown"),
                fault_type=raw.get("fault_type", "unknown"),
                subsystem=raw.get("subsystem", fc.leading_subsystem),
                prior_confidence=float(raw.get("prior_confidence", 0.3)),
                current_confidence=float(raw.get("prior_confidence", 0.3)),
                status="active",
                required_verifications=[
                    VerificationItem(**v) for v in raw.get("required_verifications", [])
                ],
                fpl_pattern_id=raw.get("fpl_pattern_id"),
            )
            # Boost if leading_subsystem matches
            if hyp.subsystem == fc.leading_subsystem:
                hyp.prior_confidence = min(1.0, hyp.prior_confidence + 0.10)
                hyp.current_confidence = hyp.prior_confidence
            new_hypotheses.append(hyp)
        except Exception as e:
            logger.warning("Skipping invalid hypothesis: %s", e)

    # 8. Merge with existing (refuted preserved)
    if rehyp_count > 0:
        all_hypotheses = [h for h in existing_hypotheses if h.status == "refuted"] + new_hypotheses
    else:
        all_hypotheses = new_hypotheses

    return {"hypotheses": all_hypotheses}


# =====================================================================
# THINK node
# =====================================================================

def _check_force_conclude(state: DiagnosisState, config: AgentConfig) -> bool:
    budget = state["budget"]
    return (
        budget.get("tool_calls_used", 0) >= budget.get("tool_calls_limit", config.budget.tool_calls_limit)
        or len(state.get("react_trace", [])) >= config.budget.max_react_iterations
    )


def _format_hypotheses(hypotheses: list[Hypothesis]) -> str:
    lines = []
    for h in hypotheses:
        lines.append(
            f"- [{h.id}] {h.fault_type} ({h.subsystem}): "
            f"confidence={h.current_confidence:.2f}, status={h.status}"
        )
        for v in h.required_verifications:
            lines.append(f"    ✓ {v.description}: {v.status}")
    return "\n".join(lines) if lines else "无假设"


def _format_evidence(evidence: list[Evidence]) -> str:
    lines = []
    for e in evidence:
        lines.append(
            f"- [{e.id}] ({e.type}) via {e.source_tool}: {e.result_digest}"
        )
    return "\n".join(lines) if lines else "无证据"


def _format_verification_status(hypotheses: list[Hypothesis]) -> str:
    lines = []
    for h in hypotheses:
        if h.status != "active":
            continue
        for v in h.required_verifications:
            lines.append(f"- [{h.id}] {v.description}: {v.status}")
    return "\n".join(lines) if lines else "无待验证项"


def think_node(
    state: DiagnosisState,
    llm_client: LLMClient,
    config: AgentConfig,
) -> dict:
    """ReAct think step: decide next action or conclude."""
    hypotheses = list(state.get("hypotheses", []))
    evidence = list(state.get("evidence", []))
    react_trace = list(state.get("react_trace", []))
    budget = dict(state.get("budget", {}))

    force_conclude = _check_force_conclude(state, config)

    # Build prompt
    force_section = ""
    if force_conclude:
        force_section = (
            "**[系统强制指令]** 你的工具调用预算已耗尽。"
            "你必须在本轮输出中将 action.type 设为 \"conclude\"。"
            "如果证据不足以确认任何假设，请输出 diagnosis_type = \"partial\" 或 \"inconclusive\"。"
        )

    gate_hint_section = ""
    gate_hint = state.get("gate_hint")
    if gate_hint:
        gate_hint_section = f"## 审查反馈\n审查员指出: {gate_hint}\n请优先响应此反馈。"

    variables = {
        "step_number": str(len(react_trace) + 1),
        "max_react_iterations": str(config.budget.max_react_iterations),
        "tool_calls_used": str(budget.get("tool_calls_used", 0)),
        "tool_calls_limit": str(budget.get("tool_calls_limit", config.budget.tool_calls_limit)),
        "hypotheses_formatted": _format_hypotheses(hypotheses),
        "evidence_formatted": _format_evidence(evidence),
        "verification_status": _format_verification_status(hypotheses),
        "gate_hint_section": gate_hint_section,
        "force_conclude_section": force_section,
    }
    prompt = render_prompt(PROMPTS_DIR / "diagnosis_think.md", variables)

    # System prompt
    sys_prompt = render_prompt(PROMPTS_DIR / "diagnosis_system.md", {
        "tool_descriptions": "",  # Simplified for now
        "max_react_iterations": str(config.budget.max_react_iterations),
        "think_output_schema": _THINK_OUTPUT_SCHEMA,
    })

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt},
    ]
    content, usage = llm_client.call(messages, temperature=config.llm.temperature)
    llm_client.tracker.record("diagnosis_think", usage)

    # Parse ThinkOutput
    try:
        json_str = _extract_json_from_text(content)
        data = json.loads(json_str)
        think_output = ThinkOutput.model_validate(data)
    except Exception as e:
        logger.error("Failed to parse THINK output: %s, forcing conclude", e)
        think_output = ThinkOutput(
            thought="Parse error, forcing conclude",
            hypothesis_updates=[],
            action=ThinkAction(type="conclude", reasoning="Parse error fallback"),
        )

    # If force_conclude but LLM still outputs tool_call, override
    if force_conclude and think_output.action.type == "tool_call":
        logger.warning("Budget exhausted but LLM requested tool_call, overriding to conclude")
        think_output.action = ThinkAction(type="conclude", reasoning="Budget exhausted override")

    # Update hypothesis confidences
    for update in think_output.hypothesis_updates:
        for h in hypotheses:
            if h.id == update.hypothesis_id:
                h.current_confidence = update.new_confidence
                break

    # Build ReActStep
    step_id = len(react_trace) + 1
    tool_call = None
    if think_output.action.type == "tool_call" and think_output.action.tool:
        tool_call = ToolCall(
            tool=think_output.action.tool,
            args=think_output.action.args or {},
            call_id=f"c{step_id}",
        )

    step = ReActStep(
        step_id=step_id,
        thought=think_output.thought,
        action_type=think_output.action.type,
        tool_call=tool_call,
        timestamp=datetime.now(),
    )

    # If conclude, mark high-confidence hypotheses as confirmed
    if think_output.action.type == "conclude":
        for h in hypotheses:
            if h.status == "active" and h.current_confidence > 0.5:
                h.status = "confirmed"

    new_trace = react_trace + [step]
    return {"react_trace": new_trace, "hypotheses": hypotheses}


# =====================================================================
# ACT node
# =====================================================================

def act_node(
    state: DiagnosisState,
    tools: dict,
    config: AgentConfig,
) -> dict:
    """Execute tool call from latest ReActStep."""
    react_trace = state.get("react_trace", [])
    budget = dict(state.get("budget", {}))

    if not react_trace:
        return {"budget": budget, "_tool_result": {"error": "No react trace"}}

    latest_step = react_trace[-1]
    if not latest_step.tool_call:
        return {"budget": budget, "_tool_result": {"error": "No tool call in latest step"}}

    tc = latest_step.tool_call
    tool_name = tc.tool
    tool_args = tc.args

    # Execute tool
    tool = tools.get(tool_name)
    if not tool:
        result = {"error": f"Unknown tool: {tool_name}"}
    else:
        try:
            result = tool.execute(tool_args)
        except Exception as e:
            logger.error("Tool %s execution failed: %s", tool_name, e)
            result = {"error": str(e)}

    # Increment budget
    budget["tool_calls_used"] = budget.get("tool_calls_used", 0) + 1

    return {"budget": budget, "_tool_result": result}


# =====================================================================
# OBSERVE node
# =====================================================================

def _generate_digest(tool_name: str, args: dict, result: dict) -> str:
    """Generate a concise result digest without LLM."""
    if "error" in result:
        return f"Error: {result['error']}"

    if tool_name == "MetricQueryTool":
        items = result.get("results", [])
        parts = []
        for r in items:
            parts.append(f"{r.get('metric', '?')}: {r.get('aggregation', '?')}={r.get('value', '?')}")
        return "; ".join(parts) if parts else "No metric results"

    elif tool_name == "KBRetrievalTool":
        entries = result.get("metric_entries", [])
        if entries:
            parts = []
            for e in entries:
                parts.append(
                    f"Metric KB: {e.get('name', '?')} belongs to {e.get('subsystem', '?')}"
                )
            return "; ".join(parts)
        hits = result.get("pattern_hits", [])
        if hits:
            parts = []
            for h in hits:
                parts.append(
                    f"FPL match: {h.get('pattern_id', '?')} ({h.get('fault_type', '?')}), "
                    f"confidence={h.get('confidence', 0):.2f}"
                )
            return "; ".join(parts)
        return "No KB results"

    elif tool_name == "DataAnalysisTool":
        summary = result.get("summary", "")
        if summary:
            return f"Analysis: {summary}"
        findings = result.get("findings", [])
        parts = [f"{f.get('statistic_name', '?')}={f.get('value', '?')}" for f in findings]
        return f"Analysis: {'; '.join(parts)}" if parts else "No analysis results"

    return str(result)[:200]


def _determine_evidence_type(
    tool_name: str,
    args: dict,
    result: dict,
    hypotheses: list[Hypothesis],
) -> str:
    """Heuristic evidence type determination."""
    if "error" in result:
        return "neutral"

    if tool_name == "MetricQueryTool":
        query_metrics = set(args.get("metrics", []))
        for h in hypotheses:
            if h.status != "active":
                continue
            # Check if queried metrics belong to hypothesis subsystem
            hyp_metrics = set()
            for v in h.required_verifications:
                hyp_metrics.update(v.required_metrics)
            if query_metrics & hyp_metrics:
                # Check value (simple heuristic: high values = supporting for + direction)
                results = result.get("results", [])
                for r in results:
                    val = r.get("value", 0)
                    if isinstance(val, (int, float)) and val > 50:
                        return "supporting"
                return "neutral"
        return "neutral"

    elif tool_name == "KBRetrievalTool":
        hits = result.get("pattern_hits", [])
        if hits:
            matched_types = {h.get("fault_type", "") for h in hits}
            hyp_types = {h.fault_type for h in hypotheses if h.status == "active"}
            if matched_types & hyp_types:
                return "supporting"
            return "mixed"
        return "neutral"

    elif tool_name == "DataAnalysisTool":
        findings = result.get("findings", [])
        for f in findings:
            name = f.get("statistic_name", "")
            val = f.get("value", 0)
            if name in ("pearson_r", "spearman_rho") and isinstance(val, (int, float)):
                if abs(val) > 0.7:
                    return "supporting"
                if abs(val) < 0.3:
                    return "refuting"
        return "neutral"

    return "neutral"


def _find_related_hypothesis_ids(
    tool_name: str,
    args: dict,
    hypotheses: list[Hypothesis],
) -> list[str]:
    """Determine which hypotheses are related to this tool call."""
    ids = []
    query_metrics = set(args.get("metrics", []))
    if not query_metrics:
        query_metrics = set()
        if "metric" in args:
            query_metrics.add(args["metric"])
        if "metric_a" in args:
            query_metrics.add(args["metric_a"])
        if "metric_b" in args:
            query_metrics.add(args["metric_b"])

    for h in hypotheses:
        if h.status not in ("active", "confirmed"):
            continue
        # Check subsystem match or metric overlap
        hyp_metrics = set()
        for v in h.required_verifications:
            hyp_metrics.update(v.required_metrics)
        if query_metrics & hyp_metrics:
            ids.append(h.id)
            continue
        # Fallback: subsystem-based association
        if tool_name == "KBRetrievalTool":
            subsystem = args.get("subsystem", "")
            if subsystem == h.subsystem:
                ids.append(h.id)

    # If no match, associate with all active hypotheses
    if not ids:
        ids = [h.id for h in hypotheses if h.status in ("active", "confirmed")]

    return ids


def observe_node(state: DiagnosisState) -> dict:
    """Compress tool output into Evidence."""
    react_trace = list(state.get("react_trace", []))
    hypotheses = list(state.get("hypotheses", []))
    evidence = list(state.get("evidence", []))
    tool_result = state.get("_tool_result", {})

    if not react_trace:
        return {"evidence": evidence}

    latest_step = react_trace[-1]
    if not latest_step.tool_call:
        return {"evidence": evidence}

    tc = latest_step.tool_call
    tool_name = tc.tool
    tool_args = tc.args

    # Generate digest
    digest = _generate_digest(tool_name, tool_args, tool_result)

    # Determine evidence type
    ev_type = _determine_evidence_type(tool_name, tool_args, tool_result, hypotheses)

    # Find related hypothesis IDs
    hyp_ids = _find_related_hypothesis_ids(tool_name, tool_args, hypotheses)

    # Extract raw stats
    raw_stats: dict[str, Any] = {}
    if tool_name == "MetricQueryTool":
        for r in tool_result.get("results", []):
            raw_stats[r.get("metric", "?")] = r.get("value")
    elif tool_name == "DataAnalysisTool":
        for f in tool_result.get("findings", []):
            raw_stats[f.get("statistic_name", "?")] = f.get("value")

    # Build Evidence
    eid = f"e{len(evidence) + 1}"
    new_evidence = Evidence(
        id=eid,
        hypothesis_ids=hyp_ids,
        type=ev_type,
        source_tool=tool_name,
        query_summary=f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})",
        result_digest=digest,
        raw_stats=raw_stats,
        created_at_step=latest_step.step_id,
    )

    # Update observation in latest step
    latest_step = latest_step.model_copy(update={"observation": digest, "evidence_generated": eid})
    react_trace[-1] = latest_step

    # Update verification items
    for h in hypotheses:
        if h.status != "active":
            continue
        for v in h.required_verifications:
            if v.status != "pending":
                continue
            query_metrics = set()
            if "metrics" in tool_args:
                query_metrics = set(tool_args["metrics"])
            elif "metric" in tool_args:
                query_metrics = {tool_args["metric"]}
            if set(v.required_metrics) & query_metrics:
                if ev_type == "supporting":
                    v.status = "verified"
                elif ev_type == "refuting":
                    v.status = "refuted"
                v.evidence_id = eid

    all_evidence = evidence + [new_evidence]
    return {"evidence": all_evidence, "hypotheses": hypotheses, "react_trace": react_trace}


# =====================================================================
# ConclusionProposal builder
# =====================================================================

def _build_conclusion_proposal(state: DiagnosisState) -> ConclusionProposal:
    """Build ConclusionProposal from final diagnosis state."""
    hypotheses = state.get("hypotheses", [])
    evidence = state.get("evidence", [])

    confirmed = [h for h in hypotheses if h.status == "confirmed"]
    active_high = [h for h in hypotheses if h.status == "active" and h.current_confidence > 0.5]

    candidates = confirmed + active_high

    # Determine diagnosis type
    if len(confirmed) == 1 and not active_high:
        dtype = "single_fault"
    elif len(confirmed) >= 2:
        dtype = "composite_fault"
    elif confirmed:
        dtype = "partial"
    else:
        dtype = "partial"

    # Build proposed_root_causes (sorted by confidence)
    root_causes = []
    for h in sorted(candidates, key=lambda x: x.current_confidence, reverse=True):
        rc_evidence = [e.id for e in evidence if h.id in e.hypothesis_ids]
        root_causes.append(ProposedRootCause(
            cause=h.root_cause,
            fault_type=h.fault_type,
            confidence=h.current_confidence,
            evidence_ids=rc_evidence,
        ))

    # Filter evidence to only those related to candidates
    candidate_ids = {h.id for h in candidates}
    relevant_evidence = [e for e in evidence if set(e.hypothesis_ids) & candidate_ids]

    return ConclusionProposal(
        hypotheses=hypotheses,
        evidence=relevant_evidence if relevant_evidence else evidence,
        proposed_diagnosis_type=dtype,
        proposed_root_causes=root_causes,
    )


# =====================================================================
# Router
# =====================================================================

def think_router(state: DiagnosisState) -> str:
    """Route based on latest ReActStep action_type."""
    trace = state.get("react_trace", [])
    if not trace:
        return "conclude"
    return trace[-1].action_type


# =====================================================================
# Graph builder
# =====================================================================

def build_diagnosis_graph(
    tools: dict,
    llm_client: LLMClient,
    config: AgentConfig,
):
    """Build and compile the Diagnosis StateGraph."""
    graph = StateGraph(DiagnosisState)

    # Wrap nodes to inject dependencies
    def _hypothesize(state: DiagnosisState) -> dict:
        return hypothesize_node(state, llm_client, tools, config)

    def _think(state: DiagnosisState) -> dict:
        return think_node(state, llm_client, config)

    def _act(state: DiagnosisState) -> dict:
        return act_node(state, tools, config)

    def _observe(state: DiagnosisState) -> dict:
        return observe_node(state)

    graph.add_node("HYPOTHESIZE", _hypothesize)
    graph.add_node("THINK", _think)
    graph.add_node("ACT", _act)
    graph.add_node("OBSERVE", _observe)

    graph.set_entry_point("HYPOTHESIZE")
    graph.add_edge("HYPOTHESIZE", "THINK")
    graph.add_conditional_edges("THINK", think_router, {
        "tool_call": "ACT",
        "conclude": END,
    })
    graph.add_edge("ACT", "OBSERVE")
    graph.add_edge("OBSERVE", "THINK")

    return graph.compile()
