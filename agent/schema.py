# agent/schema.py
# HPC-Diagnosis Agent v6 Schema.
# Three-layer State (Orchestrator / Diagnosis / Audit) + Pydantic models.

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, model_validator


# ===========================================================================
# §1. VerificationItem
# ===========================================================================

class VerificationItem(BaseModel):
    """核实步骤，由 HYPOTHESIZE 从 FPL 的 verification_steps 生成。"""
    description: str
    status: Literal["pending", "verified", "refuted", "unverifiable"] = "pending"
    evidence_id: Optional[str] = None
    required_metrics: list[str] = Field(default_factory=list)


# ===========================================================================
# §2. TopMetric
# ===========================================================================

class TopMetric(BaseModel):
    """Triage 识别的 Top-K 异常指标条目。"""
    metric: str
    subsystem: str
    direction: Literal["+", "-"]
    score: float = Field(ge=0.0)
    t_onset: datetime
    onset_rank: int = Field(ge=1)
    change_point: Optional[datetime] = None


# ===========================================================================
# §3. FocusContext — Triage 输出
# ===========================================================================

class AnomalyWindow(BaseModel):
    start: datetime
    end: datetime


class NodeSeverity(BaseModel):
    node: str
    severity: Literal["high", "medium", "low"]


class JobOverlap(BaseModel):
    job_id: str
    overlap_ratio: float = Field(ge=0.0, le=1.0)
    node_set: list[str] = Field(default_factory=list)


class FocusContext(BaseModel):
    """Triage Stage 完整输出 — Stage 1 → Stage 2 接口。"""
    run_id: str
    anomaly_window: AnomalyWindow
    top_metrics: list[TopMetric] = Field(default_factory=list)
    causal_order: list[str] = Field(default_factory=list)
    subsystem_scores: dict[str, float] = Field(default_factory=dict)
    leading_subsystem: str
    nodes: list[NodeSeverity] = Field(default_factory=list)
    jobs: list[JobOverlap] = Field(default_factory=list)
    triage_confidence: float = Field(ge=0.0, le=1.0)


# ===========================================================================
# §4. Hypothesis
# ===========================================================================

class Hypothesis(BaseModel):
    """候选假设，由 HYPOTHESIZE 生成，THINK 更新，Audit 审查。"""
    id: str
    root_cause: str
    fault_type: str
    subsystem: str
    prior_confidence: float = Field(ge=0.0, le=1.0)
    current_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    required_verifications: list[VerificationItem] = Field(default_factory=list)
    status: Literal["active", "confirmed", "refuted", "derived"] = "active"
    fpl_pattern_id: str | None = None


# ===========================================================================
# §5. Evidence
# ===========================================================================

class Evidence(BaseModel):
    """工具调用后由 OBSERVE 生成的证据。v6: 移除 strength/strength_basis。"""
    id: str
    hypothesis_ids: list[str]
    type: Literal["supporting", "refuting", "neutral", "mixed"]
    source_tool: Literal["MetricQueryTool", "KBRetrievalTool", "DataAnalysisTool"]
    query_summary: str
    result_digest: str
    raw_stats: dict[str, Any] = Field(default_factory=dict)
    created_at_step: int


# ===========================================================================
# §6. ThinkOutput — THINK 节点结构化 LLM 输出
# ===========================================================================

class HypothesisUpdate(BaseModel):
    hypothesis_id: str
    new_confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class ThinkAction(BaseModel):
    type: Literal["tool_call", "conclude"]
    tool: str | None = None
    args: dict[str, Any] | None = None
    reasoning: str


class ThinkOutput(BaseModel):
    """THINK 节点的结构化 LLM 输出。"""
    thought: str
    hypothesis_updates: list[HypothesisUpdate] = Field(default_factory=list)
    action: ThinkAction


# ===========================================================================
# §7. ConclusionProposal & AuditDecision — 信息隔离边界
# ===========================================================================

class ProposedRootCause(BaseModel):
    cause: str
    fault_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class ConclusionProposal(BaseModel):
    """Diagnosis Agent → Orchestrator → Audit Agent 的唯一信息通道。"""
    hypotheses: list[Hypothesis]
    evidence: list[Evidence]
    proposed_diagnosis_type: Literal["single_fault", "composite_fault", "partial"]
    proposed_root_causes: list[ProposedRootCause]


class AuditDecision(BaseModel):
    """Audit Agent 裁决输出。"""
    decision: Literal["pass", "continue", "rehypothesize", "degrade"]
    reason: str
    hint: str | None = None
    diagnosis_type: Literal[
        "single_fault", "composite_fault", "partial", "inconclusive"
    ] | None = None


# ===========================================================================
# §8. AuditEvidence & AuditStep
# ===========================================================================

class AuditEvidence(BaseModel):
    """Audit Agent 独立证据。"""
    id: str
    source_tool: Literal["MetricQueryTool", "KBRetrievalTool"]
    query_summary: str
    result_digest: str
    raw_stats: dict[str, Any] = Field(default_factory=dict)
    target_hypothesis_ids: list[str] = Field(default_factory=list)
    purpose: str


class ToolCall(BaseModel):
    tool: Literal["MetricQueryTool", "KBRetrievalTool", "DataAnalysisTool"]
    args: dict[str, Any] = Field(default_factory=dict)
    call_id: str = ""


class AuditStep(BaseModel):
    """Audit Agent ReAct 轨迹。"""
    step_id: int = Field(ge=1)
    thought: str
    action_type: Literal["gate_tool_call", "decision"]
    tool_call: ToolCall | None = None
    observation: str | None = None
    audit_evidence_generated: str | None = None
    timestamp: datetime


# ===========================================================================
# §9. ReActStep — Diagnosis Agent ReAct 轨迹
# ===========================================================================

class ReActStep(BaseModel):
    step_id: int = Field(ge=1)
    thought: str
    action_type: Literal["tool_call", "conclude"]
    tool_call: ToolCall | None = None
    observation: str | None = None
    evidence_generated: str | None = None
    hypotheses_updated: list[str] = Field(default_factory=list)
    timestamp: datetime
    duration_ms: int | None = None


# ===========================================================================
# §10. DiagnosisReport 子结构
# ===========================================================================

class RootCause(BaseModel):
    cause: str
    fault_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    fpl_pattern_id: str | None = None
    affected_nodes: list[str] = Field(default_factory=list)


class DerivedSymptom(BaseModel):
    symptom: str
    caused_by_root_cause_index: int
    evidence_ids: list[str] = Field(default_factory=list)


class Solution(BaseModel):
    action: str
    rationale: str
    risk: Literal["low", "medium", "high"]
    verification: str
    applies_to_root_cause_index: int | None = None


class TraceSummary(BaseModel):
    """v6: 拆分 Diagnosis/Audit 统计，移除 gate_passed_by_rule。"""
    triage_leading_subsystem: str
    triage_confidence: float
    main_tools_used: list[str] = Field(default_factory=list)
    audit_tools_used: list[str] = Field(default_factory=list)
    total_tool_calls: int
    main_iterations: int
    audit_iterations: int
    total_tokens_in: int
    total_tokens_out: int
    diagnosis_duration_sec: float | None = None


class DiagnosisReport(BaseModel):
    """最终诊断报告。"""
    run_id: str
    anomaly_summary: str
    diagnosis_type: Literal["single_fault", "composite_fault", "partial", "inconclusive"]
    root_causes: list[RootCause] = Field(default_factory=list)
    derived_symptoms: list[DerivedSymptom] = Field(default_factory=list)
    solutions: list[Solution] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    trace_summary: TraceSummary
    generated_at: datetime

    @model_validator(mode="after")
    def check_root_causes(self) -> "DiagnosisReport":
        if self.diagnosis_type != "inconclusive" and len(self.root_causes) == 0:
            raise ValueError(
                f"diagnosis_type='{self.diagnosis_type}' requires at least one root_cause"
            )
        return self


# ===========================================================================
# §11. Three-layer State (TypedDict for LangGraph)
# ===========================================================================

class OrchestratorState(TypedDict):
    run_id: str
    inputs: dict
    focus_context: FocusContext | None
    current_proposal: ConclusionProposal | None
    audit_decision: AuditDecision | None
    rehyp_count: int
    round_count: int
    diagnosis_trace: list[ReActStep]
    audit_trace: list[AuditStep]
    diagnosis_budget: dict
    audit_budget: dict
    report: DiagnosisReport | None


class DiagnosisState(TypedDict):
    run_id: str
    focus_context: FocusContext
    hypotheses: list[Hypothesis]
    evidence: list[Evidence]
    react_trace: list[ReActStep]
    gate_hint: str | None
    budget: dict
    rehyp_count: int


class AuditState(TypedDict):
    run_id: str
    focus_context: FocusContext
    proposal: ConclusionProposal
    audit_evidence: list[AuditEvidence]
    audit_trace: list[AuditStep]
    audit_budget: dict
    previous_hint: str | None
