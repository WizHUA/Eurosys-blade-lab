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

### Phase B: Triage Stage (确定性分诊)
- [ ] 异常检测算法: Z-score + 分位数 + 静态阈值
- [ ] 持久性过滤 + 变化点检测
- [ ] FocusContext 生成 (含 causal_order)

### Phase C: Diagnosis Agent (LangGraph)
- [ ] AgentState + Pydantic Schema
- [ ] LangGraph StateGraph 定义
- [ ] ReAct Loop 节点 (THINK/ACT/OBSERVE)
- [ ] Evidence Gating (GATE 节点)
- [ ] 3 个 Tools 实现

### Phase D: Reflect & 评估
- [ ] REFLECT 节点 + 规则写回
- [ ] 消融实验矩阵
- [ ] 基线对比实验
