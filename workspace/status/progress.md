# 项目进度追踪

> 本文件由主管 Agent 维护，记录项目里程碑和当前状态。

## 当前阶段
Phase 3: Agent 升级 — **设计阶段**

## 设计演进
| 版本 | 日期 | 核心变化 | 文件 |
|------|------|---------|------|
| v1 | - | 三层架构 (Triage Agent + Diagnosis Agent + Evolution) | design/v1.md |
| v2 | - | 同 v1 | design/v2.md |
| v3 | - | Triage 合并为 Agent 内部节点，Evidence Gating | design/v3.md, v3_detail.md |
| v4 | - | ReAct + 多假设并行 + 时序因果 + KB Schema | design/v4.md |

## 里程碑

### 2026-03-19: Copilot Agent 团队组建
- 完成: 构建了 9 个专业 Agent (主管/需求沟通/设计专家/DrawIO绘图/讲解员/SC26评审/工程师/实验员/论文助手)
- 产出: `.github/agents/`, `.github/copilot-instructions.md`, `workspace/`
- 下一步: 基于 v4 设计开始工程实现 (Phase A: 离线知识库构建)

### 2026-03-19: 环境配置与工具验证
- 完成: Python 环境 (blade-lab conda, Python 3.11.14) + 所有依赖包安装 + API 连通性验证
- 产出: `requirements.txt` (已更新), `.env` (已配置)
- 验证结果:
  - ✅ langchain 1.2.12 / langgraph 1.1.3 / chromadb 1.5.5 / pydantic 2.12.5
  - ✅ OpenRouter API (`deepseek/deepseek-chat` 已验证可用)
  - ✅ 数据加载: formaltest/metric.csv (230×171) + jobinfo.csv
  - ✅ web 工具 (fetch_webpage) 可访问外部资源
  - ⚠️  `openai/gpt-4o-mini` 地区限制 (403)，建议用 `deepseek/deepseek-chat`
- 下一步: 开始 Phase A 工程实现

### 2026-03-19: Phase A 知识库构建完成
- 完成: 设计专家产出 3 份补充设计文档；工程师实现 Phase A 全部组件
- 产出: `agent/schema.py`, `agent/kb/metrics.yaml`, `agent/kb/fpl.jsonl`, `agent/kb/baseline/`, `agent/kb/chroma_db/`, `design/kb_schema.md`, `design/pydantic_schema.md`, `design/evidence_gating.md`
- 已知问题: Baseline Profile 存在轻微污染（复合故障前序实验残留），memory/disk 指标 std 偏大；暂时接受，Phase D 评估时再优化
- 下一步: Phase B (Triage Stage) + Phase C (Diagnosis Agent) 并行推进

---

## 待办事项

### Phase A: 离线知识库 (Offline Knowledge Base) ✅ **已完成**
- [x] Metric KB: `agent/kb/metrics.yaml`（22 条指标，覆盖 CPU/Memory/Network/Disk/System 10 个子系统）
- [x] Fault Pattern Library: `agent/kb/fpl.jsonl`（8 条规则，fpl_001~008，含复合故障规则）
- [x] Baseline Profile: `agent/kb/baseline/`（29 个实验预fault基线 + global.json）
- [x] ChromaDB 向量索引: `agent/kb/chroma_db/`（离线 TF-IDF，无外网依赖）
- [x] Pydantic Schema: `agent/schema.py`（12 个数据类，9/9 测试通过）
- [x] 补充设计文档: `design/kb_schema.md`, `design/pydantic_schema.md`, `design/evidence_gating.md`

### 2026-03-19: v4 → v5 设计迭代
- 完成: 根据 7 条修改意见完成设计迭代 (v4 → v5)
- 核心变更:
  1. 删除 Baseline Profile，KB 简化为两层（Metric KB + FPL）
  2. FPL 初始为空，通过 Reflect 从零积累
  3. Triage 6 步合并为 3 步
  4. 明确 THINK → GATE 路由决策机制（THINK 决定意图，GATE 验证证据）
  5. Evidence Gating 精简为三项核心原理，不暴露具体阈值
  6. Reflect 不依赖 Ground Truth，GT 比对移入独立评估框架
  7. 消融实验精简为 4 ablation + 2 baseline（大块消融）
- 产出:
  - `design/v5.md`（完整 v5 设计，~600 行）
  - `doc/agent_design_report.html`（HTML 报告更新至 v5）
  - `design/evidence_gating.md`（添加 v5 变更说明）
  - `design/kb_schema.md`（添加 v5 变更说明）
  - `.github/copilot-instructions.md`（v4→v5 引用更新）
  - `.github/instructions/design-docs.instructions.md`（v4→v5 引用更新）
- 下一步: Phase B Triage 实现（按 v5 三步设计 + 数据自适应基线）

---

## 2026-03-19: 文档全面清理（视觉一致性 + 独立语境）
- 完成: 清除所有设计文档中的内联版本对比说明，使文档具有独立语境
- 核心操作:
  1. 创建 `design/changelog.md`，收录 v1→v5 完整变更历史
  2. 清理 `design/v5.md`：删除 Part I "v4→v5变更摘要"，改为"设计原则"；删除所有 `v5 变更` 注释块；修复含版本引用的标题（"3 步精简版"→"3 步"、"两层体系（v5 简化）"→"两层结构"等）
  3. 清理 `doc/agent_design_report.html`：修复 §2.2 "四阶段"→"系统架构图"；删除所有 "v5 变更" warn 框；修复 STELLAR 对比表措辞；修复各处含版本号的标题
  4. 重写 `design/evidence_gating.md` 头部：去除 ⚠️ 警告块，改为简洁文档定位说明
  5. 重写 `design/kb_schema.md` 头部：同上
- 产出: `design/changelog.md`（新建）；上述 4 个文件头部已清理
- 下一步: Phase B Triage 实现

---

### Phase B: Triage Stage (确定性分诊) — 按 v5 设计
- [ ] Step 1: 数据自适应异常评分 (Z-score + 持久性过滤 + 变化点检测)
- [ ] Step 2: 时序因果排序 (causal_order + subsystem_scores + leading_subsystem)
- [ ] Step 3: FocusContext 构建 (聚合 + jobinfo 关联 + triage_confidence)

---

## 2026-03-20: Re-hypothesis 纠错机制设计 + 路由架构统一
- 完成:
  1. 发现并修复核心架构缺陷：GATE 缺少"重新假设"出路，所有初始假设 refuted 时只能 degrade
  2. 新增 GATE → HYPOTHESIZE 的 `rehypothesize` 路由分支（GATE 四路输出：pass/continue/rehypothesize/degrade）
  3. 增加 §4.4 HYPOTHESIZE 重新进入行为：refuted 假设作为负例、已有证据延续、新假设不重复 fault_type
  4. 防无限循环：`max_rehyp = 1`（最多 1 次重新假设）+ `min_budget_for_rehyp = 4`
  5. 消融表新增 Sup-C（禁用 rehypothesize，验证纠错机制贡献）
  6. 统一 HTML §2.2 SVG / HTML §3.3.2 SVG / v5.md 三处路由图的一致性
- 产出:
  - `design/v5.md`（架构图 + §4.3 路由表 + §4.4 新增 + §6.2 Stage C + 消融 Sup-C）
  - `doc/agent_design_report.html`（§2.2 SVG + §3.3.2 SVG + §3.3.3 Schema + §3.3.4/5 + §3.4 + §5 消融）
  - `design/changelog.md`（v5 变更第 8 条）
  - `design/evidence_gating.md`（Stage C 伪代码更新）
- 下一步: Phase B Triage 实现

### Phase C: Diagnosis Agent (LangGraph) — 按 v5 设计
- [ ] AgentState + Schema 更新 (清空 FPL、删除 Baseline Profile 依赖)
- [ ] LangGraph StateGraph 定义 (含 v5 路由决策机制)
- [ ] ReAct Loop 节点 (THINK/ACT/OBSERVE + Budget Check)
- [ ] Evidence Gating (GATE 节点，三项核心原理)
- [ ] 3 个 Tools 实现
- [ ] FINALIZE 节点

## 2026-03-23: v6 设计定稿（GATE 升级为独立 Audit Agent）
- 完成: 将 GATE 从纯 Python 规则节点升级为独立 Audit Agent（与 Diagnosis Agent 架构级分离，由 Orchestrator 协调）；具备有限 ReAct 循环、独立 AuditState、audit_budget、信息隔离和 hint 协议；同步补齐 v6 完整设计文档与展示文稿
- 产出: `design/v6.md`, `doc/agent_design_report_v6.html`, `design/changelog.md`
- 下一步: 按 v6 拆分 Phase C，实现 Diagnosis Agent → 最小 Audit Agent + Orchestrator → 完整 Audit Agent 的渐进式工程落地

### Phase D: Reflect & 评估
- [ ] REFLECT 节点 (无监督规则提炼 + FPL 写回)
- [ ] 评估框架 (GT 比对，独立于 Reflect)
- [ ] 消融实验矩阵 (4 ablation + 2 baseline + 7 supplementary)

## 2026-03-23: v6 设计文档全面审阅与完善
- 完成: SC26 评审（学术视角）+ 设计专家（技术视角）双重审阅，整合意见后完成 17 项修改
- 核心修改:
  1. **Claim 精确化**："零人工阈值" → "诊断结论零硬编码阈值"，区分信号抽取层与裁决层
  2. **Schema 补全**：定义 `current_confidence` 更新机制（THINK 每轮报告 + GATE 校准）
  3. **状态机补全**：新增 budget exhaustion 路由 + GATE budget 耗尽默认决策 + gate_hint 历史策略
  4. **消融矩阵扩展**：新增 Sup-E/F/G（恢复 strength / 禁用 hint / GATE 降级为规则）
  5. **失败处理补全**：新增 6 种遗漏场景（LLM API 故障、0 假设、0 异常等）
  6. **工具契约精确化**：补充 MetricQueryTool/KBRetrievalTool/DataAnalysisTool 参数规格
  7. **FPL Schema 补全**：新增 composite_discrimination 字段 + deprecated 可复活
  8. **评估方案增强**：新增 GATE 决策质量指标 + §8.4 跨模型评估
  9. **STELLAR 对比修正**：更公正描述参考工作的知识积累机制
  10. **术语约定**：causal_order 明确为 temporal ordering as causal prior
- 产出:
  - `design/v6.md`（17 项修改）
  - `doc/agent_design_report_v6.html`（7 项同步更新）
  - `design/changelog.md`（新增 v6 条目，10 项变更记录）
- 遗留（待 Phase C 实现时处理）:
  - `agent/schema.py` 需适配 v6 变更（删 strength、新增 AuditEvidence 等）
  - `design/evidence_gating.md` 需标记为 superseded by v6
- 下一步: Phase B Triage 实现 → Phase C 按 v6 拆分实现

## 2026-03-24: 全面落实双 Agent 独立架构
- 完成: Stage 2 从"主 Agent 内嵌 GATE Sub-Agent"全面升级为"Diagnosis Agent + Audit Agent 架构级分离、由 Orchestrator 协调"的双 Agent 模式
- 主要变更:
  1. **v6.md 术语统一**：消除全部 69 处独立 "GATE" 引用 → "Audit Agent"，消除 "主 Agent" → "Diagnosis Agent"，消除 "Sub-Agent"
  2. **v6.md 架构图重写**：§2.2 ASCII 图清晰展现 Orchestrator { Diagnosis Agent (diagnosis_graph) ↔ Audit Agent (audit_graph) } 的三图结构
  3. **v6.md 字段名对齐**：gate_budget → audit_budget, gate_evidence → audit_evidence, gate_trace → audit_trace（与 §4.1 AuditState 定义一致）
  4. **v6.html §2 SVG 重写**：820×680 新布局，Diagnosis Agent 和 Audit Agent 各自独立框线 + 信息隔离虚线 + Orchestrator 外层容器
  5. **v6.html 全文术语**：消除全部 Sub-Agent / 主 Agent / 独立 GATE 引用
  6. **消融表/实现路线**：Part X-XII 的 "无 GATE" → "无 Audit Agent"，Phase C 拆分为 diagnosis.py + audit.py + orchestrator.py
- 产出: `design/v6.md`, `doc/agent_design_report_v6.html`, `design/changelog.md`
- 下一步: Phase A 实现（Metric KB 构建）
