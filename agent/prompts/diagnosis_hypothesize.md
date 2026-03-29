## 任务
你是 HPC 故障诊断专家。基于以下 Triage 结果，生成 2-4 个最可能的根因假设。

## Triage 结果
- Leading Subsystem: {leading_subsystem}
- Triage Confidence: {triage_confidence}
- Top 异常指标（按异常分数降序）:
{top_metrics_formatted}
- 时序因果序列（最早出现异常 → 最晚）:
{causal_order}
- 子系统分数: {subsystem_scores}

## 知识库命中
{fpl_hits_formatted}

{rehyp_section}

## 输出要求
返回 JSON 数组，每个元素:
```json
{{
  "id": "h1",
  "root_cause": "一句话描述根因",
  "fault_type": "标准故障类型名（如 cpu_fullload, mem_load_ram, network_loss, disk_burn, disk_fill）",
  "subsystem": "cpu | memory | network | disk",
  "prior_confidence": 0.0-1.0,
  "required_verifications": [
    {{"description": "验证步骤描述", "required_metrics": ["metric_name"]}}
  ]
}}
```

注意:
- 生成 2-4 个假设，按可能性从高到低排列
- prior_confidence 反映你对该假设的初始信心
- 复合故障场景中，每个独立根因应作为单独假设
- fault_type 应使用标准名称: cpu_fullload, mem_load_ram, mem_load_buffer, network_loss, network_delay, network_corrupt, disk_burn, disk_fill
