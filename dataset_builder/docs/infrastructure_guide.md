# HPC 实验基础设施指南：Prometheus 与 ChaosBlade

在 `dataset_builder` 中，我们使用 **Prometheus** 进行全方位系统监控，使用 **ChaosBlade** 进行可控的故障注入。本文档详细阐述这两个核心组件在本项目中的配置与使用方式，帮助理解实验数据的来源和故障模拟的原理。

---

## 1. Prometheus 监控系统

Prometheus 是一个开源的系统监控和报警工具包。在本项目中，我们主要利用它来采集 HPC 计算节点在实验过程中的各项性能指标（CPU、内存、磁盘 I/O、网络等）。

### 1.1 架构角色
*   **Prometheus Server**: 负责定期从 Target（目标节点）抓取数据。
*   **Node Exporter**: 部署在每个计算节点上，负责收集硬件和操作系统层面的指标，并通过 HTTP 接口暴露给 Server。
*   **dataset_builder/monitor.py**: 本项目的监控组件，它扮演 Prometheus 的客户端，通过 PromQL（查询语言）从 Server 获取特定时间段的数据。

### 1.2 关键配置 (`config/metrics.yaml`)

`metrics.yaml` 定义了我们需要采集哪些指标。每个指标都对应一条 PromQL 查询语句。

```yaml
# 示例配置解析
cpu:
  cpu_usage_percent:
    description: "CPU总使用率"
    # PromQL: 计算所有非 idle 模式的 CPU 时间占比
    query: '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)'
```

*   **query**: 这是核心。可以在 Prometheus 的 Web UI (通常是 `http://<server-ip>:9090/graph`) 中测试这些查询语句，确保它们能正确返回数据。
*   **常见调试**: 如果 `metrics.csv` 为空，通常是因为 PromQL 中的 Label（如 `{mode="idle"}`）与实际环境中的 Exporter 版本不匹配。请先在 Web UI 中验证。

### 1.3 常用 PromQL 速查
在 HPC 诊断中，我们重点关注以下指标：

| 指标类别 | PromQL 示例 | 含义 |
| :--- | :--- | :--- |
| **CPU** | `100 - (avg by (instance) (irate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)` | 单节点 CPU 使用率 |
| **内存** | `(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100` | 内存使用率 |
| **网络丢包** | `rate(node_network_receive_drop_total[1m])` | 网络接收丢包率 |
| **磁盘写延迟** | `rate(node_disk_write_time_seconds_total[1m]) / rate(node_disk_writes_completed_total[1m])` | 平均磁盘写延迟 |

---

## 2. ChaosBlade 故障注入工具

ChaosBlade 是阿里巴巴开源的混沌工程工具，支持丰富的故障场景。我们使用它在计算作业运行期间注入干扰，模拟 HPC 集群中常见的软硬件故障。

### 2.1 工作原理
*   **dataset_builder/chaos.py**: 解析 `chaos.yaml`，将其转化为 `blade create ...` 命令。
*   **SSH/Local Exec**: 通过 Python 的 `subprocess` 或 SSH 在目标计算节点上执行 blade 命令。
*   **UID 管理**: 每次注入故障，ChaosBlade 会返回一个唯一的 UID。脚本会记录这个 UID，以便在实验结束（recovery 阶段）执行 `blade destroy <UID>` 撤销故障。

### 2.2 关键配置 (`config/chaos.yaml`)

配置文件分为两部分：**故障模板 (Types)** 和 **注入计划 (Plans)**。

#### 定义故障模板
```yaml
fault_types:
  network_delay_500ms:    # 自定义名称
    type: "network"       # 对应 blade 的一级命令
    subtype: "delay"      # 对应 blade 的二级命令
    default_params:       # 对应 blade 的参数 (flags)
      time: 500           # --time 500
      interface: "eth0"   # --interface eth0
      offset: 10          # --offset 10
```
> **注意**: `interface` 参数必须与计算节点的实际网卡名称一致（通过 `ip addr` 查看）。

#### 定义注入计划
```yaml
chaos_plans:
  test_network_glitch:
    faults:
      - fault_type: "network_delay_500ms"
        inject_delay: 30       # 实验开始第 30 秒注入
        target_node: "node01"  # 目标节点 (若为空则默认本机)
```

### 2.3 常用故障命令映射
下表展示了 YAML 配置如何映射到 ChaosBlade 命令行：

| 故障场景 | YAML 配置 (`type` / `subtype`) | 对应 Blade 命令 | 关键参数 |
| :--- | :--- | :--- | :--- |
| **CPU 满载** | `cpu` / `fullload` | `blade create cpu fullload` | `cpu-percent`: 占用率 |
| **内存泄漏** | `mem` / `load` | `blade create mem load` | `mem-percent`: 占用率, `mode`: ram/cache |
| **网络延迟** | `network` / `delay` | `blade create network delay` | `time`: 延迟(ms), `interface`: 网卡 |
| **网络丢包** | `network` / `loss` | `blade create network loss` | `percent`: 丢包率, `interface`: 网卡 |
| **磁盘填满** | `disk` / `fill` | `blade create disk fill` | `percent`: 填充率, `path`: 挂载点 |
| **IO 压力** | `disk` / `burn` | `blade create disk burn` | `read`/`write`: 读写模式 |

### 2.4 故障调试指南
如果实验中故障未生效，请按以下步骤排查：

1.  **检查日志**: 查看 `dataset_builder/logs/chaos.log`，寻找 `blade create` 的完整命令和报错信息。
2.  **手动验证**: 复制日志中的命令，登录到计算节点手动执行。
    *   *错误示例*: `response: {"code": 404, "error": "network device not found"}` -> 网卡名填错了。
    *   *错误示例*: `Permission denied` -> 需要 sudo 权限或 root 用户。
3.  **销毁残留**: 如果实验异常中断，故障可能未被清理。请手动运行 `blade status --type create` 查看活跃故障，并使用 `blade destroy <UID>` 清理。

---

## 3. 配合关系

在 `dataset_builder` 运行过程中，这两个组件是协同工作的：

1.  **T=0s**: 实验开始，Prometheus 持续采集基准数据（Baseline）。
2.  **T=X**: `ChaosManager` 根据计划调用 `blade create` 注入故障。
3.  **T=X~Y**: 系统处于故障状态，Prometheus 记录下指标的异常波动（如 CPU 突然飙升、网络吞吐下降）。
4.  **T=Y**: `ChaosManager` 调用 `blade destroy` 恢复环境。
5.  **数据导出**: `Monitor` 组件将 Prometheus 中的时序数据导出为 `metrics.csv`，作为 Phase 2 (LLM 诊断) 的输入。

理解这一过程，有助于在 Phase 2 中更好地解读 LLM 的诊断结果。
