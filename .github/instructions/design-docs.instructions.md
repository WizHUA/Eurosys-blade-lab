---
applyTo: "design/**"
description: "设计文档规范。确保design目录下的文档遵循版本演进规范、包含消融设计、对齐SC26投稿目标。"
---

# 设计文档规范

## 版本管理
- 设计文档使用 `v<N>.md` 命名，N 单调递增
- **v6.md 是当前最新方案**，新设计应基于 v6 迭代
- 不删除历史版本，它们记录了设计演进和决策理由

## 文档结构要求
每个设计文档必须包含：
1. **动机与问题** — 解决什么痛点
2. **方案详述** — 含 Schema / 伪代码 / 状态转移
3. **与整体架构的关系** — 在系统中的位置
4. **消融设计** — 去掉后系统如何退化

## 关键术语（保持一致性）
- Triage（分诊，不用 screening/filtering）
- Evidence Gating（证据门控）
- FocusContext（专有名词）
- Fault Pattern Library（故障模式库）
- ReAct（Reason + Act 交织）
- causal_order（时序因果序列）
