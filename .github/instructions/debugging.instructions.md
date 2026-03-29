---
applyTo: "agent/**,eval/**,run_agent.py"
description: "系统性调试规范。遇到 bug、测试失败、LangGraph 状态异常时，先找根因，再提修复。"
---

# 系统性调试规范（项目定制版）

> 改编自 Superpowers systematic-debugging skill，针对本项目 LangGraph + LLM + ChromaDB 环境。

## 铁律

```
没有找到根因之前，不允许提出修复方案。
"试试看"不是调试方法，是浪费时间。
```

## 四阶段流程

你**必须**按顺序完成每个阶段，不得跳过。

---

### Phase 1：根因调查

**在动任何代码之前：**

1. **完整阅读错误信息** — 不要跳过 stack trace，记录文件名、行号、错误类型
2. **稳定复现** — 能稳定触发才能调试；如果不稳定，先加日志找规律
3. **定位组件边界** — 本项目有多个组件边界，逐层检查：

```
输入 CSV → Triage → FocusContext → Diagnosis Graph → ConclusionProposal
                                                    ↓
                              DiagnosisReport ← Orchestrator ← Audit Graph
```

**在各边界插入诊断日志，一次运行找到断裂点，再深入调查：**

```python
# 例：排查 Orchestrator 收不到正确 ConclusionProposal
print(f"[DEBUG] After INVOKE_DIAGNOSIS: {state['conclusion_proposal']}")
print(f"[DEBUG] diagnosis_budget remaining: {state['diagnosis_budget']}")
print(f"[DEBUG] After SUBMIT_TO_AUDIT: {state['audit_decision']}")
```

---

### Phase 2：常见问题模式对照

| 症状 | 首先检查 |
|------|---------|
| `KeyError` 或 `None` 在 State 中 | State TypedDict 字段是否初始化？节点返回值是否完整覆盖所有字段？ |
| LLM 输出解析失败 | JSON 格式是否正确？prompt 是否要求了正确的输出格式？temperature=0？ |
| ChromaDB 查询返回空 | collection 名称是否与 `build_kb.py` 一致？chroma_db/ 路径是否正确？ |
| OpenRouter 4xx 错误 | `.env` 中 API 密钥是否加载？模型名称是否为 `deepseek/deepseek-chat`？ |
| LangGraph 图进入死循环 | 预算字段是否递减？终止条件是否在路由逻辑中判断？ |
| Triage 结果为空 | metrics.csv 列名是否匹配 metrics.yaml 中的 metric_name？时间戳解析？ |
| 节点间状态未传递 | LangGraph 节点必须返回 **dict**，key 必须与 State TypedDict 字段名完全一致 |

---

### Phase 3：形成假设，最小化测试

1. **明确陈述假设**：说出 "我认为问题是 X，因为 Y"，写下来
2. **最小化变更**：只改一个变量，不要同时改多处
3. **验证假设**：
   - 通过 → 进入 Phase 4
   - 不通过 → 形成新假设，**不要在原来的修改上叠加**

**LLM 相关调试技巧**：
```python
# 调试期间固定 LLM 行为
config = LLMConfig(temperature=0, seed=42)

# mock LLM 隔离节点逻辑
from unittest.mock import patch
with patch("agent.diagnosis.call_llm") as m:
    m.return_value = '{"known_good_response": true}'
    result = hypothesize_node(state)
```

---

### Phase 4：修复根因

1. **先写复现测试**（参考 `tdd.instructions.md` A类规范）
2. **只修复根因**，不顺手改其他"感觉不好"的代码
3. **验证**：测试通过 + 其他测试未破坏
4. **如果 3 次修复后仍然失败** → 停下来，重新审视架构，询问是否设计假设有误

---

## 本项目特有的调试工具

```python
# 打印完整 LangGraph State（在任意节点末尾插入）
import json
print(json.dumps({k: str(v) for k, v in state.items()}, indent=2, ensure_ascii=False))

# 检查当前预算状态
print(f"diagnosis_budget={state.get('diagnosis_budget')}, audit_budget={state.get('audit_budget')}")

# 验证 ChromaDB 集合
import chromadb
client = chromadb.PersistentClient(path="agent/kb/chroma_db")
print(client.list_collections())

# 验证 API 连通性
from dotenv import load_dotenv; load_dotenv()
import os; print("KEY:", os.getenv("OPENROUTER_API_KEY", "NOT FOUND")[:10] + "...")
```

## 红色警报 — 立即停下

- "先试试改这个看看" → 回 Phase 1，先找根因
- 同时改了 3 处，不知道哪个起作用了 → 回滚，一次改一处
- 已经尝试 3+ 次修复都没用 → 停下来，可能是架构假设错误，需要讨论
- "LLM 不稳定所以无法调试" → 用 `temperature=0 + mock` 让 LLM 行为确定
