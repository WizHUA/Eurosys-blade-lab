"""Tests for agent/schema.py v6 upgrade.
RED phase: these tests define the v6 contract.
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


# ---- v6 new types ----

def test_conclusion_proposal_basic():
    from agent.schema import ConclusionProposal, ProposedRootCause, Hypothesis, Evidence
    h = Hypothesis(
        id="h1", root_cause="CPU fullload", fault_type="cpu_fullload",
        subsystem="cpu", prior_confidence=0.8, status="confirmed",
    )
    e = Evidence(
        id="e1", hypothesis_ids=["h1"], type="supporting",
        source_tool="MetricQueryTool", query_summary="cpu_usage p95",
        result_digest="cpu_usage p95=98.7%", created_at_step=1,
    )
    rc = ProposedRootCause(
        cause="CPU fullload", fault_type="cpu_fullload",
        confidence=0.9, evidence_ids=["e1"],
    )
    cp = ConclusionProposal(
        hypotheses=[h], evidence=[e],
        proposed_diagnosis_type="single_fault",
        proposed_root_causes=[rc],
    )
    assert cp.proposed_diagnosis_type == "single_fault"
    assert len(cp.proposed_root_causes) == 1


def test_audit_decision_pass():
    from agent.schema import AuditDecision
    ad = AuditDecision(decision="pass", reason="All evidence sufficient")
    assert ad.hint is None
    assert ad.decision == "pass"


def test_audit_decision_continue_with_hint():
    from agent.schema import AuditDecision
    ad = AuditDecision(
        decision="continue", reason="Insufficient disk evidence",
        hint="Check disk_io metrics",
    )
    assert ad.hint == "Check disk_io metrics"


def test_audit_evidence():
    from agent.schema import AuditEvidence
    ae = AuditEvidence(
        id="ae1", source_tool="MetricQueryTool",
        query_summary="cpu_usage check", result_digest="normal range",
        target_hypothesis_ids=["h1"], purpose="verify cpu claim",
    )
    assert ae.id == "ae1"


def test_audit_step():
    from agent.schema import AuditStep
    now = datetime.now(tz=timezone.utc)
    step = AuditStep(
        step_id=1, thought="Checking evidence",
        action_type="decision", timestamp=now,
    )
    assert step.action_type == "decision"


def test_think_output():
    from agent.schema import ThinkOutput, HypothesisUpdate, ThinkAction
    to = ThinkOutput(
        thought="CPU metrics are very high",
        hypothesis_updates=[
            HypothesisUpdate(hypothesis_id="h1", new_confidence=0.85, reason="p95 confirmed"),
        ],
        action=ThinkAction(
            type="tool_call", tool="MetricQueryTool",
            args={"metric": "cpu_usage_percent"}, reasoning="need more data",
        ),
    )
    assert to.action.type == "tool_call"
    assert len(to.hypothesis_updates) == 1


# ---- v6 modified types ----

def test_evidence_v6_no_strength_field():
    """v6 removes strength and strength_basis from Evidence."""
    from agent.schema import Evidence
    e = Evidence(
        id="e1", hypothesis_ids=["h1"], type="supporting",
        source_tool="MetricQueryTool", query_summary="test",
        result_digest="test", created_at_step=1,
    )
    assert not hasattr(e, "strength")
    assert not hasattr(e, "strength_basis")


def test_evidence_v6_mixed_type():
    """v6 adds 'mixed' as valid evidence type."""
    from agent.schema import Evidence
    e = Evidence(
        id="e1", hypothesis_ids=["h1"], type="mixed",
        source_tool="MetricQueryTool", query_summary="test",
        result_digest="test", created_at_step=1,
    )
    assert e.type == "mixed"


def test_hypothesis_v6_no_evidence_id_fields():
    """v6 removes supporting_evidence_ids and refuting_evidence_ids from Hypothesis."""
    from agent.schema import Hypothesis
    h = Hypothesis(
        id="h1", root_cause="test", fault_type="cpu_fullload",
        subsystem="cpu", prior_confidence=0.5, status="active",
    )
    assert not hasattr(h, "supporting_evidence_ids")
    assert not hasattr(h, "refuting_evidence_ids")
    assert not hasattr(h, "generated_at_step")


def test_focus_context_v6_no_baseline():
    """v6 removes baseline_profile_used and data_completeness from FocusContext."""
    from agent.schema import FocusContext, AnomalyWindow, TopMetric
    now = datetime.now(tz=timezone.utc)
    fc = FocusContext(
        run_id="test",
        anomaly_window=AnomalyWindow(start=now, end=now),
        top_metrics=[],
        causal_order=[],
        subsystem_scores={},
        leading_subsystem="cpu",
        nodes=[],
        jobs=[],
        triage_confidence=0.5,
    )
    assert not hasattr(fc, "baseline_profile_used")
    assert not hasattr(fc, "data_completeness")


def test_top_metric_v6_no_peak_baseline():
    """v6 removes peak_value and baseline_mean from TopMetric."""
    from agent.schema import TopMetric
    now = datetime.now(tz=timezone.utc)
    tm = TopMetric(
        metric="cpu_usage_percent", subsystem="cpu",
        direction="+", score=8.5, t_onset=now, onset_rank=1,
    )
    assert not hasattr(tm, "peak_value")
    assert not hasattr(tm, "baseline_mean")


def test_react_step_v6_no_escalate():
    """v6 removes 'escalate' from action_type."""
    from agent.schema import ReActStep
    now = datetime.now(tz=timezone.utc)
    with pytest.raises(ValidationError):
        ReActStep(
            step_id=1, thought="test", action_type="escalate",
            timestamp=now,
        )


# ---- Three-layer State ----

def test_orchestrator_state_keys():
    """OrchestratorState has the v6 fields."""
    from agent.schema import OrchestratorState
    # TypedDict — check __annotations__
    keys = set(OrchestratorState.__annotations__.keys())
    expected = {
        "run_id", "inputs", "focus_context", "current_proposal",
        "audit_decision", "rehyp_count", "round_count",
        "diagnosis_trace", "audit_trace", "diagnosis_budget",
        "audit_budget", "report",
    }
    assert expected.issubset(keys)


def test_diagnosis_state_keys():
    from agent.schema import DiagnosisState
    keys = set(DiagnosisState.__annotations__.keys())
    expected = {
        "run_id", "focus_context", "hypotheses", "evidence",
        "react_trace", "gate_hint", "budget", "rehyp_count",
    }
    assert expected.issubset(keys)


def test_audit_state_keys():
    from agent.schema import AuditState
    keys = set(AuditState.__annotations__.keys())
    expected = {
        "run_id", "focus_context", "proposal", "audit_evidence",
        "audit_trace", "audit_budget", "previous_hint",
    }
    assert expected.issubset(keys)


def test_old_agent_state_removed():
    """v6 removes the old single AgentState."""
    from agent import schema
    assert not hasattr(schema, "AgentState")


# ---- TraceSummary v6 ----

def test_trace_summary_v6_split_fields():
    from agent.schema import TraceSummary
    ts = TraceSummary(
        triage_leading_subsystem="cpu", triage_confidence=0.8,
        main_tools_used=["MetricQueryTool x3"],
        audit_tools_used=["KBRetrievalTool x1"],
        total_tool_calls=4, main_iterations=5, audit_iterations=2,
        total_tokens_in=1000, total_tokens_out=500,
    )
    assert ts.main_iterations == 5
    assert ts.audit_iterations == 2
    # v6 removes gate_passed_by_rule
    assert not hasattr(ts, "gate_passed_by_rule")
