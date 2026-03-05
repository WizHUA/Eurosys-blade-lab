# DataSet Builder 实验指南

`dataset_builder` 是本项目的核心数据生产模块，负责在 HPC 环境中自动执行实验流程：提交计算作业、注入系统故障、并同步采集监控数据。

## 1. 核心概念与逻辑

实验由 **Experiment Manager** 统一调度，它并行控制三个子系统：

*   **Workload Manager**: 负责向 Slurm 集群提交 NPB (NAS Parallel Benchmarks) 计算任务。
*   **Chaos Manager**: 负责调用 ChaosBlade 向计算节点注入故障（如 CPU 满载、网络丢包）。
*   **Monitor**: 负责从 Prometheus 抓取实验期间的系统性能指标。

所有配置均通过 YAML 文件管理，无需修改代码即可定义新的实验场景。

## 2. 配置详解 (`config/`)

实验的定义分散在四个配置文件中，这实现了组件的解耦。

> **New!** 关于 Prometheus 监控指标与 ChaosBlade 故障注入的深度指南，请阅读 [docs/infrastructure_guide.md](docs/infrastructure_guide.md)。

### 2.1 实验总控 (`experiments.yaml`)
这是定义实验的入口文件。

```yaml
experiments:
  - name: "test"                  # 实验名称，运行命令时使用
    description: "功能测试"
    output_dir: "data/test"       # 数据保存路径
    component_configs:            # 引用其他配置文件
      monitor: "config/metrics.yaml"
      workload: "config/workloads.yaml"
      chaos: "config/chaos.yaml"
    experiment_plan:
      workload_plan: "light_background"  # 引用 workloads.yaml 中的 plan
      chaos_plan: "single_cpu_stress"    # 引用 chaos.yaml 中的 plan
      duration:
        preparation: 10           # 准备时间 (s)
        recovery: 10              # 故障恢复等待时间 (s)
        cleanup: 10               # 清理时间 (s)
    monitoring:
      interval: 15                # 监控数据采集间隔
      step: "15s"                 # Prometheus 查询步长
```

### 2.2 故障定义 (`chaos.yaml`)
定义故障类型（模板）和具体的注入计划。

```yaml
# 1. 定义故障类型 (Templates)
fault_types:
  cpu_fullload:
    type: "cpu"
    subtype: "fullload"
    default_params:
      cpu-percent: 70
      timeout: 60

# 2. 定义注入计划 (Plans)
chaos_plans:
  single_cpu_stress:              # Plan 名称
    description: "单次CPU故障"
    faults:
      - fault_type: "cpu_fullload" # 引用上面的类型
        inject_delay: 5            # 实验开始后第 5 秒注入
        target_node: "node01"      # (可选) 指定节点
        custom_params:             # 覆盖默认参数
          cpu-percent: 90
```

### 2.3 负载定义 (`workloads.yaml`)
定义 NPB 程序和提交计划。

```yaml
# 1. 定义程序
npb_programs:
  cg_B:
    executable: "cg.B.x"
    resource_requirements:
      nodes: 1
      ntasks: 4

# 2. 定义提交计划 (Plans)
workload_plans:
  light_background:               # Plan 名称
    description: "轻量级背景负载"
    jobs:
      - job_name: "job_1"
        program: "cg_B"           # 引用上面的程序
        submit_delay: 0           # 实验开始立即提交
      - job_name: "job_2"
        program: "ep_B"
        submit_delay: 10          # 第 10 秒提交
```

### 2.4 监控指标 (`metrics.yaml`)
定义 Prometheus 查询语句。

```yaml
cpu:
  cpu_usage_percent:
    description: "CPU总使用率"
    query: '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)'
```

## 3. 如何配置一个新的实验 (Step-by-Step)

假设我们要创建一个名为 `network_latency_test` 的实验，目的是在运行 `ft.C` 作业时注入 500ms 的网络延迟。

### 第一步：定义故障 (在 `chaos.yaml`)
检查 `fault_types` 是否已有网络延迟类型。如果没有，添加它：
```yaml
fault_types:
  network_delay:
    type: "network"
    subtype: "delay"
    default_params:
      time: 500
      interface: "eth0"
```
然后定义一个 Plan：
```yaml
chaos_plans:
  my_network_delay_plan:
    faults:
      - fault_type: "network_delay"
        inject_delay: 30  # 作业运行 30s 后注入
```

### 第二步：定义负载 (在 `workloads.yaml`)
定义一个 Plan：
```yaml
workload_plans:
  my_ft_workload:
    jobs:
      - job_name: "target_job"
        program: "ft_C"
        submit_delay: 0
```

### 第三步：组合实验 (在 `experiments.yaml`)
```yaml
experiments:
  - name: "network_latency_test"
    output_dir: "data/network_latency_test"
    # ... 其他配置保持默认 ...
    experiment_plan:
      workload_plan: "my_ft_workload"
      chaos_plan: "my_network_delay_plan"
      duration:
        preparation: 10
        recovery: 60
        cleanup: 10
```

### 第四步：运行
```bash
# 确保在 dataset_builder 目录下
cd dataset_builder
python run.py --experiment network_latency_test
```

### 4.1 关键警告 (Critical Warnings)

> **⚠️ 必须修改配置**: 本项目包含部分硬编码路径，运行前请务必修改：
> 1. `config/workloads.yaml`: 修改 `base_path` 指向您环境中的 NPB bin 目录。
> 2. `config/chaos.yaml`: 修改 `interface` (默认为 `ens33`) 为您环境的真实网卡名称。

> **⚠️ 会话保护**: 故障注入实验（特别是网络丢包、高 CPU 负载）极易导致 SSH 连接不稳定或断开。
> **请务必使用 `tmux` 或 `screen` 运行 dataset_builder！**
> 否则一旦 SSH 断开，脚本将被系统杀死，导致数据采集失败且故障可能残留。

## 5. 常见问题与调试

### 5.1 故障注入失败
*   **现象**: `chaos.log` 显示 `blade create failed`。
*   **原因**: 通常是权限问题或参数错误。
*   **解决**:
    *   确保运行脚本的用户有权执行 `blade` (可能需要 sudo)。
    *   手动在终端运行构建出的 blade 命令，查看具体报错。

### 5.2 作业未提交
*   **现象**: `workload.log` 显示 `Job submission failed`。
*   **原因**: Slurm 队列满、Partition 名称错误或 NPB 可执行文件路径错误。
*   **解决**:
    *   检查 `workloads.yaml` 中的 `base_path` 是否正确指向 NPB 编译目录。
    *   使用 `sinfo` 检查 Partition 状态。

### 5.3 监控数据为空
*   **现象**: 生成的 `metrics.csv` 只有表头。
*   **原因**: Prometheus 连接失败或查询语句在该环境下无数据。
*   **解决**:
    *   检查 `metrics.yaml` 中的 `prometheus_url`。
    *   在 Prometheus Web UI 中手动执行 `query` 看看是否有数据。
