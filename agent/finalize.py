"""agent/finalize.py — FINALIZE node.

将结构化诊断状态转化为 DiagnosisReport。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.config import AgentConfig
from agent.llm_client import LLMClient, _extract_json_from_text
from agent.prompt_utils import render_prompt
from agent.schema import (
    ConclusionProposal,
    DiagnosisReport,
    OrchestratorState,
    ProposedRootCause,
    ReActStep,
    AuditStep,
    RootCause,
    TraceSummary,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


# =====================================================================
# Trace summary builder (deterministic, no LLM)
# =====================================================================

def _count_tools(trace: list) -> list[str]:
    """Count tool usage from trace steps."""
    counts: dict[str, int] = {}
    for step in trace:
        tc = getattr(step, "tool_call", None)
        if tc and tc.tool:
            counts[tc.tool] = counts.get(tc.tool, 0) + 1
    return [f"{name} x{count}" for name, count in counts.items()]


def _build_trace_summary(state: OrchestratorState) -> TraceSummary:
    """Build trace summary from state, deterministic."""
    fc = state.get("focus_context")
    diag_trace = state.get("diagnosis_trace", [])
    audit_trace_list = state.get("audit_trace", [])
    diag_budget = state.get("diagnosis_budget", {})
    audit_budget = state.get("audit_budget", {})

    # Token totals from budgets if available
    total_in = 0
    total_out = 0

    return TraceSummary(
        triage_leading_subsystem=fc.leading_subsystem if fc else "unknown",
        triage_confidence=fc.triage_confidence if fc else 0.0,
        main_tools_used=_count_tools(diag_trace),
        audit_tools_used=_count_tools(audit_trace_list),
        total_tool_calls=(
            diag_budget.get("tool_calls_used", 0)
            + audit_budget.get("tool_calls_used", 0)
        ),
        main_iterations=len(diag_trace),
        audit_iterations=len(audit_trace_list),
        total_tokens_in=total_in,
        total_tokens_out=total_out,
    )


# =====================================================================
# FINALIZE node
# =====================================================================

def finalize_node(
    state: OrchestratorState,
    llm_client: LLMClient,
    config: AgentConfig,
) -> dict:
    """Generate DiagnosisReport from ConclusionProposal + state."""
    proposal = state.get("current_proposal")
    fc = state.get("focus_context")
    audit_decision = state.get("audit_decision")

    # Determine diagnosis_type
    if audit_decision and audit_decision.decision == "degrade":
        diagnosis_type = audit_decision.diagnosis_type or "inconclusive"
    elif not config.ablation.enable_audit:
        diagnosis_type = proposal.proposed_diagnosis_type if proposal else "inconclusive"
    elif audit_decision and audit_decision.decision == "pass":
        diagnosis_type = proposal.proposed_diagnosis_type if proposal else "inconclusive"
    else:
        diagnosis_type = proposal.proposed_diagnosis_type if proposal else "inconclusive"

    # Format prompt variables
    confirmed_rc_lines = []
    key_evidence_lines = []
    if proposal:
        for rc in proposal.proposed_root_causes:
            confirmed_rc_lines.append(
                f"- {rc.fault_type}: {rc.cause} (confidence={rc.confidence:.2f})"
            )
        for e in proposal.evidence:
            key_evidence_lines.append(
                f"- [{e.id}] ({e.type}) {e.result_digest}"
            )

    audit_summary = "未进行审查"
    if audit_decision:
        audit_summary = f"审查决策: {audit_decision.decision} — {audit_decision.reason}"

    top_metrics_brief = ""
    if fc:
        top_metrics_brief = ", ".join(
            f"{tm.metric}({tm.direction}{tm.score:.1f})" for tm in fc.top_metrics[:5]
        )

    variables = {
        "diagnosis_type": diagnosis_type,
        "confirmed_root_causes": "\n".join(confirmed_rc_lines) if confirmed_rc_lines else "无确认根因",
        "key_evidence": "\n".join(key_evidence_lines) if key_evidence_lines else "无关键证据",
        "leading_subsystem": fc.leading_subsystem if fc else "unknown",
        "top_metrics_brief": top_metrics_brief or "无",
        "audit_summary": audit_summary,
    }
    prompt = render_prompt(PROMPTS_DIR / "finalize.md", variables)

    messages = [
        {"role": "system", "content": "你是 HPC 诊断报告撰写者。"},
        {"role": "user", "content": prompt},
    ]
    content, usage = llm_client.call(messages, temperature=0.2)
    llm_client.tracker.record("finalize", usage)

    # Parse response
    try:
        json_str = _extract_json_from_text(content)
        data = json.loads(json_str)
    except Exception as e:
        logger.error("Failed to parse FINALIZE response: %s", e)
        # Fallback: use empty narrative
        data = {"anomaly_summary": "诊断报告（LLM 输出解析失败）", "uncertainties": ["LLM 输出解析失败"]}

    # Build TraceSummary (deterministic)
    trace_summary = _build_trace_summary(state)

    # Update token totals from tracker
    if hasattr(llm_client, "tracker"):
        totals = llm_client.tracker.total()
        trace_summary = trace_summary.model_copy(update={
            "total_tokens_in": totals.get("prompt_tokens", 0),
            "total_tokens_out": totals.get("completion_tokens", 0),
        })

    # root_causes are sourced from proposal directly (never from LLM output).
    # FINALIZE LLM only writes narrative fields: anomaly_summary / uncertainties.
    # This prevents the LLM from hallucinating or inflating confidence.
    root_causes = _root_causes_from_proposal(proposal)

    # Prevent schema violation: partial/single_fault/composite_fault require root_causes
    if not root_causes and diagnosis_type != "inconclusive":
        logger.warning("No root_causes for diagnosis_type='%s', downgrading to inconclusive", diagnosis_type)
        diagnosis_type = "inconclusive"

    # Build DiagnosisReport
    try:
        report = DiagnosisReport(
            run_id=state["run_id"],
            anomaly_summary=data.get("anomaly_summary", "异常诊断完成"),
            diagnosis_type=diagnosis_type,
            root_causes=root_causes,
            derived_symptoms=[],
            solutions=[],
            uncertainties=data.get("uncertainties", []),
            trace_summary=trace_summary,
            generated_at=datetime.now(),
        )
    except Exception as e:
        logger.error("Failed to build DiagnosisReport: %s, using fallback", e)
        report = _build_fallback_diagnosis_report(state, diagnosis_type, trace_summary)

    return {"report": report}


# Chinese → English fault_type normalization map (fallback for model language switch)
_CN_FAULT_TYPE_MAP: dict[str, str] = {
    "磁盘空间不足": "disk_fill",
    "磁盘IO过载": "disk_burn",
    "内存负载过高": "mem_load",
    "内存压力": "mem_load",
    "网络丢包": "network_loss",
    "网络延迟": "network_delay",
    "网络损坏": "network_corrupt",
    "CPU满载": "cpu_fullload",
    "CPU过载": "cpu_fullload",
}


def _normalize_fault_type(fault_type: str) -> str:
    """Normalize fault_type to English standard label."""
    if fault_type in _CN_FAULT_TYPE_MAP:
        logger.warning("[Finalize] Chinese fault_type '%s' normalized to '%s'", fault_type, _CN_FAULT_TYPE_MAP[fault_type])
        return _CN_FAULT_TYPE_MAP[fault_type]
    return fault_type


def _root_causes_from_proposal(proposal: ConclusionProposal | None) -> list[RootCause]:
    """Convert proposed_root_causes to RootCause list (no LLM, deterministic).

    root_causes 必须严格来自 proposal，FINALIZE LLM 不能修改或增删。
    """
    if not proposal:
        return []
    result = []
    for prc in proposal.proposed_root_causes:
        # Find the matching hypothesis for metadata
        hyp = next(
            (h for h in proposal.hypotheses if h.fault_type == prc.fault_type),
            None,
        )
        # Collect counter-evidence IDs for this hypothesis
        counter_ids: list[str] = []
        if hyp:
            counter_ids = [
                e.id
                for e in proposal.evidence
                if hyp.id in e.hypothesis_ids and e.type == "refuting"
            ]
        result.append(RootCause(
            cause=prc.cause,
            fault_type=_normalize_fault_type(prc.fault_type),
            confidence=prc.confidence,
            evidence_ids=prc.evidence_ids,
            counter_evidence_ids=counter_ids,
            fpl_pattern_id=hyp.fpl_pattern_id if hyp else None,
            affected_nodes=[],
        ))
    return result


def _build_fallback_report(state: OrchestratorState, diagnosis_type: str) -> dict:
    """Build fallback report data from proposal."""
    proposal = state.get("current_proposal")
    root_causes = []
    if proposal:
        for rc in proposal.proposed_root_causes:
            root_causes.append({
                "cause": rc.cause,
                "fault_type": rc.fault_type,
                "confidence": rc.confidence,
                "evidence_ids": rc.evidence_ids,
                "counter_evidence_ids": [],
                "fpl_pattern_id": None,
                "affected_nodes": [],
            })
    return {
        "anomaly_summary": "诊断报告（LLM 输出解析失败，使用 fallback）",
        "diagnosis_type": diagnosis_type,
        "root_causes": root_causes,
        "derived_symptoms": [],
        "solutions": [],
        "uncertainties": ["LLM 输出解析失败"],
    }


def _build_fallback_diagnosis_report(
    state: OrchestratorState,
    diagnosis_type: str,
    trace_summary: TraceSummary,
) -> DiagnosisReport:
    """Build minimal DiagnosisReport when parsing fails."""
    proposal = state.get("current_proposal")
    root_causes = _root_causes_from_proposal(proposal)

    return DiagnosisReport(
        run_id=state["run_id"],
        anomaly_summary="Fallback report",
        diagnosis_type=diagnosis_type if root_causes else "inconclusive",
        root_causes=root_causes,
        trace_summary=trace_summary,
        generated_at=datetime.now(),
    )
