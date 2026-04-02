"""agent/tests/test_nodes.py — B-type contract tests for LLM-driven nodes.

Tests node scaffolding and state transitions with mocked LLM.
"""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
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
    required_verifications: list[VerificationItem] | None = None,
) -> Hypothesis:
    return Hypothesis(
        id=id,
        root_cause=f"Test {fault_type}",
        fault_type=fault_type,
        subsystem=subsystem,
        prior_confidence=confidence,
        current_confidence=confidence,
        status=status,
        required_verifications=required_verifications or [],
    )


def _make_evidence(
    id: str = "e1",
    hyp_ids: list[str] | None = None,
    ev_type: str = "supporting",
    source_tool: str = "MetricQueryTool",
) -> Evidence:
    return Evidence(
        id=id,
        hypothesis_ids=hyp_ids or ["h1"],
        type=ev_type,
        source_tool=source_tool,
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

    def test_normalizes_metrics_field_in_required_verifications(self):
        """LLM may emit metrics instead of required_metrics; parser should normalize it."""
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
                    {"description": "Check CPU usage > 90%", "metrics": ["cpu_usage_percent"]}
                ],
            }
        ])
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 100, "completion_tokens": 50})

        result = hypothesize_node(state, mock_llm, mock_tools, config)

        assert result["hypotheses"][0].required_verifications[0].required_metrics == ["cpu_usage_percent"]


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
        # v6: THINK conclude does NOT mark confirmed — Audit Agent is the gate
        # All hypotheses should remain "active" after conclude
        for h in result["hypotheses"]:
            assert h.status == "active", f"Hypothesis {h.id} should remain active after conclude, got {h.status}"

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

    def test_injects_tool_descriptions_into_system_prompt(self):
        """THINK prompt should include concrete tool schema so the LLM knows required args."""
        from agent.diagnosis import think_node

        state = _make_diagnosis_state(hypotheses=[_make_hypothesis()])
        config = _make_config()
        mock_llm = MagicMock()
        mock_llm.call.return_value = (
            json.dumps({
                "thought": "Need evidence",
                "hypothesis_updates": [],
                "action": {"type": "conclude", "reasoning": "stop"},
            }),
            {"prompt_tokens": 100, "completion_tokens": 50},
        )

        mock_tool = MagicMock()
        mock_tool.get_schema.return_value = {
            "description": "查询指定指标",
            "parameters": {
                "properties": {
                    "metrics": {"type": "array", "description": "指标名"},
                    "time_window": {"type": "object", "description": "时间窗口"},
                },
                "required": ["metrics", "time_window"],
            },
        }

        think_node(state, mock_llm, config, {"MetricQueryTool": mock_tool})

        messages = mock_llm.call.call_args.args[0]
        assert "MetricQueryTool" in messages[0]["content"]
        assert "time_window" in messages[0]["content"]


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

    def test_fills_metric_query_defaults_from_focus_context(self):
        """ACT should fill missing MetricQueryTool window/aggregation and fallback metric."""
        from agent.diagnosis import act_node
        from agent.schema import ReActStep, ToolCall

        h1 = _make_hypothesis(
            required_verifications=[
                VerificationItem(
                    description="Check CPU usage > 90%",
                    required_metrics=["cpu_usage_percent"],
                )
            ]
        )
        state = _make_diagnosis_state(hypotheses=[h1])
        step = ReActStep(
            step_id=1,
            thought="Check CPU",
            action_type="tool_call",
            tool_call=ToolCall(tool="MetricQueryTool", args={}, call_id="c1"),
            timestamp=datetime.now(),
        )
        state["react_trace"] = [step]

        mock_tool = MagicMock()
        mock_tool.df = pd.DataFrame(columns=["cpu_usage_percent"])
        mock_tool.execute.return_value = {"results": [], "missing": []}
        config = _make_config()

        act_node(state, {"MetricQueryTool": mock_tool}, config)

        execute_args = mock_tool.execute.call_args.args[0]
        assert execute_args["metrics"] == ["cpu_usage_percent"]
        assert execute_args["aggregation"] == "mean"
        assert execute_args["time_window"]["start"] == "2025-09-20T07:00:00"
        assert execute_args["time_window"]["end"] == "2025-09-20T07:02:00"


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

    def test_metric_evidence_is_not_broadcast_to_all_hypotheses(self):
        """Metric evidence should only attach to hypotheses whose verification metrics match."""
        from agent.diagnosis import observe_node
        from agent.schema import ReActStep, ToolCall

        h1 = _make_hypothesis(
            id="h1",
            fault_type="cpu_fullload",
            subsystem="cpu",
            required_verifications=[
                VerificationItem(
                    description="Check CPU usage > 90%",
                    required_metrics=["cpu_usage_percent"],
                )
            ],
        )
        h2 = _make_hypothesis(
            id="h2",
            fault_type="mem_load_ram",
            subsystem="memory",
            required_verifications=[
                VerificationItem(
                    description="Check memory usage",
                    required_metrics=["memory_usage_percent"],
                )
            ],
        )
        state = _make_diagnosis_state(hypotheses=[h1, h2])
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

        ev = result["evidence"][0]
        assert ev.hypothesis_ids == ["h1"]

    def test_kb_retrieval_is_neutral_and_unbound(self):
        """KB hits provide prior context only and must not directly support a hypothesis."""
        from agent.diagnosis import observe_node
        from agent.schema import ReActStep, ToolCall

        h1 = _make_hypothesis(id="h1", fault_type="cpu_fullload", subsystem="cpu")
        h2 = _make_hypothesis(id="h2", fault_type="disk_burn", subsystem="disk")
        state = _make_diagnosis_state(hypotheses=[h1, h2])
        step = ReActStep(
            step_id=1,
            thought="Check FPL matches",
            action_type="tool_call",
            tool_call=ToolCall(tool="KBRetrievalTool", args={
                "mode": "pattern_match",
                "subsystem": "disk",
                "anomaly_metrics": ["cpu_usage_percent"],
            }, call_id="c1"),
            timestamp=datetime.now(),
        )
        state["react_trace"] = [step]
        state["_tool_result"] = {
            "pattern_hits": [
                {"pattern_id": "p1", "fault_type": "disk_burn", "confidence": 0.8, "match_score": 0.7}
            ]
        }

        result = observe_node(state)

        ev = result["evidence"][0]
        assert ev.type == "neutral"
        assert ev.hypothesis_ids == []

    def test_metric_query_on_triage_anomaly_is_supporting(self):
        """Queries hitting triage top metrics should count as direct supporting evidence."""
        from agent.diagnosis import observe_node
        from agent.schema import ReActStep, ToolCall

        h1 = _make_hypothesis(
            id="h1",
            fault_type="cpu_fullload",
            subsystem="cpu",
            required_verifications=[
                VerificationItem(
                    description="Check CPU usage > 90%",
                    required_metrics=["cpu_usage_percent"],
                )
            ],
        )
        state = _make_diagnosis_state(hypotheses=[h1])
        step = ReActStep(
            step_id=1,
            thought="Check CPU anomaly directly",
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
            "results": [{"metric": "cpu_usage_percent", "value": 12.0, "aggregation": "mean"}],
            "missing": [],
        }

        result = observe_node(state)

        ev = result["evidence"][0]
        assert ev.type == "supporting"
        assert ev.hypothesis_ids == ["h1"]


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
        """FINALIZE uses proposal root_causes (not LLM output) for root_causes."""
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

        # LLM only returns narrative fields now (no root_causes)
        llm_response = json.dumps({
            "anomaly_summary": "CPU overload detected",
            "uncertainties": [],
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 300, "completion_tokens": 200})

        result = finalize_node(state, mock_llm, config)

        assert "report" in result
        report = result["report"]
        assert report.diagnosis_type == "single_fault"
        # root_causes must come from proposal, not LLM output
        assert len(report.root_causes) == 1
        assert report.root_causes[0].fault_type == "cpu_fullload"
        assert report.root_causes[0].confidence == 0.9
        assert report.trace_summary is not None

    def test_root_causes_not_from_llm(self):
        """root_causes must come from proposal even if LLM returns different ones."""
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
        state = OrchestratorState(
            run_id="no_hallucination",
            inputs={},
            focus_context=fc,
            current_proposal=proposal,
            audit_decision=None,
            rehyp_count=0,
            round_count=1,
            diagnosis_trace=[],
            audit_trace=[],
            diagnosis_budget={},
            audit_budget={},
            report=None,
        )
        mock_llm = MagicMock()
        # LLM tries to add extra hallucinated root cause — should be ignored
        llm_response = json.dumps({
            "anomaly_summary": "CPU and network issue",
            "root_causes": [
                {"cause": "...", "fault_type": "network_loss", "confidence": 0.7,
                 "evidence_ids": [], "counter_evidence_ids": [], "fpl_pattern_id": None, "affected_nodes": []},
            ],
            "uncertainties": ["some uncertainty"],
        })
        mock_llm.call.return_value = (llm_response, {"prompt_tokens": 200, "completion_tokens": 100})

        result = finalize_node(state, mock_llm, _make_config())
        report = result["report"]

        # Must only contain cpu_fullload from proposal, not the hallucinated network_loss
        assert len(report.root_causes) == 1
        assert report.root_causes[0].fault_type == "cpu_fullload"


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

    def test_no_confirmed_uses_third_fallback(self):
        """Active hypothesis with no evidence triggers third fallback (all non-refuted)."""
        from agent.diagnosis import _build_conclusion_proposal

        h1 = _make_hypothesis(id="h1", status="active", confidence=0.6)
        state = _make_diagnosis_state(hypotheses=[h1])

        proposal = _build_conclusion_proposal(state)

        # Third fallback: all non-refuted hypotheses become candidates
        assert proposal.proposed_diagnosis_type == "single_fault"
        assert len(proposal.proposed_root_causes) == 1

    def test_proposal_excludes_non_refuted_hypotheses_without_evidence(self):
        """Only supported or otherwise investigated hypotheses should enter the proposal."""
        from agent.diagnosis import _build_conclusion_proposal

        h1 = _make_hypothesis(id="h1", confidence=0.8)
        h2 = _make_hypothesis(id="h2", fault_type="mem_load_ram", subsystem="memory", confidence=0.3)
        h3 = _make_hypothesis(id="h3", fault_type="disk_burn", subsystem="disk", status="refuted", confidence=0.1)
        e1 = _make_evidence(id="e1", hyp_ids=["h1"])
        state = _make_diagnosis_state(hypotheses=[h1, h2, h3], evidence=[e1])

        proposal = _build_conclusion_proposal(state)

        assert len(proposal.proposed_root_causes) == 1
        fault_types = [rc.fault_type for rc in proposal.proposed_root_causes]
        assert "cpu_fullload" in fault_types
        assert "mem_load_ram" not in fault_types
        assert "disk_burn" not in fault_types
        assert proposal.proposed_diagnosis_type == "single_fault"

    def test_all_refuted_gives_partial(self):
        """When all hypotheses are refuted, should give partial."""
        from agent.diagnosis import _build_conclusion_proposal

        h1 = _make_hypothesis(id="h1", status="refuted", confidence=0.1)
        state = _make_diagnosis_state(hypotheses=[h1])

        proposal = _build_conclusion_proposal(state)

        assert proposal.proposed_diagnosis_type == "partial"
        assert len(proposal.proposed_root_causes) == 0

    def test_kb_only_evidence_uses_third_fallback(self):
        """KB retrieval alone does not qualify via Tier 1/2, but third fallback includes non-refuted."""
        from agent.diagnosis import _build_conclusion_proposal

        h1 = _make_hypothesis(id="h1", confidence=0.8)
        e1 = _make_evidence(id="e1", hyp_ids=["h1"], ev_type="supporting", source_tool="KBRetrievalTool")
        state = _make_diagnosis_state(hypotheses=[h1], evidence=[e1])

        proposal = _build_conclusion_proposal(state)

        # KB-only doesn't qualify via Tier 1/2, but third fallback picks it up
        assert proposal.proposed_diagnosis_type == "single_fault"
        assert len(proposal.proposed_root_causes) == 1

    def test_leading_subsystem_observational_fallback_is_allowed(self):
        """With neutral evidence only, second-tier fallback includes all investigated hypotheses."""
        from agent.diagnosis import _build_conclusion_proposal

        h1 = _make_hypothesis(id="h1", fault_type="cpu_fullload", subsystem="cpu", confidence=0.8)
        h2 = _make_hypothesis(id="h2", fault_type="disk_burn", subsystem="disk", confidence=0.9)
        e1 = _make_evidence(id="e1", hyp_ids=["h1"], ev_type="neutral", source_tool="MetricQueryTool")
        e2 = _make_evidence(id="e2", hyp_ids=["h2"], ev_type="neutral", source_tool="MetricQueryTool")
        state = _make_diagnosis_state(hypotheses=[h1, h2], evidence=[e1, e2])

        proposal = _build_conclusion_proposal(state)

        # Both investigated non-refuted hypotheses qualify (subsystem-agnostic fallback)
        assert proposal.proposed_diagnosis_type == "composite_fault"
        fault_types = [rc.fault_type for rc in proposal.proposed_root_causes]
        assert "disk_burn" in fault_types
        assert "cpu_fullload" in fault_types


# =====================================================================
# Reflect integration tests
# =====================================================================

class TestReflectIntegration:
    """Test that Reflect is properly integrated into orchestrator."""

    def test_should_reflect_triggers(self):
        """should_reflect returns True for confirmed diagnoses."""
        from agent.reflect import should_reflect
        from agent.schema import DiagnosisReport, RootCause, TraceSummary

        report = DiagnosisReport(
            run_id="test",
            anomaly_summary="test",
            diagnosis_type="single_fault",
            root_causes=[RootCause(
                cause="CPU overload",
                fault_type="cpu_fullload",
                confidence=0.9,
                evidence_ids=["e1"],
            )],
            trace_summary=TraceSummary(
                triage_leading_subsystem="cpu",
                triage_confidence=0.8,
                main_tools_used=["MetricQueryTool x1"],
                audit_tools_used=[],
                total_tool_calls=1,
                main_iterations=2,
                audit_iterations=1,
                total_tokens_in=100,
                total_tokens_out=50,
            ),
            generated_at=datetime.now(),
        )
        config = AgentConfig()
        assert should_reflect(report, config) is True

    def test_should_reflect_skips_inconclusive(self):
        """should_reflect returns False for inconclusive diagnoses."""
        from agent.reflect import should_reflect
        from agent.schema import DiagnosisReport, RootCause, TraceSummary

        report = DiagnosisReport(
            run_id="test",
            anomaly_summary="test",
            diagnosis_type="inconclusive",
            root_causes=[],
            trace_summary=TraceSummary(
                triage_leading_subsystem="cpu",
                triage_confidence=0.8,
                main_tools_used=[],
                audit_tools_used=[],
                total_tool_calls=0,
                main_iterations=0,
                audit_iterations=0,
                total_tokens_in=0,
                total_tokens_out=0,
            ),
            generated_at=datetime.now(),
        )
        config = AgentConfig()
        assert should_reflect(report, config) is False
