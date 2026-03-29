
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
         构建 ConclusionProposal 并写入 state
    
    返回:
      {"react_trace": [new_step], "hypotheses": updated_hypotheses}
      若 conclude: 还返回 ConclusionProposal（以特殊 key 放入 state）
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
      - type: 由工具结果与假设预期对比决定
        - 结果符合假设预期 → "supporting"
        - 结果反驳假设预期 → "refuting"  
        - 结果与假设无明显关联 → "neutral"
        - 部分支持部分反驳 → "mixed"
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
      "{metric} on {node}: {aggregation}={value}{unit}, window={start}-{end}"
    
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
    graph.add_conditional_edges("SUBMIT_TO_AUDIT", audit_router, {
        # audit_router 在 SUBMIT_TO_AUDIT 结束后根据 audit_decision 路由
        # 但实际上我们在 ROUTE_DECISION 做路由
    })
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
    
    返回: {"current_proposal": proposal, "diagnosis_trace": ...}
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
    
    返回: {"audit_decision": decision, "audit_trace": ..., "round_count": +1}
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
          nodes: list[str]         # 节点列表（当前单节点，保留扩展性）
          metrics: list[str]       # 指标名列表
          time_window: {start: str, end: str}  # ISO8601
          aggregation: str         # "mean" | "p95" | "max" | "duration_above_threshold"
          threshold_value: float | None  # 仅 duration_above_threshold 时需要
        
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
              {"node": str, "metric": str, "value": float, "unit": str, "aggregation": str}
            ],
            "missing": [
              {"node": str, "metric": str, "reason": "column_not_found | no_data_in_window"}
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
