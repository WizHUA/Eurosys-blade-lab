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
