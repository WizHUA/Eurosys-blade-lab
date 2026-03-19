# HPC 异常诊断实验平台 — Copilot 项目指南

## 项目一句话

基于 LLM Agent 的 HPC 系统异常诊断工具，目标投稿 **SC'26**。

## 项目阶段与当前状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 1: 数据采集 (dataset_builder) | ✅ 完成 | Prometheus 150+ 指标 + ChaosBlade 故障注入 + NPB 工作负载 |
| Phase 2: LLM 诊断 (llm, 基线版) | ✅ 完成 | 单次 LLM 调用, few-shot, 多模型批量评测 |
| Phase 3: Agent 升级 (llm → agent) | 🔧 **设计中** | 将单次 LLM 调用升级为 Agentic Workflow |

**当前焦点**: Phase 3 — 参考 STELLAR (SC'25) 的 Agent 设计范式，构建 HPC-Diagnosis Agent。

## 核心架构 (v5 设计)

```
inputs (metrics.csv + jobinfo.csv)
  → Stage 1: Triage (确定性 Python, 不消耗 LLM token)
    → FocusContext (Top-K 异常指标 + causal_order + 子系统分组)
  → Stage 2: Diagnosis Agent (LangGraph, ReAct 循环)
    → HYPOTHESIZE → THINK/ACT/OBSERVE 循环 → GATE → FINALIZE
    → Tools: MetricQueryTool, KBRetrievalTool, DataAnalysisTool
  → Stage 3: REFLECT (规则提炼 → Fault Pattern Library 回写)
  → Output: DiagnosisReport (JSON) + ExecutionTrace
```

## 关键目录

| 路径 | 内容 |
|------|------|
| `design/` | 架构设计文档 (v1→v5 演进), **v5.md 是当前方案** |
| `design/draw/` | Draw.io 架构图 + 绘图风格规范 (principle.md) |
| `design/stellar.md` | 参考论文 STELLAR (SC'25) |
| `dataset_builder/` | Phase 1 数据采集 (已完成, 一般不改动) |
| `dataset_builder/data/formaltest/` | 29+ 预采集实验数据 (7 单故障 + 21 组合 + 1 无故障) |
| `llm/` | Phase 2 诊断模块 (基线版 + **待升级的 Agent 版**) |
| `llm/ref/` | Prompt 模板 + few-shot 示例 |
| `workspace/` | Agent 协作工作区 (进度、草稿、存档) |

## 技术栈

- **语言**: Python 3.10+
- **Agent 框架**: LangGraph / LangChain
- **向量数据库**: ChromaDB
- **LLM API**: OpenRouter (OpenAI-compatible), 密钥在 `.env`
- **数据分析**: pandas, scipy, numpy
- **HPC 工具**: Slurm, Prometheus, ChaosBlade, NPB

## 研究定位

**投稿目标**: SC'26 (The International Conference for High Performance Computing)

**核心贡献点**:
1. Hybrid Control — 确定性 Triage + LLM Agent 诊断, 两阶段可独立消融
2. Evidence Gating — 结论必须有最低证据支撑, 防止 LLM "编故事"
3. Fault Pattern Library — 可积累、可回写的诊断知识库
4. ReAct + 多假设并行 — 适配 HPC 复合故障场景

**论文写作要求**: 设计精巧但不过度复杂; 每个机制都要有消融实验支撑; 有 artifact 可复现。

## 开发命令

```bash
# Phase 1: 运行数据采集 (需要 HPC 集群)
cd dataset_builder && python run.py --experiment formaltest

# Phase 2: 运行基线 LLM 诊断
cd llm && python run.py --experiment formaltest

# 安装依赖
pip install -r requirements.txt
```

## 代码风格

- 响应语言: 中文 (除代码注释、变量名、术语用英文)
- Python: 类型标注, Pydantic 校验关键 schema
- 文件编码: UTF-8
- 设计文档: Markdown, 中文为主
