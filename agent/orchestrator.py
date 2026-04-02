"""agent/orchestrator.py — Orchestrator graph + run_diagnosis() entry point.

协调 Triage, Diagnosis Graph, Audit Graph, Finalize 节点。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from langgraph.graph import END, StateGraph

from agent.audit import build_audit_graph, build_audit_state
from agent.config import AgentConfig, FORMALTEST_DIR, FPL_JSONL, METRICS_YAML
from agent.diagnosis import build_diagnosis_graph, _build_conclusion_proposal
from agent.finalize import finalize_node as _finalize_node
from agent.llm_client import LLMClient
from agent.reflect import should_reflect, run_reflect
from agent.schema import (
    AuditDecision,
    DiagnosisReport,
    DiagnosisState,
    OrchestratorState,
)
from agent.tools.registry import create_tools
from agent.triage import run_triage

logger = logging.getLogger(__name__)


# =====================================================================
# KB loader helpers
# =====================================================================

def _load_metric_kb(path: Path | None = None) -> list[dict]:
    p = path or METRICS_YAML
    with open(p) as f:
        return yaml.safe_load(f)


def _load_fpl(path: Path | None = None) -> list[dict]:
    p = path or FPL_JSONL
    entries = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _load_metrics_df(metrics_path: str | Path):
    import pandas as pd
    df = pd.read_csv(metrics_path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")
    return df


# =====================================================================
# Orchestrator nodes
# =====================================================================

def triage_node(
    state: OrchestratorState,
    config: AgentConfig,
) -> dict:
    """Run Triage (deterministic Python)."""
    inputs = state["inputs"]
    metrics_path = inputs["metrics_path"]
    jobinfo_path = inputs["jobinfo_path"]
    metric_kb = _load_metric_kb()

    focus_context = run_triage(
        metrics_path=metrics_path,
        jobinfo_path=jobinfo_path,
        metric_kb=metric_kb,
        config=config.triage,
        ablation=config.ablation,
        run_id=state["run_id"],
    )
    return {"focus_context": focus_context}


def invoke_diagnosis_node(
    state: OrchestratorState,
    diagnosis_graph,
    config: AgentConfig,
) -> dict:
    """Build DiagnosisState, invoke diagnosis_graph, extract ConclusionProposal."""
    fc = state["focus_context"]
    audit_decision = state.get("audit_decision")
    rehyp_count = state.get("rehyp_count", 0)
    round_count = state.get("round_count", 0)

    # Determine gate_hint and hypothesis handling
    gate_hint = None
    prev_hypotheses = []
    prev_evidence = []

    if audit_decision:
        if audit_decision.decision == "continue":
            gate_hint = audit_decision.hint
            # Preserve all hypotheses and evidence from previous round
            if state.get("_prev_diagnosis_state"):
                prev_hypotheses = state["_prev_diagnosis_state"].get("hypotheses", [])
                prev_evidence = state["_prev_diagnosis_state"].get("evidence", [])
        elif audit_decision.decision == "rehypothesize":
            # Keep refuted, clear active
            if state.get("_prev_diagnosis_state"):
                prev_hypotheses = [
                    h for h in state["_prev_diagnosis_state"].get("hypotheses", [])
                    if h.status == "refuted"
                ]
                prev_evidence = state["_prev_diagnosis_state"].get("evidence", [])

    # Build budget
    if audit_decision and audit_decision.decision == "continue" and state.get("diagnosis_budget"):
        # Continue: use existing budget (don't reset)
        budget = dict(state["diagnosis_budget"])
    else:
        budget = {
            "tool_calls_used": 0,
            "tool_calls_limit": config.budget.tool_calls_limit,
        }

    diag_state = DiagnosisState(
        run_id=state["run_id"],
        focus_context=fc,
        hypotheses=prev_hypotheses,
        evidence=prev_evidence,
        react_trace=[],
        gate_hint=gate_hint,
        budget=budget,
        rehyp_count=rehyp_count,
    )

    # Invoke diagnosis graph
    result_state = diagnosis_graph.invoke(diag_state)

    # Extract ConclusionProposal
    proposal = _build_conclusion_proposal(result_state)

    return {
        "current_proposal": proposal,
        "diagnosis_trace": result_state.get("react_trace", []),
        "diagnosis_budget": result_state.get("budget", {}),
        # _prev_diagnosis_state: runtime carry-forward key (not in TypedDict schema).
        # LangGraph accepts extra keys in non-strict mode. Used by next INVOKE_DIAGNOSIS
        # to preserve hypotheses/evidence across continue/rehypothesize rounds.
        "_prev_diagnosis_state": result_state,
    }


def submit_to_audit_node(
    state: OrchestratorState,
    audit_graph,
    config: AgentConfig,
) -> dict:
    """Build AuditState, invoke audit_graph, extract AuditDecision."""
    # Ablation: skip audit
    if not config.ablation.enable_audit:
        return {
            "audit_decision": AuditDecision(
                decision="pass",
                reason="Audit disabled (Abl-B)",
            ),
            "audit_trace": [],
            "audit_budget": {"tool_calls_used": 0},
            "round_count": state.get("round_count", 0) + 1,
        }

    audit_state = build_audit_state(state, config)
    result_state = audit_graph.invoke(audit_state)

    # Extract AuditDecision
    decision = result_state.get("audit_decision")
    if not decision:
        decision = AuditDecision(
            decision="pass",
            reason="No decision returned from audit graph",
        )

    return {
        "audit_decision": decision,
        "audit_trace": result_state.get("audit_trace", []),
        "audit_budget": result_state.get("audit_budget", {}),
        "round_count": state.get("round_count", 0) + 1,
    }


def route_decision_node(
    state: OrchestratorState,
    config: AgentConfig,
) -> dict:
    """Deterministic routing, no LLM."""
    decision = state.get("audit_decision")
    if not decision:
        return {}

    if decision.decision == "rehypothesize":
        if state.get("rehyp_count", 0) < config.budget.max_rehyp:
            return {"rehyp_count": state.get("rehyp_count", 0) + 1}
        else:
            # Degrade: can't rehypothesize anymore
            return {
                "audit_decision": AuditDecision(
                    decision="degrade",
                    reason="Max rehyp attempts reached, degrading",
                    diagnosis_type="partial",
                ),
            }

    return {}


def orchestrator_router(state: OrchestratorState, config: AgentConfig) -> str:
    """Routing function for conditional edges."""
    decision = state.get("audit_decision")
    round_count = state.get("round_count", 0)

    # Safety: max rounds
    if round_count >= config.budget.max_orchestrator_rounds:
        return "finalize"

    if not decision:
        return "finalize"

    if decision.decision == "pass":
        return "finalize"
    elif decision.decision == "degrade":
        # On round 1, degrade triggers rehypothesize to give diagnosis another chance
        if round_count <= 1 and state.get("rehyp_count", 0) < config.budget.max_rehyp:
            return "rehypothesize"
        return "finalize"
    elif decision.decision == "continue":
        return "continue"
    elif decision.decision == "rehypothesize":
        return "rehypothesize"

    return "finalize"


# =====================================================================
# Graph builder
# =====================================================================

def build_orchestrator_graph(
    diagnosis_graph,
    audit_graph,
    tools: dict,
    llm_client: LLMClient,
    config: AgentConfig,
):
    """Build and compile the Orchestrator StateGraph."""
    graph = StateGraph(OrchestratorState)

    def _triage(state: OrchestratorState) -> dict:
        return triage_node(state, config)

    def _invoke_diagnosis(state: OrchestratorState) -> dict:
        return invoke_diagnosis_node(state, diagnosis_graph, config)

    def _submit_to_audit(state: OrchestratorState) -> dict:
        return submit_to_audit_node(state, audit_graph, config)

    def _route_decision(state: OrchestratorState) -> dict:
        return route_decision_node(state, config)

    def _finalize(state: OrchestratorState) -> dict:
        return _finalize_node(state, llm_client, config)

    def _router(state: OrchestratorState) -> str:
        return orchestrator_router(state, config)

    graph.add_node("TRIAGE", _triage)
    graph.add_node("INVOKE_DIAGNOSIS", _invoke_diagnosis)
    graph.add_node("SUBMIT_TO_AUDIT", _submit_to_audit)
    graph.add_node("ROUTE_DECISION", _route_decision)
    graph.add_node("FINALIZE", _finalize)

    graph.set_entry_point("TRIAGE")
    graph.add_edge("TRIAGE", "INVOKE_DIAGNOSIS")
    graph.add_edge("INVOKE_DIAGNOSIS", "SUBMIT_TO_AUDIT")
    graph.add_edge("SUBMIT_TO_AUDIT", "ROUTE_DECISION")
    graph.add_conditional_edges("ROUTE_DECISION", _router, {
        "finalize": "FINALIZE",
        "continue": "INVOKE_DIAGNOSIS",
        "rehypothesize": "INVOKE_DIAGNOSIS",
    })
    graph.add_edge("FINALIZE", END)

    return graph.compile()


# =====================================================================
# Total entry point
# =====================================================================

def run_diagnosis(
    metrics_path: str | Path,
    jobinfo_path: str | Path,
    config: AgentConfig | None = None,
    run_id: str | None = None,
) -> tuple[DiagnosisReport, dict]:
    """System entry point.

    1. Generate run_id
    2. Build three graphs
    3. Initialize OrchestratorState
    4. Invoke orchestrator_graph
    5. Extract DiagnosisReport
    6. Save output

    Returns: (DiagnosisReport, execution_trace_dict)
    """
    if config is None:
        config = AgentConfig()

    if run_id is None:
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    logger.info("[Orchestrator] Starting run %s", run_id)

    # Load KB and data
    metric_kb = _load_metric_kb()
    fpl_entries = _load_fpl()
    metrics_df = _load_metrics_df(metrics_path)

    # Create LLM client
    llm_client = LLMClient(config.llm)

    # Pre-flight: validate API key before building graphs
    llm_client.validate_api_key()

    # Create tools
    tools = create_tools(
        metrics_df=metrics_df,
        metric_kb=metric_kb,
        fpl_entries=fpl_entries,
        chroma_collection=None,  # ChromaDB optional for now
    )

    # Build graphs
    diag_graph = build_diagnosis_graph(tools, llm_client, config)
    audit_g = build_audit_graph(tools, llm_client, config)
    orch_graph = build_orchestrator_graph(diag_graph, audit_g, tools, llm_client, config)

    # Initialize state
    initial_state = OrchestratorState(
        run_id=run_id,
        inputs={
            "metrics_path": str(metrics_path),
            "jobinfo_path": str(jobinfo_path),
        },
        focus_context=None,
        current_proposal=None,
        audit_decision=None,
        rehyp_count=0,
        round_count=0,
        diagnosis_trace=[],
        audit_trace=[],
        diagnosis_budget={},
        audit_budget={},
        report=None,
    )

    # Invoke
    result = orch_graph.invoke(initial_state)

    # Extract report
    report = result.get("report")
    if not report:
        logger.error("[Orchestrator] No report generated!")
        raise RuntimeError("Orchestrator did not produce a DiagnosisReport")

    # Build execution trace (full serialization for hallucination audit)
    proposal = result.get("current_proposal")
    diag_trace_raw = result.get("diagnosis_trace", [])
    audit_trace_raw = result.get("audit_trace", [])
    trace = {
        "run_id": run_id,
        "diagnosis_trace_steps": len(diag_trace_raw),
        "audit_trace_steps": len(audit_trace_raw),
        "diagnosis_trace": [
            s.model_dump(mode="json") if hasattr(s, "model_dump") else s
            for s in diag_trace_raw
        ],
        "audit_trace": [
            s.model_dump(mode="json") if hasattr(s, "model_dump") else s
            for s in audit_trace_raw
        ],
        "round_count": result.get("round_count", 0),
        "rehyp_count": result.get("rehyp_count", 0),
        "token_usage": llm_client.tracker.summary(),
        "hypotheses": [
            h.model_dump(mode="json") if hasattr(h, "model_dump") else h
            for h in (proposal.hypotheses if proposal else [])
        ],
        "evidence": [
            e.model_dump(mode="json") if hasattr(e, "model_dump") else e
            for e in (proposal.evidence if proposal else [])
        ],
        "proposal": result.get("current_proposal").model_dump(mode="json")
            if result.get("current_proposal") and hasattr(result.get("current_proposal"), "model_dump")
            else None,
    }

    # Stage 3: Reflect — FPL writeback (conditional)
    if should_reflect(report, config):
        logger.info("[Orchestrator] Running Reflect for %s", run_id)
        try:
            updated_fpl = run_reflect(
                report=report,
                focus_context=result.get("focus_context"),
                existing_fpl=fpl_entries,
                llm_client=llm_client,
                config=config,
            )
            logger.info("[Orchestrator] Reflect complete: %d FPL entries", len(updated_fpl))
        except Exception as e:
            logger.warning("[Orchestrator] Reflect failed (non-fatal): %s", e)

    # Save output
    _save_output(report, trace, config, run_id, metrics_path, result.get("focus_context"))

    logger.info("[Orchestrator] Run %s complete: diagnosis_type=%s", run_id, report.diagnosis_type)

    return report, trace, result.get("focus_context")


def _save_output(
    report: DiagnosisReport,
    trace: dict,
    config: AgentConfig,
    run_id: str,
    metrics_path: str | Path,
    focus_context=None,
) -> None:
    """Save diagnosis report and trace to output directory."""
    # Determine experiment ID from path
    metrics_p = Path(metrics_path)
    exp_id = metrics_p.parent.name

    output_dir = config.output_dir / exp_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save report
    report_path = output_dir / "diagnosis_report.json"
    report_path.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )

    # Save trace
    trace_path = output_dir / "execution_trace.json"
    trace_path.write_text(
        json.dumps(trace, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Save focus_context if available
    if focus_context is not None:
        fc_path = output_dir / "focus_context.json"
        fc_data = focus_context.model_dump(mode="json") if hasattr(focus_context, "model_dump") else focus_context
        fc_path.write_text(
            json.dumps(fc_data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    logger.info("[Output] Saved to %s", output_dir)
