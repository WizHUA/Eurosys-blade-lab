#!/bin/bash

#SBATCH --job-name=npb_experiment
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:05:00
#SBATCH --output=/opt/exp/logs/npb_%j.out
#SBATCH --error=/opt/exp/logs/npb_%j.err

# 确保日志目录存在
mkdir -p /opt/exp/logs

# 获取参数
NPB_PROGRAM=${1:-"cg.B.x"}
ITERATIONS=6  # 写死运行10次
NPB_DIR="/home/quantum/Eurosys-blade-lab/npb/NPB3.4.2/NPB3.4-MPI/bin"

echo "开始执行NPB程序: $NPB_PROGRAM"
echo "作业ID: $SLURM_JOB_ID"
echo "节点列表: $SLURM_JOB_NODELIST"
echo "开始时间: $(date)"

# 检查程序是否存在
if [[ ! -f "$NPB_DIR/$NPB_PROGRAM" ]]; then
    echo "错误: NPB程序 $NPB_PROGRAM 不存在于 $NPB_DIR"
    echo "可用程序:"
    ls -la "$NPB_DIR" || echo "NPB目录不存在"
    exit 1
fi

# 设置MPI环境变量以允许root运行（仅测试环境）
export OMPI_ALLOW_RUN_AS_ROOT=1
export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1

# 执行NPB程序
cd "$NPB_DIR"
echo "使用 $SLURM_NTASKS 个任务运行 $NPB_PROGRAM"

for ((i=1; i<=ITERATIONS; i++)); do
    echo "=== 第 $i/$ITERATIONS 次运行 $(date) ==="
    
    if command -v mpirun >/dev/null 2>&1; then
        mpirun --allow-run-as-root -np $SLURM_NTASKS ./$NPB_PROGRAM
        exit_code=$?
    else
        ./$NPB_PROGRAM
        exit_code=$?
    fi
    
    echo "第 $i 次运行完成，退出码: $exit_code"
    
    # 如果不是最后一次运行，添加短暂延迟
    if [ $i -lt $ITERATIONS ]; then
        echo "等待3秒后开始下一次运行..."
        sleep 3
    fi
done

echo "结束时间: $(date)"
echo "NPB程序执行完成"