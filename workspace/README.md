# Agent 协作工作区

本目录是 Copilot Agent 团队的共享工作空间。

## 目录结构

```
workspace/
├── status/           # 项目状态追踪
│   └── progress.md   # 里程碑与进度（主管维护）
├── scratch/          # 临时工作区（需求草稿、设计迭代等）
└── archive/          # 已完成阶段的存档
```

## 使用规则

1. **progress.md** 是唯一的进度真相来源（Single Source of Truth）
2. **scratch/** 中的文件是临时的，可以被覆盖
3. 完成一个大阶段后，将重要产出移入 **archive/**
4. 设计文档的正式版本应在 `design/` 目录，不在此处

## Agent 团队

| Agent | 文件 | 职责 |
|-------|------|------|
| 主管 | `.github/agents/主管.agent.md` | 全局协调、进度追踪、任务分派 |
| 需求沟通 | `.github/agents/需求沟通.agent.md` | 需求梳理、边界确认 |
| 设计专家 | `.github/agents/设计专家.agent.md` | Agent 架构设计 |
| DrawIO绘图 | `.github/agents/DrawIO绘图.agent.md` | 架构图可视化 |
| 讲解员 | `.github/agents/讲解员.agent.md` | Agent 概念讲解 |
| SC26评审 | `.github/agents/SC26评审.agent.md` | 学术贡献评审 |
| 工程师 | `.github/agents/工程师.agent.md` | 代码实现 |
| 实验员 | `.github/agents/实验员.agent.md` | 实验设计与执行 |
| 论文助手 | `.github/agents/论文助手.agent.md` | 论文写作 |
