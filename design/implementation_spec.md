# HPC-Diagnosis Agent 全流程实现需求文档

> 本文档基于 `design/v6.md` 设计规格，定义从代码实现到验收测试的全部细节。  
> 后续工程实现 **完全参照本文档**，不再回溯设计文档做推断。  
> 版本：v1.0 | 日期：2026-03-28

---

## 第 1 章：总体架构与模块关系

### 1.1 目标文件结构

```
agent/
  __init__.py
  schema.py              # v6 Pydantic schema + 三层 TypedDict State
  config.py              # 全局配置常量、消融开关、模型参数
  triage.py              # Stage 1: Triage（确定性 Python）
  diagnosis.py           # Diagnosis Agent（diagnosis_graph）
  audit.py               # Audit Agent（audit_graph）
  orchestrator.py        # Orchestrator（orchestrator_graph + 总入口）
  reflect.py             # Stage 3: Reflect + FPL 写回
  finalize.py            # FINALIZE 节点（LLM 生成报告）
  llm_client.py          # OpenRouter LLM 调用封装
  prompts/
    __init__.py
    diagnosis_system.md
    diagnosis_hypothesize.md
    diagnosis_think.md
    audit_system.md
    finalize.md
    reflect.md
  tools/
    __init__.py
    metric_query.py       # MetricQueryTool
    kb_retrieval.py       # KBRetrievalTool
    data_analysis.py      # DataAnalysisTool
  kb/                     # (已有，保持不变)
    metrics.yaml
    fpl.jsonl
    chroma_db/
    build_kb.py
eval/
  __init__.py
  evaluate.py             # 评估框架（GT 比对）
  ablation.py             # 消融实验运行器
run_agent.py              # 总入口脚本
```

### 1.2 模块依赖图

```
run_agent.py
  ├── agent/config.py          (全局配置)
  ├── agent/orchestrator.py    (总入口)
  │     ├── agent/triage.py          (Stage 1)
  │     │     ├── agent/kb/metrics.yaml   (Metric KB)
  │     │     └── agent/tools/metric_query.py (数据读取复用)
  │     ├── agent/diagnosis.py       (Stage 2 - Diagnosis Agent)
  │     │     ├── agent/tools/*            (3 个工具)
  │     │     ├── agent/prompts/*          (prompt 模板)
  │     │     └── agent/llm_client.py      (LLM 调用)
  │     ├── agent/audit.py           (Stage 2 - Audit Agent)
  │     │     ├── agent/tools/metric_query.py
  │     │     ├── agent/tools/kb_retrieval.py
  │     │     ├── agent/prompts/audit_system.md
  │     │     └── agent/llm_client.py
  │     ├── agent/finalize.py        (FINALIZE)
  │     └── agent/reflect.py        (Stage 3)
  │           └── agent/kb/fpl.jsonl (写回)
  └── eval/evaluate.py        (评估框架)
```

### 1.3 数据流全景

```
metrics.csv + jobinfo.csv
        │
        ▼
  ┌─ triage.py ──────────────────────────────────────────┐
  │  pd.read_csv → Z-score → PELT → FocusContext         │
  └──────────────────┬───────────────────────────────────┘
                     │ FocusContext (Pydantic)
                     ▼
  ┌─ orchestrator.py ─────────────────────────────────────┐
  │                                                        │
  │  INVOKE_DIAGNOSIS ──┐                                  │
  │    diagnosis.py     │ DiagnosisState                   │
  │    ├ HYPOTHESIZE    │  → hypotheses[]                  │
  │    ├ THINK (LLM)    │  → thought + action              │
  │    ├ ACT            │  → tool 原始输出                  │
  │    └ OBSERVE        │  → Evidence + result_digest      │
  │    (循环至 conclude) │                                  │
  │                     │ ConclusionProposal               │
  │  SUBMIT_TO_AUDIT ──┐│                                  │
  │    audit.py        ││ AuditState                       │
  │    ├ GATE_THINK    ││  → 逻辑审查 / gate_tool_call     │
  │    ├ GATE_ACT      ││  → 受限工具调用                   │
  │    └ GATE_OBSERVE  ││  → audit_evidence                │
  │                    ││ AuditDecision                    │
  │  ROUTE_DECISION ───┘│                                  │
  │    pass → FINALIZE   │                                 │
  │    continue → 重启诊断 (带 hint)                        │
  │    rehypothesize → 重启假设                             │
  │    degrade → FINALIZE (partial)                        │
  │                                                        │
  │  FINALIZE ─── finalize.py ─── DiagnosisReport (JSON)   │
  └────────────────────────────────────────────────────────┘
        │
        ▼
  ┌─ reflect.py ─────────────────────────────────┐
  │  LLM 提炼 → FPL 去重写入 → fpl.jsonl 回写     │
  └───────────────────────────────────────────────┘
        │
        ▼
  输出: agent/data/<exp_id>/
    diagnosis_report.json   # DiagnosisReport
    execution_trace.json    # DiagnosisTrace + AuditTrace
    focus_context.json      # Triage 产物
```

### 1.4 三个 LangGraph StateGraph 的调用关系

```python
# orchestrator.py 伪代码
orchestrator_graph = StateGraph(OrchestratorState)

# 节点注册
orchestrator_graph.add_node("INVOKE_DIAGNOSIS", invoke_diagnosis)
orchestrator_graph.add_node("SUBMIT_TO_AUDIT", submit_to_audit)
orchestrator_graph.add_node("ROUTE_DECISION", route_decision)
orchestrator_graph.add_node("FINALIZE", finalize_node)

# invoke_diagnosis 内部:
#   diagnosis_state = build_diagnosis_state(orchestrator_state)
#   result = diagnosis_graph.invoke(diagnosis_state)
#   proposal = extract_conclusion_proposal(result)
#   return {"current_proposal": proposal}

# submit_to_audit 内部:
#   audit_state = build_audit_state(orchestrator_state)
#   result = audit_graph.invoke(audit_state)
#   decision = extract_audit_decision(result)
#   return {"audit_decision": decision}
```

关键设计：diagnosis_graph 和 audit_graph 是**独立编译的 StateGraph**，由 orchestrator_graph 的节点函数通过 `.invoke()` 调用。三个图有各自独立的 State 类型，不共享内存。

---

## 第 2 章：schema.py 升级规格（v4 → v6）

### 2.1 变更总览

| 变更类型 | 项目 | 说明 |
|---------|------|------|
| **删除** | `Evidence.strength` | 移除确定性强度标签 |
| **删除** | `Evidence.strength_basis` | 同上 |
| **删除** | `Hypothesis.supporting_evidence_ids` | 改为 evidence 池统一管理 |
| **删除** | `Hypothesis.refuting_evidence_ids` | 同上 |
| **删除** | `Hypothesis.generated_at_step` | 简化 |
| **删除** | `AgentState` (旧版单图) | 拆为三层 State |
| **删除** | `FocusContext.baseline_profile_used` | v6 废弃 Baseline Profile |
| **删除** | `FocusContext.data_completeness` | 简化 |
| **删除** | `TopMetric.peak_value` | 简化 |
| **删除** | `TopMetric.baseline_mean` | 废弃 Baseline Profile |
| **删除** | `TraceSummary.gate_passed_by_rule` | Audit Agent 不再基于规则 |
| **修改** | `Evidence.type` | 新增 `"mixed"` 选项 |
| **修改** | `Hypothesis.current_confidence` | 更新机制改为 THINK 每轮报告 |
| **修改** | `ReActStep.action_type` | 移除 `"escalate"` |
| **修改** | `TraceSummary` | 拆分 Diagnosis/Audit 统计 |
| **新增** | `OrchestratorState` | Orchestrator 层 TypedDict |
| **新增** | `DiagnosisState` | Diagnosis Agent 层 TypedDict |
| **新增** | `AuditState` | Audit Agent 层 TypedDict |
| **新增** | `ConclusionProposal` | 信息隔离边界 |
| **新增** | `AuditDecision` | Audit Agent 裁决输出 |
| **新增** | `AuditEvidence` | Audit Agent 独立证据 |
| **新增** | `AuditStep` | Audit Agent ReAct 轨迹 |
| **新增** | `ThinkOutput` | THINK 节点结构化输出 |

### 2.2 新增 Schema 完整定义

#### ConclusionProposal

```python
class ConclusionProposal(BaseModel):
    """Diagnosis Agent → Orchestrator → Audit Agent 的唯一信息通道"""
    hypotheses: list[Hypothesis]
    evidence: list[Evidence]
    proposed_diagnosis_type: Literal[
        "single_fault", "composite_fault", "partial"
    ]
    proposed_root_causes: list[ProposedRootCause]

class ProposedRootCause(BaseModel):
    cause: str
    fault_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str]
```

ConclusionProposal **不包含**：`react_trace`、THINK 的 `thought`、`budget` 详情。

#### AuditDecision

```python
class AuditDecision(BaseModel):
    decision: Literal["pass", "continue", "rehypothesize", "degrade"]
    reason: str
    hint: str | None = None        # 仅 continue 时非空
    diagnosis_type: Literal[
        "single_fault", "composite_fault", "partial", "inconclusive"
    ] | None = None
```

#### AuditEvidence

```python
class AuditEvidence(BaseModel):
    id: str                           # 格式 ae1, ae2, ...
    source_tool: Literal["MetricQueryTool", "KBRetrievalTool"]
    query_summary: str
    result_digest: str
    raw_stats: dict[str, Any] = Field(default_factory=dict)
    target_hypothesis_ids: list[str]
    purpose: str                      # 审查目的说明
```

#### AuditStep

```python
class AuditStep(BaseModel):
    step_id: int = Field(ge=1)
    thought: str
    action_type: Literal["gate_tool_call", "decision"]
    tool_call: ToolCall | None = None
    observation: str | None = None
    audit_evidence_generated: str | None = None  # ae1, ae2, ...
    timestamp: datetime
```

#### ThinkOutput

```python
class ThinkOutput(BaseModel):
    """THINK 节点的结构化 LLM 输出"""
    thought: str
    hypothesis_updates: list[HypothesisUpdate]  # 各假设的 confidence 更新
    action: ThinkAction

class HypothesisUpdate(BaseModel):
    hypothesis_id: str
    new_confidence: float = Field(ge=0.0, le=1.0)
    reason: str  # 一句话说明更新理由

class ThinkAction(BaseModel):
    type: Literal["tool_call", "conclude"]
    tool: str | None = None            # tool_call 时非空
    args: dict[str, Any] | None = None # tool_call 时非空
    reasoning: str
```

### 2.3 修改后的 Evidence

```python
class Evidence(BaseModel):
    id: str
    hypothesis_ids: list[str]
    type: Literal["supporting", "refuting", "neutral", "mixed"]
    source_tool: Literal["MetricQueryTool", "KBRetrievalTool", "DataAnalysisTool"]
    query_summary: str
    result_digest: str
    raw_stats: dict[str, Any] = Field(default_factory=dict)
    created_at_step: int
```

**删除的字段**：`strength`、`strength_basis`。OBSERVE 不再打标，证据质量由 THINK/Audit Agent LLM 自主判断。

### 2.4 修改后的 Hypothesis

```python
class Hypothesis(BaseModel):
    id: str
    root_cause: str
    fault_type: str
    subsystem: str
    prior_confidence: float = Field(ge=0.0, le=1.0)
    current_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    required_verifications: list[VerificationItem] = Field(default_factory=list)
    status: Literal["active", "confirmed", "refuted", "derived"]
    fpl_pattern_id: str | None = None
```

**删除的字段**：`supporting_evidence_ids`、`refuting_evidence_ids`、`generated_at_step`。证据归属通过 `Evidence.hypothesis_ids` 反查。

**current_confidence 更新机制**：THINK 每轮在 `ThinkOutput.hypothesis_updates` 中报告各假设的最新 confidence。约束：无新证据时 confidence 不允许上升。

### 2.5 修改后的 FocusContext

```python
class FocusContext(BaseModel):
    run_id: str
    anomaly_window: AnomalyWindow
    top_metrics: list[TopMetric]
    causal_order: list[str]
    subsystem_scores: dict[str, float]
    leading_subsystem: str
    nodes: list[NodeSeverity]
    jobs: list[JobOverlap]
    triage_confidence: float = Field(ge=0.0, le=1.0)
```

**删除**：`baseline_profile_used`、`data_completeness`。

TopMetric **删除**：`peak_value`、`baseline_mean`。

### 2.6 三层 State 定义

```python
class OrchestratorState(TypedDict):
    run_id: str
    inputs: dict                         # {metrics_path, jobinfo_path}
    focus_context: FocusContext | None
    current_proposal: ConclusionProposal | None
    audit_decision: AuditDecision | None
    rehyp_count: int
    round_count: int
    diagnosis_trace: list[ReActStep]     # 从 DiagnosisState 拷贝（存档用）
    audit_trace: list[AuditStep]         # 从 AuditState 拷贝（存档用）
    diagnosis_budget: dict               # 从 DiagnosisState 拷贝 {tool_calls_used, tokens_in, tokens_out}
    audit_budget: dict                   # 从 AuditState 拷贝 {tool_calls_used}
    report: DiagnosisReport | None

class DiagnosisState(TypedDict):
    run_id: str
    focus_context: FocusContext
    hypotheses: list[Hypothesis]
    evidence: list[Evidence]
    react_trace: list[ReActStep]
    gate_hint: str | None
    budget: dict                         # {tool_calls_used, tool_calls_limit, tokens_in, tokens_out}
    rehyp_count: int

class AuditState(TypedDict):
    run_id: str
    focus_context: FocusContext
    proposal: ConclusionProposal
    audit_evidence: list[AuditEvidence]
    audit_trace: list[AuditStep]
    audit_budget: dict                   # {tool_calls_used, tool_calls_limit, max_rounds}
    previous_hint: str | None
```

### 2.7 修改后的 TraceSummary

```python
class TraceSummary(BaseModel):
    triage_leading_subsystem: str
    triage_confidence: float
    main_tools_used: list[str]           # ["MetricQueryTool x3", ...]
    audit_tools_used: list[str]          # ["KBRetrievalTool x1", ...]
    total_tool_calls: int
    main_iterations: int                 # Diagnosis Agent ReAct 迭代数
    audit_iterations: int                # Audit Agent 迭代数
    total_tokens_in: int
    total_tokens_out: int
    diagnosis_duration_sec: float | None = None
```

### 2.8 ReActStep 修改

```python
class ReActStep(BaseModel):
    step_id: int = Field(ge=1)
    thought: str
    action_type: Literal["tool_call", "conclude"]   # 移除 "escalate"
    tool_call: ToolCall | None = None
    observation: str | None = None
    evidence_generated: str | None = None
    hypotheses_updated: list[str] = Field(default_factory=list)
    timestamp: datetime
    duration_ms: int | None = None
```

---

## 第 3 章：config.py — 全局配置

```python
"""agent/config.py — 全局配置常量"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

# ===========================================================================
# 路径常量
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
KB_DIR = PROJECT_ROOT / "agent" / "kb"
METRICS_YAML = KB_DIR / "metrics.yaml"
FPL_JSONL = KB_DIR / "fpl.jsonl"
CHROMA_DIR = KB_DIR / "chroma_db"
FORMALTEST_DIR = PROJECT_ROOT / "dataset_builder" / "data" / "formaltest" / "extracted_data"
OUTPUT_DIR = PROJECT_ROOT / "agent" / "data"

# ===========================================================================
# LLM 配置
# ===========================================================================
@dataclass
class LLMConfig:
    base_url: str = "https://openrouter.ai/api/v1"
    model_name: str = "deepseek/deepseek-chat"
    api_key: str = ""                    # 从 .env 加载，不硬编码
    temperature: float = 0.3
    max_tokens: int = 4096
    retry_max: int = 3
    retry_delay_sec: float = 2.0
    timeout_sec: float = 120.0

# ===========================================================================
# Budget 配置
# ===========================================================================
@dataclass
class BudgetConfig:
    # Diagnosis Agent
    tool_calls_limit: int = 15
    max_react_iterations: int = 15   # 超过后强制 conclude
    # Audit Agent
    audit_tool_calls_limit: int = 3
    audit_max_rounds: int = 2
    # Orchestrator
    max_rehyp: int = 1               # 最多 1 次重新假设
    max_orchestrator_rounds: int = 3  # Diagnosis-Audit 最大交互轮次

# ===========================================================================
# Triage 超参数
# ===========================================================================
@dataclass
class TriageConfig:
    baseline_window_points: int = 5    # 前 N 个采样点作为基线
    z_score_threshold: float = 3.0     # Z-score 异常阈值
    persistence_ratio: float = 0.3     # 连续超阈值比例
    top_k: int = 10                    # 保留 Top-K 异常指标
    min_anomaly_duration_sec: int = 30 # 最短异常持续时间

# ===========================================================================
# 消融开关
# ===========================================================================
@dataclass
class AblationFlags:
    """每个 flag 为 True 时表示启用该组件（默认全启用 = Full 版本）"""
    enable_triage: bool = True         # Abl-A: False 时跳过 Triage
    enable_audit: bool = True          # Abl-B: False 时跳过 Audit Agent

# ===========================================================================
# 综合配置
# ===========================================================================
@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    triage: TriageConfig = field(default_factory=TriageConfig)
    ablation: AblationFlags = field(default_factory=AblationFlags)
    output_dir: Path = OUTPUT_DIR

    @classmethod
    def from_ablation_id(cls, ablation_id: str) -> "AgentConfig":
        """根据消融实验 ID 构建配置"""
        flags_map = {
            "Full": AblationFlags(),
            "Abl-A": AblationFlags(enable_triage=False),
            "Abl-B": AblationFlags(enable_audit=False),
        }
        if ablation_id not in flags_map:
            raise ValueError(f"Unknown ablation_id: {ablation_id}, must be one of {list(flags_map)}")
        return cls(ablation=flags_map[ablation_id])
```

消融实验编号与 flag 的映射：

| 编号 | 消融方式 | AblationFlags 设置 |
|------|---------|-------------------|
| Full | 无消融 | 全默认 |
| Abl-A | 无 Triage | `enable_triage=False` |
| Abl-B | 无 Audit Agent | `enable_audit=False` |

---

## 第 4 章：Stage 1 — Triage (triage.py)

### 4.1 模块接口

```python
def run_triage(
    metrics_path: str | Path,
    jobinfo_path: str | Path,
    metric_kb: list[dict],       # 从 metrics.yaml 加载
    config: TriageConfig,
    ablation: AblationFlags,
    run_id: str,
) -> FocusContext:
    """
    Stage 1 入口。纯 Python 确定性计算，不调用 LLM。
    
    消融行为:
    - ablation.enable_triage = False: 直接构建 fallback FocusContext
    """
```

### 4.2 Step 1：异常指标筛选

#### 4.2.1 数据加载

```python
def _load_metrics(metrics_path: str | Path) -> pd.DataFrame:
    """
    加载 metrics.csv，设置 timestamp 为 DatetimeIndex。
    跳过全常量列（std == 0）和纯计数器列（monotonically increasing）。
    返回: DataFrame，index = DatetimeIndex，columns = 数值指标名
    """
    df = pd.read_csv(metrics_path, parse_dates=["timestamp"], index_col="timestamp")
    # 移除全常量列
    df = df.loc[:, df.std() > 0]
    return df
```

#### 4.2.2 数据自适应基线

```python
def _compute_baseline(series: pd.Series, n_baseline: int = 5) -> tuple[float, float]:
    """
    取前 n_baseline 个采样点的均值和标准差作为基线。
    若 std < 1e-10，使用 abs(mean) * 0.01 作为最小 std（防除零）。
    返回: (baseline_mean, baseline_std)
    """
```

理由：v6 明确废弃 Baseline Profile，改用**实验自身数据**计算基线。前 N 个点通常在故障注入前（注入时间通常在实验开始后 ~60s），因此可作为正常基线。

`n_baseline` 默认 5（= 5×15s = 75s 窗口），通过 `TriageConfig.baseline_window_points` 配置。

#### 4.2.3 Z-score 异常评分

```python
def _zscore_anomaly(
    df: pd.DataFrame,
    n_baseline: int,
    z_threshold: float,
    persistence_ratio: float,
) -> list[dict]:
    """
    对每列:
    1. 计算基线 (mean, std)
    2. Z-score = (value - mean) / std
    3. 判断异常方向: 若多数异常点 > 基线 → direction = "+"，否则 "-"
    4. 持久性过滤: 连续超 z_threshold 的点数 / 总点数 >= persistence_ratio
    5. 得分 = 超阈值 Z-score 均值
    
    返回: [{"metric": str, "score": float, "direction": "+|-", "z_scores": Series}]
            按 score 降序排列
    """
```

#### 4.2.4 变化点检测

```python
def _detect_onset(
    series: pd.Series,
    z_scores: pd.Series,
    z_threshold: float,
) -> tuple[datetime, datetime | None]:
    """
    1. t_onset: z_scores 首次连续超过 z_threshold 的时间点
    2. change_point: 使用 ruptures.Pelt (model="rbf", min_size=3, penalty=5)
       - 若检测到变化点且在 t_onset 之前，返回该点
       - 否则返回 None
    
    依赖: ruptures 库（需加入 requirements.txt）
    Fallback: 若 ruptures 不可用，change_point = None
    
    返回: (t_onset, change_point)
    """
```

#### 4.2.5 Step 1 组装

```python
def _step1_anomaly_scoring(
    df: pd.DataFrame,
    metric_kb: list[dict],
    config: TriageConfig,
) -> list[TopMetric]:
    """
    1. 调用 _zscore_anomaly → 候选异常指标列表
    2. 取 Top-K (config.top_k)
    3. 对每个候选调用 _detect_onset → t_onset, change_point
    4. 从 metric_kb 查找 subsystem（未命中 → "unknown"）
    5. 按 t_onset 升序分配 onset_rank
    
    返回: list[TopMetric]（按 score 降序）
    """
```

### 4.3 Step 2：时序因果排序

```python
def _step2_temporal_ordering(
    top_metrics: list[TopMetric],
    ablation: AblationFlags,
) -> tuple[list[str], dict[str, float], str]:
    """
    1. causal_order: 按 t_onset 升序排列指标名
    2. subsystem_scores: 按 subsystem 分组，sum(score)
    3. leading_subsystem: max(subsystem_scores)
    
    返回: (causal_order, subsystem_scores, leading_subsystem)
    """
```

### 4.4 Step 3：FocusContext 构建

```python
def _step3_build_context(
    run_id: str,
    top_metrics: list[TopMetric],
    causal_order: list[str],
    subsystem_scores: dict[str, float],
    leading_subsystem: str,
    df: pd.DataFrame,
    jobinfo_path: str | Path,
) -> FocusContext:
    """
    1. anomaly_window:
       - start = min(t_onset for all top_metrics)
       - end = df.index[-1]（数据截止时间）
    2. nodes: 从 metrics.csv 中提取（单节点环境固定为实验配置节点）
       - severity: 按 subsystem_scores 最高项判定
         - 最高 subsystem_score > 8.0 → "high"
         - > 4.0 → "medium"
         - otherwise → "low"
    3. jobs: 解析 jobinfo.csv（| 分隔），与 anomaly_window 做时间交集
       - overlap_ratio = 交集时长 / job 运行时长
       - 只保留 overlap_ratio > 0 的 job
    4. triage_confidence:
       confidence = min(1.0, top_score / 10.0) * signal_ratio
       其中:
       - top_score = top_metrics[0].score（最高异常分）
       - signal_ratio = len(top_metrics) / config.top_k
    
    返回: FocusContext
    """
```

### 4.5 Fallback FocusContext（Abl-A）

当 `enable_triage = False` 时，跳过 Step 1-3，构建 fallback:

```python
def _build_fallback_context(
    run_id: str,
    metrics_path: str | Path,
    jobinfo_path: str | Path,
    metric_kb: list[dict],
) -> FocusContext:
    """
    全量摘要模式（Abl-A）:
    1. 对所有数值列计算 mean/std
    2. top_metrics = 前 20 个 std 最大的列，score = std, direction = "+"
    3. causal_order = 按列顺序
    4. subsystem_scores = 均分
    5. triage_confidence = 0.1（表示无 Triage 辅助）
    """
```

### 4.6 指标名 → 子系统映射

Triage 需要将 metrics.csv 的列名映射到 subsystem。映射源：

1. **优先**：metric_kb（metrics.yaml）中的 `name → subsystem`
2. **兜底**：按列名前缀规则匹配

```python
# 前缀规则映射表（兜底）
SUBSYSTEM_PREFIX_MAP: dict[str, str] = {
    "cpu_": "cpu",
    "load_": "load",
    "context_switches": "cpu",
    "interrupts": "cpu",
    "process_": "system",
    "processes_": "system",
    "memory_": "memory",
    "cache_": "memory",
    "buffer_": "memory",
    "anon_memory": "memory",
    "swap_": "swap",
    "page_": "memory",
    "vm_": "memory",
    "disk_": "disk",
    "filesystem_": "filesystem",
    "filefd_": "filesystem",
    "network_": "network",
    "tcp_": "network",
    "udp_": "network",
    "icmp_": "network",
    "sockets_": "network",
    "systemd_": "system",
    "system_": "system",
    "time_": "system",
    "entropy_": "system",
    "softnet_": "network",
    "arp_": "network",
    "cpu_pressure": "cpu",
    "memory_pressure": "memory",
    "io_pressure": "disk",
    "schedstat_": "cpu",
    "node_netstat_": "network",
}
```

subsystem 聚合时使用的分组（4 大类 + 辅助类）：

| subsystem | 涵盖 | 主要用于 |
|-----------|------|---------|
| cpu | cpu, load, schedstat | cpu_fullload 故障 |
| memory | memory, swap, cache, buffer, page, vm | mem_load 故障 |
| network | network, tcp, udp, icmp, sockets, softnet, arp | network_loss 故障 |
| disk | disk, filesystem, io_pressure | disk_burn / disk_fill 故障 |
| system | system, systemd, time, entropy, process | 辅助，不作为 leading_subsystem |

subsystem_scores 按 4 大类聚合（cpu, memory, network, disk），system 类指标不参与 leading_subsystem 竞争。


## 第 5 章：Stage 2 — Diagnosis Agent (diagnosis.py)

### 5.1 StateGraph 定义

```python
from langgraph.graph import StateGraph, END

def build_diagnosis_graph(
    tools: dict,           # {"MetricQueryTool": ..., "KBRetrievalTool": ..., "DataAnalysisTool": ...}
    llm_client: LLMClient,
    config: AgentConfig,
) -> StateGraph:
    graph = StateGraph(DiagnosisState)
    
    graph.add_node("HYPOTHESIZE", hypothesize_node)
    graph.add_node("THINK", think_node)
    graph.add_node("ACT", act_node)
    graph.add_node("OBSERVE", observe_node)
    
    graph.set_entry_point("HYPOTHESIZE")
    graph.add_edge("HYPOTHESIZE", "THINK")
    graph.add_conditional_edges("THINK", think_router, {
        "tool_call": "ACT",
        "conclude": END,
    })
    graph.add_edge("ACT", "OBSERVE")
    graph.add_edge("OBSERVE", "THINK")
    
    return graph.compile()
```

### 5.2 HYPOTHESIZE 节点

```python
def hypothesize_node(state: DiagnosisState) -> dict:
    """
    基于 FocusContext + FPL Top-K 命中生成 2-4 个候选假设。
    
    输入来源:
      - state["focus_context"]: FocusContext
      - state["gate_hint"]: str | None（rehyp 时非空）
      - state["rehyp_count"]: int
      - state["hypotheses"]: list[Hypothesis]（rehyp 时含 refuted 假设）
      - FPL 检索结果（通过 KBRetrievalTool.pattern_match）
    
    LLM 调用:
      - prompt = diagnosis_hypothesize.md 模板 + 变量注入
      - 结构化输出: JSON list[{id, root_cause, fault_type, subsystem, prior_confidence}]
    
    处理逻辑:
      1. 调用 KBRetrievalTool.pattern_match 获取 FPL 命中
      2. 构建 prompt:
         - 注入 FocusContext 的 top_metrics, causal_order, leading_subsystem
         - 注入 FPL 命中结果（if any）
         - if rehyp_count > 0:
           - 注入 refuted 假设列表 + 驳回理由
           - 附加指令: "不要重复已 refuted 的 fault_type"
      3. 调用 LLM，解析输出为 list[Hypothesis]
      4. prior_confidence 设置:
         - FPL 命中 → 取规则的 confidence
         - 未命中 → 0.30
         - leading_subsystem 匹配 → +0.10
      5. 假设 ID 续编: rehyp 时从最大现有 ID + 1 开始
    
    返回: {"hypotheses": new_hypotheses}
    """
```

**HYPOTHESIZE prompt 模板关键段落**（`prompts/diagnosis_hypothesize.md`）：

```markdown
## 任务
你是 HPC 故障诊断专家。基于以下 Triage 结果，生成 2-4 个最可能的根因假设。

## Triage 结果
- Leading Subsystem: {leading_subsystem}
- Top 异常指标（按异常分数降序）:
{top_metrics_formatted}
- 时序因果序列（最早出现异常 → 最晚）:
{causal_order}
- 子系统分数: {subsystem_scores}

## 知识库命中
{fpl_hits_formatted}

{rehyp_section}

## 输出要求
返回 JSON 数组，每个元素:
{
  "id": "h1",
  "root_cause": "一句话描述根因",
  "fault_type": "标准故障类型名（如 cpu_fullload, mem_load_ram）",
  "subsystem": "cpu | memory | network | disk",
  "prior_confidence": 0.0-1.0
}
```

### 5.3 THINK 节点

```python
def think_node(state: DiagnosisState) -> dict:
    """
    ReAct 循环核心: 结合证据状态决定下一步行动或尝试下结论。
    
    输入来源:
      - state 全量（hypotheses, evidence, react_trace, gate_hint, budget）
    
    LLM 调用:
      - prompt = diagnosis_think.md 模板 + 变量注入
      - 结构化输出: ThinkOutput (JSON)
    
    处理逻辑:
      1. 检查 budget:
         - if tool_calls_used >= tool_calls_limit:
           system prompt 注入: "你必须在本轮输出 conclude"
         - if len(react_trace) >= max_react_iterations:
           同上
      2. 构建 prompt:
         - 注入当前 hypotheses（各自 confidence + status）
         - 注入 evidence pool（result_digest 列表）
         - 注入 verification 状态（哪些已完成、哪些 pending）
         - if gate_hint: 注入 "审查反馈: {gate_hint}，请优先响应"
      3. 调用 LLM → ThinkOutput
      4. 更新 hypotheses confidence:
         for update in think_output.hypothesis_updates:
           找到对应 hypothesis，更新 current_confidence
           约束: 若无新证据（本轮 step > 1 且上一步无 evidence），不允许上升
      5. 记录 ReActStep
      6. 若 action.type == "conclude":
         将所有 active 且 confidence > 0.5 的假设标记为 confirmed
         diagnosis_graph 终止（路由到 END）
         注: ConclusionProposal 由 orchestrator 的 invoke_diagnosis_node 从
             返回的 DiagnosisState 中构建（见 §5.6 + §7.2），不在 THINK 内部构建
    
    返回:
      {"react_trace": [new_step], "hypotheses": updated_hypotheses}
    """
```

#### THINK 的强制 conclude 机制

```python
def _check_force_conclude(state: DiagnosisState, config: AgentConfig) -> bool:
    budget = state["budget"]
    return (
        budget["tool_calls_used"] >= config.budget.tool_calls_limit
        or len(state["react_trace"]) >= config.budget.max_react_iterations
    )
```

当需要强制 conclude 时，在 THINK 的 system prompt 末尾追加：

> **[系统强制指令]** 你的工具调用预算已耗尽。你必须在本轮输出中将 action.type 设为 "conclude"。如果证据不足以确认任何假设，请输出 diagnosis_type = "partial" 或 "inconclusive"。

若 THINK 仍违规输出 tool_call，**ACT 节点拒绝执行**，将 action_type 改写为 conclude 并重新走 conclude 逻辑。

### 5.4 ACT 节点

```python
def act_node(state: DiagnosisState) -> dict:
    """
    确定性节点: 执行工具调用。
    
    输入:
      - 最新 ReActStep 中的 tool_call（tool, args, call_id）
    
    处理逻辑:
      1. 路由到对应工具:
         tools[tool_call.tool].execute(tool_call.args)
      2. budget 计数: tool_calls_used += 1
      3. 超时处理: 设 30s 超时，超时返回 error 结果
      4. 结果写入 state（临时，OBSERVE 会处理）
    
    返回: {"budget": updated_budget, "_tool_result": raw_result}
    """
```

### 5.5 OBSERVE 节点

```python
def observe_node(state: DiagnosisState) -> dict:
    """
    确定性节点: 压缩工具输出，生成 Evidence。
    
    职责（4 件事，不多不少）:
      1. 结果压缩 → result_digest（1-2 句话摘要）
      2. 统计量提取 → raw_stats
      3. verification 对齐 → 标记哪些 VerificationItem 被触达
      4. 异常处理 → 工具超时/缺失/空结果编码为结构化状态
    
    OBSERVE 不做:
      - 不打 strength 标签
      - 不决定 hypothesis confirmed/refuted
      - 不决定是否 FINALIZE
    
    Evidence 构建:
      - id: "e{n}"（自增）
      - hypothesis_ids: 从 tool_call.args 中推断关联假设
        - MetricQueryTool: 查询的 metric 属于哪些假设的 subsystem
        - KBRetrievalTool: 直接关联检索对应的假设
        - DataAnalysisTool: 分析涉及的指标对应的假设
      - type: 由工具结果与假设预期对比决定（启发式规则）
        - MetricQueryTool: 查询指标属于假设 subsystem 且数值异常 → "supporting"；
          属于假设 subsystem 但数值正常 → "refuting"；不属于任何假设 subsystem → "neutral"
        - KBRetrievalTool (metric_lookup): 查到 related_faults 包含假设 fault_type → "supporting"；
          不包含 → "neutral"
        - KBRetrievalTool (pattern_match): 命中 fault_type 匹配 → "supporting"；命中但不匹配 → "mixed"
        - DataAnalysisTool: correlation > 0.7 于假设预期方向 → "supporting"；
          correlation < -0.5 → "refuting"；否则 → "neutral"
        - 多个假设同时涉及时 → "mixed"
        - 注: 此判断仅为初步标记，THINK 和 Audit Agent 可在推理中覆盖
      - source_tool: 工具名
      - query_summary: 格式化查询描述
      - result_digest: 摘要
      - raw_stats: 关键数值提取
    
    verification 更新:
      遍历所有 active 假设的 required_verifications:
        若 verification.required_metrics 与当前查询指标有交集:
          根据 result_digest 判断 → "verified" / "refuted" / 保持 "pending"
          写入 evidence_id
    
    返回: {"evidence": [new_evidence], "hypotheses": updated_hypotheses}
    """
```

#### result_digest 生成规则

```python
def _generate_digest(tool_name: str, args: dict, result: dict) -> str:
    """
    不调用 LLM，纯模板化生成:
    
    MetricQueryTool:
      "{metric}: {aggregation}={value}{unit}, window={start}-{end}"
    
    KBRetrievalTool:
      metric_lookup: "Metric KB: {metric} belongs to {subsystem}, normal_range=[{min},{max}]"
      pattern_match: "FPL match: {pattern_id} ({fault_type}), confidence={confidence}"
    
    DataAnalysisTool:
      "Analysis({analysis_type}): {summary}"
    """
```

### 5.6 ConclusionProposal 构建

当 THINK 输出 `action.type == "conclude"` 时：

```python
def _build_conclusion_proposal(state: DiagnosisState) -> ConclusionProposal:
    """
    1. 从 hypotheses 中选取:
       - confirmed: status == "confirmed"
       - active 且 confidence > 0.5: 视为候选
    2. 确定 proposed_diagnosis_type:
       - 恰好 1 个 confirmed → single_fault
       - >=2 个 confirmed → composite_fault
       - 有 confirmed 但还有 active → partial
       - 无 confirmed → partial (由 Audit 判定降级)
    3. 构建 proposed_root_causes:
       按 confidence 降序排列
    4. evidence: 只包含与 confirmed/active 假设相关的 evidence
    """
```

### 5.7 路由函数

```python
def think_router(state: DiagnosisState) -> str:
    """
    读取最新 ReActStep 的 action_type:
      "tool_call" → "tool_call"
      "conclude" → "conclude"
    """
    latest = state["react_trace"][-1]
    return latest.action_type
```

---

## 第 6 章：Stage 2 — Audit Agent (audit.py)

### 6.1 StateGraph 定义

```python
def build_audit_graph(
    tools: dict,           # {"MetricQueryTool": ..., "KBRetrievalTool": ...}
    llm_client: LLMClient,
    config: AgentConfig,
) -> StateGraph:
    graph = StateGraph(AuditState)
    
    graph.add_node("GATE_THINK", gate_think_node)
    graph.add_node("GATE_ACT", gate_act_node)
    graph.add_node("GATE_OBSERVE", gate_observe_node)
    
    graph.set_entry_point("GATE_THINK")
    graph.add_conditional_edges("GATE_THINK", gate_think_router, {
        "gate_tool_call": "GATE_ACT",
        "decision": END,
    })
    graph.add_edge("GATE_ACT", "GATE_OBSERVE")
    graph.add_edge("GATE_OBSERVE", "GATE_THINK")
    
    return graph.compile()
```

### 6.2 AuditState 构建（信息隔离）

```python
def build_audit_state(
    orchestrator_state: OrchestratorState,
    config: AgentConfig,
) -> AuditState:
    """
    从 OrchestratorState 构建 AuditState。
    关键: 只传递 ConclusionProposal 内的信息，不传递 react_trace。
    
    字段映射:
      run_id ← orchestrator_state["run_id"]
      focus_context ← orchestrator_state["focus_context"]（只读副本）
      proposal ← orchestrator_state["current_proposal"]
      audit_evidence ← []（空）
      audit_trace ← []（空）
      audit_budget ← {
          tool_calls_used: 0,
          tool_calls_limit: config.budget.audit_tool_calls_limit,
          max_rounds: config.budget.audit_max_rounds
      }
      previous_hint ← 从 orchestrator_state 中提取上一轮 hint（如有）
    """
```

### 6.3 GATE_THINK 节点

```python
def gate_think_node(state: AuditState) -> dict:
    """
    Audit Agent 核心: 独立审查 ConclusionProposal。
    
    LLM 调用:
      prompt = audit_system.md + 当前审查上下文
      结构化输出: {thought, action_type, tool_call?, decision?}
    
    处理逻辑:
      1. 构建审查上下文:
         - ConclusionProposal 的 hypotheses + evidence + proposed_root_causes
         - FocusContext 的 top_metrics, causal_order
         - previous_hint（如非空）
         - 已收集的 audit_evidence（如有）
      2. 不注入 Diagnosis Agent 的 react_trace 或 thought
      3. 调用 LLM:
         - 若 action_type == "decision" → 解析 AuditDecision
         - 若 action_type == "gate_tool_call" → 解析 ToolCall
      4. Budget 检查:
         - tool_calls_used >= tool_calls_limit → 强制 decision
         - 当前 round >= max_rounds → 强制 decision
    
    快路径:
      - 零工具 pass: 证据充分、所有替代假设已排除
      - 零工具 continue: 明显缺口（如只有一条证据）
    
    Budget 耗尽默认决策:
      - 已发现强反驳 → continue
      - 未发现问题 → pass
      - 假设空间失效 → degrade
    """
```

### 6.4 GATE_ACT / GATE_OBSERVE 节点

```python
def gate_act_node(state: AuditState) -> dict:
    """
    执行受限工具调用。
    工具约束: 只允许 MetricQueryTool 和 KBRetrievalTool。
    不允许 DataAnalysisTool（防止 Audit Agent 退化为第二个诊断者）。
    """

def gate_observe_node(state: AuditState) -> dict:
    """
    生成 AuditEvidence。
    与 Diagnosis 的 OBSERVE 类似，但:
      - 生成 AuditEvidence（id 格式 "ae{n}"）
      - 写入 state["audit_evidence"]
      - 不更新 hypotheses（Audit Agent 不修改假设状态）
    """
```

### 6.5 Audit Agent System Prompt

`prompts/audit_system.md` 关键结构：

```markdown
## 角色
你是证据审查员（Evidence Reviewer），不负责给出诊断，只负责寻找结论中的漏洞。

## 审查原则
1. 单一现象不能被重复包装成多条支持证据
2. 替代假设未排除时不得放行
3. 强反驳证据优先于支持性描述
4. 多根因情形必须区分独立故障与下游症状

## 审查步骤
Stage A: 逐个假设审查 — 每个 confirmed 假设的证据是否充分？
Stage B: 多假设关系审查 — 是独立故障还是因果链？
Stage C: 全局裁决 — pass / continue / rehypothesize / degrade

## 工具使用约束
- 只有在逻辑检查不能直接得出裁决时才允许调用工具
- 最多 {audit_tool_calls_limit} 次工具调用
- 只允许使用: MetricQueryTool, KBRetrievalTool

## 输出格式
{
  "thought": "审查推理过程",
  "action_type": "decision | gate_tool_call",
  "decision": { ... },   // action_type == "decision" 时
  "tool_call": { ... }   // action_type == "gate_tool_call" 时
}
```

### 6.6 hint 语义规范

`hint` 在 `decision == "continue"` 时必填：
- 是可执行的问题，指向 1-2 个具体验证缺口
- 不直接包含 Audit Agent 自己的最终判断

验证逻辑：

```python
def _validate_audit_decision(decision: AuditDecision) -> AuditDecision:
    if decision.decision == "continue" and not decision.hint:
        raise ValueError("continue 决策必须提供 hint")
    if decision.decision != "continue":
        decision.hint = None  # 非 continue 时清空 hint
    return decision
```

### 6.7 消融变体

| Flag | 行为 |
|------|------|
| `enable_audit=False` (Abl-B) | 跳过整个 Audit Agent，Diagnosis conclude 后直接 FINALIZE |

---

## 第 7 章：Stage 2 — Orchestrator (orchestrator.py)

### 7.1 StateGraph 定义

```python
def build_orchestrator_graph(
    diagnosis_graph,       # 已编译的 diagnosis StateGraph
    audit_graph,           # 已编译的 audit StateGraph
    tools: dict,
    llm_client: LLMClient,
    config: AgentConfig,
) -> StateGraph:
    graph = StateGraph(OrchestratorState)
    
    graph.add_node("TRIAGE", triage_node)
    graph.add_node("INVOKE_DIAGNOSIS", invoke_diagnosis_node)
    graph.add_node("SUBMIT_TO_AUDIT", submit_to_audit_node)
    graph.add_node("ROUTE_DECISION", route_decision_node)
    graph.add_node("FINALIZE", finalize_node)
    
    graph.set_entry_point("TRIAGE")
    graph.add_edge("TRIAGE", "INVOKE_DIAGNOSIS")
    graph.add_edge("INVOKE_DIAGNOSIS", "SUBMIT_TO_AUDIT")
    graph.add_edge("SUBMIT_TO_AUDIT", "ROUTE_DECISION")
    graph.add_conditional_edges("ROUTE_DECISION", orchestrator_router, {
        "finalize": "FINALIZE",
        "continue": "INVOKE_DIAGNOSIS",
        "rehypothesize": "INVOKE_DIAGNOSIS",
    })
    graph.add_edge("FINALIZE", END)
    
    return graph.compile()
```

### 7.2 各节点实现

#### TRIAGE 节点

```python
def triage_node(state: OrchestratorState) -> dict:
    """
    调用 triage.run_triage() 生成 FocusContext。
    若 Triage 检测到 0 个异常指标: 直接输出 no_anomaly_detected。
    """
    focus_context = run_triage(
        state["inputs"]["metrics_path"],
        state["inputs"]["jobinfo_path"],
        load_metric_kb(),
        config.triage,
        config.ablation,
        state["run_id"],
    )
    return {"focus_context": focus_context}
```

#### INVOKE_DIAGNOSIS 节点

```python
def invoke_diagnosis_node(state: OrchestratorState) -> dict:
    """
    1. 构建 DiagnosisState:
       - run_id, focus_context ← OrchestratorState
       - gate_hint ← 从上一轮 AuditDecision 提取（如有）
       - rehyp_count ← OrchestratorState.rehyp_count
       - budget ← 新建或续用（取决于 continue vs rehypothesize）
       - hypotheses:
         - 首次调用: []
         - continue: 保留上一轮的 hypotheses + evidence
         - rehypothesize: 保留 refuted 假设，清空 active
    2. 调用 diagnosis_graph.invoke(diagnosis_state)
    3. 提取 ConclusionProposal
    4. 拷贝 react_trace 到 OrchestratorState.diagnosis_trace（存档）
    5. 拷贝 budget 到 OrchestratorState.diagnosis_budget（统计用）
    
    返回: {"current_proposal": proposal, "diagnosis_trace": ..., "diagnosis_budget": ...}
    """
```

**状态传递细节**：

| 路由来源 | hypotheses 处理 | evidence 处理 | budget 处理 | gate_hint |
|---------|----------------|--------------|------------|-----------|
| 首次 | `[]` | `[]` | 新建 | `None` |
| `continue` | 完整保留 | 完整保留 | 续用（不重置） | `audit_decision.hint` |
| `rehypothesize` | 保留 refuted，清空 active | 完整保留 | 续用（不重置） | `None` |

#### SUBMIT_TO_AUDIT 节点

```python
def submit_to_audit_node(state: OrchestratorState) -> dict:
    """
    1. 检查消融: ablation.enable_audit = False → 直接构建 pass 决策
    2. 构建 AuditState（见 §6.2）
    3. 调用 audit_graph.invoke(audit_state)
    4. 提取 AuditDecision
    5. 拷贝 audit_trace 到 OrchestratorState.audit_trace（存档）
    
    返回: {"audit_decision": decision, "audit_trace": ..., "audit_budget": ..., "round_count": +1}
    """
```

#### ROUTE_DECISION 节点

```python
def route_decision_node(state: OrchestratorState) -> dict:
    """
    确定性路由，不调用 LLM。
    
    逻辑:
    1. decision = state["audit_decision"].decision
    2. if decision == "pass" → 不修改 state
    3. if decision == "continue":
       state 中标记 gate_hint = audit_decision.hint
    4. if decision == "rehypothesize":
       - if rehyp_count < max_rehyp:
         rehyp_count += 1
       - else: 降级为 degrade
    5. if decision == "degrade" → 不修改 state
    """

def orchestrator_router(state: OrchestratorState) -> str:
    """
    路由函数:
      - audit_decision.decision == "pass" → "finalize"
      - audit_decision.decision == "degrade" → "finalize"
      - audit_decision.decision == "continue" → "continue"
      - audit_decision.decision == "rehypothesize" → "rehypothesize"
    
    额外检查:
      - round_count >= max_orchestrator_rounds → "finalize"（防无限循环）
    """
```

#### FINALIZE 节点

详见第 9 章。

### 7.3 总入口函数

```python
def run_diagnosis(
    metrics_path: str,
    jobinfo_path: str,
    config: AgentConfig,
    run_id: str | None = None,
) -> tuple[DiagnosisReport, dict]:
    """
    系统总入口。
    
    1. 生成 run_id (格式: run_YYYYMMDD_HHMMSS)
    2. 构建三个 graph
    3. 初始化 OrchestratorState
    4. 调用 orchestrator_graph.invoke()
    5. 提取 DiagnosisReport
    6. 有条件触发 Reflect
    7. 保存输出到 output_dir/<exp_id>/
    
    返回: (DiagnosisReport, execution_trace_dict)
    """
```

---

## 第 8 章：Tools (tools/)

### 8.1 工具基类

```python
class BaseTool:
    """工具基类"""
    name: str
    description: str
    
    def execute(self, args: dict) -> dict:
        raise NotImplementedError
    
    def get_schema(self) -> dict:
        """返回 LLM function calling 的参数 schema"""
        raise NotImplementedError
```

### 8.2 MetricQueryTool (tools/metric_query.py)

```python
class MetricQueryTool(BaseTool):
    name = "MetricQueryTool"
    description = "查询指定节点、指标、时间窗口的聚合统计量"
    
    def __init__(self, metrics_df: pd.DataFrame):
        """
        初始化时加载整个 metrics.csv 到内存。
        metrics_df: index=DatetimeIndex, columns=指标名
        """
        self.df = metrics_df
    
    def execute(self, args: dict) -> dict:
        """
        参数:
          metrics: list[str]       # 指标名列表
          time_window: {start: str, end: str}  # ISO8601
          aggregation: str         # "mean" | "p95" | "max" | "duration_above_threshold"
          threshold_value: float | None  # 仅 duration_above_threshold 时需要
        
        注: 当前为单节点环境，不需要 nodes 参数。后续扩展多节点时再添加。
        
        处理:
          1. 按 time_window 过滤 df
          2. 对每个 metric:
             - 若 metric 不在 df.columns → missing
             - mean: df[metric].mean()
             - p95: df[metric].quantile(0.95)
             - max: df[metric].max()
             - duration_above_threshold:
               超阈值的采样点数 × 采样间隔(15s)
          3. 构建结果
        
        返回:
          {
            "results": [
              {"metric": str, "value": float, "unit": str, "aggregation": str}
            ],
            "missing": [
              {"metric": str, "reason": "column_not_found | no_data_in_window"}
            ],
            "window_info": {"start": str, "end": str, "n_points": int}
          }
        """
```

**function calling schema**:

```json
{
  "name": "MetricQueryTool",
  "description": "查询指定指标在指定时间窗口内的聚合统计量",
  "parameters": {
    "type": "object",
    "properties": {
      "metrics": {"type": "array", "items": {"type": "string"}, "description": "指标名列表"},
      "time_window": {
        "type": "object",
        "properties": {
          "start": {"type": "string", "description": "ISO8601 开始时间"},
          "end": {"type": "string", "description": "ISO8601 结束时间"}
        },
        "required": ["start", "end"]
      },
      "aggregation": {"type": "string", "enum": ["mean", "p95", "max", "duration_above_threshold"]},
      "threshold_value": {"type": "number", "description": "仅 duration_above_threshold 时需要"}
    },
    "required": ["metrics", "time_window", "aggregation"]
  }
}
```

### 8.3 KBRetrievalTool (tools/kb_retrieval.py)

```python
class KBRetrievalTool(BaseTool):
    name = "KBRetrievalTool"
    description = "查询 Metric KB 或 Fault Pattern Library"
    
    def __init__(
        self,
        metric_kb: list[dict],      # 从 metrics.yaml 加载
        fpl_entries: list[dict],     # 从 fpl.jsonl 加载
        chroma_collection,           # ChromaDB collection
    ):
        self.metric_kb = {m["name"]: m for m in metric_kb}
        self.fpl_entries = fpl_entries
        self.chroma = chroma_collection
    
    def execute(self, args: dict) -> dict:
        """
        参数:
          mode: "metric_lookup" | "pattern_match"
          
          metric_lookup 模式:
            metric_name: str  → 精确查找 metrics.yaml 条目
          
          pattern_match 模式:
            subsystem: str
            anomaly_metrics: list[str]
            causal_order: list[str]   # 可选
          
        处理:
          metric_lookup:
            1. 从 self.metric_kb[metric_name] 返回条目
            2. 若未命中，尝试 ChromaDB 语义检索 Top-3
          
          pattern_match:
            1. 遍历 fpl_entries:
               - 比较 leading_subsystem 是否匹配
               - 比较 required_metrics 与 anomaly_metrics 的交集
               - 按命中比例 + confidence 排序
            3. 返回 Top-3 匹配
        
        返回:
          metric_lookup: {"metric_entries": [{...}]}
          pattern_match: {"pattern_hits": [{pattern_id, fault_type, confidence, match_score}]}
        """
```

### 8.4 DataAnalysisTool (tools/data_analysis.py)

```python
class DataAnalysisTool(BaseTool):
    name = "DataAnalysisTool"
    description = "对时序数据执行统计分析"
    
    def __init__(self, metrics_df: pd.DataFrame):
        self.df = metrics_df
    
    def execute(self, args: dict) -> dict:
        """
        参数:
          analysis_type: "correlation" | "changepoint" | "group_compare" | "lag_analysis"
          + 各类型特定参数（见下）
        
        返回:
          {"findings": [{statistic_name, value, interpretation}], "summary": str}
          禁止返回原始序列！
        """
```

#### correlation

```python
# args: {metric_a: str, metric_b: str, time_window: {start, end}}
# 实现: scipy.stats.pearsonr + spearmanr
# 返回:
#   findings = [
#     {"statistic_name": "pearson_r", "value": 0.95, "interpretation": "强正相关"},
#     {"statistic_name": "spearman_rho", "value": 0.93, "interpretation": "强单调正相关"},
#     {"statistic_name": "p_value", "value": 1e-15, "interpretation": "统计显著"}
#   ]
```

#### changepoint

```python
# args: {metric: str, time_window: {start, end}}
# 实现: ruptures.Pelt(model="rbf", min_size=3).fit_predict(penalty=5)
# Fallback: 若 ruptures 不可用，使用滚动均值差分法
# 返回:
#   findings = [
#     {"statistic_name": "changepoint_time", "value": "2025-09-20T07:02:30", "interpretation": "检测到变化点"},
#     {"statistic_name": "mean_before", "value": 45.2, "interpretation": "变化点前均值"},
#     {"statistic_name": "mean_after", "value": 98.1, "interpretation": "变化点后均值"}
#   ]
```

#### group_compare

```python
# args: {metric: str, split_time: str, time_window: {start, end}}
# 实现: 按 split_time 分为前后两组，计算均值差、t-test
# 返回:
#   findings = [
#     {"statistic_name": "mean_before", "value": 45.2, ...},
#     {"statistic_name": "mean_after", "value": 98.1, ...},
#     {"statistic_name": "t_statistic", "value": 15.3, ...},
#     {"statistic_name": "p_value", "value": 1e-20, ...}
#   ]
```

#### lag_analysis

```python
# args: {metric_a: str, metric_b: str, time_window: {start, end}}
# 实现: 交叉相关 (np.correlate normalized) 在 ±10 个 lag 范围内
# 返回:
#   findings = [
#     {"statistic_name": "best_lag_seconds", "value": 60, "interpretation": "metric_a 领先 metric_b 约 60 秒"},
#     {"statistic_name": "max_cross_correlation", "value": 0.89, "interpretation": "强延迟相关"}
#   ]
```

### 8.5 工具注册表

```python
def create_tools(
    metrics_df: pd.DataFrame,
    metric_kb: list[dict],
    fpl_entries: list[dict],
    chroma_collection,
) -> dict[str, BaseTool]:
    return {
        "MetricQueryTool": MetricQueryTool(metrics_df),
        "KBRetrievalTool": KBRetrievalTool(metric_kb, fpl_entries, chroma_collection),
        "DataAnalysisTool": DataAnalysisTool(metrics_df),
    }
```


## 第 9 章：FINALIZE (finalize.py)

### 9.1 职责

FINALIZE 在 Orchestrator 的末端执行，将结构化的诊断状态转化为面向用户的 `DiagnosisReport`。

### 9.2 函数签名

```python
def finalize_node(state: OrchestratorState) -> dict:
    """
    输入:
      - state["current_proposal"]: ConclusionProposal
      - state["focus_context"]: FocusContext
      - state["diagnosis_trace"]: list[ReActStep]
      - state["audit_trace"]: list[AuditStep]（如有）
      - state["audit_decision"]: AuditDecision
    
    LLM 调用:
      prompt = finalize.md 模板
      结构化输出: DiagnosisReport (JSON)
    
    处理逻辑:
      1. 确定 diagnosis_type:
         - audit_decision.decision == "pass" → 使用 proposal 的类型
         - audit_decision.decision == "degrade" → "partial" 或 "inconclusive"
         - ablation.enable_audit == False → 使用 proposal 的类型（无审查）
      
      2. 构建 finalize prompt:
         - 注入 ConclusionProposal 的结论与证据
         - 注入 FocusContext 的 leading_subsystem, top_metrics
         - 不注入完整 react_trace（token 节省）
         - 注入 Audit Agent 审查摘要（如有）
      
      3. 调用 LLM → DiagnosisReport JSON
      
      4. 后处理:
         - 填充 trace_summary（从 state 统计生成，不依赖 LLM）
         - 校验 Pydantic schema
         - 若 LLM 输出格式异常：重试 1 次，仍失败则用模板填充
    
    返回: {"diagnosis_report": DiagnosisReport}
    """
```

### 9.3 trace_summary 构建

```python
def _build_trace_summary(state: OrchestratorState) -> dict:
    """
    确定性构建，不消耗 LLM token。
    
    返回:
    {
        "triage_leading_subsystem": state["focus_context"].leading_subsystem,
        "main_tools_used": _count_tools(state["diagnosis_trace"]),
        "audit_tools_used": _count_tools(state.get("audit_trace", [])),
        "total_tool_calls": state["diagnosis_budget"]["tool_calls_used"]
                          + state.get("audit_budget", {}).get("tool_calls_used", 0),
        "main_iterations": len(state["diagnosis_trace"]),
        "audit_iterations": len(state.get("audit_trace", [])),
    }
    
    _count_tools 示例输出: ["MetricQueryTool x3", "DataAnalysisTool x1"]
    """
```

### 9.4 FINALIZE prompt 模板

`prompts/finalize.md`：

```markdown
## 任务
你是 HPC 诊断报告撰写者。基于以下诊断结论和证据，生成最终诊断报告。

## 诊断结论
- 诊断类型: {diagnosis_type}
- 确认的根因:
{confirmed_root_causes}
- 关键证据:
{key_evidence}

## Triage 上下文
- Leading Subsystem: {leading_subsystem}
- 异常指标概览: {top_metrics_brief}

## 审查结果
{audit_summary}

## 输出格式
返回 JSON，必须符合 DiagnosisReport schema。
```

---

## 第 10 章：Reflect (reflect.py)

### 10.1 触发条件

```python
def should_reflect(report: DiagnosisReport, config: AgentConfig) -> bool:
    """
    触发条件:
      1. diagnosis_type in ("single_fault", "composite_fault", "partial")
      2. 至少有 1 个 confirmed root_cause
    
    "inconclusive" 时不触发：无可信信号可提炼。
    """
    if report.diagnosis_type == "inconclusive":
        return False
    return len([rc for rc in report.root_causes if rc.confidence > 0.5]) > 0
```

### 10.2 执行步骤

```python
def run_reflect(
    report: DiagnosisReport,
    focus_context: FocusContext,
    existing_fpl: list[dict],
    llm_client: LLMClient,
    config: AgentConfig,
) -> list[dict]:
    """
    1. 规则提炼:
       - 为每个 confirmed root_cause 构建提炼 prompt
       - LLM 生成候选 FPL 规则（JSON）
       - Pydantic 校验（防污染）
    
    2. 去重比较:
       - 对每个候选规则，与 existing_fpl 按 fault_type 匹配
       - 若 fault_type 完全相同:
         - 比较 symptom_signature 的 required_metrics 重合度
         - 重合度 > 0.8 → 更新现有规则（version +1，合并 provenance_exp_ids）
         - 重合度 ≤ 0.8 → 作为同 fault_type 的变体新增
       - 若 fault_type 不同 → 新增
    
    3. 写回:
       - 追加写入 fpl.jsonl（每行一个 JSON）
       - 失败时记录 warning，不影响主流程
    
    返回: 更新后的 fpl_entries
    """
```

### 10.3 FPL 规则 Schema

与现有 `fpl.jsonl` 一致（8 条种子规则已定义），候选规则必须遵循：

```python
class FPLRule(BaseModel):
    pattern_id: str                          # "fpl_{nnn}" 自增
    fault_type: str                          # 标准故障类型名
    version: int = 1
    status: Literal["confirmed", "active", "deprecated"] = "active"
    source: Literal["manual", "reflected"] = "reflected"
    provenance_exp_ids: list[str]
    confidence: float                        # 0.0-1.0，反映验证结果初值=0.5
    symptom_signature: dict                  # {leading_subsystem, required_metrics, ...}
    verification_steps: list[str]            # 自然语言验证步骤
    solutions: list[str]                     # 推荐解决方案
    # 可选 (composite 时)
    composite_discrimination: dict | None = None
```

**Reflect 生成的规则与 manual 种子规则的区别**：
- `source = "reflected"`（manual = 人工编写）
- `confidence` 初始值 = 0.5（manual 种子更高）
- `status = "active"`（需要后续验证才能升级为 confirmed）

### 10.4 防污染校验

```python
def _validate_reflected_rule(rule: dict) -> FPLRule:
    """
    1. Pydantic 校验：字段类型、必填项
    2. confidence 强制设为 0.5（忽略 LLM 自行填写的数值）
    3. status 强制设为 "active"
    4. source 强制设为 "reflected"
    5. pattern_id 由系统分配（忽略 LLM 自行填写）
    """
```

---

## 第 11 章：Prompt 模板管理 (prompts/)

### 11.1 模板文件清单

| 文件 | 调用方 | 用途 |
|------|--------|------|
| `diagnosis_system.md` | THINK | Diagnosis Agent 的 system prompt，定义角色、约束、输出格式 |
| `diagnosis_hypothesize.md` | HYPOTHESIZE | 假设生成提示 |
| `diagnosis_think.md` | THINK | ReAct 循环推理提示（含 tool 描述） |
| `audit_system.md` | GATE_THINK | Audit Agent 的 system prompt |
| `finalize.md` | FINALIZE | 报告生成提示 |
| `reflect.md` | Reflect | FPL 规则提炼提示 |

### 11.2 模板变量注入机制

```python
def render_prompt(template_path: str, variables: dict) -> str:
    """
    简单字符串模板：{variable_name} 替换。
    
    不使用 Jinja2（避免额外依赖复杂度），使用 Python str.format_map()。
    - 所有变量名使用 snake_case
    - 未提供的变量保留原始 {placeholder}（便于调试）
    - 模板中的 {{ 和 }} 转义为字面量
    """
    with open(template_path, 'r') as f:
        template = f.read()
    return template.format_map(defaultdict(lambda: "{MISSING}", variables))
```

### 11.3 diagnosis_system.md 设计要点

```markdown
## 角色
你是 HPC 系统故障诊断 Agent。你遵循 ReAct（Think-Act-Observe）范式：
每一步你必须先思考（Think），然后决定是调用工具（Act）还是提出结论（Conclude）。

## 工具
你有以下三个工具可用:
{tool_descriptions}

## 约束
1. 你只能基于工具返回的证据做推理，不允许编造数据
2. 每次只能调用一个工具
3. 你必须在 {max_react_iterations} 步内完成诊断
4. 当证据充分时，立即 conclude，不要过度收集

## 输出格式
每步输出一个 JSON object:
{think_output_schema}
```

### 11.4 diagnosis_think.md 设计要点

```markdown
## 当前状态
Step {step_number} / {max_react_iterations}
Tool calls used: {tool_calls_used} / {tool_calls_limit}

## 假设状态
{hypotheses_formatted}

## 已收集证据
{evidence_formatted}

## 验证清单
{verification_status}

{gate_hint_section}

{force_conclude_section}

## 请推理并决定下一步
```

### 11.5 工具描述格式

注入到 system prompt 的工具描述，采用精简 JSON Schema：

```python
def format_tool_descriptions(tools: dict[str, BaseTool]) -> str:
    """
    生成统一格式的工具描述:
    
    ### MetricQueryTool
    查询指定指标在指定时间窗口内的聚合统计量。
    参数:
    - metrics (array[string]): 指标名列表
    - time_window ({start, end}): ISO8601 时间范围
    - aggregation (enum: mean|p95|max|duration_above_threshold): 聚合方式
    - threshold_value (number, optional): 仅 duration_above_threshold 时需要
    
    ### KBRetrievalTool ...
    ### DataAnalysisTool ...
    """
```

---

## 第 12 章：LLM Client (llm_client.py)

### 12.1 封装目标

统一 LLM 调用接口，屏蔽 OpenRouter / 本地模型差异，集中管理 token 计数与重试。

### 12.2 类定义

```python
class LLMClient:
    def __init__(self, config: LLMConfig):
        """
        config 包含:
          model: str                  # "deepseek/deepseek-chat"
          base_url: str               # "https://openrouter.ai/api/v1"
          api_key: str                # 从 .env 加载
          temperature: float          # 0.2
          max_tokens: int             # 4096
          timeout: int                # 60
          max_retries: int            # 2
        """
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        self.config = config
        self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    
    def call(
        self,
        messages: list[dict],
        response_format: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> tuple[str, dict]:
        """
        统一 LLM 调用。
        
        参数:
          messages: OpenAI 格式消息列表
          response_format: 若提供，使用 structured output（JSON mode）
          temperature: 覆盖默认温度
        
        处理:
          1. 构建 API 请求
          2. 调用 OpenAI SDK
          3. 重试逻辑:
             - RateLimitError → 指数退避重试
             - APIConnectionError → 重试
             - InvalidRequestError → 不重试，直接抛出
          4. 累计 token_usage
        
        返回: (content_str, usage_dict)
        
        异常:
          - LLMCallError: 重试耗尽后抛出
        """
    
    def call_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
    ) -> tuple[BaseModel, dict]:
        """
        带结构化输出的调用。
        
        处理:
          1. 调用 self.call() 获取 JSON 字符串
          2. json.loads() 解析
          3. schema.model_validate() 校验
          4. 解析失败 → 重试 1 次（在 prompt 末尾追加格式提示）
        
        返回: (parsed_model_instance, usage_dict)
        """
    
    def get_usage(self) -> dict:
        """返回累计 token 使用量"""
        return dict(self.token_usage)
    
    def reset_usage(self):
        """重置 token 计数器（每个实验重置）"""
        self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
```

### 12.3 结构化输出策略

优先使用 OpenAI SDK 的 `response_format={"type": "json_object"}` + prompt 中嵌入 schema。
若模型不支持 JSON mode，fallback 到 prompt-only 方式 + 后处理 regex 提取 JSON。

```python
def _extract_json_from_text(text: str) -> str:
    """
    Fallback JSON 提取:
    1. 尝试 json.loads(text)
    2. 匹配 ```json ... ``` 块
    3. 匹配第一个 { ... } 或 [ ... ] 块
    4. 全部失败 → 抛出 JSONParseError
    """
```

### 12.4 Token 分项计量

不同调用方的 token 应分开统计，便于消融分析：

```python
class TokenTracker:
    def __init__(self):
        self.categories = {
            "diagnosis_hypothesize": {"prompt": 0, "completion": 0},
            "diagnosis_think": {"prompt": 0, "completion": 0},
            "audit_think": {"prompt": 0, "completion": 0},
            "finalize": {"prompt": 0, "completion": 0},
            "reflect": {"prompt": 0, "completion": 0},
        }
    
    def record(self, category: str, usage: dict):
        self.categories[category]["prompt"] += usage.get("prompt_tokens", 0)
        self.categories[category]["completion"] += usage.get("completion_tokens", 0)
    
    def summary(self) -> dict:
        return {k: {**v, "total": v["prompt"] + v["completion"]} 
                for k, v in self.categories.items()}
```

---

## 第 13 章：评估框架 (eval/)

### 13.1 Ground Truth 格式

每个实验目录下的 `fault_info.txt` 格式：

```
Fault Type: cpu_fullload
Fault Start Time: 2025-09-20 07:01:15
Fault End Time: 2025-09-20 07:06:15
Fault Duration: 300 seconds
```

复合故障有多组：

```
Fault Type: cpu_fullload
Fault Start Time: 2025-09-20 07:01:15
...
Fault Type: mem_load_ram
Fault Start Time: 2025-09-20 07:01:30
...
```

### 13.2 Ground Truth 解析

```python
def parse_fault_info(path: str) -> list[dict]:
    """
    解析 fault_info.txt → list[{fault_type, start_time, end_time, duration}]
    
    解析规则:
      - 按 "Fault Type:" 分段
      - 每段提取 4 个字段
      - 时间格式: "%Y-%m-%d %H:%M:%S"
      - 若字段缺失 → 跳过该段，记录 warning
    
    特殊情况:
      - exp_029（无故障实验）: 文件内容为 "No fault injected" → 返回空列表
    """
```

### 13.3 评估指标计算

```python
class Evaluator:
    def evaluate_single(
        self,
        report: DiagnosisReport,
        ground_truth: list[dict],
    ) -> dict:
        """
        单实验评估。
        
        返回:
        {
            "hit_at_1": bool,          # Top-1 root_cause.fault_type 匹配 GT
            "hit_at_3": bool,          # Top-3 中任一匹配
            "composite_coverage": float, # 复合故障: matched_gt_faults / total_gt_faults
            "false_positives": int,    # report 中不在 GT 中的 fault_type 数量
            "tool_calls": int,
            "audit_tool_calls": int,
            "latency_seconds": float,
            "token_usage": dict,
            "diagnosis_type": str,
        }
        """
    
    def evaluate_batch(
        self,
        results_dir: str,
        gt_dir: str,
    ) -> pd.DataFrame:
        """
        批量评估所有实验。
        
        遍历 results_dir 下所有 exp_* 目录:
          1. 加载 diagnosis_report.json
          2. 加载对应 gt_dir 下的 fault_info.txt
          3. 调用 evaluate_single
          4. 汇总为 DataFrame
        
        聚合指标:
          - Hit@1 Accuracy = mean(hit_at_1)
          - Hit@3 Accuracy = mean(hit_at_3)
          - Composite Coverage = mean(composite_coverage) over composite exps
          - Avg Tool Calls = mean(tool_calls)
          - Avg Latency = mean(latency_seconds)
          - Total Token Usage
        """
```

### 13.4 fault_type 匹配规则

```python
def match_fault_type(predicted: str, ground_truth: str) -> bool:
    """
    允许灵活匹配:
      - 完全一致: cpu_fullload == cpu_fullload → True
      - 忽略大小写: CPU_Fullload == cpu_fullload → True
      - 别名映射:
        {"mem_load": ["mem_load_ram", "mem_load_buffer", "memory_load"],
         "network_loss": ["net_loss", "packet_loss"],
         "disk_burn": ["disk_io_burn", "io_burn"],
         "disk_fill": ["disk_space_fill", "filesystem_fill"]}
      - composite 匹配: "composite:cpu_fullload+mem_load" → 拆分为单独类型逐一匹配
    """
```

### 13.5 消融实验运行器 (eval/ablation.py)

```python
class AblationRunner:
    def __init__(self, base_config: AgentConfig, data_dir: str, output_dir: str):
        self.base_config = base_config
        self.data_dir = data_dir
        self.output_dir = output_dir
    
    def run_ablation_suite(self, experiments: list[str] | None = None):
        """
        运行消融实验矩阵。
        
        消融 ID → AblationFlags 映射（见 Ch3 实现）:
          "Full", "Abl-A", "Abl-B"
        
        流程:
          1. 遍历消融配置
          2. 对每组消融:
             a. 构建 AgentConfig（覆盖 ablation flags）
             b. 对每个实验运行 run_diagnosis()
             c. 保存结果到 output_dir/<ablation_id>/<exp_id>/
          3. 汇总评估
             a. 对每组消融运行 evaluate_batch()
             b. 生成对比表格
        
        输出:
          - output_dir/<ablation_id>/<exp_id>/diagnosis_report.json
          - output_dir/<ablation_id>/<exp_id>/execution_trace.json
          - output_dir/ablation_summary.csv  # 消融对比表
        """
    
    def run_single_ablation(
        self,
        ablation_id: str,
        flags: AblationFlags,
        experiments: list[str],
    ) -> pd.DataFrame:
        """运行单组消融实验"""
```

---

## 第 14 章：总入口脚本 (run_agent.py)

### 14.1 命令行接口

```python
import argparse

def main():
    parser = argparse.ArgumentParser(description="HPC Diagnosis Agent")
    parser.add_argument("--experiment", type=str, default="formaltest",
                        help="实验集名称")
    parser.add_argument("--exp-id", type=str, default=None,
                        help="单个实验 ID (如 exp_001), 不指定则跑全部")
    parser.add_argument("--model", type=str, default="deepseek/deepseek-chat",
                        help="LLM 模型名")
    parser.add_argument("--ablation", type=str, default="Full",
                        help="消融配置 ID (Full/Abl-A/Abl-B)")
    parser.add_argument("--output-dir", type=str, default="agent/data",
                        help="输出目录")
    parser.add_argument("--evaluate", action="store_true",
                        help="运行后自动评估")
    parser.add_argument("--ablation-suite", action="store_true",
                        help="运行完整消融实验矩阵")
    args = parser.parse_args()
```

### 14.2 运行流程

```python
    # 1. 加载配置
    config = AgentConfig.from_ablation_id(args.ablation)
    config.llm.model = args.model
    
    # 2. 确定数据路径
    data_base = f"dataset_builder/data/{args.experiment}/extracted_data"
    
    # 3. 枚举实验
    if args.exp_id:
        exp_ids = [args.exp_id]
    else:
        exp_ids = sorted([d for d in os.listdir(data_base) 
                         if os.path.isdir(os.path.join(data_base, d))])
    
    # 4. 逐实验运行
    for exp_id in exp_ids:
        metrics_path = os.path.join(data_base, exp_id, "metrics.csv")
        jobinfo_path = os.path.join(data_base, exp_id, "jobinfo.csv")
        
        report, trace = run_diagnosis(metrics_path, jobinfo_path, config)
        
        # 保存结果
        output_path = os.path.join(args.output_dir, args.ablation, exp_id)
        os.makedirs(output_path, exist_ok=True)
        save_report(report, output_path)
        if trace:
            save_trace(trace, output_path)
    
    # 5. 可选评估
    if args.evaluate:
        evaluator = Evaluator()
        results = evaluator.evaluate_batch(
            os.path.join(args.output_dir, args.ablation),
            data_base,
        )
        print(results.to_string())
    
    # 6. 消融套件
    if args.ablation_suite:
        runner = AblationRunner(config, data_base, args.output_dir)
        runner.run_ablation_suite(exp_ids)
```

### 14.3 输出目录结构

```
agent/data/
  Full/
    exp_001/
      diagnosis_report.json
      execution_trace.json
    exp_002/
      ...
  Abl-A/
    exp_001/
      ...
  ablation_summary.csv
```

---

## 第 15 章：实现分阶段与验收标准

### 15.1 Phase B: Triage

**产物**: `agent/triage.py`, `agent/config.py`, `agent/schema.py`（v6 升级）

**验收标准**:
1. `run_triage()` 接受 metrics.csv 路径，输出 FocusContext
2. 对 exp_001（cpu_fullload）: leading_subsystem == "cpu"
3. 对 exp_005（network_loss）: leading_subsystem == "network"
4. 对 exp_008（cpu+mem 复合）: top_metrics 同时包含 cpu 和 memory 指标
5. 对 exp_029（无故障）: top_metrics 为空或评分极低
6. 运行时间 < 2s（单实验）
7. 完全确定性（不调用 LLM）

### 15.2 Phase C: Diagnosis + Audit + Orchestrator

**产物**: `agent/diagnosis.py`, `agent/audit.py`, `agent/orchestrator.py`, `agent/tools/`, `agent/finalize.py`, `agent/llm_client.py`, `agent/prompts/`

**验收标准**:
1. 单实验端到端运行: `run_diagnosis(metrics_path, jobinfo_path, config)` 返回 DiagnosisReport
2. 对 exp_001（单故障 cpu_fullload）: Hit@1 == True
3. 对 exp_008（复合故障 cpu+mem）: composite_coverage >= 0.5
4. Audit Agent 可被消融禁用: `enable_audit=False` 时跳过审查
5. tool_calls_used 不超过 budget.tool_calls_limit
6. execution_trace.json 可追溯完整推理过程

**Phase C 内部分步验收**:

| 步骤 | 内容 | 验收 |
|------|------|------|
| C.1 | Diagnosis Graph + Tools（无 Audit） | 单实验可产出 ConclusionProposal |
| C.2 | 最小 Audit Agent（零工具） + Orchestrator | pass/continue 路由正常 |
| C.3 | 完整 Audit Agent + hint 协议 | continue → 补查 → re-submit 正常工作 |

### 15.3 Phase D: Reflect + 评估

**产物**: `agent/reflect.py`, `eval/evaluate.py`, `eval/ablation.py`

**验收标准**:
1. Reflect 可正常写回 fpl.jsonl
2. evaluate_batch() 输出完整评估 DataFrame
3. 消融 AblationRunner 可自动运行 Full + Abl-A + Abl-B

### 15.4 Phase E: 消融实验 + 论文

**产物**: 完整消融结果、论文表格与图表

**验收标准**:
1. 3 组消融实验（Full + Abl-A + Abl-B）全部有结果
2. 消融对比表可直接用于论文
3. 跨模型评估至少 2 个额外模型

---

## 附录 A：子系统前缀映射表

```python
SUBSYSTEM_PREFIX_MAP = {
    # CPU 子系统
    "cpu_": "cpu",
    "load_": "cpu",
    "context_switches": "cpu",
    "processes_running": "cpu",
    "processes_blocked": "cpu",
    "procs_": "cpu",
    "interrupts_": "cpu",
    "softirq_": "cpu",
    "entropy_": "cpu",
    
    # Memory 子系统
    "memory_": "memory",
    "anon_memory": "memory",
    "buffer_": "memory",
    "cache_memory": "memory",
    "swap_": "memory",
    "page_": "memory",
    "slab_": "memory",
    "mapped_": "memory",
    "shmem_": "memory",
    "hugepages_": "memory",
    "commit_": "memory",
    "vmalloc_": "memory",
    "writeback_": "memory",
    
    # Network 子系统
    "network_": "network",
    "tcp_": "network",
    "udp_": "network",
    "icmp_": "network",
    "socket_": "network",
    "netstat_": "network",
    
    # Disk 子系统
    "disk_": "disk",
    "filesystem_": "disk",
    
    # System (辅助)
    "boot_time": "system",
    "file_descriptor": "system",
    "nf_conntrack": "system",
    "time_offset": "system",
}
```

**归属规则**：按最长前缀匹配优先。若无前缀匹配 → 归入 `"system"` 。

---

## 附录 B：fault_info.txt 解析规范

### 格式定义

```
Fault Type: <fault_type_string>
Fault Start Time: <YYYY-MM-DD HH:MM:SS>
Fault End Time: <YYYY-MM-DD HH:MM:SS>
Fault Duration: <integer> seconds
```

### 复合故障

多组连续排列，以空行分隔（部分文件无空行分隔，需兼容）。

### 无故障实验

exp_029 的 fault_info.txt 内容：`No fault injected` 或空文件。

### 容错

- 字段名大小写不敏感
- 允许冒号后有额外空格
- Duration 字段可缺失（从 start/end 计算）

---

## 附录 C：已知风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM 不服从结构化输出格式 | THINK/HYPOTHESIZE 解析失败 | 重试机制 + fallback 模板 |
| OpenRouter API 限流 | 批量实验中断 | 指数退避 + 实验断点续跑 |
| FPL 反射规则质量差 | 知识库退化 | confidence 初值低 + 需验证升级 |
| 单节点数据前提 | 多节点场景不适用 | 保留 nodes 字段, 当前不实现 |
| Triage 假阴性 | 异常指标被遗漏 | top_k 可配置, Abl-A 验证 |
| Audit Agent 过度保守 | continue 循环浪费 budget | max_rounds=2 限制 |
| token 占用超预期 | 成本过高 | result_digest 压缩 + 分项监控 |
| ruptures 安装问题 | changepoint 降级 | 提供滚动均值 fallback |

---

## 附录 D：29 个实验 Ground Truth 速查

| 实验 | 故障类型 |
|------|---------|
| exp_001 | cpu_fullload |
| exp_002 | mem_load_ram |
| exp_003 | mem_load_ram |
| exp_004 | mem_load_buffer |
| exp_005 | network_loss |
| exp_006 | disk_burn |
| exp_007 | disk_fill |
| exp_008 | cpu_fullload + mem_load_ram |
| exp_009 | cpu_fullload + mem_load_ram |
| exp_010 | cpu_fullload + mem_load_ram |
| exp_011 | cpu_fullload + network_loss |
| exp_012 | cpu_fullload + disk_burn |
| exp_013 | cpu_fullload + disk_fill |
| exp_014 | mem_load_ram + network_loss |
| exp_015 | mem_load_ram + disk_burn |
| exp_016 | mem_load_ram + disk_fill |
| exp_017 | network_loss + disk_burn |
| exp_018 | network_loss + disk_fill |
| exp_019 | disk_burn + disk_fill |
| exp_020 | cpu_fullload + mem_load_ram + network_loss |
| exp_021 | cpu_fullload + mem_load_ram + disk_burn |
| exp_022 | cpu_fullload + mem_load_ram + disk_fill |
| exp_023 | cpu_fullload + network_loss + disk_burn |
| exp_024 | cpu_fullload + network_loss + disk_fill |
| exp_025 | cpu_fullload + disk_burn + disk_fill |
| exp_026 | mem_load_ram + network_loss + disk_burn |
| exp_027 | mem_load_ram + network_loss + disk_fill |
| exp_028 | mem_load_ram + disk_burn + disk_fill |
| exp_029 | 无故障 |

> 注意: 上表需在实现前通过逐一解析 fault_info.txt 做最终确认。
