# Prerequisites

本实验项目依赖以下外部系统和工具。在运行代码之前，请确保您的 HPC 环境已满足这些要求。

## 1. 作业调度系统
*   **软件**: [Slurm Workload Manager](https://slurm.schedmd.com/)
*   **版本要求**: 推荐 20.02+
*   **验证**: 运行 `sinfo` 和 `squeue` 应能正常输出集群状态。
*   **配置**: 
    *   需要至少一个名为 `CPU` 的 partition (或在 `dataset_builder/config/workloads.yaml` 中修改配置)。
    *   确保当前用户有权限提交作业。
*   **💡 单机复现**: 如果您没有 HPC 集群，请参考 [docs/single_node_vm_guide.md](docs/single_node_vm_guide.md) 在单台虚拟机上搭建模拟环境。

## 2. 监控系统
*   **软件**: [Prometheus](https://prometheus.io/)
*   **版本要求**: 2.0+
*   **验证**: 访问 `http://<prometheus_host>:9090` 应能看到 Web UI。
*   **配置**:
    *   **Node Exporter**: 集群的所有计算节点必须安装并运行 `node_exporter`。
    *   Prometheus 必须配置为抓取这些 `node_exporter` 的 metrics。
    *   在 `dataset_builder/config/metrics.yaml` 中配置 Prometheus 的 URL。

## 3. 故障注入工具
*   **软件**: [ChaosBlade](https://github.com/chaosblade-io/chaosblade)
*   **版本要求**: 1.7.0+
*   **验证**: 运行 `blade version` 应输出版本信息。
*   **配置**:
    *   `blade` 命令必须在系统的 PATH 中。
    *   对于涉及网络或系统级故障（如丢包、磁盘I/O），可能需要 `sudo` 权限或相应的 Capabilities。

## 4. 基准测试套件
*   **软件**: [NAS Parallel Benchmarks (NPB)](https://www.nas.nasa.gov/software/npb.html)
*   **版本要求**: 3.4.2 (MPI 版本)
*   **验证**: 编译后的二进制文件（如 `cg.B.x`, `ep.C.x` 等）必须存在。
*   **配置**:
    *   编译好的可执行文件应放置在 `npb/NPB3.4.2/NPB3.4-MPI/bin` 目录下，或者在 `dataset_builder/config/workloads.yaml` 中修改路径。
    *   需要 MPI 运行环境 (如 OpenMPI 或 MPICH)。

## 5. Python 环境
*   **版本**: Python 3.8+
*   **依赖**: 见 `requirements.txt`
