## 任务
你是 HPC 诊断报告撰写者。基于以下诊断结论和证据，生成最终诊断报告。

## 诊断结论
- 诊断类型: {diagnosis_type}
- 确认的根因:
{confirmed_root_causes}
- 关键证据:
{key_evidence}

## Triage 上下文
- Leading Subsystem: {leading_subsystem}
- 异常指标概览: {top_metrics_brief}

## 审查结果
{audit_summary}

## 输出格式
返回 JSON，必须符合以下结构:
```json
{{
  "anomaly_summary": "异常概述（1-2句）",
  "diagnosis_type": "{diagnosis_type}",
  "root_causes": [
    {{
      "cause": "根因描述",
      "fault_type": "标准故障类型名",
      "confidence": 0.0-1.0,
      "evidence_ids": ["e1", "e2"],
      "counter_evidence_ids": [],
      "fpl_pattern_id": null,
      "affected_nodes": []
    }}
  ],
  "derived_symptoms": [
    {{
      "symptom": "衍生症状描述",
      "caused_by_root_cause_index": 0,
      "evidence_ids": ["e3"]
    }}
  ],
  "solutions": [
    {{
      "action": "建议操作",
      "rationale": "操作理由",
      "risk": "low | medium | high",
      "verification": "验证方法",
      "applies_to_root_cause_index": 0
    }}
  ],
  "uncertainties": ["不确定因素列表"]
}}
```

注意:
- anomaly_summary 应简洁概括异常情况
- root_causes 按 confidence 降序排列
- 每个 root_cause 必须有 evidence_ids 支撑
- solutions 应具体可操作
- uncertainties 列出诊断中的不确定因素
