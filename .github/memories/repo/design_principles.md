# 关键设计原则 (v6) — 2026-04-02 更新

## 1. Agent 本身是无监督的 (CRITICAL)
- Agent 推理过程中**不预设任何故障类型标签**
- Prompt 和代码逻辑中不暴露具体故障类型名（如 cpu_fullload）
- Agent 输出**自由文本诊断描述**，不是闭集分类
- FPL 初始为空，通过 Reflect 从诊断经验中积累模式

## 2. 故障标签仅用于评估 (CRITICAL)
- 我们定义了一组标签（cpu_fullload, mem_load, disk_fill 等）来表征 HPC 节点异常的大部分情况
- 这些标签**不参与 Agent 推理**，仅在评估阶段使用
- `eval/label_mapper.py` 用独立 LLM 调用将 Agent 的自由文本诊断映射为标准标签
- 这个映射过程完全独立于 Agent 流程，仅用于计算 Hit@1/Hit@3 等指标
- 标签定义放在 eval 模块中，不放在 agent/ 目录下

## 3. 保留完整输出供人工审查 (CRITICAL)
- 实验运行时，**所有中间输出必须完整保留**
- 包括：execution_trace.json、diagnosis_report.json、LLM 原始响应
- 目的：人工 check 是否存在幻觉（格式错误、编造指标、逻辑跳跃）
- 输出目录：agent/data/<ablation>/<exp_name>/

## 4. FPL 空启动
- FPL (fpl.jsonl) 初始为空文件
- 种子数据备份在 fpl_seed_backup.jsonl，仅供开发参考
- Reflect 阶段会向 FPL 写入新学到的模式（可含 fault_type，因为这是从诊断经验中推理出的）

## 5. 已清理的泄露点 (2026-04-01)
- agent/prompts/ — 已移除所有 fault_type 枚举和映射
- agent/diagnosis.py — 已移除 metric_hints 中的故障标签和 LEADING_SUBSYSTEM_FAULT_MAP
- agent/kb/fpl.jsonl — 已清空
- agent/kb/metrics.yaml — 已移除 related_faults 字段

## 6. eval 中的故障标签列表是允许的
- eval/evaluate.py 的 FAULT_TYPE_ALIASES 是评估用的，不泄露给 Agent
- eval/label_mapper.py 的 KNOWN_FAULT_TYPES 是评估用的，不泄露给 Agent
- 这不违反"无监督"原则，因为标签只在评估阶段使用
