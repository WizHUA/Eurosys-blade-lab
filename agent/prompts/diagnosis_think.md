## 当前状态
Step {step_number} / {max_react_iterations}
Tool calls used: {tool_calls_used} / {tool_calls_limit}

> **重要**: 本实验的异常时间窗口为 `{anomaly_window_start}` ~ `{anomaly_window_end}`。
> 调用 MetricQueryTool 时，**必须使用此时间范围**，不要编造其他时间。
> **metrics 参数必须使用下方「可用指标」中的精确列名，不要自创名称。**

## 可用指标（MetricQueryTool 的 metrics 参数只能从此列表选择）
{available_metrics}

## 假设状态
{hypotheses_formatted}

## 已收集证据
{evidence_formatted}

## 验证清单
{verification_status}

{gate_hint_section}

{force_conclude_section}

> **重要**: fault_type 字段**必须**使用英文标准标签（cpu_fullload, mem_load, network_loss, network_delay, network_corrupt, disk_burn, disk_fill）。**严禁使用中文标签**（如「磁盘空间不足」等）。

## 请推理并决定下一步
基于当前假设和证据，分析下一步应该做什么：
1. 如果需要更多证据 → 选择合适的工具调用
2. 如果证据充分 → 输出 conclude

额外约束:
- 只有当某个假设拿到了该子系统的**直接观测证据**（来自 MetricQueryTool 或 DataAnalysisTool，对应指标属于该子系统）时，才能把它保留到 conclude
- KB/FPL 命中只是先验线索，不能单独作为 conclude 的依据
- 间接副作用指标不能替代直接验证。例如 CPU 忙、iowait 或 blocked processes 不能单独确认磁盘 I/O 异常；必须看到 disk/filesystem 相关指标的直接异常
- **内存-磁盘级联效应识别**：当 memory_active_anon_bytes、memory_mapped_bytes、memory_sunreclaim_bytes 等内存指标异常，同时出现 disk_write_iops/disk_total_iops 升高时，应优先判定为 **mem_load**——因为内存压力导致操作系统将匿名页换出到 swap，表现为磁盘 IO 上升，这是继发性效应而非磁盘原发故障。只有当 disk 指标异常出现在内存指标之前（时序因果序列靠前）时，才考虑 disk_burn 假设

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
