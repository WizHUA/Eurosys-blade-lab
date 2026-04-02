## 任务
你是 HPC 诊断报告撰写者。基于以下已经确定的诊断结论，用中文撰写诊断报告的叙述部分。

**重要约束**: 根因列表已经由诊断流程确定，你不需要也不能修改根因。

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
返回 JSON，只包含以下字段（不要包含 root_causes 字段）:
```json
{{
  "anomaly_summary": "异常概述（1-2句，简洁概括系统异常情况）",
  "uncertainties": ["诊断中的不确定因素或需要进一步调查的问题"]
}}
```

注意:
- anomaly_summary 应基于已确认根因做简洁概述
- uncertainties 列出证据不足或尚不明确的地方，若无不确定因素可填空数组
- 不要在输出 JSON 中包含 root_causes 字段
