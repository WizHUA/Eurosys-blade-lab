# HPC 异常诊断实验平台 — Copilot 项目指南

## 项目一句话

基于 Dual-Agent（Diagnosis + Audit）的 HPC 系统异常诊断工具，目标投稿 **SC'26**。

## 项目阶段与当前状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 1: 数据采集 (dataset_builder) | ✅ 完成 | Prometheus 150+ 指标 + ChaosBlade 故障注入 + NPB 工作负载 |
| Phase 2: LLM 诊断 (llm, 基线版) | ✅ 完成 | 单次 LLM 调用, few-shot, 多模型批量评测 |
| Phase A: 知识库构建 (agent/kb) | ✅ 完成 | metrics.yaml + fpl.jsonl + ChromaDB 向量化 |
| Phase 3: Agent 升级 (agent/) | 🔧 **实现中** | v6 Dual-Agent 架构，三图分离 |

**当前焦点**: Phase B — 实现 Triage + Config + Schema（v6 升级）

## 核心架构 (v6 Dual-Agent 设计)

```
inputs (metrics.csv + jobinfo.csv)
  → Stage 1: Triage (确定性 Python, 不消耗 LLM token)
    → FocusContext (Top-K 异常指标 + subsystem 分组)
  → Stage 2: Orchestrator Graph (LangGraph, 协调循环)
    → invoke Diagnosis Graph:
      → HYPOTHESIZE → INVESTIGATE (THINK/ACT/OBSERVE 循环) → ConclusionProposal
    → invoke Audit Graph:
      → Audit Agent 对抗性审计 → ACCEPT/REVISE/REJECT
    → 迭代直到 ACCEPT 或预算耗尽
  → Output: DiagnosisReport (JSON) + ExecutionTrace
```

## 关键目录

| 路径 | 内容 |
|------|------|
| `design/v6.md` | **当前方案** — v6 Dual-Agent 架构设计 |
| `design/implementation_spec.md` | **实现规格** — 15 章 + 4 附录, 权威实现指南 |
| `design/` | 架构设计文档 (v1→v6 演进) |
| `design/draw/` | Draw.io 架构图 + 绘图风格规范 (principle.md) |
| `design/stellar.md` | 参考论文 STELLAR (SC'25) |
| `agent/` | Phase 3 Agent 模块代码 (schema.py, kb/, 待实现) |
| `dataset_builder/` | Phase 1 数据采集 (已完成, 一般不改动) |
| `dataset_builder/data/formaltest/` | 29 预采集实验数据 (7 单故障 + 21 组合 + 1 无故障) |
| `llm/` | Phase 2 基线诊断模块 (已完成) |
| `llm/ref/` | Prompt 模板 + few-shot 示例 |
| `workspace/` | Agent 协作工作区 (进度、草稿、存档) |

## 技术栈

- **语言**: Python 3.10+
- **Agent 框架**: LangGraph ≥0.2, LangChain
- **向量数据库**: ChromaDB
- **LLM API**: OpenRouter (OpenAI-compatible), 密钥在 `.env`
- **数据分析**: pandas, scipy, numpy, ruptures
- **Schema**: Pydantic v2
- **HPC 工具**: Slurm, Prometheus, ChaosBlade, NPB

## 研究定位

**投稿目标**: SC'26 (The International Conference for High Performance Computing)

**核心贡献点**:
1. Deterministic Triage — 确定性统计分析压缩上下文，减少 token 消耗，可独立消融
2. Adversarial Evidence Gating — Dual-Agent 分离诊断与审计，零硬编码阈值，防止 confirmation bias
3. Fault Pattern Library — 可积累的诊断知识库，active/reflected 两态生命周期
4. ReAct + Multi-Hypothesis — 适配 HPC 复合故障场景的交织推理机制

**消融实验**: 仅 3 组 — Full / Abl-A (skip Triage) / Abl-B (skip Audit)

**论文写作要求**: 设计精巧但不过度复杂; 每个机制都要有消融实验支撑; 有 artifact 可复现。

## 开发命令

```bash
# Phase 1: 运行数据采集 (需要 HPC 集群)
cd dataset_builder && python run.py --experiment formaltest

# Phase 2: 运行基线 LLM 诊断
cd llm && python run.py --experiment formaltest

# Phase 3: 运行 Agent 诊断 (实现中)
cd agent && python main.py --experiment exp_001

# 安装依赖
pip install -r requirements.txt
```

## 代码风格

- 响应语言: 中文 (除代码注释、变量名、术语用英文)
- Python: 类型标注, Pydantic 校验关键 schema
- 文件编码: UTF-8
- 设计文档: Markdown, 中文为主
