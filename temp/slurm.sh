set -e

HOSTNAME=$(hostname -s)
CPU_COUNT=$(nproc)
# 预留 512MB 给系统，防止 Slurm OOM kill 自身
MEM_MB=$(( $(free -m | awk '/^Mem:/{print $2}') - 512 ))

echo "[INFO] 将写入以下节点参数:"
echo "  hostname  = ${HOSTNAME}"
echo "  CPUs      = ${CPU_COUNT}"
echo "  RealMemory= ${MEM_MB} MB"

# 创建必要目录并设置权限
sudo mkdir -p \
  /var/log/slurm \
  /var/lib/slurm/slurmctld \
  /var/spool/slurmd
sudo chown slurm:slurm \
  /var/log/slurm \
  /var/lib/slurm/slurmctld \
  /var/spool/slurmd

sudo tee /etc/slurm/slurm.conf > /dev/null <<EOF
# ============================================
# Slurm 单机开发配置
# 由 HPC-FDS Architect 生成 — $(date)
# 用途: Eurosys-blade-lab 单机实验环境
# ============================================

ClusterName=blade-lab-dev
SlurmctldHost=${HOSTNAME}

# --- 认证 (Munge) ---
AuthType=auth/munge
CryptoType=crypto/munge

# --- 进程追踪 ---
# [重要] proctrack/cgroup 是 ChaosBlade 按 cgroup
# 隔离故障注入目标进程的前提条件
ProctrackType=proctrack/cgroup
TaskPlugin=task/cgroup

# --- 日志 ---
SlurmctldLogFile=/var/log/slurm/slurmctld.log
SlurmdLogFile=/var/log/slurm/slurmd.log
SlurmctldPidFile=/var/run/slurmctld.pid
SlurmdPidFile=/var/run/slurmd.pid
SlurmctldDebug=info
SlurmdDebug=info

# --- 状态持久化 ---
StateSaveLocation=/var/lib/slurm/slurmctld
SlurmdSpoolDir=/var/spool/slurmd

# --- 调度策略 ---
SchedulerType=sched/backfill
SelectType=select/cons_tres
SelectTypeParameters=CR_Core_Memory

# --- 节点定义 ---
NodeName=${HOSTNAME} CPUs=${CPU_COUNT} RealMemory=${MEM_MB} State=UNKNOWN

# --- 分区定义 ---
# OverSubscribe=YES 允许单机上多个测试作业共享资源
PartitionName=debug Nodes=${HOSTNAME} Default=YES MaxTime=INFINITE State=UP OverSubscribe=YES
EOF

echo "[OK] /etc/slurm/slurm.conf 已写入"