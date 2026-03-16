# HPC-Diagnosis Agent Design (v3) — Detail Spec

本文是 [v3.md](file:///home/quantum/Eurosys-blade-lab/design/v3.md) 的补充规格说明，目标是把“概念架构”细化成可实现、可评估、可复现的 Agent 系统规范，并穿插必要的 Agent 工程基本要点。

---

## 0. 顶会视角：什么算“合格的 Agent 系统贡献”

体系结构/系统顶会评审通常不关心“你用了 LLM”，而关心你是否提供了可验证的系统机制与证据。对本项目而言，建议将贡献表述落在以下可验要点上：

- **Hybrid 控制面**：确定性分诊（可控成本、可复现）+ 证据驱动推理闭环（覆盖组合故障）。
- **Evidence-gated 诊断语义**：结论必须绑定证据与反证过程，避免“编故事式推理”。
- **工具化交互边界**：LLM 只负责决策与解释，数据访问与统计计算由受控工具实现。
- **可复现的执行语义**：状态机、工具契约、失败处理、随机性控制、成本核算可复刻。

工程上，顶会“规范感”来自：形式化的执行语义 + 消融实验 + 可复现 artifact。

---

## 1. 系统模型（System Model）

### 1.1 输入与输出

- **输入**
  - `metrics.csv`：Prometheus 导出的时间序列表，包含 timestamp + 150+ 指标列（可能按节点展开）。
  - `jobinfo.csv`：作业运行区间、作业 ID、节点分配、状态等。
  - 可选 `chaos.csv`：仅用于离线评估 ground truth，不参与在线诊断逻辑。
- **输出**
  - `DiagnosisReport`：结构化 JSON（见 §2.4），包含 root cause、置信度、关键证据、建议、未决疑点。
  - `Trace`：执行轨迹（state snapshots + tool calls + gating decisions），用于复现与审计。

### 1.2 关键约束（必须写进实现）

- **Token 预算**：禁止把全量 metrics 注入 Prompt。必须先形成 Focus Context（§2.1）。
- **工具调用上限**：每次诊断最多 N 次工具调用（例如 8–15），超限进入降级策略（§5.3）。
- **强结构化输出**：所有工具输入输出、最终报告必须满足 schema（§3，§2.4）。

---

## 2. Agent 执行语义（Execution Semantics）

### 2.1 为什么要先做 Focus Context

Agent 工程的第一性原理：LLM 的上下文是昂贵且不稳定的资源。把全量时序塞进 Prompt 会导致：

- 成本线性增长（token）且难以复现（模型采样误差被放大）。
- 推理注意力分散（噪声 > 信号），导致结论漂移。

因此 v3 把原先 Layer 1 作为 **Diagnosis Agent 内部的确定性分诊阶段**：它不是“另一个 Agent”，而是同一个 Agent 的第一个节点/工具，输出短小、可推理的 Focus Context。

### 2.2 状态机（LangGraph/StateGraph 语义）

建议的最小状态机节点：

1) `TRIAGE`（纯 Python）：生成 Focus Context 与异常摘要。
2) `HYPOTHESIZE`（LLM）：给出候选根因及验证计划（验证计划必须可执行、可映射到工具）。
3) `GATHER`（工具执行器）：按计划调用工具收集证据。
4) `VERIFY`（LLM + 轻量统计）：对证据做一致性检验/反证，更新候选集合。
5) `GATE`（规则门控，纯 Python）：判断是否满足“可下结论条件”（§4）。
6) `FINALIZE`（LLM）：生成结构化诊断报告。
7) `REFLECT`（LLM + 规则合并）：提炼新规则写回 Fault Pattern Library（可同步/异步）。

对应原则：

- **LLM 节点只做决策/解释**；数值统计与数据访问由工具做。
- **关键控制逻辑必须确定性**（门控、预算、失败策略），保证系统可控与可复现。

### 2.3 State（可复现的状态结构）

建议定义为可 JSON 序列化的对象，字段不求多，但必须支持复现与评估：

- `run_id`: string
- `inputs`: {metrics_path, jobinfo_path, chaos_path?}
- `focus_context`: {time_windows[], nodes[], jobs[], top_metrics[], anomaly_scores[]}
- `hypotheses`: [{id, statement, prior_confidence, required_evidence[], disconfirming_evidence[]}]
- `evidence`: [{id, source_tool, query, result_digest, time_window, nodes, metrics, strength}]
- `tool_calls`: [{tool, args, started_at, finished_at, status, cost_estimate}]
- `budget`: {tool_calls_used, tool_calls_limit, tokens_in, tokens_out}
- `decision`: {selected_root_cause?, confidence?, unresolved?}
- `report`: DiagnosisReport?

基本知识点：在 Agent 系统里，“状态结构”就是你的“系统接口”。没有它，系统不可测、不可比、不可复现，论文也难写。

### 2.4 DiagnosisReport（最终输出 schema）

建议固定字段，避免自由文本：

- `anomaly`: string（异常现象摘要）
- `root_causes`: [{cause, confidence, evidence_ids[], counter_evidence_ids[]}]
- `solutions`: [{action, rationale, risk, verification}]
- `trace_summary`: {focus_window, affected_nodes, affected_jobs, tools_used}
- `uncertainties`: [string]

---

## 3. Tools（工具契约与实现要点）

### 3.1 工具不是 Prompt

工具箱不是靠 prompt“幻想出来”的。工具是代码实现的函数集合，LLM 通过 function calling 选择工具并给参数。系统框架负责执行工具并把结果返回给 LLM。

顶会写作里建议明确写出：工具的输入输出 schema、失败语义、预算限制、缓存策略。

### 3.2 TriageTool（确定性分诊工具）

- **输入**：`metrics.csv`, `jobinfo.csv`, `baseline_profile`
- **输出（Focus Context）**
  - `time_windows`: [{start, end, severity}]
  - `top_metrics`: [{metric, direction, score, baseline_ref}]
  - `nodes`: [{node, severity}]
  - `jobs`: [{job_id, overlap_ratio}]
  - `subsystem_hints`: [{subsystem, score, based_on}]

实现建议：

- 用 Z-score/分位数偏离/静态阈值组合，但输出统一为 `score` 与 `severity`，便于后续排序。
- 做持久性过滤（例如窗口内超过阈值的占比 > p）。

### 3.3 MetricQueryTool（按需查数）

目的：让 Agent 不回看全量，而只查“验证假设所需的最小证据”。

- **输入 schema**
  - `nodes`: [string]
  - `metrics`: [string]
  - `time_window`: {start, end}
  - `aggregation`: one of {raw, mean, p95, max, rate}
- **输出 schema**
  - `series`: [{node, metric, points[]}] 或 `aggregates`: [{node, metric, value}]
  - `missing`: [{node, metric}]

实现要点：

- 必须强制结构化参数，禁止自由文本 query 直连数据源。
- 所有返回必须带 `time_window` 与 `aggregation` 元信息，避免证据失配。

### 3.4 KBRetrievalTool（检索先验）

来源：

- Metric KB：指标定义、单位、物理意义、常见误用。
- Fault Pattern Library：症状签名→根因→验证路径→建议。

- **输入**：`query`（建议结构化：metric_name 或 symptom_signature）
- **输出**：`hits`: [{type, key, content, confidence, provenance}]

实现要点：

- 对 rule hits 必须返回 provenance（规则来源与版本），支持溯源与消融实验。

### 3.5 DataAnalysisTool（统计分析）

典型能力：

- 相关性/滞后相关（lagged correlation）
- 变化点检测（change point）
- 异常节点 vs 正常节点对比（group comparison）

输出必须是结构化统计量与结论摘要（例如 correlation 系数、p-value、滞后）。

---

## 4. Evidence Gating（证据门控：让系统“像系统”）

这部分是把 Agent 系统写成顶会系统的关键。

### 4.1 为什么需要门控

LLM 在不确定时倾向于“给一个看起来合理的答案”。门控的作用是用确定性规则定义“什么时候允许下结论”，从而把输出绑定到证据。

### 4.2 最小可行门控规则（建议）

定义证据强度 `strength` ∈ {weak, medium, strong}（由工具输出或 VERIFY 节点打分，但最终门控在纯 Python 执行）。

允许 FINALIZE 的条件之一（可选其一）：

- **Rule A（双证据）**：同一根因至少 2 条独立证据，且至少 1 条为 medium+。
- **Rule B（强证据 + 反证为空）**：存在 strong 证据，且关键反证检查未触发。
- **Rule C（规则命中 + 最小验证）**：Fault Pattern Library 命中高置信规则，且完成规则要求的最小验证步骤。

否则：

- 继续 GATHER/VERIFY（如果预算允许），或进入降级输出（§5.3）。

基本知识点：门控规则是你论文里能“说清楚并被评审相信”的可靠性机制；也是消融实验的核心维度。

---

## 5. Failure Handling & Safety（失败处理与安全）

### 5.1 工具失败

- 超时/缺失数据：记录在 `tool_calls.status`，证据标记为不可用，禁止当作支持性证据。
- 退化策略：改用聚合数据、扩大时间窗、或改用替代指标（由 KB 提供映射）。

### 5.2 输出不合规

- 强制 JSON schema 校验。
- 不合规则重试（最多 R 次），仍失败则输出最小安全报告：只包含 Focus Context 与“无法下结论原因”。

### 5.3 预算耗尽

当 tool-call 次数或 token 超预算：

- 输出 `uncertainties` 明确写出“缺失的关键证据是什么”。
- 输出候选根因 Top-2（非单点结论），并给出下一步建议。

---

## 6. Evaluation Plan（顶会评估建议）

### 6.1 指标（建议至少覆盖四类）

- **准确性**：root cause Top-1/Top-k 命中率；多故障场景的覆盖。
- **效率**：time-to-diagnosis（端到端时间）、工具调用次数、统计计算开销。
- **成本**：tokens（in/out）、API 花费估计、缓存命中率。
- **鲁棒性**：噪声、缺失指标、时间对齐偏差、非见过 workload 的泛化。

### 6.2 基线与消融（写论文必备）

建议至少包含：

- Baseline-1：one-shot LLM（当前实现范式，可参考 [main.py](file:///home/quantum/Eurosys-blade-lab/llm/src/main.py#L1-L220)）
- Baseline-2：纯统计规则（无 LLM）
- Ablation-A：无 KB（去掉 KBRetrieval）
- Ablation-B：无门控（去掉 Evidence Gating）
- Ablation-C：无反思积累（去掉 Reflect）
- Ablation-D：无分诊（把更多原始数据输入 LLM，观察成本/漂移）

### 6.3 可复现性清单（Artifact 友好）

- 固定数据集划分与版本（hash）。
- 固定随机种子（对工具与采样策略）。
- 记录模型版本、温度、max tokens、重试策略。
- 输出每次 run 的 trace（state + tool logs），支持复盘与审计。

---

## 7. 讨论要点（下一步我们该具体定什么）

为了把 v3 从“概念正确”推进到“工程落地 + 顶会评估可写”，建议下一轮聚焦三件事：

1) **State schema 最终定稿**（字段、序列化、trace 规范）。
2) **工具契约定稿**（严格 schema、失败语义、预算策略、缓存策略）。
3) **Evidence gating 规则定稿**（最小条件、反证列表、降级输出模板）。

