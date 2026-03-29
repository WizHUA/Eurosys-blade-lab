# agent/schema.py
# HPC-Diagnosis Agent v4 的代码级 Schema 定义。
# 直接用于 LangGraph State、LLM function calling 参数校验和最终报告吐出。

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator


# ===========================================================================
# §1. VerificationItem — Hypothesis 需要完成的单条核实任务
# ===========================================================================

class VerificationItem(BaseModel):
    """
    每条 Hypothesis 在 REACT_LOOP 期间需要完成的核实步骤。
    由 HYPOTHESIZE 节点从 FPL 的 verification_steps 字段生成，
    由 OBSERVE 节点在每次工具返回后更新 status。
    """
    description: str = Field(
        description="核实任务的自然语言描述，例如 'confirm cpu_usage p95 > 90% sustained for > 5 min'"
    )
    status: Literal["pending", "verified", "refuted", "unverifiable"] = Field(
        default="pending",
        description=(
            "pending: 尚未尝试核实；"
            "verified: 已通过工具观察确认；"
            "refuted: 工具观察结果否定了该步骤；"
            "unverifiable: 工具返回空数据或超时，无法判断"
        )
    )
    evidence_id: Optional[str] = Field(
        default=None,
        description="完成核实的证据 ID（来自 Evidence.id），pending 时为 null"
    )
    required_metrics: list[str] = Field(
        default_factory=list,
        description="完成该核实步骤所需查询的指标列表，用于 THINK 节点规划工具调用"
    )


# ===========================================================================
# §2. TopMetric — FocusContext 中每个异常指标的条目
# ===========================================================================

class TopMetric(BaseModel):
    """
    Triage 阶段识别出的 Top-K 异常指标之一。
    包含统计异常信息和时序位置，是 HYPOTHESIZE 节点的输入材料。
    """
    metric: str = Field(description="指标名，与 Metric KB 中的 name 字段一致")
    subsystem: str = Field(
        description="所属子系统：cpu | memory | swap | vmstat | disk | filesystem | network | load | processes"
    )
    direction: Literal["+", "-"] = Field(
        description="异常方向：+ 表示指标值偏高，- 表示偏低（如 cpu_idle 降低）"
    )
    score: float = Field(
        ge=0.0,
        description="异常分数（Z-score 或分位数偏离量），越大表示越异常，用于 Top-K 排序"
    )
    t_onset: datetime = Field(
        description="该指标首次超过异常阈值的时刻（ISO8601），用于时序因果排序"
    )
    onset_rank: int = Field(
        ge=1,
        description="在所有 Top-K 指标中按 t_onset 排序的序号，1 表示最早出现异常"
    )
    change_point: Optional[datetime] = Field(
        default=None,
        description="统计变化点时刻（PELT/BOCPD 算法输出），可能早于 t_onset；null 表示未检测到明显变化点"
    )
    peak_value: Optional[float] = Field(
        default=None,
        description="异常窗口内的峰值，用于初步强度估计"
    )
    baseline_mean: Optional[float] = Field(
        default=None,
        description="对应 Baseline Profile 的均值，用于计算相对偏离量"
    )


# ===========================================================================
# §3. FocusContext — Triage 阶段的完整输出（Stage 1 → Stage 2 的接口）
# ===========================================================================

class AnomalyWindow(BaseModel):
    start: datetime = Field(description="异常窗口开始时刻（故障注入时刻或首个异常检测时刻）")
    end: datetime = Field(description="异常窗口结束时刻（故障恢复时刻或数据截止时刻）")


class NodeSeverity(BaseModel):
    node: str = Field(description="节点名，如 blade01")
    severity: Literal["high", "medium", "low"] = Field(
        description="该节点的综合异常严重度，由子系统分数聚合决定"
    )


class JobOverlap(BaseModel):
    job_id: str = Field(description="Slurm Job ID")
    overlap_ratio: float = Field(
        ge=0.0, le=1.0,
        description="该 Job 的运行时间与异常窗口的时间重叠比例，1.0 表示完全重叠"
    )
    node_set: list[str] = Field(description="该 Job 分配的节点列表")


class FocusContext(BaseModel):
    """
    Triage Stage 的完整输出，注入到 AgentState.focus_context。
    是 HYPOTHESIZE 节点生成假设的主要输入。
    """
    run_id: str = Field(description="本次诊断运行的唯一 ID，格式如 run_20250920_070213")
    anomaly_window: AnomalyWindow
    top_metrics: list[TopMetric] = Field(
        description="按异常分数降序排列的 Top-K 异常指标列表，K 默认为 10"
    )
    causal_order: list[str] = Field(
        description=(
            "按 t_onset 升序排列的指标名列表（即时序因果先验）。"
            "列表第一个元素是最早出现异常的指标，是 HYPOTHESIZE 节点推断根因子系统的重要依据。"
            "示例：['cpu_usage_percent', 'load_1min', 'memory_usage_percent']"
        )
    )
    subsystem_scores: dict[str, float] = Field(
        description=(
            "各子系统的综合异常分数，键为子系统名（cpu/memory/network/disk），"
            "值为该子系统内所有异常指标分数的加权聚合。"
            "示例：{'cpu': 8.5, 'memory': 1.2, 'network': 0.3, 'disk': 0.1}"
        )
    )
    leading_subsystem: str = Field(
        description="子系统初判——Triage 认为最可能是首发异常的子系统（subsystem_scores 最高者）"
    )
    nodes: list[NodeSeverity] = Field(description="各节点的异常严重度列表")
    jobs: list[JobOverlap] = Field(description="时间窗口内的相关 Slurm 作业列表")
    triage_confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Triage 的自信度（0-1），基于以下因素计算："
            "Baseline Profile 匹配质量 × 数据完整率 × 异常信号强度。"
            "低于 0.5 时 HYPOTHESIZE 节点应生成更多假设以覆盖不确定性。"
        )
    )
    baseline_profile_used: str = Field(
        description="实际使用的 Baseline Profile ID，如 bp_npb_bt_4nodes 或 global（fallback）"
    )
    data_completeness: float = Field(
        ge=0.0, le=1.0,
        description="原始 metrics.csv 中非 NaN 的数据行比例，影响 Triage 可靠性"
    )


# ===========================================================================
# §4. Hypothesis — Agent 维护的单个候选假设
# ===========================================================================

class Hypothesis(BaseModel):
    """
    HYPOTHESIZE 节点生成、OBSERVE 节点更新、GATE 节点判断的假设单元。
    一次诊断可以并行维护 2-4 个 Hypothesis（对应多根因场景）。
    """
    id: str = Field(description="假设唯一标识，格式为 h1、h2、h3...（按生成顺序）")
    root_cause: str = Field(
        description="假设的根因自然语言描述，例如 'CPU fullload on blade01 caused by runaway ChaosBlade injection'"
    )
    fault_type: str = Field(
        description="对应的 FPL 故障类型，如 cpu_fullload、mem_load_ram；用于 KBRetrievalTool 检索"
    )
    subsystem: str = Field(
        description="该假设针对的主要子系统：cpu | memory | network | disk"
    )
    prior_confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "先验置信度（0-1）：若 KBRetrievalTool 命中 FPL 规则，取该规则的 confidence；"
            "若无命中，默认值 0.30；"
            "若 FocusContext.leading_subsystem 与本假设子系统一致，额外加 0.10。"
        )
    )
    current_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description=(
            "当前动态置信度，由 OBSERVE 节点在每次工具返回后更新："
            "current_confidence = prior_confidence + Σ(supporting_strength_score) - Σ(refuting_strength_score)，"
            "strength_score 映射：strong=0.35, medium=0.20, weak=0.10，上限截断至 [0, 1]"
        )
    )
    supporting_evidence_ids: list[str] = Field(
        default_factory=list,
        description="支持该假设的证据 ID 列表（来自 Evidence.id）"
    )
    refuting_evidence_ids: list[str] = Field(
        default_factory=list,
        description="反驳该假设的证据 ID 列表（来自 Evidence.id）"
    )
    required_verifications: list[VerificationItem] = Field(
        default_factory=list,
        description=(
            "需要完成的核实步骤列表，由 HYPOTHESIZE 节点从 FPL.verification_steps 生成。"
            "所有步骤 verified 时 GATE Rule B 的前置条件满足。"
        )
    )
    status: Literal["active", "confirmed", "refuted", "derived"] = Field(
        default="active",
        description=(
            "active: 正在积累证据；"
            "confirmed: GATE 节点判定已通过；"
            "refuted: 反驳证据超过支持证据，排除；"
            "derived: 判定为另一已 confirmed 假设的继发症状，不作为独立根因输出"
        )
    )
    fpl_pattern_id: Optional[str] = Field(
        default=None,
        description="命中的 FPL 规则 ID，如 fpl_001；未命中时为 null"
    )
    generated_at_step: int = Field(
        default=0,
        description="该假设在第几个 ReAct 步骤时生成（0 表示在 HYPOTHESIZE 节点初始生成）"
    )


# ===========================================================================
# §5. Evidence — 工具调用返回后生成的证据条目
# ===========================================================================

class Evidence(BaseModel):
    """
    每次工具调用后由 OBSERVE 节点生成的证据记录。
    所有证据写入 AgentState.evidence 列表（append-only）。
    注意：Evidence 只存储统计摘要（result_digest + raw_stats），
    不存储原始时序序列（防止 token 膨胀），原始数据由 MetricQueryTool 内部持久化。
    """
    id: str = Field(description="证据唯一标识，格式为 e1、e2、e3...（按生成顺序）")
    hypothesis_ids: list[str] = Field(
        description=(
            "该证据与哪些假设相关（一条证据可以同时涉及多个假设）。"
            "由 OBSERVE 节点基于 evidence.type 和 hypothesis.subsystem 匹配填充。"
        )
    )
    type: Literal["supporting", "refuting", "neutral"] = Field(
        description=(
            "supporting: 支持至少一个 hypothesis_ids 中的假设；"
            "refuting: 反驳至少一个假设；"
            "neutral: 证据存在但与任何假设无显著关联（记录存档，不影响置信度）"
        )
    )
    source_tool: Literal["MetricQueryTool", "KBRetrievalTool", "DataAnalysisTool"] = Field(
        description="产生该证据的工具名"
    )
    query_summary: str = Field(
        description=(
            "工具调用的简短描述，格式：'{metric}@{node}, {window}, {aggregation}'，"
            "例如：'cpu_usage_percent@blade01, 07:02-07:10, p95'。"
            "独立性去重时使用此字段做组合哈希。"
        )
    )
    result_digest: str = Field(
        description=(
            "工具返回结果的自然语言摘要（1-2 句话），直接写入 LLM 的 THINK 上下文。"
            "例如：'cpu_usage_percent p95=98.7% on blade01, sustained for 8 min'"
        )
    )
    raw_stats: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "结构化统计量，供 OBSERVE 节点做 strength 判定和 verification_status 更新。"
            "通常包含 {p95, mean, max, duration_sec, value} 等字段。"
        )
    )
    strength: Literal["strong", "medium", "weak", "none"] = Field(
        description=(
            "证据强度，由 OBSERVE 节点对照 Metric KB 中 strength_thresholds 确定性计算，"
            "不由 LLM 主观判断。"
            "'none' 表示工具返回了数据但不满足任何阈值（即无显著异常）。"
        )
    )
    strength_basis: str = Field(
        description=(
            "强度判定依据的规则描述，便于审计。"
            "例如：'Metric KB rule: cpu_usage p95 > 97% sustained > 300s → strong'"
        )
    )
    created_at_step: int = Field(
        description="该证据在第几个 ReAct 步骤时生成（对应 ReActStep.step_id）"
    )

    @field_validator("hypothesis_ids")
    @classmethod
    def hypothesis_ids_not_empty(cls, v: list[str]) -> list[str]:
        # neutral 类型证据也需要关联至少一个假设（否则记录意义不大）
        if len(v) == 0:
            raise ValueError("hypothesis_ids must contain at least one hypothesis ID")
        return v


# ===========================================================================
# §6. ReActStep — ReAct 循环的单步轨迹记录
# ===========================================================================

class ToolCall(BaseModel):
    tool: Literal["MetricQueryTool", "KBRetrievalTool", "DataAnalysisTool"] = Field(
        description="调用的工具名"
    )
    args: dict[str, Any] = Field(description="传递给工具的完整参数字典")
    call_id: str = Field(description="工具调用的唯一 ID，用于关联观察结果")


class ReActStep(BaseModel):
    """
    REACT_LOOP 中每个 THINK-ACT-OBSERVE 三元组的记录。
    完整保存至 AgentState.react_trace，用于可解释性和论文中的执行轨迹展示。
    """
    step_id: int = Field(ge=1, description="步骤序号，从 1 开始递增")
    thought: str = Field(
        description=(
            "THINK 节点（LLM）输出的推理文本。"
            "应包含：当前假设状态、上一步观察的影响、下一步行动的理由。"
            "不允许为空字符串（LLM 必须输出 Thought）。"
        )
    )
    action_type: Literal["tool_call", "conclude", "escalate"] = Field(
        description=(
            "tool_call: 调用工具获取更多证据；"
            "conclude: THINK 节点认为可以提前终止（仍需 GATE 验证）；"
            "escalate: 发现预期外的严重故障，请求提高 budget 上限"
        )
    )
    tool_call: Optional[ToolCall] = Field(
        default=None,
        description="当 action_type=tool_call 时，工具调用详情；否则为 null"
    )
    observation: Optional[str] = Field(
        default=None,
        description=(
            "OBSERVE 节点生成的观察摘要（result_digest），"
            "在 ACT 完成后填充；action_type!=tool_call 时为 null"
        )
    )
    evidence_generated: Optional[str] = Field(
        default=None,
        description="本步骤生成的 Evidence ID（如 e3），null 表示本步骤未生成新证据"
    )
    hypotheses_updated: list[str] = Field(
        default_factory=list,
        description="本步骤中状态或置信度被更新的假设 ID 列表"
    )
    timestamp: datetime = Field(description="本步骤开始执行的时刻")
    duration_ms: Optional[int] = Field(
        default=None,
        description="本步骤耗时（毫秒），包含 LLM 调用和工具调用时间"
    )


# ===========================================================================
# §7. DiagnosisReport 的子结构
# ===========================================================================

class RootCause(BaseModel):
    """DiagnosisReport.root_causes 中的单条根因记录。"""
    cause: str = Field(
        description="根因的自然语言描述，例如 'CPU fullload on blade01 injected by ChaosBlade'"
    )
    fault_type: str = Field(
        description="对应标准化故障类型，如 cpu_fullload、mem_load_ram、network_loss"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="最终置信度，来自对应 Hypothesis.current_confidence（FINALIZE 节点归一化后）"
    )
    evidence_ids: list[str] = Field(
        description="支持该根因的证据 ID 列表（来自 Evidence.id）"
    )
    counter_evidence_ids: list[str] = Field(
        default_factory=list,
        description="反驳该根因但被最终排除的证据 ID 列表（用于透明度）"
    )
    fpl_pattern_id: Optional[str] = Field(
        default=None,
        description="匹配的 FPL 规则 ID；null 表示结论纯靠 Agent 推理（无 KB 先验支撑）"
    )
    affected_nodes: list[str] = Field(
        default_factory=list,
        description="受该根因影响的节点列表"
    )


class DerivedSymptom(BaseModel):
    """DiagnosisReport.derived_symptoms 中的单条继发症状记录。"""
    symptom: str = Field(
        description="症状的自然语言描述，例如 'elevated CPU iowait due to memory swap pressure'"
    )
    caused_by_root_cause_index: int = Field(
        description="root_causes 列表中的索引（0-based），表示哪条根因导致了此症状"
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="观察到该症状的证据 ID"
    )


class Solution(BaseModel):
    """DiagnosisReport.solutions 中的单条解决方案。"""
    action: str = Field(
        description="具体操作描述，例如 'blade destroy create cpu fullload'"
    )
    rationale: str = Field(
        description="为何采取该操作的简要解释，应引用具体证据，如 'CPU p95=98.7% confirmed by e1'"
    )
    risk: Literal["low", "medium", "high"] = Field(
        description="执行该操作的风险等级：low/medium/high"
    )
    verification: str = Field(
        description="执行后如何验证问题已解决，例如 'monitor cpu_usage_percent drops below 80% within 2 min'"
    )
    applies_to_root_cause_index: Optional[int] = Field(
        default=None,
        description="该方案对应 root_causes 列表中的哪条根因（0-based 索引）；null 表示通用方案"
    )


class TraceSummary(BaseModel):
    """DiagnosisReport.trace_summary 中的执行摘要。"""
    triage_leading_subsystem: str = Field(description="Triage 初判的领先子系统")
    triage_confidence: float = Field(description="Triage 置信度分数")
    tools_used: list[str] = Field(
        description="按工具类型汇总的使用次数，例如 ['MetricQueryTool x3', 'KBRetrievalTool x1']"
    )
    total_tool_calls: int = Field(description="总工具调用次数")
    react_iterations: int = Field(description="ReAct 循环总迭代次数")
    total_tokens_in: int = Field(description="LLM 输入 token 总数（所有节点累加）")
    total_tokens_out: int = Field(description="LLM 输出 token 总数（所有节点累加）")
    gate_passed_by_rule: Optional[str] = Field(
        default=None,
        description="最终触发 GATE PASS 的规则名，如 Rule_A、Rule_B、Rule_C、Rule_D；"
                    "inconclusive 时为 null"
    )
    diagnosis_duration_sec: Optional[float] = Field(
        default=None,
        description="从 Triage 开始到 FINALIZE 完成的总耗时（秒）"
    )


# ===========================================================================
# §8. DiagnosisReport — 最终输出格式
# ===========================================================================

class DiagnosisReport(BaseModel):
    """
    Stage 2 FINALIZE 节点的输出，也是整个系统的最终产物。
    写入 AgentState.report，同时序列化到 data/<exp_id>/diagnosis_report.json。
    """
    run_id: str = Field(description="本次诊断运行 ID（与 FocusContext.run_id 一致）")
    anomaly_summary: str = Field(
        description=(
            "1-3 句话的异常摘要，例如："
            "'Node blade01 experienced simultaneous CPU fullload and memory pressure "
            "during NPB BT job execution between 07:02-07:03.'"
        )
    )
    diagnosis_type: Literal["single_fault", "composite_fault", "partial", "inconclusive"] = Field(
        description=(
            "single_fault: 恰好 1 条 confirmed 根因；"
            "composite_fault: ≥ 2 条独立 confirmed 根因（非 derived 关系）；"
            "partial: ≥ 1 条 confirmed 根因，但还有未解决的活跃假设（budget 耗尽）；"
            "inconclusive: 无任何 confirmed 根因（budget 耗尽或所有假设被 refuted）"
        )
    )
    root_causes: list[RootCause] = Field(
        description="所有 confirmed 根因列表（composite_fault 时有多条）"
    )
    derived_symptoms: list[DerivedSymptom] = Field(
        default_factory=list,
        description="观察到的继发症状（因果链次级效应，不作为根因处理）"
    )
    solutions: list[Solution] = Field(
        description="解决方案列表，按优先级排序（最紧急的操作排在最前）"
    )
    uncertainties: list[str] = Field(
        default_factory=list,
        description=(
            "未解决的不确定性描述，例如："
            "['h2 (disk_burn) could not be verified due to missing disk metrics on blade02']"
        )
    )
    trace_summary: TraceSummary
    generated_at: datetime = Field(description="报告生成时刻")

    @model_validator(mode="after")
    def check_root_causes_not_empty_for_non_inconclusive(self) -> "DiagnosisReport":
        if self.diagnosis_type != "inconclusive" and len(self.root_causes) == 0:
            raise ValueError(
                f"diagnosis_type='{self.diagnosis_type}' requires at least one root_cause"
            )
        return self


# ===========================================================================
# §9. AgentState — LangGraph TypedDict（核心状态，非 Pydantic）
# ===========================================================================
# 注意：LangGraph 要求 State 为 TypedDict（Python 标准库类型）。
# 嵌套的 Pydantic 对象以其 model 类型存储；序列化/反序列化由 LangGraph 框架处理。
# list 类型字段默认使用 Annotated[list, operator.add] 以支持并发 append 操作。

class AgentState(TypedDict):
    # --- 基础信息 ---
    run_id: str
    inputs: dict[str, str]  # keys: metrics_path, jobinfo_path, chaos_path(optional)

    # --- Stage 1 产物 ---
    focus_context: Optional[FocusContext]

    # --- 假设集合（多假设并行跟踪）---
    hypotheses: Annotated[list[Hypothesis], operator.add]

    # --- 证据库 ---
    evidence: Annotated[list[Evidence], operator.add]

    # --- ReAct 执行轨迹 ---
    react_trace: Annotated[list[ReActStep], operator.add]

    # --- 预算追踪 ---
    budget: dict[str, int]  # tool_calls_used, tool_calls_limit, tokens_in, tokens_out

    # --- 门控决策 ---
    gate_result: dict  # decision, reason, triggered_rule, active_hypothesis_ids, confirmed_hypothesis_ids

    # --- 最终报告 ---
    report: Optional[DiagnosisReport]


# ===========================================================================
# Self-test
# ===========================================================================

if __name__ == "__main__":
    from datetime import timezone

    print("=== Testing agent/schema.py ===\n")

    # Test VerificationItem
    vi = VerificationItem(
        description="confirm cpu_usage_percent p95 > 90% sustained for > 5 min",
        status="pending",
        required_metrics=["cpu_usage_percent"],
    )
    assert vi.status == "pending"
    print(f"[OK] VerificationItem: {vi.description[:50]}...")

    # Test TopMetric
    now = datetime.now(tz=timezone.utc)
    tm = TopMetric(
        metric="cpu_usage_percent",
        subsystem="cpu",
        direction="+",
        score=8.5,
        t_onset=now,
        onset_rank=1,
        peak_value=98.7,
        baseline_mean=78.5,
    )
    assert tm.onset_rank == 1
    print(f"[OK] TopMetric: {tm.metric}, score={tm.score}, peak={tm.peak_value}")

    # Test FocusContext
    fc = FocusContext(
        run_id="run_20250920_070213",
        anomaly_window=AnomalyWindow(start=now, end=now),
        top_metrics=[tm],
        causal_order=["cpu_usage_percent", "load_1min"],
        subsystem_scores={"cpu": 8.5, "memory": 1.2},
        leading_subsystem="cpu",
        nodes=[NodeSeverity(node="blade01", severity="high")],
        jobs=[JobOverlap(job_id="12345", overlap_ratio=0.95, node_set=["blade01"])],
        triage_confidence=0.88,
        baseline_profile_used="global",
        data_completeness=0.97,
    )
    assert fc.leading_subsystem == "cpu"
    print(f"[OK] FocusContext: run_id={fc.run_id}, leading={fc.leading_subsystem}")

    # Test Hypothesis
    h1 = Hypothesis(
        id="h1",
        root_cause="CPU fullload on blade01, likely caused by ChaosBlade cpu-fullload injection",
        fault_type="cpu_fullload",
        subsystem="cpu",
        prior_confidence=0.90,
        current_confidence=0.90,
        fpl_pattern_id="fpl_001",
        required_verifications=[vi],
    )
    assert h1.status == "active"
    print(f"[OK] Hypothesis: {h1.id}, fault_type={h1.fault_type}, confidence={h1.current_confidence}")

    # Test Evidence
    e1 = Evidence(
        id="e1",
        hypothesis_ids=["h1"],
        type="supporting",
        source_tool="MetricQueryTool",
        query_summary="cpu_usage_percent@blade01, 07:02-07:10, p95",
        result_digest="cpu_usage_percent p95=98.7% on blade01, sustained for 8 min",
        raw_stats={"p95": 98.7, "mean": 97.2, "duration_sec": 480},
        strength="strong",
        strength_basis="Metric KB rule: cpu_usage p95 > 97% sustained > 300s → strong",
        created_at_step=1,
    )
    assert e1.strength == "strong"
    print(f"[OK] Evidence: {e1.id}, type={e1.type}, strength={e1.strength}")

    # Test Evidence validation: hypothesis_ids cannot be empty
    try:
        Evidence(
            id="e_bad",
            hypothesis_ids=[],
            type="neutral",
            source_tool="MetricQueryTool",
            query_summary="test",
            result_digest="test",
            strength="none",
            strength_basis="no anomaly",
            created_at_step=1,
        )
        print("[FAIL] Evidence with empty hypothesis_ids should have raised ValueError")
    except ValueError:
        print("[OK] Evidence validation: empty hypothesis_ids correctly rejected")

    # Test ReActStep
    step = ReActStep(
        step_id=1,
        thought="Initial analysis shows CPU subsystem is the most anomalous. I will query cpu_usage_percent to verify.",
        action_type="tool_call",
        tool_call=ToolCall(
            tool="MetricQueryTool",
            args={"metric": "cpu_usage_percent", "node": "blade01", "window_minutes": 10},
            call_id="tc_001",
        ),
        observation="cpu_usage_percent p95=98.7% on blade01, sustained for 8 min",
        evidence_generated="e1",
        hypotheses_updated=["h1"],
        timestamp=now,
        duration_ms=1250,
    )
    assert step.step_id == 1
    print(f"[OK] ReActStep: step_id={step.step_id}, action={step.action_type}")

    # Test DiagnosisReport
    report = DiagnosisReport(
        run_id="run_20250920_070213",
        anomaly_summary="Node blade01 experienced CPU fullload during NPB BT job between 07:02-07:03.",
        diagnosis_type="single_fault",
        root_causes=[
            RootCause(
                cause="CPU fullload on blade01 injected by ChaosBlade",
                fault_type="cpu_fullload",
                confidence=0.92,
                evidence_ids=["e1"],
                fpl_pattern_id="fpl_001",
                affected_nodes=["blade01"],
            )
        ],
        solutions=[
            Solution(
                action="blade destroy create cpu fullload",
                rationale="CPU p95=98.7% confirmed by e1, matching fpl_001 pattern",
                risk="low",
                verification="monitor cpu_usage_percent drops below 80% within 2 min",
                applies_to_root_cause_index=0,
            )
        ],
        trace_summary=TraceSummary(
            triage_leading_subsystem="cpu",
            triage_confidence=0.88,
            tools_used=["MetricQueryTool x1", "KBRetrievalTool x1"],
            total_tool_calls=2,
            react_iterations=1,
            total_tokens_in=1200,
            total_tokens_out=350,
            gate_passed_by_rule="Rule_A",
            diagnosis_duration_sec=12.5,
        ),
        generated_at=now,
    )
    assert report.diagnosis_type == "single_fault"
    assert len(report.root_causes) == 1
    print(f"[OK] DiagnosisReport: type={report.diagnosis_type}, root_causes={len(report.root_causes)}")

    # Test DiagnosisReport validation: non-inconclusive must have root_causes
    try:
        DiagnosisReport(
            run_id="run_test",
            anomaly_summary="test",
            diagnosis_type="single_fault",
            root_causes=[],  # should fail
            solutions=[],
            trace_summary=TraceSummary(
                triage_leading_subsystem="cpu",
                triage_confidence=0.5,
                tools_used=[],
                total_tool_calls=0,
                react_iterations=0,
                total_tokens_in=0,
                total_tokens_out=0,
            ),
            generated_at=now,
        )
        print("[FAIL] DiagnosisReport with empty root_causes+single_fault should have raised ValueError")
    except ValueError:
        print("[OK] DiagnosisReport validation: empty root_causes for single_fault correctly rejected")

    print("\n=== All schema tests passed ✓ ===")
