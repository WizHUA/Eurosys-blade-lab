"""agent/diagnosis.py — Diagnosis Agent (diagnosis_graph).

四节点 StateGraph: HYPOTHESIZE → THINK → ACT → OBSERVE.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from langgraph.graph import END, StateGraph

from agent.config import AgentConfig
from agent.llm_client import LLMClient, _extract_json_from_text
from agent.prompt_utils import render_prompt
from agent.triage import SUBSYSTEM_PREFIX_MAP, SUBSYSTEM_GROUP
from agent.schema import (
    ConclusionProposal,
    DiagnosisState,
    Evidence,
    FocusContext,
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

def _format_available_metrics(tools: dict | None) -> str:
    """Generate a grouped list of available metric column names from MetricQueryTool."""
    if not tools:
        return "(工具不可用)"
    metric_tool = tools.get("MetricQueryTool")
    if metric_tool is None:
        return "(MetricQueryTool 不可用)"
    df = getattr(metric_tool, "df", None)
    if df is None:
        return "(无可用指标)"
    columns = list(df.columns)
    if not columns:
        return "(无可用指标)"
    # Group by prefix (first word before _)
    groups: dict[str, list[str]] = {}
    for col in columns:
        prefix = col.split("_")[0] if "_" in col else col
        groups.setdefault(prefix, []).append(col)
    parts = []
    for prefix in sorted(groups.keys()):
        cols = groups[prefix]
        parts.append(f"- **{prefix}**: {', '.join(sorted(cols))}")
    return "\n".join(parts)



def _format_available_metrics(tools: dict | None) -> str:
    """Generate a grouped list of available metric column names from MetricQueryTool."""
    if not tools:
        return "(工具不可用)"
    metric_tool = tools.get("MetricQueryTool")
    if metric_tool is None:
        return "(MetricQueryTool 不可用)"
    columns = list(getattr(metric_tool, "df", pd.DataFrame()).columns)
    if not columns:
        return "(无可用指标)"
    # Group by prefix (first word before _)
    groups: dict[str, list[str]] = {}
    for col in columns:
        prefix = col.split("_")[0] if "_" in col else col
        groups.setdefault(prefix, []).append(col)
    parts = []
    for prefix in sorted(groups.keys()):
        cols = groups[prefix]
        parts.append(f"- **{prefix}**: {', '.join(sorted(cols))}")
    return "\n".join(parts)


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


_VALID_TOOLS = {"MetricQueryTool", "KBRetrievalTool", "DataAnalysisTool"}

_TOOL_ALIASES: dict[str, str] = {
    # MetricQueryTool aliases
    "metricquery": "MetricQueryTool",
    "metricquerytool": "MetricQueryTool",
    "metric_query": "MetricQueryTool",
    "query_metric": "MetricQueryTool",
    "check_cpu": "MetricQueryTool",
    "check_memory": "MetricQueryTool",
    "check_metric": "MetricQueryTool",
    "check_metrics": "MetricQueryTool",
    "check_cpu_usage": "MetricQueryTool",
    "check_memory_usage": "MetricQueryTool",
    "check_network": "MetricQueryTool",
    "check_disk": "MetricQueryTool",
    "query_metrics": "MetricQueryTool",
    "get_metrics": "MetricQueryTool",
    # KBRetrievalTool aliases
    "kbretrieval": "KBRetrievalTool",
    "kbretrievaltool": "KBRetrievalTool",
    "kb_retrieval": "KBRetrievalTool",
    "retrieve_kb": "KBRetrievalTool",
    "knowledge_retrieval": "KBRetrievalTool",
    "search_kb": "KBRetrievalTool",
    "fault_pattern": "KBRetrievalTool",
    "search_knowledge": "KBRetrievalTool",
    # DataAnalysisTool aliases
    "dataanalysis": "DataAnalysisTool",
    "dataanalysistool": "DataAnalysisTool",
    "data_analysis": "DataAnalysisTool",
    "analyze_data": "DataAnalysisTool",
    "statistical_analysis": "DataAnalysisTool",
    "anomaly_detection": "DataAnalysisTool",
    "detect_anomaly": "DataAnalysisTool",
    "analyze_metrics": "DataAnalysisTool",
}


def _normalize_tool_name(raw: str) -> str | None:
    """Normalize LLM-provided tool name to one of the three valid tool names.

    Returns None if the name cannot be mapped to any valid tool.
    """
    if raw in _VALID_TOOLS:
        return raw
    lower = raw.lower().replace("-", "_").replace(" ", "_")
    if lower in _TOOL_ALIASES:
        return _TOOL_ALIASES[lower]
    # Keyword-based fallback
    if any(kw in lower for kw in ("metric", "cpu", "mem", "network", "disk", "query", "check")):
        return "MetricQueryTool"
    if any(kw in lower for kw in ("kb", "knowledge", "pattern", "fault", "retrieve", "retriev")):
        return "KBRetrievalTool"
    if any(kw in lower for kw in ("analysis", "analys", "statistic", "anomal", "detect")):
        return "DataAnalysisTool"
    return None


# =====================================================================
# HYPOTHESIZE node
# =====================================================================

def _normalize_verification_item(v: dict) -> dict:
    """Normalize verification item dict — handle LLM field name variations.

    LLMs sometimes output 'metrics' instead of 'required_metrics'.
    Only keep fields that VerificationItem accepts; extra fields are dropped.
    """
    v = dict(v)
    # Rename 'metrics' → 'required_metrics' if needed
    if "metrics" in v and "required_metrics" not in v:
        v["required_metrics"] = v.pop("metrics")
    # Keep only known fields to avoid Pydantic validation noise
    known = {"description", "status", "evidence_id", "required_metrics"}
    return {k: val for k, val in v.items() if k in known}

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
    # When triage confidence is low, query ALL competing subsystems to avoid bias
    low_conf_threshold = config.triage.low_confidence_threshold
    fpl_hits_formatted = "无知识库命中"
    kb_tool = tools.get("KBRetrievalTool")
    if kb_tool:
        try:
            anomaly_metrics = [tm.metric for tm in fc.top_metrics]
            if fc.triage_confidence >= low_conf_threshold:
                fpl_subsystems = [fc.leading_subsystem]
            else:
                # Low confidence: query all 4 competing subsystems
                fpl_subsystems = ["cpu", "memory", "network", "disk"]
            all_hits: list[dict] = []
            for sub in fpl_subsystems:
                fpl_result = kb_tool.execute({
                    "mode": "pattern_match",
                    "subsystem": sub,
                    "anomaly_metrics": anomaly_metrics,
                })
                all_hits.extend(fpl_result.get("pattern_hits", []))
            # Deduplicate by fault_type, keep highest match_score
            seen: dict[str, dict] = {}
            for h in all_hits:
                ft = h.get("fault_type", "?")
                if ft not in seen or h.get("match_score", 0) > seen[ft].get("match_score", 0):
                    seen[ft] = h
            hits = sorted(seen.values(), key=lambda x: x.get("match_score", 0), reverse=True)
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

    # 2. Format top_metrics with explicit direction labels
    top_lines = []
    for tm in fc.top_metrics[:10]:
        dir_label = "↑高于基线" if tm.direction == "+" else "↓低于基线"
        top_lines.append(
            f"  - {tm.metric} ({tm.subsystem}): score={tm.score:.2f}, 异常方向={dir_label}"
        )
    top_metrics_formatted = "\n".join(top_lines) if top_lines else "无异常指标"

    # 2b. Generate explicit metric interpretation hints
    metric_hints_lines: list[str] = []
    fs_decreasing = [
        tm.metric for tm in fc.top_metrics
        if tm.metric in ("filesystem_avail_bytes", "filesystem_free_bytes") and tm.direction == "-"
    ]
    if fs_decreasing:
        metric_hints_lines.append(
            f"⚠️ 磁盘空间下降信号：{', '.join(fs_decreasing)} 正在持续减少（↓低于基线），"
            "这是磁盘空间持续减少的直接证据，请将磁盘空间不足列为核心候选假设之一。"
        )
    blocked_up = any(
        tm.metric == "processes_blocked" and tm.direction == "+"
        for tm in fc.top_metrics
    )
    if blocked_up:
        metric_hints_lines.append(
            "⚠️ I/O 阻塞信号：processes_blocked ↑高于基线，表明进程正阻塞在磁盘 I/O，"
            "这是磁盘 I/O 异常的典型继发症状。"
        )
    schedstat_down = any(
        tm.metric == "schedstat_running_rate" and tm.direction == "-"
        for tm in fc.top_metrics
    )
    if schedstat_down:
        metric_hints_lines.append(
            "⚠️ CPU 调度下降：schedstat_running_rate ↓低于基线，表明 CPU 在等待 I/O "
            "（进程处于阻塞状态而非运行状态）。这 **不是 CPU 计算过载** 的证据！"
        )
    iowait_up = any(
        tm.metric == "cpu_iowait_percent" and tm.direction == "+"
        for tm in fc.top_metrics
    )
    if iowait_up:
        metric_hints_lines.append(
            "⚠️ iowait 升高是磁盘 I/O 阻塞的继发症状，不应归因于 CPU 计算过载，"
            "请优先考虑磁盘 I/O 异常或磁盘空间不足。"
        )

    # cpu_iowait_percent ↓ = cpu is busy computing (CPU overload indicator)
    iowait_down = any(
        tm.metric == "cpu_iowait_percent" and tm.direction == "-"
        for tm in fc.top_metrics
    )
    if iowait_down:
        metric_hints_lines.append(
            "⚠️ iowait 下降：cpu_iowait_percent ↓低于基线，表明 CPU 忙于计算而非等待 I/O。"
            "这是 CPU 满载运行的典型特征（进程无需等待I/O）。"
        )

    # cpu_usage or load rising = CPU overload
    cpu_high = [
        tm.metric for tm in fc.top_metrics
        if tm.metric in ("cpu_usage_percent", "cpu_user_percent", "load_1min") and tm.direction == "+"
    ]
    if cpu_high:
        metric_hints_lines.append(
            f"⚠️ CPU 高负载信号：{', '.join(cpu_high)} ↑高于基线，"
            "这是 CPU 过载的直接证据。请将 CPU 过载列为高优先级假设。"
        )

    # memory pressure = mem_load
    mem_high = [
        tm.metric for tm in fc.top_metrics
        if tm.metric in ("memory_usage_percent", "anon_memory_percent", "memory_active_anon_bytes", "memory_sunreclaim_bytes") and tm.direction == "+"
    ]
    mem_low = [
        tm.metric for tm in fc.top_metrics
        if tm.metric in ("memory_available_bytes", "memory_inactive_file_bytes", "memory_inactive_anon_bytes") and tm.direction == "-"
    ]
    if mem_high or mem_low:
        signals = mem_high + mem_low
        metric_hints_lines.append(
            f"⚠️ 内存压力信号：{', '.join(signals)} 异常，"
            "这是内存资源紧张的典型特征。请将内存异常列为核心假设。"
        )

    # network drops/retransmissions = network anomaly
    net_drop = [
        tm.metric for tm in fc.top_metrics
        if tm.metric in ("network_transmit_drop_rate", "network_receive_drop_rate") and tm.direction == "+"
    ]
    net_retrans = [
        tm.metric for tm in fc.top_metrics
        if "retransmit" in tm.metric.lower() and tm.direction == "+"
    ]
    if net_drop or net_retrans:
        signals = net_drop + net_retrans
        metric_hints_lines.append(
            f"⚠️ 网络丢包/重传信号：{', '.join(signals)} ↑高于基线，"
            "这是网络通信异常的直接证据。"
        )

    metric_hints = "\n".join(metric_hints_lines) if metric_hints_lines else ""

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
    triage_warning = ""
    # No hardcoded fault type mapping - use subsystem name only
    if fc.triage_confidence < low_conf_threshold:
        triage_warning = (
            f"⚠️ Triage 置信度较低（{fc.triage_confidence:.2f} < {low_conf_threshold}），"
            "Leading Subsystem 仅供参考，不一定准确。"
            "请基于 **所有异常指标** 和子系统分数综合判断，"
            "务必生成覆盖多个不同子系统（cpu/memory/network/disk）的假设，不要只关注 Leading Subsystem。"
        )
    else:
        # Medium-high confidence: guide LLM but do not force
        triage_warning = (
            f"📌 Triage 置信度为 {fc.triage_confidence:.2f}，"
            f"统计分析显示 **{fc.leading_subsystem}** 子系统异常较为突出（供参考）。"
            f"请综合 Top 异常指标的实际内容独立判断——注意 disk I/O 升高可能是内存压力的**继发效应**，"
            f"需结合 memory/disk 指标的时序先后关系区分根因。"
        )
    variables = {
        "leading_subsystem": fc.leading_subsystem,
        "triage_confidence": f"{fc.triage_confidence:.2f}",
        "triage_warning": triage_warning,
        "top_metrics_formatted": top_metrics_formatted,
        "metric_hints": metric_hints,
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
                    VerificationItem(**_normalize_verification_item(v))
                    for v in raw.get("required_verifications", [])
                ],
                fpl_pattern_id=raw.get("fpl_pattern_id"),
            )
            # Small boost if leading_subsystem matches (reduced from 0.10 to 0.05)
            if hyp.subsystem == fc.leading_subsystem:
                hyp.prior_confidence = min(1.0, hyp.prior_confidence + 0.05)
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
    tools: dict | None = None,
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

    # Supply the actual anomaly window so LLM does not hallucinate timestamps
    focus_context = state.get("focus_context")
    if focus_context is not None:
        anomaly_window_start = focus_context.anomaly_window.start.isoformat()
        anomaly_window_end = focus_context.anomaly_window.end.isoformat()
    else:
        anomaly_window_start = "unknown"
        anomaly_window_end = "unknown"

    # Generate available metrics hint from tools
    available_metrics_hint = _format_available_metrics(tools)

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
        "anomaly_window_start": anomaly_window_start,
        "anomaly_window_end": anomaly_window_end,
        "available_metrics": available_metrics_hint,
    }
    prompt = render_prompt(PROMPTS_DIR / "diagnosis_think.md", variables)

    # System prompt
    sys_prompt = render_prompt(PROMPTS_DIR / "diagnosis_system.md", {
        "tool_descriptions": format_tool_descriptions(tools or {}),
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
        normalized_tool = _normalize_tool_name(think_output.action.tool)
        if normalized_tool is None:
            logger.warning(
                "LLM returned unknown tool name %r, converting to conclude",
                think_output.action.tool,
            )
            think_output.action = ThinkAction(
                type="conclude",
                reasoning=f"Unknown tool {think_output.action.tool!r}, forcing conclude",
            )
        else:
            tool_call = ToolCall(
                tool=normalized_tool,
                args=think_output.action.args or {},
                call_id=f"c{step_id}",
            )

    step = ReActStep(
        step_id=step_id,
        thought=think_output.thought,
        action_type=think_output.action.type,
        tool_call=tool_call,
        timestamp=datetime.now(),
        llm_raw_response=content,
    )

    # If conclude, mark high-confidence hypotheses as confirmed
    # When THINK concludes, hypotheses remain "active".
    # The confirmed status is set by Orchestrator after Audit Agent passes.
    # (v6 principle #3: zero hard-coded threshold for diagnostic conclusions)

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
    tool_args = _prepare_tool_args(state, tool_name, tc.args, tools.get(tool_name))

    # Execute tool
    tool = tools.get(tool_name)
    if not tool:
        result = {"error": f"Unknown tool: {tool_name}"}
    else:
        try:
            logger.debug("[ACT] %s args=%s", tool_name, tool_args)
            result = tool.execute(tool_args)
            logger.debug("[ACT] %s result=%s", tool_name, result)
        except Exception as e:
            logger.error("Tool %s execution failed: %s", tool_name, e)
            result = {"error": str(e)}

    # Increment budget
    budget["tool_calls_used"] = budget.get("tool_calls_used", 0) + 1

    return {"budget": budget, "_tool_result": result}


def _normalize_metric_names(raw_metrics: Any, available_columns: list[str]) -> list[str]:
    """Normalize LLM-provided metric names against dataframe columns."""
    if isinstance(raw_metrics, str):
        metrics = [raw_metrics]
    elif isinstance(raw_metrics, list):
        metrics = [metric for metric in raw_metrics if isinstance(metric, str)]
    else:
        metrics = []

    lower_map = {column.lower(): column for column in available_columns}
    compact_map = {
        column.lower().replace("-", "_").replace(" ", "_"): column
        for column in available_columns
    }

    normalized: list[str] = []
    for metric in metrics:
        if metric in available_columns:
            normalized.append(metric)
            continue
        lower = metric.lower()
        compact = lower.replace("-", "_").replace(" ", "_")
        if lower in lower_map:
            normalized.append(lower_map[lower])
        elif compact in compact_map:
            normalized.append(compact_map[compact])
        else:
            # Substring matching: LLM name is substring of column or vice versa
            for col_compact, col_original in compact_map.items():
                if compact in col_compact or col_compact in compact:
                    normalized.append(col_original)
                    break

    deduped: list[str] = []
    seen = set()
    for metric in normalized:
        if metric not in seen:
            seen.add(metric)
            deduped.append(metric)
    return deduped


def _fallback_metrics_for_query(state: DiagnosisState, available_columns: list[str]) -> list[str]:
    """Choose conservative fallback metrics when the LLM omits valid metric names."""
    hypotheses = [
        hypothesis for hypothesis in state.get("hypotheses", [])
        if hypothesis.status == "active"
    ]
    hypotheses.sort(key=lambda hypothesis: hypothesis.current_confidence, reverse=True)

    for hypothesis in hypotheses:
        for verification in hypothesis.required_verifications:
            if verification.status != "pending":
                continue
            # Use normalize to handle fuzzy matching of LLM-generated metric names
            metrics = _normalize_metric_names(verification.required_metrics, available_columns)
            if metrics:
                return metrics

    focus_context = state.get("focus_context")
    if focus_context is not None:
        fallback = [tm.metric for tm in focus_context.top_metrics if tm.metric in available_columns]
        if fallback:
            return fallback[:5]

    return []


def _default_time_window(state: DiagnosisState) -> dict[str, str] | None:
    focus_context = state.get("focus_context")
    if focus_context is None:
        return None
    return {
        "start": focus_context.anomaly_window.start.isoformat(),
        "end": focus_context.anomaly_window.end.isoformat(),
    }


def _prepare_tool_args(
    state: DiagnosisState,
    tool_name: str,
    args: dict[str, Any],
    tool: Any | None,
) -> dict[str, Any]:
    """Deterministically fill common missing tool-call arguments from state."""
    prepared = dict(args or {})

    if tool_name == "MetricQueryTool" and tool is not None:
        available_columns = list(getattr(tool, "df", []).columns)
        normalized_metrics = _normalize_metric_names(prepared.get("metrics", []), available_columns)
        if not normalized_metrics:
            normalized_metrics = _fallback_metrics_for_query(state, available_columns)
        prepared["metrics"] = normalized_metrics
        raw_agg = prepared.get("aggregation", "")
        if not raw_agg:
            prepared["aggregation"] = "mean"
        elif raw_agg in ("avg", "average"):
            prepared["aggregation"] = "mean"
        # Always override time_window with the known anomaly_window so the LLM
        # cannot accidentally use a wrong timestamp (e.g. 2023-10-01).
        default_window = _default_time_window(state)
        if default_window is not None:
            prepared["time_window"] = default_window

    if tool_name == "DataAnalysisTool":
        default_window = _default_time_window(state)
        if default_window is not None:
            prepared["time_window"] = default_window

    return prepared


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


def _infer_metric_subsystem(metric_name: str) -> str | None:
    """Infer the parent subsystem group for a metric name using prefix matching."""
    for prefix, subsystem in SUBSYSTEM_PREFIX_MAP.items():
        if metric_name.startswith(prefix) or metric_name.startswith(prefix.rstrip("_")):
            return subsystem
    return None


def _determine_evidence_type(
    tool_name: str,
    args: dict,
    result: dict,
    hypotheses: list[Hypothesis],
    focus_context: FocusContext | None = None,
) -> str:
    """Heuristic evidence type determination.

    Uses subsystem-level matching to handle metric name mismatches between
    what Triage flagged and what the LLM actually queries.
    """
    if "error" in result:
        return "neutral"

    if tool_name == "MetricQueryTool":
        query_metrics = set(args.get("metrics", []))
        has_data = bool(result.get("results", []))

        # Build normalized subsystem->metrics map from focus_context
        # Normalize raw subsystem names (swap->memory, filesystem->disk)
        normalized_anomaly_subs: dict[str, set[str]] = {}
        if focus_context:
            for tm in focus_context.top_metrics:
                norm_sub = SUBSYSTEM_GROUP.get(tm.subsystem, tm.subsystem)
                normalized_anomaly_subs.setdefault(norm_sub, set()).add(tm.metric)

        # Infer subsystems for each queried metric
        queried_subsystems: dict[str, str] = {}
        for qm in query_metrics:
            inferred = _infer_metric_subsystem(qm)
            if inferred:
                queried_subsystems[qm] = inferred

        for h in hypotheses:
            if h.status != "active":
                continue

            # Check 1: exact metric overlap with triage anomalous in same subsystem
            anomalous_for_hyp = normalized_anomaly_subs.get(h.subsystem, set())
            if query_metrics & anomalous_for_hyp:
                return "supporting"

            # Check 2: queried metric's inferred subsystem matches hypothesis subsystem
            if has_data:
                for qm, qsub in queried_subsystems.items():
                    if qsub == h.subsystem:
                        return "supporting"

        return "neutral"

    elif tool_name == "KBRetrievalTool":
        return "neutral"

    elif tool_name == "DataAnalysisTool":
        return "neutral"

    return "neutral"


def _find_related_hypothesis_ids(
    tool_name: str,
    args: dict,
    hypotheses: list[Hypothesis],
) -> list[str]:
    """Determine which hypotheses are related to this tool call."""
    if tool_name == "KBRetrievalTool":
        # Keep KB retrieval as unbound context rather than attaching it to any
        # specific hypothesis; otherwise prior knowledge pollutes evidence_ids.
        return []

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
    # If no metric/subsystem match, infer subsystem from metric names rather than
    # falling back to ALL hypotheses (which inflates evidence for every hypothesis).
    if not ids:
        all_metrics_str = " ".join(query_metrics).lower()
        _SUBSYSTEM_KW: dict[str, list[str]] = {
            "cpu": ["cpu", "load", "schedstat", "runqueue", "pressure_cpu", "proc"],
            "memory": ["mem", "memory", "swap", "huge", "pressure_mem"],
            "network": ["net", "network", "tcp", "recv", "transmit", "packet", "drop"],
            "disk": ["disk", "filesystem", "block", "io_time", "reads_completed", "writes_completed", "file"],
        }
        for sys_name, keywords in _SUBSYSTEM_KW.items():
            if any(kw in all_metrics_str for kw in keywords):
                for h in hypotheses:
                    if h.status in ("active", "confirmed") and h.subsystem == sys_name:
                        ids.append(h.id)
                if ids:
                    break
        # Last resort: associate with the single highest-confidence active hypothesis only
        if not ids:
            active = [h for h in hypotheses if h.status in ("active", "confirmed")]
            if active:
                top = max(active, key=lambda x: x.current_confidence)
                ids = [top.id]

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
    ev_type = _determine_evidence_type(
        tool_name,
        tool_args,
        tool_result,
        hypotheses,
        state.get("focus_context"),
    )

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

    # Only propose hypotheses that have direct observational support.
    # KB/FPL retrieval is prior knowledge and must not qualify a root cause.
    focus_context = state.get("focus_context")
    observational_tools = ("MetricQueryTool", "DataAnalysisTool")
    supported_ids = {
        hyp_id
        for e in evidence
        for hyp_id in e.hypothesis_ids
        if e.type == "supporting" and e.source_tool in observational_tools
    }
    confirmed_ids = {h.id for h in hypotheses if h.status == "confirmed"}
    qualifying_ids = supported_ids | confirmed_ids

    # Conservative fallback: if no direct-support hypothesis survives, allow
    # any non-refuted hypotheses that were actually investigated by
    # observational tools (no subsystem restriction).
    if not qualifying_ids:
        investigated_ids = {
            hyp_id
            for e in evidence
            for hyp_id in e.hypothesis_ids
            if e.source_tool in observational_tools
        }
        qualifying_ids = {
            h.id
            for h in hypotheses
            if h.id in investigated_ids
            and h.status != "refuted"
        }

    # Third fallback: single best non-refuted hypothesis by prior_confidence
    if not qualifying_ids:
        best = max(
            (h for h in hypotheses if h.status != "refuted"),
            key=lambda h: h.prior_confidence,
            default=None,
        )
        if best is not None:
            qualifying_ids = {best.id}

    candidates = [
        h for h in hypotheses
        if h.status != "refuted" and h.id in qualifying_ids
    ]
    candidates.sort(key=lambda x: x.current_confidence, reverse=True)

    # Determine diagnosis type based on candidate count
    if len(candidates) == 0:
        dtype = "partial"
    elif len(candidates) == 1:
        dtype = "single_fault"
    else:
        dtype = "composite_fault"

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
        return think_node(state, llm_client, config, tools)

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
