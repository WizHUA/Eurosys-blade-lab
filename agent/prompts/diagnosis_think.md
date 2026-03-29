## 当前状态
Step {step_number} / {max_react_iterations}
Tool calls used: {tool_calls_used} / {tool_calls_limit}

## 假设状态
{hypotheses_formatted}

## 已收集证据
{evidence_formatted}

## 验证清单
{verification_status}

{gate_hint_section}

{force_conclude_section}

## 请推理并决定下一步
基于当前假设和证据，分析下一步应该做什么：
1. 如果需要更多证据 → 选择合适的工具调用
2. 如果证据充分 → 输出 conclude

输出 JSON:
```json
{{
  "thought": "你的推理过程",
  "hypothesis_updates": [
    {{"hypothesis_id": "h1", "new_confidence": 0.85, "reason": "证据支持"}}
  ],
  "action": {{
    "type": "tool_call | conclude",
    "tool": "工具名（tool_call 时必填）",
    "args": {{}},
    "reasoning": "选择此行动的理由"
  }}
}}
```
