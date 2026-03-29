---
applyTo: "agent/**,eval/**,run_agent.py"
description: "TDD 规范。在 agent/ 和 eval/ 下编写代码时，必须先写测试、再写实现。"
---

# 测试驱动开发规范（项目定制版）

> 改编自 Superpowers TDD skill，针对本项目 Python/pytest/LangGraph 环境。

## 铁律

```
先写测试，再写实现代码。
没有亲眼看到测试失败，就不知道它是否测了正确的事情。
```

## 模块分类与测试策略

### A 类：纯确定性函数 → 严格 TDD

适用：`triage.py`、`schema.py`（Pydantic 验证）、`tools/*.py`（工具的数据处理逻辑）、`eval/evaluate.py`

**流程**：
1. **RED** — 写最小 pytest 用例，描述期望行为，运行，**确认失败**
2. **GREEN** — 写刚好让测试通过的最少代码，运行，**确认通过**
3. **REFACTOR** — 清理重复，不新增行为

```python
# 例：triage.py 验收标准（先写这个测试，再写 run_triage）
def test_cpu_fullload_leading_subsystem():
    focus = run_triage("dataset_builder/data/formaltest/extracted_data/exp_001")
    assert focus.leading_subsystem == "cpu"

def test_no_fault_empty_metrics():
    focus = run_triage("dataset_builder/data/formaltest/extracted_data/exp_029")
    assert len(focus.top_metrics) == 0 or focus.triage_confidence < 0.3
```

### B 类：LLM 驱动节点 → 契约测试（Mock LLM）

适用：`diagnosis.py`（HYPOTHESIZE/THINK 节点）、`audit.py`（GATE_THINK 节点）、`finalize.py`、`reflect.py`

**LLM 节点不做端到端 TDD，但必须测节点脚手架**：

```python
from unittest.mock import patch

def test_hypothesize_node_state_transition():
    """测试节点是否正确更新 State，而非测 LLM 输出内容。"""
    state = DiagnosisState(focus_context=mock_focus, hypotheses=[], ...)
    with patch("agent.diagnosis.call_llm") as mock_llm:
        mock_llm.return_value = '[{"fault_type": "cpu_fullload", "confidence": 0.8}]'
        new_state = hypothesize_node(state)
    assert len(new_state["hypotheses"]) > 0
    assert new_state["hypotheses"][0].fault_type == "cpu_fullload"
```

测试内容：State 字段是否正确更新、预算是否递减、错误时是否有 fallback。

### C 类：集成/验收 → 冒烟测试

适用：`orchestrator.py`、完整 pipeline

使用标准冒烟数据集，允许 LLM 调用，在 Phase C 尾部验证：

| 实验 | 期望 leading_subsystem | 期望 top-1 fault_type |
|------|----------------------|----------------------|
| exp_001 | cpu | cpu_fullload |
| exp_005 | network | network_loss |
| exp_008 | cpu + memory | cpu_fullload + mem_load_ram |
| exp_029 | （空）| no_fault |

## 测试文件位置

```
agent/tests/
  test_schema.py        # Phase B: schema 验证
  test_triage.py        # Phase B: run_triage() 验收
  test_config.py        # Phase B: AblationFlags 工厂方法
  test_tools.py         # Phase C: 工具 mock 测试
  test_nodes.py         # Phase C: 节点契约测试
eval/tests/
  test_evaluate.py      # Phase D: GT 比对逻辑
```

## 运行命令

```bash
# 激活环境
conda activate blade-lab

# 单文件测试
python -m pytest agent/tests/test_triage.py -v

# 全部单元测试（排除集成）
python -m pytest agent/tests/ eval/tests/ -v -m "not integration"

# 冒烟集成测试
python -m pytest agent/tests/ -v -m integration
```

## 例外情况（以下不需要 TDD）

- `agent/prompts/*.md` — prompt 模板，无需测试
- `agent/config.py` 中的常量定义 — 直接写，schema 测试覆盖
- `agent/kb/` — 知识库已完成，不修改

## 红色警报 — 立即停下

- 已经写了实现代码才想起要写测试 → **删除实现，从测试开始**
- 测试写完直接通过（没有失败阶段）→ 测试在测试实现细节而非行为，重写
- "Triage 太简单了不用测" → triage.py 是最适合 TDD 的部分，必须测
- "LLM 不确定，测不了" → mock LLM 来测节点脚手架，这是可测的
