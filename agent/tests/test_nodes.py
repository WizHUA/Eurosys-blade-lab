"""agent/tests/test_nodes.py — B-type contract tests for LLM-driven nodes.

Tests node scaffolding and state transitions with mocked LLM.
"""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agent.config import AgentConfig, BudgetConfig
from agent.schema import (
    AnomalyWindow,
    AuditDecision,
    AuditState,
    ConclusionProposal,
    DiagnosisState,
    Evidence,
    FocusContext,
    Hypothesis,
    NodeSeverity,
    OrchestratorState,
    ProposedRootCause,
    TopMetric,
    VerificationItem,
)


# =====================================================================
# Fixtures
# =====================================================================

def _make_focus_context() -> FocusContext:
    return FocusContext(
        run_id="test_run",
        anomaly_window=AnomalyWindow(
            start=datetime(2025, 9, 20, 7, 0, 0),
            end=datetime(2025, 9, 20, 7, 2, 0),
        ),
        top_metrics=[
            TopMetric(
                metric="cpu_usage_percent",
                subsystem="cpu",
                direction="+",
                score=5.0,
                t_onset=datetime(2025, 9, 20, 7, 0, 15),
                onset_rank=1,
            ),
            TopMetric(
                metric="cpu_iowait_percent",
                subsystem="cpu",
                direction="+",
                score=3.5,
                t_onset=datetime(2025, 9, 20, 7, 0, 30),
                onset_rank=2,
            ),
        ],
        causal_order=["cpu_usage_percent", "cpu_iowait_percent"],
        subsystem_scores={"cpu": 4.25, "memory": 0.5},
        leading_subsystem="cpu",
        nodes=[NodeSeverity(node="node1", severity="high")],
        jobs=[],
        triage_confidence=0.85,
    )


def _make_diagnosis_state(
    focus: FocusContext | None = None,
    hypotheses: list[Hypothesis] | None = None,
    evidence: list[Evidence] | None = None,
) -> DiagnosisState:
    fc = focus or _make_focus_context()
    return DiagnosisState(
        run_id="test_run",
        focus_context=fc,
        hypotheses=hypotheses or [],
        evidence=evidence or [],
        react_trace=[],
        gate_hint=None,
        budget={"tool_calls_used": 0, "tool_calls_limit": 15},
        rehyp_count=0,
    )


def _make_hypothesis(
    id: str = "h1",
    fault_type: str = "cpu_fullload",
    subsystem: str = "cpu",
    confidence: float = 0.8,
    status: str = "active",
) -> Hypothesis:
    return Hypothesis(
        id=id,
        root_cause=f"Test {fault_type}",
        fault_type=fault_type,
        subsystem=subsystem,
        prior_confidence=confidence,
        current_confidence=confidence,
        status=status,
    )


def _make_evidence(
    id: str = "e1",
    hyp_ids: list[str] | None = None,
    ev_type: str = "supporting",
) -> Evidence:
    return Evidence(
        id=id,
        hypothesis_ids=hyp_ids or ["h1"],
        type=ev_type,
        source_tool="MetricQueryTool",
        query_summary="cpu_usage_percent mean",
        result_digest="cpu_usage_percent: mean=95.2%",
        raw_stats={"mean": 95.2},
        created_at_step=1,
    )


def _make_config(**kwargs) -> AgentConfig:
    return AgentConfig(**kwargs)


# =====================================================================
# HYPOTHESIZE node tests
# =====================================================================

class TestHypothesizeNode:
    """Test that HYPOTHESIZE correctly updates state with LLM-generated hypotheses."""

    def test_generates_hypotheses_from_llm(self):
        """Node should parse LLM output into Hypothesis list and add to state."""
        from agent.diagnosis import hypothesize_node

        state = _make_diagnosis_state()
        mock_llm = MagicMock()
        mock_tools = {"KBRetrievalTool": MagicMock()}
        mock_tools["KBRetrievalTool"].execute.return_value = {"pattern_hits": []}
        config = _make_config()

        llm_response = json.dumps([
            {
                "id": "h1",
                "root_cause": "CPU overload due to stress test",
                "fault_type": "cpu_fullload",
                "subsystem": "cpu",
                "prior_confidence": 0.8,
                "required_verifications": [
                    {"description": "Check CPU usage > 90%", "required_metrics": ["cpu_usage_percent"]}
                ],
            },
            {
                "id": "h2",
                "root_cause": "Memory pressure",
                "fault_type": "mem_load_ram",
                "subsystem": "memory",
                "prior_confidence": 0.3,
                "required_verifications": [],
            },
        ])
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 100, "completion_tokens": 50})

        result = hypothesize_node(state, mock_llm, mock_tools, config)

        assert "hypotheses" in result
        assert len(result["hypotheses"]) == 2
        assert result["hypotheses"][0].fault_type == "cpu_fullload"
        assert result["hypotheses"][1].fault_type == "mem_load_ram"

    def test_rehyp_preserves_refuted(self):
        """When rehyp_count > 0, refuted hypotheses should be preserved."""
        from agent.diagnosis import hypothesize_node

        refuted_h = _make_hypothesis(id="h1", status="refuted", confidence=0.1)
        state = _make_diagnosis_state(hypotheses=[refuted_h])
        state["rehyp_count"] = 1
        state["gate_hint"] = None

        mock_llm = MagicMock()
        mock_tools = {"KBRetrievalTool": MagicMock()}
        mock_tools["KBRetrievalTool"].execute.return_value = {"pattern_hits": []}
        config = _make_config()

        llm_response = json.dumps([
            {
                "id": "h2",
                "root_cause": "Network packet loss",
                "fault_type": "network_loss",
                "subsystem": "network",
                "prior_confidence": 0.5,
                "required_verifications": [],
            }
        ])
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 100, "completion_tokens": 50})

        result = hypothesize_node(state, mock_llm, mock_tools, config)

        all_h = result["hypotheses"]
        fault_types = {h.fault_type for h in all_h}
        assert "cpu_fullload" in fault_types  # refuted preserved
        assert "network_loss" in fault_types  # new added


# =====================================================================
# THINK node tests
# =====================================================================

class TestThinkNode:
    """Test THINK node state transitions."""

    def test_tool_call_action(self):
        """THINK requesting tool_call should create ReActStep with action_type=tool_call."""
        from agent.diagnosis import think_node

        h1 = _make_hypothesis()
        state = _make_diagnosis_state(hypotheses=[h1])
        config = _make_config()
        mock_llm = MagicMock()

        llm_response = json.dumps({
            "thought": "Need to check CPU usage values",
            "hypothesis_updates": [
                {"hypothesis_id": "h1", "new_confidence": 0.85, "reason": "Initial check"},
            ],
            "action": {
                "type": "tool_call",
                "tool": "MetricQueryTool",
                "args": {
                    "metrics": ["cpu_usage_percent"],
                    "time_window": {"start": "2025-09-20T07:00:00", "end": "2025-09-20T07:02:00"},
                    "aggregation": "mean",
                },
                "reasoning": "Check CPU usage during anomaly window",
            },
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 200, "completion_tokens": 80})

        result = think_node(state, mock_llm, config)

        assert "react_trace" in result
        assert len(result["react_trace"]) == 1
        step = result["react_trace"][0]
        assert step.action_type == "tool_call"
        assert step.tool_call.tool == "MetricQueryTool"

    def test_conclude_action_marks_confirmed(self):
        """THINK with conclude should mark high-confidence hypotheses as confirmed."""
        from agent.diagnosis import think_node

        h1 = _make_hypothesis(confidence=0.85)
        e1 = _make_evidence()
        state = _make_diagnosis_state(hypotheses=[h1], evidence=[e1])
        config = _make_config()
        mock_llm = MagicMock()

        llm_response = json.dumps({
            "thought": "Evidence is sufficient to confirm CPU overload",
            "hypothesis_updates": [
                {"hypothesis_id": "h1", "new_confidence": 0.92, "reason": "Strong evidence"},
            ],
            "action": {
                "type": "conclude",
                "tool": None,
                "args": None,
                "reasoning": "CPU overload confirmed with high confidence",
            },
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 200, "completion_tokens": 80})

        result = think_node(state, mock_llm, config)

        assert len(result["react_trace"]) == 1
        assert result["react_trace"][0].action_type == "conclude"
        # Check confirmed status on high-confidence hypotheses
        confirmed = [h for h in result["hypotheses"] if h.status == "confirmed"]
        assert len(confirmed) >= 1

    def test_force_conclude_when_budget_exhausted(self):
        """When budget is exhausted, system should inject force-conclude instruction."""
        from agent.diagnosis import think_node

        h1 = _make_hypothesis()
        state = _make_diagnosis_state(hypotheses=[h1])
        state["budget"] = {"tool_calls_used": 15, "tool_calls_limit": 15}
        config = _make_config()
        mock_llm = MagicMock()

        # Even if LLM still outputs tool_call, think_node should handle gracefully
        llm_response = json.dumps({
            "thought": "Budget exhausted, concluding",
            "hypothesis_updates": [],
            "action": {
                "type": "conclude",
                "tool": None,
                "args": None,
                "reasoning": "Budget exhausted",
            },
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 200, "completion_tokens": 80})

        result = think_node(state, mock_llm, config)

        assert result["react_trace"][0].action_type == "conclude"


# =====================================================================
# ACT node tests
# =====================================================================

class TestActNode:
    """Test ACT node tool execution."""

    def test_executes_tool_and_increments_budget(self):
        """ACT should execute the tool and increment tool_calls_used."""
        from agent.diagnosis import act_node
        from agent.schema import ReActStep, ToolCall

        h1 = _make_hypothesis()
        state = _make_diagnosis_state(hypotheses=[h1])
        step = ReActStep(
            step_id=1,
            thought="Check CPU",
            action_type="tool_call",
            tool_call=ToolCall(tool="MetricQueryTool", args={
                "metrics": ["cpu_usage_percent"],
                "time_window": {"start": "2025-09-20T07:00:00", "end": "2025-09-20T07:02:00"},
                "aggregation": "mean",
            }, call_id="c1"),
            timestamp=datetime.now(),
        )
        state["react_trace"] = [step]

        mock_tools = {"MetricQueryTool": MagicMock()}
        mock_tools["MetricQueryTool"].execute.return_value = {
            "results": [{"metric": "cpu_usage_percent", "value": 95.2, "aggregation": "mean"}],
            "missing": [],
        }
        config = _make_config()

        result = act_node(state, mock_tools, config)

        assert result["budget"]["tool_calls_used"] == 1
        assert "_tool_result" in result


# =====================================================================
# OBSERVE node tests
# =====================================================================

class TestObserveNode:
    """Test OBSERVE node evidence generation."""

    def test_generates_evidence_from_tool_result(self):
        """OBSERVE should create Evidence from tool result."""
        from agent.diagnosis import observe_node
        from agent.schema import ReActStep, ToolCall

        h1 = _make_hypothesis()
        state = _make_diagnosis_state(hypotheses=[h1])
        step = ReActStep(
            step_id=1,
            thought="Check CPU",
            action_type="tool_call",
            tool_call=ToolCall(tool="MetricQueryTool", args={
                "metrics": ["cpu_usage_percent"],
                "time_window": {"start": "2025-09-20T07:00:00", "end": "2025-09-20T07:02:00"},
                "aggregation": "mean",
            }, call_id="c1"),
            timestamp=datetime.now(),
        )
        state["react_trace"] = [step]
        state["_tool_result"] = {
            "results": [{"metric": "cpu_usage_percent", "value": 95.2, "aggregation": "mean"}],
            "missing": [],
        }

        result = observe_node(state)

        assert "evidence" in result
        assert len(result["evidence"]) == 1
        ev = result["evidence"][0]
        assert ev.source_tool == "MetricQueryTool"
        assert "cpu_usage_percent" in ev.result_digest


# =====================================================================
# GATE_THINK node tests (Audit)
# =====================================================================

class TestGateThinkNode:
    """Test Audit Agent GATE_THINK node."""

    def _make_audit_state(self) -> AuditState:
        fc = _make_focus_context()
        proposal = ConclusionProposal(
            hypotheses=[_make_hypothesis(status="confirmed", confidence=0.9)],
            evidence=[_make_evidence()],
            proposed_diagnosis_type="single_fault",
            proposed_root_causes=[
                ProposedRootCause(
                    cause="CPU overload",
                    fault_type="cpu_fullload",
                    confidence=0.9,
                    evidence_ids=["e1"],
                )
            ],
        )
        return AuditState(
            run_id="test_run",
            focus_context=fc,
            proposal=proposal,
            audit_evidence=[],
            audit_trace=[],
            audit_budget={"tool_calls_used": 0, "tool_calls_limit": 3, "max_rounds": 2},
            previous_hint=None,
        )

    def test_pass_decision(self):
        """GATE_THINK should parse pass decision from LLM."""
        from agent.audit import gate_think_node

        state = self._make_audit_state()
        mock_llm = MagicMock()
        config = _make_config()

        llm_response = json.dumps({
            "thought": "Evidence is sufficient. CPU usage confirmed at 95%.",
            "action_type": "decision",
            "decision": {
                "decision": "pass",
                "reason": "Evidence sufficiently supports CPU overload diagnosis",
                "hint": None,
                "diagnosis_type": "single_fault",
            },
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 300, "completion_tokens": 100})

        result = gate_think_node(state, mock_llm, {}, config)

        assert "audit_decision" in result
        assert result["audit_decision"].decision == "pass"

    def test_continue_decision_requires_hint(self):
        """Continue decision must have a hint."""
        from agent.audit import gate_think_node

        state = self._make_audit_state()
        mock_llm = MagicMock()
        config = _make_config()

        llm_response = json.dumps({
            "thought": "Need more evidence about memory",
            "action_type": "decision",
            "decision": {
                "decision": "continue",
                "reason": "Insufficient evidence for alternative hypotheses",
                "hint": "Check memory usage to rule out mem_load_ram",
                "diagnosis_type": None,
            },
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 300, "completion_tokens": 100})

        result = gate_think_node(state, mock_llm, {}, config)

        assert result["audit_decision"].decision == "continue"
        assert result["audit_decision"].hint is not None

    def test_gate_tool_call(self):
        """GATE_THINK requesting tool should create audit trace step."""
        from agent.audit import gate_think_node

        state = self._make_audit_state()
        mock_llm = MagicMock()
        config = _make_config()

        llm_response = json.dumps({
            "thought": "Need to verify CPU metrics independently",
            "action_type": "gate_tool_call",
            "tool_call": {
                "tool": "MetricQueryTool",
                "args": {
                    "metrics": ["cpu_usage_percent"],
                    "time_window": {"start": "2025-09-20T07:00:00", "end": "2025-09-20T07:02:00"},
                    "aggregation": "mean",
                },
            },
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 300, "completion_tokens": 100})

        result = gate_think_node(state, mock_llm, {}, config)

        assert "audit_trace" in result
        assert len(result["audit_trace"]) == 1
        assert result["audit_trace"][0].action_type == "gate_tool_call"


# =====================================================================
# Finalize node tests
# =====================================================================

class TestFinalizeNode:
    """Test FINALIZE node report generation."""

    def test_generates_diagnosis_report(self):
        """FINALIZE should produce a DiagnosisReport from LLM output."""
        from agent.finalize import finalize_node

        fc = _make_focus_context()
        proposal = ConclusionProposal(
            hypotheses=[_make_hypothesis(status="confirmed", confidence=0.9)],
            evidence=[_make_evidence()],
            proposed_diagnosis_type="single_fault",
            proposed_root_causes=[
                ProposedRootCause(
                    cause="CPU overload",
                    fault_type="cpu_fullload",
                    confidence=0.9,
                    evidence_ids=["e1"],
                )
            ],
        )
        audit_decision = AuditDecision(
            decision="pass",
            reason="Evidence sufficient",
        )
        state = OrchestratorState(
            run_id="test_run",
            inputs={},
            focus_context=fc,
            current_proposal=proposal,
            audit_decision=audit_decision,
            rehyp_count=0,
            round_count=1,
            diagnosis_trace=[],
            audit_trace=[],
            diagnosis_budget={"tool_calls_used": 3},
            audit_budget={"tool_calls_used": 0},
            report=None,
        )
        mock_llm = MagicMock()
        config = _make_config()

        llm_response = json.dumps({
            "anomaly_summary": "CPU overload detected",
            "diagnosis_type": "single_fault",
            "root_causes": [
                {
                    "cause": "CPU overload due to stress test",
                    "fault_type": "cpu_fullload",
                    "confidence": 0.9,
                    "evidence_ids": ["e1"],
                    "counter_evidence_ids": [],
                    "fpl_pattern_id": None,
                    "affected_nodes": [],
                }
            ],
            "derived_symptoms": [],
            "solutions": [
                {
                    "action": "Reduce CPU load",
                    "rationale": "High CPU utilization",
                    "risk": "low",
                    "verification": "Check CPU usage drops below 80%",
                    "applies_to_root_cause_index": 0,
                }
            ],
            "uncertainties": [],
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 300, "completion_tokens": 200})

        result = finalize_node(state, mock_llm, config)

        assert "report" in result
        report = result["report"]
        assert report.diagnosis_type == "single_fault"
        assert len(report.root_causes) == 1
        assert report.root_causes[0].fault_type == "cpu_fullload"
        assert report.trace_summary is not None


# =====================================================================
# ConclusionProposal builder test
# =====================================================================

class TestConclusionProposalBuilder:
    """Test _build_conclusion_proposal deterministic logic."""

    def test_single_confirmed_hypothesis(self):
        from agent.diagnosis import _build_conclusion_proposal

        h1 = _make_hypothesis(id="h1", status="confirmed", confidence=0.9)
        e1 = _make_evidence(id="e1", hyp_ids=["h1"])
        state = _make_diagnosis_state(hypotheses=[h1], evidence=[e1])

        proposal = _build_conclusion_proposal(state)

        assert proposal.proposed_diagnosis_type == "single_fault"
        assert len(proposal.proposed_root_causes) == 1
        assert proposal.proposed_root_causes[0].fault_type == "cpu_fullload"

    def test_multiple_confirmed_composite(self):
        from agent.diagnosis import _build_conclusion_proposal

        h1 = _make_hypothesis(id="h1", fault_type="cpu_fullload", status="confirmed", confidence=0.9)
        h2 = _make_hypothesis(id="h2", fault_type="mem_load_ram", subsystem="memory", status="confirmed", confidence=0.8)
        e1 = _make_evidence(id="e1", hyp_ids=["h1"])
        e2 = _make_evidence(id="e2", hyp_ids=["h2"])
        state = _make_diagnosis_state(hypotheses=[h1, h2], evidence=[e1, e2])

        proposal = _build_conclusion_proposal(state)

        assert proposal.proposed_diagnosis_type == "composite_fault"
        assert len(proposal.proposed_root_causes) == 2

    def test_no_confirmed_gives_partial(self):
        from agent.diagnosis import _build_conclusion_proposal

        h1 = _make_hypothesis(id="h1", status="active", confidence=0.6)
        state = _make_diagnosis_state(hypotheses=[h1])

        proposal = _build_conclusion_proposal(state)

        assert proposal.proposed_diagnosis_type == "partial"
