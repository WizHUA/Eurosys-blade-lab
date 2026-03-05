# 单机虚拟机复现配置指南 (Single-Node VM Guide)

本指南旨在帮助没有 HPC 集群资源的复现者，在单台虚拟机 (VM) 上构建一个"迷你 HPC 环境"来运行本实验。

## 1. 硬件配置推荐 (Hardware Recommendations)

由于实验涉及 MPI 并行计算 (NPB) 和故障注入，建议配置如下：

| 组件 | 最低配置 (Minimum) | **推荐配置 (Recommended)** | 说明 |
| :--- | :--- | :--- | :--- |
| **CPU** | 4 vCPUs | **8 vCPUs** | NPB 作业通常配置为 4 任务并行 (`ntasks: 4`)。8核可确保在注入CPU故障时系统仍有响应。 |

> **CPU 拓扑设置小贴士 (VMware/VirtualBox)**:
> 为了获得最佳性能，建议配置为：
> *   **处理器数量 (Processors)**: 2
> *   **每个处理器的内核数量 (Cores per processor)**: 4
> *   **总核心数 (Total cores)**: 8
> 
> 理由：模拟双路 CPU (2 Sockets) 更贴近真实 HPC 节点的 NUMA 架构，虽然在 VM 里可能只是逻辑上的，但对 OS 调度器更友好。如果宿主机核心不够，**1 处理器 x 8 内核** 也是完全可以接受的，重点是总核心数要够。

| **内存 (RAM)** | 8 GB | **16 GB** | NPB Class C 负载较大；Prometheus 和 OS 也需要内存。16GB 更稳健。 |
| **磁盘 (Disk)** | 40 GB | **60 GB+** | 需预留空间给 Prometheus 时序数据、日志和故障注入产生的临时文件。 |
| **网络** | NAT 模式 | **桥接 (Bridged)** | 桥接模式方便宿主机访问 VM 里的 Prometheus Dashboard，也可使用 NAT + 端口转发。 |

**操作系统**: 推荐 **Ubuntu 20.04 LTS** 或 **22.04 LTS**。
> **验证通过版本**: `ubuntu-22.04.5-live-server-amd64.iso` 是非常完美的选择，完全兼容本实验的所有依赖。

### 1.1 操作系统版本建议：Server vs Desktop

**我不推荐使用 Desktop (桌面) 版本**，建议安装 **Server (服务器/无界面) 版本**。理由如下：

1.  **资源开销**: Ubuntu Desktop (GNOME) 即使空闲也会占用 1.5GB+ 内存和一定的 CPU 资源。而在我们的实验中，NPB 基准测试是计算密集型的，每一分 CPU 算力都应该留给实验本身，以获得更纯净、更稳定的性能数据。
2.  **真实性**: 真实的 HPC 计算节点通常都没有图形界面，使用 Server 版能获得更贴近生产环境的体验。
3.  **如何查看图表**: 虽然 VM 里没有浏览器，但你可以通过**端口转发**或**桥接网络**，直接在宿主机 (你的 Windows/Mac) 的浏览器中访问 VM 的 `http://<vm-ip>:9090` 来查看 Prometheus 图表。这比在 VM 里卡顿地操作浏览器体验更好。

---

## 2. 单机环境搭建速查 (Quick Setup)

在单机上模拟 HPC 环境的核心在于配置 Slurm 单节点模式。

### 2.1 安装基础软件

```bash
sudo apt update
sudo apt install -y build-essential python3-pip git openmpi-bin libopenmpi-dev tmux
```

### 2.2 安装与配置 Slurm (单节点模式)

这是最关键的一步。我们需要在同一台机器上运行控制节点 (slurmctld) 和计算节点 (slurmd)。

1.  **安装 Slurm**:
    ```bash
    sudo apt install -y slurm-wlm
    ```

2.  **获取主机名**:
    ```bash
    hostname
    # 记下输出的名字，假设为 "vm-ubuntu"
    ```

3.  **生成配置文件 (`/etc/slurm/slurm.conf`)**:
    使用以下精简配置（请将 `vm-ubuntu` 替换为您的实际主机名）：

    ```conf
    # /etc/slurm/slurm.conf
    ClusterName=local-cluster
    SlurmctldHost=vm-ubuntu
    MpiDefault=none
    ProctrackType=proctrack/pgid
    ReturnToService=1
    SlurmctldPidFile=/var/run/slurmctld.pid
    SlurmdPidFile=/var/run/slurmd.pid
    SlurmdSpoolDir=/var/lib/slurm/slurmd
    SlurmUser=slurm
    StateSaveLocation=/var/lib/slurm/slurmctld
    SwitchType=switch/none
    TaskPlugin=task/none

    # 节点定义 (CPUs=实际核数, RealMemory=实际内存MB)
    NodeName=vm-ubuntu CPUs=8 RealMemory=15000 State=UNKNOWN

    # 分区定义
    PartitionName=CPU Nodes=vm-ubuntu Default=YES MaxTime=INFINITE State=UP
    ```

4.  **启动服务**:
    ```bash
    sudo service munge start
    sudo service slurmctld start
    sudo service slurmd start
    ```

5.  **验证**:
    ```bash
    sinfo
    # 应输出: CPU*  up  infinite  1  idle  vm-ubuntu
    ```

### 2.3 部署监控 (Prometheus + Node Exporter)

1.  **Node Exporter**: 下载二进制文件并后台运行。
    ```bash
    wget https://github.com/prometheus/node_exporter/releases/download/v1.5.0/node_exporter-1.5.0.linux-amd64.tar.gz
    tar xvfz node_exporter-*.tar.gz
    cd node_exporter-*
    ./node_exporter &
    ```

2.  **Prometheus**:
    修改 `prometheus.yml`，添加 localhost 任务：
    ```yaml
    scrape_configs:
      - job_name: 'hpc_node'
        static_configs:
          - targets: ['localhost:9100']
    ```
    启动 Prometheus:
    ```bash
    ./prometheus --config.file=prometheus.yml &
    ```

### 2.4 安装 ChaosBlade

```bash
wget https://github.com/chaosblade-io/chaosblade/releases/download/v1.7.0/chaosblade-1.7.0-linux-amd64.tar.gz
tar xvfz chaosblade-*.tar.gz
# 将 blade 添加到 PATH
export PATH=$PATH:$(pwd)/chaosblade-1.7.0-linux-amd64
```

---

## 3. 实验配置修改建议

针对单机 VM 环境，请对 `dataset_builder` 的配置做以下微调：

1.  **`dataset_builder/config/workloads.yaml`**:
    *   确保 `npb_config.base_path` 指向您编译好的 NPB bin 目录。
    *   如果您的 VM 只有 4 核，建议将 `ntasks` 从 4 改为 2，避免过载。

2.  **`dataset_builder/config/chaos.yaml`**:
    *   修改 `interface` 为您的 VM 网卡名 (通常是 `ens33`, `eth0` 或 `enp0s3`)。
    *   **重要**: 网络丢包实验 (`network_loss`) 在单机上会导致 SSH 卡顿。

3.  **`dataset_builder/config/experiments.yaml`**:
    *   可以减少 `duration` (如 preparation/recovery 设为 10s) 以加快测试速度。

## 4. 运行注意事项

*   **⚠️ 必须使用 tmux/screen**: 单机注入网络故障非常危险，一旦 SSH 断开，实验就会中断且无法自动恢复环境。务必在 tmux 会话中运行 `python run.py`。
*   **资源监控**: 建议开一个终端运行 `htop`，实时观察 CPU 和内存压力。
