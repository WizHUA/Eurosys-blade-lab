# 知识库完整 Schema 规格

> **⚠️ v5 变更说明（2026-03-19）**：v5 删除 Baseline Profile 组件（KB 由三层简化为两层：Metric KB + FPL）。
> FPL 初始为空（v4 的 fpl_001~008 手工规则不再预填），系统通过 Reflect 从零积累。
> Triage 的 Z-score 基线改为数据自适应计算。以 `design/v5.md Part V` 为准。
> 本文件的 Metric KB 条目规格和 FPL Schema 仍然有效，Baseline Profile 相关内容已废弃。

> **文档定位**：`design/v4.md §6` 的补充细化。v4.md 给出了 KB 三层结构的框架，
> 本文档为工程师提供**可直接落地的完整条目示例、字段说明和构建规程**。
> 工程师完成本文档后即可启动 Phase A 实施。

---

## 1. Metric KB 条目完整规格

### 1.1 字段说明

```yaml
- name: "<指标名，与 metrics.yaml 保持一致>"
  subsystem: "cpu|memory|swap|vmstat|disk|filesystem|network|load|processes|system"
  description: "<面向诊断的中文描述，说明什么场景下此指标会异常>"
  prometheus_query: "<与 metrics.yaml 里的 query 字段保持一致>"
  metric_type: "counter|gauge|derived_rate"
  # counter: 原始计数器，需 rate() 换算；gauge: 当前值，直接读取；
  # derived_rate: metrics.yaml 已内嵌 rate()，直接返回速率值
  unit: "<物理单位，rate()换算后的单位>"
  normal_range:
    min: <float>   # HPC 正常负载下的合理下限
    max: <float>   # HPC 正常负载下的合理上限（较高，计算节点本来就忙）
    unit: "<与 unit 字段一致>"
    note: "<对 normal_range 的补充说明，如'依赖 workload 类型'>"
  strength_thresholds:
    # 每个级别由 condition（值域）+ min_duration_sec（持续时间）共同判定
    weak:
      condition: "<比较表达式，如 > 80>"
      min_duration_sec: 30
    medium:
      condition: "<...>"
      min_duration_sec: 60
    strong:
      condition: "<...>"
      min_duration_sec: 300
  related_faults: ["<chaos.yaml 中的 fault_type 名>"]
  downstream_effects:
    # 该指标异常时，哪些其他指标会被间接影响（用于 cascade vs composite 判断）
    - metric: "<下游指标名>"
      lag_range_sec: [<min_lag>, <max_lag>]
      condition: "if_sustained"  # only_if_extreme | always | if_sustained
  common_misconceptions: "<常见误判说明，工程师、LLM 提示>"
```

**`metric_type` 说明**（重要，决定 OBSERVE 节点的处理方式）：
- **counter**：`node_cpu_seconds_total`、`node_disk_read_bytes_total` 等。MetricQueryTool 必须先 `rate()` 换算，再做聚合统计。
- **gauge**：`node_load1`、`node_memory_MemAvailable_bytes` 等。直接读当前值，无需 `rate()`。
- **derived_rate**：`metrics.yaml` 的 query 已经内嵌了 `rate()` 或算术换算（如 `cpu_usage_percent`），MetricQueryTool 直接返回换算后的值。

---

### 1.2 CPU 子系统（5 个核心条目）

```yaml
# ---- CPU 子系统 ----

- name: "cpu_usage_percent"
  subsystem: "cpu"
  description: "CPU 总使用率（100 - idle%）。正常 HPC 计算任务会跑到 80-95%；
    若持续 ≥ 95% 超过 5 分钟且 load1 > num_cpus，强烈提示 cpu_fullload 故障。"
  prometheus_query: '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)'
  metric_type: "derived_rate"
  unit: "%"
  normal_range:
    min: 20.0
    max: 95.0
    unit: "%"
    note: "NPB-BT/SP/CG 等 MPI 类 workload 正常运行时 70-95% 均属正常；idle > 5% 表示未满载"
  strength_thresholds:
    weak:
      condition: "> 90"
      min_duration_sec: 30
    medium:
      condition: "> 95"
      min_duration_sec: 60
    strong:
      condition: "> 97"
      min_duration_sec: 300
  related_faults: ["cpu_fullload"]
  downstream_effects:
    - metric: "memory_usage_percent"
      lag_range_sec: [120, 600]
      condition: "if_sustained"   # CPU 满载长期会导致内存换页压力
    - metric: "cpu_iowait_percent"
      lag_range_sec: [60, 300]
      condition: "only_if_extreme"  # 极端 CPU 满载可能触发 swapout → IO 等待
  common_misconceptions: "单节点高 CPU 不代表故障；需结合 load1 > num_cpus 和 job 分配情况判断。
    若只有 1 个 CPU 核心跑到 100%（多核平均后 < 20%），不应认定为 cpu_fullload。"

- name: "cpu_iowait_percent"
  subsystem: "cpu"
  description: "CPU 等待 IO 的时间比例。高 iowait 通常是磁盘或网络 IO 瓶颈的继发症状，
    也可在内存严重不足触发 swap 时出现。单独高 iowait 首先排查 disk_burn / disk_fill。"
  prometheus_query: 'avg(rate(node_cpu_seconds_total{mode="iowait"}[1m])) * 100'
  metric_type: "derived_rate"
  unit: "%"
  normal_range:
    min: 0.0
    max: 10.0
    unit: "%"
    note: "MPI 密集型计算任务的 iowait 通常 < 5%；> 15% 即可关注"
  strength_thresholds:
    weak:
      condition: "> 15"
      min_duration_sec: 30
    medium:
      condition: "> 25"
      min_duration_sec: 60
    strong:
      condition: "> 40"
      min_duration_sec: 120
  related_faults: ["disk_burn", "disk_fill", "mem_load_ram"]
  downstream_effects: []
  common_misconceptions: "iowait 高不等于 CPU 故障；它是下游症状，根因通常在磁盘或内存。
    不要将 iowait 计入 cpu_fullload 的支持证据。"

- name: "context_switches_rate"
  subsystem: "cpu"
  description: "每秒上下文切换次数（rate 后的速率值）。CPU 满载时进程频繁争抢 CPU，
    上下文切换率会显著高于 baseline。此指标变化是 cpu_fullload 的辅助确认证据。"
  prometheus_query: "rate(node_context_switches_total[1m])"
  metric_type: "derived_rate"
  unit: "switches/s"
  normal_range:
    min: 1000.0
    max: 50000.0
    unit: "switches/s"
    note: "依赖节点 CPU 核心数和 workload 进程数；应参照 Baseline Profile 的倍数，而非绝对值"
  strength_thresholds:
    weak:
      condition: "> baseline * 2.0"
      min_duration_sec: 60
    medium:
      condition: "> baseline * 3.0"
      min_duration_sec: 60
    strong:
      condition: "> baseline * 5.0"
      min_duration_sec: 120
  related_faults: ["cpu_fullload"]
  downstream_effects: []
  common_misconceptions: "绝对值意义不大，必须与 Baseline Profile 对比；
    不同 workload 的 baseline 差异可达 10x，不能用固定阈值。"

- name: "load_1min"
  subsystem: "load"
  description: "系统 1 分钟平均负载（运行+等待队列进程数）。
    load1 > num_vcpus 表示系统存在过载；cpu_fullload 故障会导致 load1 急剧上升。"
  prometheus_query: "node_load1"
  metric_type: "gauge"
  unit: "dimensionless"
  normal_range:
    min: 0.1
    max: 4.0   # 假设 4 核节点；应动态读取 num_cpus 替换此值
    unit: "dimensionless"
    note: "阈值应为 num_vcpus（从节点信息动态获取）；此处默认值仅供参考"
  strength_thresholds:
    weak:
      condition: "> num_vcpus * 0.9"
      min_duration_sec: 30
    medium:
      condition: "> num_vcpus * 1.2"
      min_duration_sec: 60
    strong:
      condition: "> num_vcpus * 1.5"
      min_duration_sec: 180
  related_faults: ["cpu_fullload"]
  downstream_effects: []
  common_misconceptions: "load1 是滑动平均，故障注入后有 ~1 分钟延迟才会充分体现；
    不要用 load1 极值作为 onset_time，应用 cpu_usage 的 onset_time。"

- name: "processes_blocked"
  subsystem: "processes"
  description: "D 状态（不可中断睡眠，通常等待 IO）进程数。
    disk_burn 或 disk_fill 导致磁盘 IO 阻塞时，此值会上升；严重内存换页时也会上涨。"
  prometheus_query: "node_procs_blocked"
  metric_type: "gauge"
  unit: "count"
  normal_range:
    min: 0
    max: 5
    unit: "count"
    note: "HPC 集群正常运行时应 ≤ 2；持续 > 10 是异常信号"
  strength_thresholds:
    weak:
      condition: "> 5"
      min_duration_sec: 30
    medium:
      condition: "> 15"
      min_duration_sec: 60
    strong:
      condition: "> 30"
      min_duration_sec: 120
  related_faults: ["disk_burn", "disk_fill", "mem_load_ram"]
  downstream_effects: []
  common_misconceptions: "blocked 进程增加是症状，不是根因；需配合 disk / memory 指标联合判断。"
```

---

### 1.3 Memory 子系统（5 个核心条目）

```yaml
# ---- Memory 子系统 ----

- name: "memory_usage_percent"
  subsystem: "memory"
  description: "物理内存使用率（1 - MemAvailable / MemTotal）。
    mem_load_ram 故障会将此值快速推高至注入参数设定的目标百分比（默认 70%）。"
  prometheus_query: "(1 - node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes) * 100"
  metric_type: "derived_rate"
  unit: "%"
  normal_range:
    min: 20.0
    max: 75.0
    unit: "%"
    note: "NPB workload 正常运行内存占用约 30-60%；超过 80% 开始进入高风险区"
  strength_thresholds:
    weak:
      condition: "> 80"
      min_duration_sec: 30
    medium:
      condition: "> 88"
      min_duration_sec: 60
    strong:
      condition: "> 93"
      min_duration_sec: 180
  related_faults: ["mem_load_ram", "mem_load_buffer", "mem_load_stack"]
  downstream_effects:
    - metric: "swap_usage_percent"
      lag_range_sec: [30, 180]
      condition: "only_if_extreme"   # 内存耗尽时才会触发 swap
    - metric: "page_major_faults_rate"
      lag_range_sec: [30, 300]
      condition: "only_if_extreme"
    - metric: "cpu_iowait_percent"
      lag_range_sec: [60, 300]
      condition: "only_if_extreme"
  common_misconceptions: "Linux 的 page cache 会占用大量内存，但属可回收内存，
    MemAvailable 已排除可回收部分；直接用 MemFree 会误报大量 false positive。"

- name: "swap_usage_percent"
  subsystem: "swap"
  description: "Swap 使用率（1 - SwapFree / SwapTotal）。
    任何 Swap 使用在 HPC 环境都是严重信号（正常计算任务不应使用 Swap）；
    mem_load 故障超出物理内存上限时会触发此指标。"
  prometheus_query: "(1 - node_memory_SwapFree_bytes/node_memory_SwapTotal_bytes) * 100"
  metric_type: "derived_rate"
  unit: "%"
  normal_range:
    min: 0.0
    max: 1.0
    unit: "%"
    note: "HPC 场景 Swap > 1% 即视为异常；>10% 表示已发生显著的内存换出"
  strength_thresholds:
    weak:
      condition: "> 1"
      min_duration_sec: 30
    medium:
      condition: "> 10"
      min_duration_sec: 60
    strong:
      condition: "> 30"
      min_duration_sec: 120
  related_faults: ["mem_load_ram", "mem_load_stack"]
  downstream_effects:
    - metric: "disk_io_usage_percent"
      lag_range_sec: [10, 60]
      condition: "always"   # swap_out 直接产生磁盘 IO
    - metric: "cpu_iowait_percent"
      lag_range_sec: [10, 60]
      condition: "always"
  common_misconceptions: "swap_usage_percent=0 不代表完全没有内存压力；
    page_major_faults_rate 更能反映实时换页活动。"

- name: "page_major_faults_rate"
  subsystem: "vmstat"
  description: "每秒主缺页异常次数（rate 后）。主缺页表示进程访问了不在物理内存中的页面，
    需要从磁盘（Swap）读取，直接导致进程阻塞。mem_load 重度场景会出现此指标飙升。"
  prometheus_query: "rate(node_vmstat_pgmajfault[1m])"
  metric_type: "derived_rate"
  unit: "faults/s"
  normal_range:
    min: 0.0
    max: 5.0
    unit: "faults/s"
    note: "HPC 作业稳定运行后应接近 0；启动阶段可能有短暂的 minor faults，不用关注"
  strength_thresholds:
    weak:
      condition: "> 5"
      min_duration_sec: 30
    medium:
      condition: "> 50"
      min_duration_sec: 30
    strong:
      condition: "> 200"
      min_duration_sec: 60
  related_faults: ["mem_load_ram", "mem_load_stack"]
  downstream_effects: []
  common_misconceptions: "与 page_faults_rate（pgfault）区分：pgfault 包含 minor fault
    （仅需页表更新，无磁盘 IO），在正常程序启动时大量出现；pgmajfault 才是真正的 swap 活动。"

- name: "anon_memory_percent"
  subsystem: "memory"
  description: "匿名内存（进程堆和 mmap 分配的用户空间内存）占物理内存的比例。
    mem_load_ram (ChaosBlade) 会创建大量匿名内存（mmap + madvise），
    此指标是区分 ram 模式与 buffer 模式内存故障的关键。"
  prometheus_query: >
    ((node_memory_Active_anon_bytes + node_memory_Inactive_anon_bytes)
    /node_memory_MemTotal_bytes) * 100
  metric_type: "derived_rate"
  unit: "%"
  normal_range:
    min: 5.0
    max: 40.0
    unit: "%"
    note: "NPB 作业运行中匿名内存约 20-40%；mem_load_ram 会将其推至 60%+"
  strength_thresholds:
    weak:
      condition: "> 50"
      min_duration_sec: 30
    medium:
      condition: "> 65"
      min_duration_sec: 60
    strong:
      condition: "> 80"
      min_duration_sec: 120
  related_faults: ["mem_load_ram"]
  downstream_effects: []
  common_misconceptions: "buffer_usage_percent 高但 anon_memory_percent 正常，
    更可能是 mem_load_buffer 故障而非 mem_load_ram。"

- name: "memory_dirty_bytes"
  subsystem: "memory"
  description: "内核脏页大小（已修改但未写回磁盘）。mem_load_buffer 故障会分配大量
    buffer cache（page cache），间接推高脏页；disk_burn 写压力也会使此值波动。"
  prometheus_query: "node_memory_Dirty_bytes"
  metric_type: "gauge"
  unit: "bytes"
  normal_range:
    min: 0
    max: 104857600   # 100 MB
    unit: "bytes"
    note: "正常应 < 100MB；持续 > 500MB 表示 writeback 压力"
  strength_thresholds:
    weak:
      condition: "> 524288000"    # 500 MB
      min_duration_sec: 30
    medium:
      condition: "> 1073741824"   # 1 GB
      min_duration_sec: 60
    strong:
      condition: "> 2147483648"   # 2 GB
      min_duration_sec: 120
  related_faults: ["mem_load_buffer", "disk_burn"]
  downstream_effects:
    - metric: "disk_write_rate_bytes_per_sec"
      lag_range_sec: [30, 120]
      condition: "if_sustained"   # writeback daemon 触发写入
  common_misconceptions: "脏页高不一定是故障，写密集型应用本就会有大量脏页；
    配合 disk_write_rate 趋势和 Baseline Profile 来判断。"
```

---

### 1.4 Network 子系统（4 个核心条目）

```yaml
# ---- Network 子系统 ----

- name: "network_receive_drop_rate"
  subsystem: "network"
  description: "网络接收方向丢包速率（每秒）。ChaosBlade network-loss 使用 tc-netem 注入丢包，
    丢掉的包会计入 kernel 的 drop 计数器，此指标会在故障注入后立即上升。
    HPC MPI 通信对丢包极其敏感，任何持续丢包都是严重故障信号。"
  prometheus_query: 'rate(node_network_receive_drop_total{device="ens33"}[1m])'
  metric_type: "derived_rate"
  unit: "packets/s"
  normal_range:
    min: 0.0
    max: 0.5
    unit: "packets/s"
    note: "正常 HPC 集群网络丢包应趋近 0；任何 > 1 packets/s 持续出现均需关注"
  strength_thresholds:
    weak:
      condition: "> 1"
      min_duration_sec: 15
    medium:
      condition: "> 50"
      min_duration_sec: 30
    strong:
      condition: "> 200"
      min_duration_sec: 60
  related_faults: ["network_loss"]
  downstream_effects:
    - metric: "network_receive_packets_rate"
      lag_range_sec: [0, 30]
      condition: "always"   # 有效接收包减少
  common_misconceptions: "tc-netem 的 network-loss 可能主要体现在发送方向丢包，
    也可能是接收方向，取决于 ChaosBlade 的配置参数 (--network-interface 和方向)；
    应同时检查 transmit_drop_rate。"

- name: "network_transmit_drop_rate"
  subsystem: "network"
  description: "网络发送方向丢包速率（每秒）。ChaosBlade 默认注入出向（egress）丢包，
    此指标是 network_loss 故障的主要信号；与 receive_drop_rate 联合判断故障方向。"
  prometheus_query: 'rate(node_network_transmit_drop_total{device="ens33"}[1m])'
  metric_type: "derived_rate"
  unit: "packets/s"
  normal_range:
    min: 0.0
    max: 0.5
    unit: "packets/s"
    note: "与 receive_drop_rate 相同，任何持续丢包均为严重异常"
  strength_thresholds:
    weak:
      condition: "> 1"
      min_duration_sec: 15
    medium:
      condition: "> 50"
      min_duration_sec: 30
    strong:
      condition: "> 200"
      min_duration_sec: 60
  related_faults: ["network_loss"]
  downstream_effects: []
  common_misconceptions: "与 receive_drop_rate 的区别：发送方向丢包通常由本地 tc 规则造成，
    接收方向丢包由对端或中间设备造成。ChaosBlade 注入的是本地 tc 规则，因此
    transmit_drop_rate 更直接反映故障。"

- name: "network_receive_errors_rate"
  subsystem: "network"
  description: "网络接收错误速率（CRC、帧错误等）。与丢包不同，errors 表示物理层问题。
    在 network_loss 故障注入场景下此指标通常不变；若升高，提示真实硬件问题。"
  prometheus_query: 'rate(node_network_receive_errs_total{device="ens33"}[1m])'
  metric_type: "derived_rate"
  unit: "errors/s"
  normal_range:
    min: 0.0
    max: 0.1
    unit: "errors/s"
    note: "正常应为 0；任何持续 errors 均是硬件级问题信号"
  strength_thresholds:
    weak:
      condition: "> 0.1"
      min_duration_sec: 15
    medium:
      condition: "> 5"
      min_duration_sec: 30
    strong:
      condition: "> 20"
      min_duration_sec: 60
  related_faults: []
  downstream_effects: []
  common_misconceptions: "network_loss 故障（软件注入）不会产生 network errors；
    errors 上升提示物理链路问题，需要排查网线和交换机端口。"

- name: "network_receive_rate_bytes_per_sec"
  subsystem: "network"
  description: "网络接收吞吐量（字节/秒）。network_loss 故障注入时，MPI 重传会稍微升高带宽，
    但更显著的是吞吐量的不稳定性（方差增大）；正常 MPI 通信的吞吐应相对稳定。"
  prometheus_query: 'rate(node_network_receive_bytes_total{device="ens33"}[1m])'
  metric_type: "derived_rate"
  unit: "bytes/s"
  normal_range:
    min: 1000000.0    # 1 MB/s
    max: 100000000.0  # 100 MB/s（取决于节点内通信量）
    unit: "bytes/s"
    note: "阈值高度依赖 workload 和节点数；主要用 Baseline Profile 比较，而非绝对值"
  strength_thresholds:
    weak:
      condition: "< baseline * 0.7 OR > baseline * 3.0"
      min_duration_sec: 30
    medium:
      condition: "< baseline * 0.5 OR > baseline * 5.0"
      min_duration_sec: 30
    strong:
      condition: "< baseline * 0.2"
      min_duration_sec: 60
  related_faults: ["network_loss"]
  downstream_effects: []
  common_misconceptions: "吞吐量下降与吞吐量上升都可以是 network_loss 的表现（取决于重传量）；
    单独使用此指标不如 drop_rate 可靠；应作为辅助证据。"
```

---

### 1.5 Disk 子系统（4 个核心条目）

```yaml
# ---- Disk 子系统 ----

- name: "disk_io_usage_percent"
  subsystem: "disk"
  description: "磁盘 IO 利用率（设备忙碌时间百分比）。disk_burn 故障（持续读写）会将此值
    推至接近 100%；区分于 disk_fill（disk_fill 期间此值也高，但会在填充完成后下降）。"
  prometheus_query: 'rate(node_disk_io_time_seconds_total{device="sda"}[1m]) * 100'
  metric_type: "derived_rate"
  unit: "%"
  normal_range:
    min: 0.0
    max: 30.0
    unit: "%"
    note: "NPB 作业运行时磁盘 IO 通常 < 20%（多数计算在内存中进行）"
  strength_thresholds:
    weak:
      condition: "> 60"
      min_duration_sec: 30
    medium:
      condition: "> 80"
      min_duration_sec: 60
    strong:
      condition: "> 95"
      min_duration_sec: 120
  related_faults: ["disk_burn", "disk_fill"]
  downstream_effects:
    - metric: "cpu_iowait_percent"
      lag_range_sec: [5, 30]
      condition: "always"
    - metric: "processes_blocked"
      lag_range_sec: [5, 60]
      condition: "if_sustained"
  common_misconceptions: "disk_io_usage_percent=100% 不等于磁盘坏了，仅表示设备已饱和。
    区分 disk_burn 和 disk_fill：disk_burn 是持续高 IO 直到故障结束；
    disk_fill 的 IO 在填满后会骤降而文件系统占用率持续高。"

- name: "disk_write_rate_bytes_per_sec"
  subsystem: "disk"
  description: "磁盘写入吞吐量（字节/秒，1 分钟滑动速率）。
    disk_fill 阶段此值持续高（约 50-200 MB/s）；disk_burn write 模式此值也高；
    通过时序变化区分：disk_fill 在写完后骤降，disk_burn 持续稳定高位。"
  prometheus_query: 'rate(node_disk_written_bytes_total{device="sda"}[1m])'
  metric_type: "derived_rate"
  unit: "bytes/s"
  normal_range:
    min: 0.0
    max: 10000000.0   # 10 MB/s
    unit: "bytes/s"
    note: "NPB 在 /tmp 写检查点文件；正常写出 < 10 MB/s，持续 > 50 MB/s 为异常"
  strength_thresholds:
    weak:
      condition: "> 52428800"     # 50 MB/s
      min_duration_sec: 30
    medium:
      condition: "> 104857600"    # 100 MB/s
      min_duration_sec: 60
    strong:
      condition: "> 157286400"    # 150 MB/s
      min_duration_sec: 120
  related_faults: ["disk_burn", "disk_fill"]
  downstream_effects: []
  common_misconceptions: "100 MB/s 的写入速率是否合理取决于硬件规格（HDD ~100-200 MB/s,
    SSD ~500 MB/s+）；阈值应根据实际硬件测试的 peak write bandwidth 设置。"

- name: "filesystem_usage_percent"
  subsystem: "filesystem"
  description: "根文件系统（/）使用率。disk_fill 故障的核心信号：
    ChaosBlade 向 /tmp 写入数据，与根文件系统位于同一分区时，此值会持续增长直至达到
    注入时设定的 percent 目标（默认 90%）。"
  prometheus_query: >
    (1 - node_filesystem_avail_bytes{mountpoint="/"}
    /node_filesystem_size_bytes{mountpoint="/"}) * 100
  metric_type: "derived_rate"
  unit: "%"
  normal_range:
    min: 10.0
    max: 65.0
    unit: "%"
    note: "HPC 节点根分区正常使用 < 60%；/tmp 写入会体现在此指标上（若同一分区）"
  strength_thresholds:
    weak:
      condition: "> 75"
      min_duration_sec: 30
    medium:
      condition: "> 85"
      min_duration_sec: 60
    strong:
      condition: "> 92"
      min_duration_sec: 60
  related_faults: ["disk_fill"]
  downstream_effects:
    - metric: "disk_io_usage_percent"
      lag_range_sec: [0, 10]
      condition: "if_sustained"   # 填充过程中产生 IO
  common_misconceptions: "filesystem_usage_percent 上升速度取决于 ChaosBlade 的写入速率；
    slow fill 可能在数分钟后才明显；不要因为短时间内变化不大就忽略此指标。"

- name: "disk_read_rate_bytes_per_sec"
  subsystem: "disk"
  description: "磁盘读取吞吐量（字节/秒）。disk_burn read 模式的主要信号。
    配合 disk_write_rate 可以判断 burn 方向（read-only / write-only / bidirectional）。"
  prometheus_query: 'rate(node_disk_read_bytes_total{device="sda"}[1m])'
  metric_type: "derived_rate"
  unit: "bytes/s"
  normal_range:
    min: 0.0
    max: 10000000.0   # 10 MB/s
    unit: "bytes/s"
    note: "NPB 正常读盘 < 10 MB/s；disk_burn 默认 read+write，读写均会飙升"
  strength_thresholds:
    weak:
      condition: "> 52428800"     # 50 MB/s
      min_duration_sec: 30
    medium:
      condition: "> 104857600"    # 100 MB/s
      min_duration_sec: 60
    strong:
      condition: "> 157286400"    # 150 MB/s
      min_duration_sec: 120
  related_faults: ["disk_burn"]
  downstream_effects: []
  common_misconceptions: "disk_fill 主要是写操作（write large file）；读速率高主要指向 disk_burn。
    若读写都高，优先考虑 disk_burn（bidirectional mode）。"
```

---

### 1.6 System 级指标补充说明

以下指标不单独作为主要故障信号，但在 OBSERVE 节点中作为**辅助排除证据**使用：

| 指标名 | 作用 | 说明 |
|--------|------|------|
| `network_up` | 排除网卡 down | 等于 1 时网卡正常；= 0 时为物理故障 |
| `filesystem_readonly` | 排除磁盘 full 导致 read-only 挂载 | = 1 时分区已 read-only，disk_fill 级故障 |
| `vm_oom_kills_total` | OOM 事件 | gauge, 递增说明进程被 OOM killer 终止；mem_load 极端情况会触发 |
| `filefd_usage_percent` | 文件描述符耗尽 | 接近 100% 时进程无法创建 socket，影响 MPI |

---

## 2. Fault Pattern Library 初始条目

存储文件：`kb/fpl.jsonl`（每行一个 JSON 对象）

**版本说明**：以下为手工编写的初始条目（`source: "manual"`），均需要至少 2 次成功诊断确认后才升为正式规则（`status: "active"` → `"confirmed"`）。新增学习规则通过 REFLECT 节点写入，初始 `status: "candidate"`。

---

### 2.1 单故障规则

```jsonl
{
  "pattern_id": "fpl_001",
  "fault_type": "cpu_fullload",
  "version": 1,
  "status": "confirmed",
  "source": "manual",
  "provenance_exp_ids": ["exp_001_cpu_fullload"],
  "confidence": 0.90,
  "symptom_signature": {
    "leading_subsystem": "cpu",
    "required_metrics": ["cpu_usage_percent", "load_1min"],
    "optional_metrics": ["context_switches_rate", "processes_blocked"],
    "temporal_pattern": "cpu_usage rises sharply within 30s of fault injection;
      load_1min follows with ~60s lag; memory and disk metrics remain stable",
    "causal_order_hint": ["cpu_usage_percent", "load_1min", "context_switches_rate"]
  },
  "verification_steps": [
    "confirm cpu_usage_percent p95 > 90% sustained for > 5 minutes",
    "confirm load_1min > num_vcpus * 1.0 (system overloaded)",
    "confirm memory_usage_percent < 85% (to rule out mem_load co-fault)",
    "confirm disk_io_usage_percent < 60% (to rule out disk_burn co-fault)"
  ],
  "solutions": [
    "identify the overloading process: check top/ps output in jobinfo",
    "check Slurm job resource limits (--cpus-per-task) and enforce them",
    "if ChaosBlade fault injection: blade destroy create cpu fullload"
  ]
}

{
  "pattern_id": "fpl_002",
  "fault_type": "mem_load_ram",
  "version": 1,
  "status": "confirmed",
  "source": "manual",
  "provenance_exp_ids": ["exp_002_mem_load", "exp_003_mem_load"],
  "confidence": 0.88,
  "symptom_signature": {
    "leading_subsystem": "memory",
    "required_metrics": ["memory_usage_percent", "anon_memory_percent"],
    "optional_metrics": ["swap_usage_percent", "page_major_faults_rate"],
    "temporal_pattern": "memory_usage rises steadily to target level (70% default);
      anon_memory_percent rises concurrently; cpu_usage may slightly increase but stays < 80%",
    "causal_order_hint": ["memory_usage_percent", "anon_memory_percent", "swap_usage_percent"]
  },
  "verification_steps": [
    "confirm memory_usage_percent p95 > 80% sustained for > 3 minutes",
    "confirm anon_memory_percent increased by > 20% relative to baseline",
    "confirm cpu_usage_percent < 85% (sustained cpu anomaly would indicate co-fault)",
    "confirm disk_io_usage_percent < 60% OR iowait < 20% (swap IO is expected but should not dominate)"
  ],
  "solutions": [
    "identify process consuming high anonymous memory: check /proc/meminfo smaps",
    "check Slurm job --mem limit and enforce it",
    "if ChaosBlade fault injection: blade destroy create mem load"
  ]
}

{
  "pattern_id": "fpl_003",
  "fault_type": "mem_load_buffer",
  "version": 1,
  "status": "active",
  "source": "manual",
  "provenance_exp_ids": ["exp_004_mem_load"],
  "confidence": 0.75,
  "symptom_signature": {
    "leading_subsystem": "memory",
    "required_metrics": ["memory_usage_percent", "memory_dirty_bytes"],
    "optional_metrics": ["buffer_usage_percent", "disk_write_rate_bytes_per_sec"],
    "temporal_pattern": "memory_usage rises; buffer cache(Buffers) grows significantly;
      anon_memory_percent remains relatively stable (distinguishes from ram mode);
      dirty_bytes may increase if writeback cannot keep up",
    "causal_order_hint": ["memory_usage_percent", "memory_dirty_bytes", "buffer_usage_percent"]
  },
  "verification_steps": [
    "confirm memory_usage_percent p95 > 75% sustained for > 3 minutes",
    "confirm buffer_usage_percent increased by > 10 percentage points from baseline",
    "confirm anon_memory_percent increase < 10 percentage points (low anonymous growth distinguishes from ram mode)"
  ],
  "solutions": [
    "drop page cache: echo 3 > /proc/sys/vm/drop_caches",
    "check if a process is doing large sequential reads that flood buffer cache",
    "if ChaosBlade fault injection: blade destroy create mem load"
  ]
}

{
  "pattern_id": "fpl_004",
  "fault_type": "network_loss",
  "version": 1,
  "status": "confirmed",
  "source": "manual",
  "provenance_exp_ids": ["exp_005_network_loss", "exp_026_network_loss"],
  "confidence": 0.92,
  "symptom_signature": {
    "leading_subsystem": "network",
    "required_metrics": ["network_transmit_drop_rate", "network_receive_drop_rate"],
    "optional_metrics": ["network_receive_rate_bytes_per_sec", "processes_blocked"],
    "temporal_pattern": "drop rate spikes immediately at fault injection;
      MPI job may stall or exhibit irregular communication patterns;
      cpu and memory metrics remain normal or slightly decreased (job stalled)",
    "causal_order_hint": ["network_transmit_drop_rate", "network_receive_drop_rate"]
  },
  "verification_steps": [
    "confirm network_transmit_drop_rate OR network_receive_drop_rate > 50 packets/s sustained for > 1 minute",
    "confirm affected interface is the MPI communication interface (ens33 in this cluster)",
    "confirm network_receive_errors_rate remains near 0 (exclude physical link errors)",
    "confirm cpu_usage and memory_usage_percent are NOT simultaneously elevated (isolate fault to network only)"
  ],
  "solutions": [
    "check tc qdisc rules on ens33: tc qdisc show dev ens33",
    "if ChaosBlade fault injection: blade destroy create network loss",
    "for persistent drops: check switch port statistics and cable integrity"
  ]
}

{
  "pattern_id": "fpl_005",
  "fault_type": "disk_burn",
  "version": 1,
  "status": "confirmed",
  "source": "manual",
  "provenance_exp_ids": ["exp_006_disk_burn", "exp_027_disk_burn"],
  "confidence": 0.87,
  "symptom_signature": {
    "leading_subsystem": "disk",
    "required_metrics": ["disk_io_usage_percent", "disk_write_rate_bytes_per_sec"],
    "optional_metrics": ["disk_read_rate_bytes_per_sec", "cpu_iowait_percent", "processes_blocked"],
    "temporal_pattern": "IO utilization jumps to near 100% at fault injection;
      both read and write rates spike simultaneously (bidirectional burn);
      filesystem_usage_percent remains STABLE (distinguishes from disk_fill);
      cpu_iowait rises as a secondary effect",
    "causal_order_hint": ["disk_io_usage_percent", "disk_write_rate_bytes_per_sec", "cpu_iowait_percent"]
  },
  "verification_steps": [
    "confirm disk_io_usage_percent > 90% sustained for > 2 minutes",
    "confirm disk_write_rate_bytes_per_sec > 50 MB/s OR disk_read_rate_bytes_per_sec > 50 MB/s",
    "confirm filesystem_usage_percent is NOT increasing (growth > 5% would indicate disk_fill instead)",
    "confirm the high-IO device is the target of ChaosBlade injection (/tmp path → check which device /tmp is on)"
  ],
  "solutions": [
    "identify the IO-intensive process: iotop -a",
    "if ChaosBlade fault injection: blade destroy create disk burn",
    "temporary mitigation: ionice -c 3 on the offending process to reduce IO priority"
  ]
}

{
  "pattern_id": "fpl_006",
  "fault_type": "disk_fill",
  "version": 1,
  "status": "confirmed",
  "source": "manual",
  "provenance_exp_ids": ["exp_007_disk_fill"],
  "confidence": 0.85,
  "symptom_signature": {
    "leading_subsystem": "disk",
    "required_metrics": ["filesystem_usage_percent"],
    "optional_metrics": ["disk_write_rate_bytes_per_sec", "disk_io_usage_percent", "filesystem_files_free"],
    "temporal_pattern": "filesystem_usage_percent increases steadily during fill phase,
      then plateaus at target (default ~90%); disk_write_rate is high during fill but drops after;
      disk_io_usage_percent follows write rate pattern (high then stable-or-low);
      cpu_usage remains normal",
    "causal_order_hint": ["disk_write_rate_bytes_per_sec", "filesystem_usage_percent"]
  },
  "verification_steps": [
    "confirm filesystem_usage_percent > 85% AND increasing trend during fault window",
    "confirm disk_io_usage_percent is NOT sustained at > 90% after fill completes (disk_burn would stay high)",
    "confirm cpu_usage_percent < 80% (CPU should be unaffected)"
  ],
  "solutions": [
    "identify large files: find /tmp -size +100M -ls",
    "if ChaosBlade fault injection: blade destroy create disk fill",
    "emergency: rm -rf /tmp/chaosblade* to reclaim space"
  ]
}
```

---

### 2.2 复合故障规则

```jsonl
{
  "pattern_id": "fpl_007",
  "fault_type": "composite:cpu_fullload+mem_load",
  "version": 1,
  "status": "active",
  "source": "manual",
  "provenance_exp_ids": ["exp_008_cpu_fullload_mem_load", "exp_009_cpu_fullload_mem_load", "exp_010_cpu_fullload_mem_load"],
  "confidence": 0.82,
  "symptom_signature": {
    "leading_subsystem": "cpu",
    "required_metrics": ["cpu_usage_percent", "memory_usage_percent"],
    "optional_metrics": ["anon_memory_percent", "load_1min", "page_major_faults_rate"],
    "temporal_pattern": "cpu_usage and memory_usage BOTH rise within a short window (< 60s of each other);
      if memory anomaly appears > 2 min AFTER cpu anomaly is established, consider cascade vs composite;
      anon_memory_percent rising confirms independent mem_load injection (not cache eviction cascade)",
    "causal_order_hint": ["cpu_usage_percent", "memory_usage_percent", "anon_memory_percent"]
  },
  "composite_discrimination": {
    "cascade_indicator": "if memory_usage onset is > 120s after cpu_usage onset AND anon_memory increase < 15%,
      likely a cascade (CPU pressure evicts cache, raising memory_usage_percent slightly) — classify as single_fault:cpu_fullload",
    "composite_indicator": "if anon_memory_percent rises > 20 percentage points from baseline,
      this is an independent mem_load injection — classify as composite_fault"
  },
  "verification_steps": [
    "confirm cpu_usage_percent p95 > 90% sustained for > 3 minutes (CPU fault present)",
    "confirm memory_usage_percent > 80% sustained for > 3 minutes (Memory fault present)",
    "confirm anon_memory_percent increased > 20 percentage points from baseline (independent malloc, not cache pressure)",
    "check onset time difference: |t_onset(cpu) - t_onset(memory)| < 120s supports co-injection"
  ],
  "solutions": [
    "address CPU: identify and constrain CPU-hogging processes",
    "address Memory: identify high-anon-memory processes, check job --mem limits",
    "both faults likely injected simultaneously by ChaosBlade; destroy both: blade destroy create cpu fullload && blade destroy create mem load"
  ]
}

{
  "pattern_id": "fpl_008",
  "fault_type": "composite:cpu_fullload+network_loss",
  "version": 1,
  "status": "active",
  "source": "manual",
  "provenance_exp_ids": ["exp_011_cpu_fullload_network_loss"],
  "confidence": 0.83,
  "symptom_signature": {
    "leading_subsystem": "cpu",
    "required_metrics": ["cpu_usage_percent", "network_transmit_drop_rate"],
    "optional_metrics": ["load_1min", "network_receive_drop_rate"],
    "temporal_pattern": "cpu_usage and network drop rates BOTH spike; these two metrics are
      causally independent (high CPU does not cause network drops in normal hardware);
      any co-occurrence should be treated as composite fault immediately;
      job typically shows both CPU slowdown AND MPI communication stall",
    "causal_order_hint": ["cpu_usage_percent", "network_transmit_drop_rate"]
  },
  "composite_discrimination": {
    "cascade_indicator": "NONE — CPU fullload does NOT cause network drops in software-injected scenarios;
      any combination of cpu anomaly + network drop is definitively composite",
    "composite_indicator": "both cpu_usage p95 > 90% AND network_drop_rate > 50/s simultaneously"
  },
  "verification_steps": [
    "confirm cpu_usage_percent p95 > 90% sustained for > 3 minutes",
    "confirm network_transmit_drop_rate OR network_receive_drop_rate > 50 packets/s sustained for > 1 minute",
    "confirm these two anomalies co-exist in the same fault window (not sequential)",
    "confirm memory_usage_percent < 85% and filesystem_usage_percent < 80% (exclude third fault)"
  ],
  "solutions": [
    "address network first (MPI stall can cause job failure rapidly): blade destroy create network loss",
    "then address CPU: blade destroy create cpu fullload",
    "restart affected MPI job if it timed out during network fault"
  ]
}
```

---

## 3. Baseline Profile 格式规格

### 3.1 文件存储规范

```
kb/baseline/
├── global.json                     ← 所有实验聚合的全局 baseline（fallback）
├── bp_npb_bt_4nodes.json           ← NPB BT class B, 4 节点
├── bp_npb_cg_4nodes.json           ← NPB CG class B, 4 节点
├── bp_npb_sp_4nodes.json           ← NPB SP class B, 4 节点
└── bp_no_workload.json             ← 无 workload 时的系统空闲 baseline
```

**命名规范**：`bp_{workload_type}_{num_nodes}nodes.json`
- `workload_type`：`npb_bt` / `npb_cg` / `npb_sp` / `npb_is` / `no_workload`
- `num_nodes`：参与 MPI 作业的节点数量

### 3.2 Profile 文件格式

```json
{
  "profile_id": "bp_npb_bt_4nodes",
  "workload": "NPB_BT",
  "workload_class": "B",
  "node_count": 4,
  "nodes": ["blade01", "blade02", "blade03", "blade04"],
  "source_experiments": ["exp_normal_001", "exp_normal_002"],
  "computation_window": {
    "description": "取每个实验故障注入前 120 秒稳定运行窗口",
    "method": "first_stable_window_before_fault"
  },
  "created_at": "2025-09-20T00:00:00Z",
  "metrics": {
    "cpu_usage_percent": {
      "mean": 78.5,
      "std": 5.2,
      "p5": 68.0,
      "p25": 74.0,
      "p50": 79.0,
      "p75": 83.0,
      "p95": 88.0,
      "max": 94.0,
      "window_size_sec": 120,
      "sample_count": 240
    },
    "memory_usage_percent": {
      "mean": 42.3,
      "std": 3.1,
      "p5": 37.0,
      "p25": 40.0,
      "p50": 42.0,
      "p75": 45.0,
      "p95": 48.0,
      "max": 52.0,
      "window_size_sec": 120,
      "sample_count": 240
    }
    // ...其他指标
  }
}
```

### 3.3 构建方法

**Step 1**：从现有实验数据中提取故障前稳定窗口

```python
def extract_baseline_window(df: pd.DataFrame, fault_start_time, pre_fault_sec=120):
    """
    从 metrics.csv 中取故障注入前 120 秒的数据作为 baseline 窗口。
    过滤条件：窗口内无故障记录（参考 chaos.csv）。
    """
    window_end = fault_start_time
    window_start = fault_start_time - timedelta(seconds=pre_fault_sec)
    return df[(df['timestamp'] >= window_start) & (df['timestamp'] < window_end)]
```

**Step 2**：对每列计算统计量

```python
def compute_profile_stats(series: pd.Series) -> dict:
    return {
        "mean": float(series.mean()),
        "std": float(series.std()),
        "p5": float(series.quantile(0.05)),
        "p25": float(series.quantile(0.25)),
        "p50": float(series.quantile(0.50)),
        "p75": float(series.quantile(0.75)),
        "p95": float(series.quantile(0.95)),
        "max": float(series.max()),
        "window_size_sec": len(series) * SCRAPE_INTERVAL_SEC,
        "sample_count": len(series)
    }
```

**Step 3**：Triage 使用时，用 Z-score 与 profile 对比

```python
def compute_zscore(current_value: float, profile_stats: dict) -> float:
    if profile_stats["std"] < 1e-9:
        return 0.0  # 常量指标
    return (current_value - profile_stats["mean"]) / profile_stats["std"]
```

### 3.4 Fallback 策略

当找不到对应 workload/节点数的 Baseline Profile 时，按以下优先级降级：

| 优先级 | 策略 | 条件 |
|--------|------|------|
| 1 | 精确匹配：`bp_{workload}_{N}nodes.json` | 完全一致 |
| 2 | workload 类型匹配：`bp_{workload}_*.json` 的均值 | 节点数不同 |
| 3 | 全局 fallback：`global.json` | 无任何 workload 匹配 |
| 4 | 静态阈值（无需 profile）| global.json 也不存在 |

当使用 fallback 时，FocusContext 中的 `triage_confidence` 应乘以降级因子：
- 精确匹配：× 1.0
- workload 匹配：× 0.85
- 全局 fallback：× 0.65
- 静态阈值：× 0.50

这个因子向后传递到 Hypothesis 的 `prior_confidence` 计算中，降低无 profile 支撑的先验可信度。
