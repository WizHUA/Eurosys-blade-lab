# Design Changelog

本文档记录 HPC-Diagnosis Agent 各版本的核心设计变更。

---

## v6（当前版本）— 2026-03-23

**核心目标**：将 GATE 从纯 Python 规则节点升级为独立 Audit Agent（与 Diagnosis Agent 架构级分离，由 Orchestrator 协调），实现诊断结论零硬编码阈值。

| # | 变更 | 理由 |
|---|------|------|
| 1 | **GATE 升级为独立 Audit Agent**：与 Diagnosis Agent 架构级分离（独立 StateGraph、独立 State），具备有限 ReAct 循环（GATE_THINK → GATE_ACT → GATE_OBSERVE）、受限工具集、独立 budget；Orchestrator 强制将 ConclusionProposal 提交给 Audit Agent | v5 的确定性 Rule A/B/C/D 需要人工编写通过规则；升级为独立 Agent 后裁决由模型自主判断，且信息隔离由架构保证 |
| 2 | **删除 `Evidence.strength` 字段**：OBSERVE 不再给证据打 strong/medium/weak 标签 | 半自动标签容易误导 Audit Agent 判断；证据质量由 Audit Agent LLM 根据 `raw_stats` 和上下文综合判断 |
| 3 | **OBSERVE 职责收窄**：只做压缩 + raw_stats 提取 + verification 对齐 + 异常处理，不再裁决证据强弱 | 避免 OBSERVE 变成"半个裁判"，保持上下文压缩器的单一职责 |
| 4 | **引入方案 C hint 协议**：Audit Agent 不直接把自身查到的结果并入主 evidence，而是由 Orchestrator 以 `gate_hint` 传递给 Diagnosis Agent | 保留角色边界，减少 THINK 重复摸索 Audit Agent 已发现的漏洞 |
| 5 | **新增 `AuditEvidence` Schema**：Audit Agent 独立证据池，物理隔离在 AuditState 中 | 架构级信息隔离 + 审计边界 |
| 6 | **Claim 精确化**："零人工阈值" → "诊断结论零硬编码阈值"；区分信号抽取层超参数与诊断裁决层 | 审稿人可能用 Z-score 阈值反驳"零人工阈值"claim；精确限定范围 |
| 7 | **消融矩阵精简**：保留 Full / Abl-A（无 Triage）/ Abl-B（无 Audit Agent），移除 Abl-C、BL-1/BL-2、Sup-A~G | 聚焦两个核心设计贡献的消融验证 |
| 8 | **补充失败处理**：新增 LLM API 故障、HYPOTHESIZE 0 假设、Triage 0 异常、THINK 不 conclude 循环等 6 种场景 | 增强系统鲁棒性定义 |
| 9 | **术语约定**：`causal_order` 明确为 temporal ordering as causal prior | 避免将时序排序等同于严格因果关系 |
| 10 | **新增跨模型评伊方案**（§8.4）+ Audit Agent 决策质量专项指标 | 评估 LLM 依赖度 + 验证 Audit Agent 可靠性 |

---

## v5 — 2026-03-19

**核心目标**：面向 SC'26 论文写作优化设计表达，修正若干架构问题。

| # | 变更 | 理由 |
|---|------|------|
| 1 | **删除 Baseline Profile**，KB 简化为两层（Metric KB + FPL）；Triage Z-score 改为数据自适应基线 | 真实部署无预计算基线；减少对离线数据的依赖 |
| 2 | **FPL 初始为空**，不预填任何规则 | 证明系统可从零学习故障模式；消融对比更干净（空 vs 积累 vs 不用） |
| 3 | **Triage 6 步合并为 3 步**（异常筛选 → 时序排序 → FocusContext 构建） | 减少设计冗余，突出核心处理逻辑 |
| 4 | **明确 THINK → GATE 路由决策机制**：THINK 输出 `action.type`（`tool_call` / `conclude`），Budget Check 提供确定性兜底 | v4 未定义"谁决定进入 GATE"，需给出清晰路由规则 |
| 5 | **Evidence Gating 精简**：收敛为三项核心原理（证据独立性、反驳优先、复合故障甄别），具体阈值移入 `config/gate_config.yaml` | 论文写作友好；阈值细节为可配置实现参数 |
| 6 | **Reflect 不依赖 Ground Truth**，GT 比对移入独立评估框架（`eval/evaluate.py`） | 真实环境无法实时获取 chaos.csv；Reflect 应为无监督积累 |
| 7 | **消融实验精简为 4 ablation + 2 baseline**，小粒度消融降为 supplementary | 聚焦大块设计贡献；避免审稿人质疑消融粒度不一致 |
| 8 | **新增 RE-HYPOTHESIZE 纠错机制**：GATE Stage C 新增 `rehypothesize` 决策分支，当所有假设被 refuted 时回跳 HYPOTHESIZE 重新生成假设（最多 1 次），修复了初始假设全部偏差时系统无纠错路径的架构缺陷 | 初始假设可能因 Triage 初判偏差或 FPL 冷启动而全部错误，需要纠错闭环 |

---

## v4 — 2026-03

**核心目标**：三层 KB 设计 + Evidence Gating Rule A/B/C/D + 详细 Pydantic Schema。

主要设计点：
- KB 三层：Metric KB + Baseline Profile + FPL（含 8 条手工预填规则）
- Triage 6 步流程；Z-score 基线来自 Baseline Profile 查表
- Evidence Gating：Rule A（独立证据）/ Rule B（全面核实）/ Rule C（FPL 先验）/ Rule D（级联甄别）
- Reflect 第 1 步包含 Ground Truth 比对（使用 chaos.csv）
- 消融矩阵 7 组（含多组小粒度消融）

参考文档：`design/v4.md`，细化规格：`design/evidence_gating.md`, `design/kb_schema.md`, `design/pydantic_schema.md`

---

## v3 — 2025-Q4

**核心目标**：单一统一 Agent + 确定性 Triage 前置 + Evidence Gating 初版。

主要变更（相对 v2）：
- 从"多 Agent 协作"改为"单 Agent + 工具调用"架构
- 引入 Triage 前置阶段，确定性压缩 context
- Evidence Gating 初版：结论必须有最低证据数量支撑
- LangGraph StateGraph 替代原始递归调用

参考文档：`design/v3.md`, `design/v3_detail.md`

---

## v2 — 2025-Q3

**核心目标**：多 Agent 协作架构探索。

主要设计点：
- Planner Agent + Executor Agent + Critic Agent 分角色
- 未引入正式 Evidence Gating 机制
- 知识库为简单 RAG 文档片段检索

参考文档：`design/v2.md`

---

## v1 — 2025-Q2

**核心目标**：概念验证，单次 LLM 调用基线。

主要设计点：
- 单次 LLM 调用，全量 150+ 指标输入
- Few-shot prompt 工程
- 无 Agent、无知识库、无 Evidence Gating

参考文档：`design/v1.md`，实现：`llm/src/main.py`
