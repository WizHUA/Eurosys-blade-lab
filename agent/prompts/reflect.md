## 任务
基于以下已确认的诊断结果，提炼出可复用的故障模式规则（Fault Pattern Library 条目）。

## 诊断结果
- Run ID: {run_id}
- Root Cause: {root_cause}
- Fault Type: {fault_type}
- Confidence: {confidence}
- 关键证据:
{evidence_summary}

## Triage 上下文
- Leading Subsystem: {leading_subsystem}
- Top 异常指标: {top_metrics}

## 输出格式
返回 JSON:
```json
{{
  "fault_type": "{fault_type}",
  "symptom_signature": {{
    "leading_subsystem": "子系统名",
    "required_metrics": ["关键异常指标列表"],
    "optional_metrics": ["可选辅助指标"],
    "metric_behavior": {{
      "metric_name": "描述异常行为（如 >90%, spike, drop）"
    }}
  }},
  "verification_steps": [
    "自然语言验证步骤1",
    "自然语言验证步骤2"
  ],
  "solutions": [
    "推荐解决方案1",
    "推荐解决方案2"
  ]
}}
```

注意:
- required_metrics 只包含该故障类型的核心诊断指标
- verification_steps 应具体可执行
- solutions 应基于实际 HPC 运维经验
