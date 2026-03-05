# HPC 异常诊断实验复现检查清单 (Reproducibility Checklist)

这份清单旨在帮助复现者在开始实验前核对环境与配置，确保实验能够顺利进行。

## 1. 基础环境核对 (Infrastructure)

- [ ] **操作系统**: Linux (推荐 Ubuntu 20.04/22.04 或 CentOS 7/8)，Windows (仅限开发/查看代码，实际运行需 HPC 环境)。
- [ ] **作业调度系统**: Slurm Workload Manager 已安装并运行。
    - [ ] 运行 `sinfo` 能看到分区状态。
    - [ ] 运行 `squeue` 能查看作业队列。
- [ ] **监控系统**: Prometheus + Node Exporter 已部署。
    - [ ] Node Exporter 在所有计算节点上运行。
    - [ ] Prometheus Server 可通过 HTTP 访问。
    - [ ] 验证 `dataset_builder/config/metrics.yaml` 中的 `prometheus_url` 是否可达。
- [ ] **故障注入工具**: ChaosBlade 已安装。
    - [ ] `blade` 命令在路径中可用 (或使用绝对路径)。
    - [ ] 验证是否有权限执行故障注入 (通常需要 sudo 或 root 权限)。

## 2. 软件依赖 (Software Dependencies)

- [ ] **Python 环境**: Python 3.8+
- [ ] **Python 包**: 已安装 `requirements.txt` 中的依赖。
    ```bash
    pip install -r requirements.txt
    ```
- [ ] **MPI 环境**: OpenMPI 或 MPICH 已安装，用于运行 NPB。
    ```bash
    mpirun --version
    ```
- [ ] **基准测试程序 (NPB)**:
    - [ ] NPB 3.4.2 (MPI版本) 已下载并解压。
    - [ ] 已修改 `config/make.def` 适配当前环境。
    - [ ] 已编译生成所需的可执行文件 (如 `bt.C.x`, `cg.B.x` 等)。
    - [ ] **⚠️ 关键配置 (MANDATORY)**: 务必修改 `dataset_builder/config/workloads.yaml` 中的 `base_path`，使其指向您环境中真实的 NPB `bin` 目录。
    - [ ] **⚠️ 关键配置 (MANDATORY)**: 务必修改 `dataset_builder/config/chaos.yaml` 中的 `interface`，使其匹配您环境中的真实网卡名称 (如 `eth0`)。

## 3. 配置检查 (Configuration Check)

### Dataset Builder
- [ ] **实验定义 (`config/experiments.yaml`)**:
    - [ ] 确认 `output_dir` 路径有效（建议使用相对路径）。
    - [ ] 确认引用的 `workload_plan` 和 `chaos_plan` 在对应文件中存在。
- [ ] **监控配置 (`config/metrics.yaml`)**:
    - [ ] 确认 Prometheus 查询语句 (PromQL) 适配当前监控指标名称 (不同 Exporter 版本可能略有不同)。
- [ ] **故障配置 (`config/chaos.yaml`)**:
    - [ ] 确认目标节点 (`target_node`) 名称与 `sinfo` 输出一致。

### LLM Diagnosis
- [ ] **API 密钥**:
    - [ ] `.env` 文件已创建。
    - [ ] `OPENROUTER_API_KEY` (或其他对应 Key) 已正确填入。
- [ ] **模型选择**:
    - [ ] 确认 `llm/src/run.py` 中选择的模型 (`model="..."`) 是您期望使用的模型。

## 4. 运行流程核对 (Execution Workflow)

### 阶段一：数据构建
- [ ] 进入目录: `cd dataset_builder`
- [ ] **⚠️ 会话保护 (Session Protection)**: 强烈建议使用 `tmux` 或 `screen` 运行脚本。故障注入（尤其是网络故障或高负载）可能导致 SSH 连接断开，若无会话保护，脚本将被终止，导致数据丢失。
- [ ] 运行命令: `python run.py --experiment <experiment_name>`
- [ ] **验证**:
    - [ ] 检查 `logs/workload.log`: 确认作业 ID 生成。
    - [ ] 检查 `logs/chaos.log`: 确认故障注入成功 (UID 生成)。
    - [ ] 检查 `data/<experiment_name>/`: 确认 `metrics.csv` 和 `jobinfo.csv` 生成且非空。

### 阶段二：智能诊断
- [ ] 进入目录: `cd llm`
- [ ] 运行命令: `python run.py --experiment <experiment_name>`
- [ ] **验证**:
    - [ ] 控制台输出 LLM 的 JSON 响应。
    - [ ] 检查 `llm/data/<experiment_name>/diagnosis_report.json` 是否生成。

## 5. 常见复现障碍 (Troubleshooting)

- **路径未修改 (Top Error)**: 90% 的失败源于 `workloads.yaml` 中的 `base_path` 仍指向作者的绝对路径。请务必修改！
- **网卡名错误**: 若 `chaos.log` 报错 `device not found`，请检查 `chaos.yaml` 中的 `interface` 参数。
- **运行节点错误**: 若 `metrics.csv` 数据无波动但 `chaos.log` 显示注入成功，说明您可能在登录节点运行了脚本，导致故障注入到了登录节点而非计算节点。
- **权限不足**: ChaosBlade 需要高权限，Slurm 提交作业需要普通用户权限。请注意用户切换。
- **网络隔离**: 如果运行脚本的节点无法访问 Prometheus 端口，数据采集将失败。
