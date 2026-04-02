## 任务
你是 HPC 故障诊断专家。基于以下 Triage 结果，生成 2-4 个最可能的根因假设。

{triage_warning}

## Triage 结果
- Leading Subsystem: **{leading_subsystem}**（统计分析确定的最可能故障子系统）
- Triage Confidence: {triage_confidence}
- Top 异常指标（按异常分数降序）:
  > 说明：异常方向 ↑高于基线 = 该指标值**升高**（超过正常水平）；
  >       异常方向 ↓低于基线 = 该指标值**降低**（低于正常水平）。
{top_metrics_formatted}
- 时序因果序列（最早出现异常 → 最晚）:
{causal_order}
- 子系统分数: {subsystem_scores}

{metric_hints}

## 知识库命中
{fpl_hits_formatted}

{rehyp_section}

## 已知故障类型参考
以下是本 HPC 环境中已知的故障注入类型，**请优先使用这些标签作为 fault_type**：
- `cpu_fullload` — CPU 满载运行（计算密集型过载）
- `mem_load` — 内存压力过大（匿名页/缓冲区占用异常）
- `network_loss` — 网络丢包
- `network_delay` — 网络延迟增大
- `network_corrupt` — 网络数据包损坏
- `disk_burn` — 磁盘 I/O 过载（读写压力）
- `disk_fill` — 磁盘空间被填满

如果观测到的异常**不匹配**上述任何类型，你可以使用自定义描述。
对于复合故障场景，每个独立根因应使用**单独的标签**。

## 输出要求
返回 JSON 数组，每个元素:
```json
{{
  "id": "h1",
  "root_cause": "一句话描述根因",
  "fault_type": "从已知故障类型参考中选择最匹配的标签（如 cpu_fullload、mem_load、disk_fill 等），若不匹配已知类型可自定义",
  "subsystem": "cpu | memory | network | disk",
  "prior_confidence": 0.0-1.0,
  "required_verifications": [
    {{"description": "验证步骤描述", "required_metrics": ["metric_name"]}}
  ]
}}
```

## 重要规则（必须遵守）
1. **Leading Subsystem 参考**：Triage 给出的 leading_subsystem 仅作**参考**，不作为 h1 的硬性约束。需结合 Top 异常指标的实际内容进行独立判断——特别注意**继发性指标**可能误导子系统归属（例如：内存压力满载后操作系统会将数据换页到磁盘，导致 disk_write_iops/disk_total_iops 上升；此时磁盘指标异常属于内存问题的**次级效应**，不应归因为磁盘故障）。应优先按 Top 异常指标所揭示的**直接故障信号**确定 h1。

2. **⚠️ 指标方向必须正确理解**：
   - filesystem_avail_bytes ↓ = 磁盘空间**减少** → 磁盘空间不足的强烈信号
   - cpu_iowait_percent ↓ = CPU **占满**（无需等待IO，在高速运算）→ CPU 过载的典型特征，不要误判为磁盘问题
   - cpu_iowait_percent ↑ = 进程等待磁盘IO → 磁盘 I/O 阻塞的继发症状
   - schedstat_running_rate ↓ = CPU 调度等待 → 磁盘阻塞（非 CPU 计算过载）
   - processes_blocked ↑ = I/O 阻塞 → 磁盘 I/O 异常的典型症状

3. **禁止跨子系统误判**：只有当某子系统存在**直接指标异常**时，才生成该子系统的假设。间接症状（如 cpu_iowait 反映 disk 问题）不可单独支撑跨子系统假设。

4. **prior_confidence 反映实际信心**：prior_confidence 应反映该假设与实际 Top 异常指标的匹配程度，而非 leading_subsystem。若 Top 异常指标中有 3 个以上直接支持某假设，prior_confidence 可达 0.6+；若仅有 1-2 个间接证据，则设为 0.3-0.5。

5. **fault_type 必须使用英文标准标签**：无论推理过程使用何种语言，`fault_type` 字段**必须**使用已知故障类型参考表中的英文标签（cpu_fullload, mem_load, network_loss, network_delay, network_corrupt, disk_burn, disk_fill）。**严禁使用中文标签**（如磁盘空间不足、内存负载等）。

- 生成 2-4 个假设，按可能性从高到低排列
- 复合故障场景中，每个独立根因应作为单独假设
- fault_type 应优先使用已知故障类型参考中的标准标签，基于观察到的异常现象选择最匹配的标签
