#!/bin/bash

# 临时改为调试模式
set -ex

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data/raw"
LOGS_DIR="$SCRIPT_DIR/logs"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 创建必要目录
mkdir -p "$DATA_DIR" "$LOGS_DIR"

log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# 故障配置 - 8个单一故障 + 6个复合故障 = 14个实验
declare -A FAULT_CONFIGS
FAULT_CONFIGS=(
    # 单一故障
    ["cpu_fullload"]="cpu fullload --cpu-percent 95 --timeout 60"
    ["cpu_burn"]="cpu burn --cpu-percent 80 --timeout 60"
    ["mem_load"]="mem load --mode ram --mem-percent 80 --timeout 60"
    ["mem_burn"]="mem burn --mem-percent 70 --timeout 60"
    ["disk_burn_read"]="disk burn --read --path /tmp --size 2048 --timeout 60"
    ["disk_burn_write"]="disk burn --write --path /tmp --size 2048 --timeout 60"
    ["network_delay"]="network delay --time 500 --interface ens33 --timeout 60"
    ["network_loss"]="network loss --percent 30 --interface ens33 --timeout 60"
    
    # 复合故障（两个异常同时注入）
    ["cpu_mem"]="\"cpu fullload --cpu-percent 70 --timeout 60\" \"mem load --mode ram --mem-percent 60 --timeout 60\""
    ["cpu_disk"]="\"cpu fullload --cpu-percent 60 --timeout 60\" \"disk burn --read --write --path /tmp --size 1024 --timeout 60\""
    ["cpu_network"]="\"cpu fullload --cpu-percent 60 --timeout 60\" \"network delay --time 300 --interface ens33 --timeout 60\""
    ["mem_disk"]="\"mem load --mode ram --mem-percent 60 --timeout 60\" \"disk burn --write --path /tmp --size 1024 --timeout 60\""
    ["mem_network"]="\"mem load --mode ram --mem-percent 60 --timeout 60\" \"network loss --percent 20 --interface ens33 --timeout 60\""
    ["disk_network"]="\"disk burn --read --path /tmp --size 1024 --timeout 60\" \"network delay --time 200 --interface ens33 --timeout 60\""
)

# NPB程序列表
NPB_PROGRAMS=(
    "cg.B.x"    # Conjugate Gradient
    "ep.B.x"    # Embarrassingly Parallel  
    "is.B.x"    # Integer Sort
    "ft.B.x"    # Fast Fourier Transform
)

cleanup() {
    log "执行清理操作..."
    
    # 停止所有ChaosBlade实验
    blade list 2>/dev/null | grep -E "^[a-f0-9-]+.*success" | awk '{print $1}' | while read uid; do
        if [[ ! -z "$uid" ]]; then
            blade destroy "$uid" 2>/dev/null || true
        fi
    done
    
    # 取消所有运行中的Slurm作业
    squeue --noheader --format="%i" --user="$(whoami)" 2>/dev/null | while read job_id; do
        if [[ ! -z "$job_id" ]]; then
            scancel "$job_id" 2>/dev/null || true
        fi
    done
    
    # 停止数据采集进程
    pkill -f "collect_metrics.py" 2>/dev/null || true
    
    log "清理完成"
}

# 简化的测试函数 - 先测试第一个实验
test_first_experiment() {
    log "🧪 测试第一个实验: cpu_fullload"
    
    local fault_name="cpu_fullload"
    local fault_config="${FAULT_CONFIGS[$fault_name]}"
    
    echo "DEBUG: fault_name = $fault_name"
    echo "DEBUG: fault_config = $fault_config"
    
    if [[ -z "$fault_config" ]]; then
        error "找不到故障配置: $fault_name"
    fi
    
    log "开始执行测试实验..."
    
    # 检查数据采集脚本是否存在
    if [[ ! -f "$SCRIPT_DIR/scripts/collect_metrics.py" ]]; then
        error "数据采集脚本不存在: $SCRIPT_DIR/scripts/collect_metrics.py"
    fi
    
    # 检查作业脚本是否存在
    if [[ ! -f "$SCRIPT_DIR/jobs/npb_job.sbatch" ]]; then
        error "作业脚本不存在: $SCRIPT_DIR/jobs/npb_job.sbatch"
    fi
    
    local experiment_id="${fault_name}_test_$(date +%Y%m%d_%H%M%S)"
    local raw_data_file="$DATA_DIR/${experiment_id}.csv"
    
    log "测试数据采集脚本启动..."
    
    # 测试启动数据采集
    python3 "$SCRIPT_DIR/scripts/collect_metrics.py" \
        --output "$raw_data_file" \
        --label "$fault_name" \
        --interval 10 &
    local collector_pid=$!
    
    echo "DEBUG: collector_pid = $collector_pid"
    
    sleep 3
    
    if ! kill -0 $collector_pid 2>/dev/null; then
        error "数据采集器启动失败"
    fi
    
    log "数据采集器启动成功，运行10秒测试..."
    sleep 10
    
    # 停止采集器
    kill $collector_pid 2>/dev/null || true
    wait $collector_pid 2>/dev/null || true
    
    if [[ -f "$raw_data_file" ]]; then
        local record_count=$(wc -l < "$raw_data_file" 2>/dev/null || echo 0)
        log "测试成功！采集了 $((record_count - 1)) 条数据记录"
        head -3 "$raw_data_file"
    else
        error "测试失败！没有生成数据文件"
    fi
}

# 构建完整数据集
build_complete_dataset() {
    log "🚀 开始构建HPC异常检测完整数据集"
    
    # 先测试第一个实验
    echo "DEBUG: 开始测试第一个实验..."
    test_first_experiment
    
    log "测试通过，开始正式实验..."
    
    # 明确定义实验列表顺序 - 先只做前3个测试
    local experiment_list=(
        "cpu_fullload"
        "cpu_burn" 
        "mem_load"
    )
    
    local total_experiments=${#experiment_list[@]}
    local completed_experiments=0
    local failed_experiments=0
    
    # 创建实验总结文件
    local summary_file="$DATA_DIR/experiment_summary.csv"
    echo "ExperimentID,FaultType,Status,DataRecords,StartTime,EndTime" > "$summary_file"
    
    log "计划执行 $total_experiments 个故障注入实验（测试模式）"
    info "预计总时长: $((total_experiments * 3)) 分钟"
    
    # 执行所有实验
    for fault_name in "${experiment_list[@]}"; do
        ((completed_experiments++))
        local start_time=$(date '+%Y-%m-%d %H:%M:%S')
        info "进度: $completed_experiments/$total_experiments - 执行 $fault_name"
        
        # 获取故障配置
        local fault_config="${FAULT_CONFIGS[$fault_name]}"
        if [[ -z "$fault_config" ]]; then
            ((failed_experiments++))
            warn "❌ 未找到故障配置: $fault_name"
            continue
        fi
        
        echo "DEBUG: 准备运行实验 $fault_name，配置: $fault_config"
        
        # 为了调试，先只运行简化版本
        local experiment_id="${fault_name}_$(date +%Y%m%d_%H%M%S)"
        local raw_data_file="$DATA_DIR/${experiment_id}.csv"
        
        log "开始简化实验: $experiment_id"
        
        # 启动数据采集（简化版）
        python3 "$SCRIPT_DIR/scripts/collect_metrics.py" \
            --output "$raw_data_file" \
            --label "$fault_name" \
            --interval 10 &
        local collector_pid=$!
        
        sleep 3
        
        if ! kill -0 $collector_pid 2>/dev/null; then
            ((failed_experiments++))
            warn "❌ 数据采集器启动失败: $fault_name"
            continue
        fi
        
        # 简化的实验流程：10秒基线 + 20秒故障 + 10秒恢复
        log "基线期（10秒）..."
        sleep 10
        
        log "故障注入..."
        # 注入故障（简化）
        if [[ "$fault_config" == *\"* ]]; then
            warn "跳过复合故障（调试模式）: $fault_name"
        else
            blade create $fault_config || warn "故障注入失败: $fault_name"
        fi
        
        log "故障期（20秒）..."
        sleep 20
        
        log "恢复期（10秒）..."
        # 清理故障
        blade list 2>/dev/null | grep -E "^[a-f0-9-]+.*success" | awk '{print $1}' | while read uid; do
            if [[ ! -z "$uid" ]]; then
                blade destroy "$uid" 2>/dev/null || true
            fi
        done
        
        sleep 10
        
        # 停止数据采集
        kill $collector_pid 2>/dev/null || true
        wait $collector_pid 2>/dev/null || true
        
        local end_time=$(date '+%Y-%m-%d %H:%M:%S')
        
        # 检查结果
        if [[ -f "$raw_data_file" ]]; then
            local data_records=$(wc -l < "$raw_data_file" 2>/dev/null || echo 0)
            data_records=$((data_records - 1))  # 减去标题行
            
            echo "${fault_name},cpu,SUCCESS,$data_records,$start_time,$end_time" >> "$summary_file"
            info "✅ $fault_name 实验成功完成 ($data_records 条记录)"
        else
            ((failed_experiments++))
            echo "${fault_name},unknown,FAILED,0,$start_time,$end_time" >> "$summary_file"
            warn "❌ $fault_name 实验失败"
        fi
        
        sleep 5  # 实验间隔
    done
    
    log "🎉 测试模式数据集构建完成！"
    info "成功实验: $((total_experiments - failed_experiments))/$total_experiments"
    info "失败实验: $failed_experiments"
    info "数据文件位置: $DATA_DIR"
}

# 主函数
main() {
    log "🔧 检查环境依赖..."
    
    # 临时禁用set -e进行环境检查
    set +e
    
    # 检查依赖
    if ! command -v blade >/dev/null 2>&1; then
        error "ChaosBlade未安装或不在PATH中"
    fi
    
    if ! command -v sbatch >/dev/null 2>&1; then
        error "Slurm未安装或不可用"
    fi
    
    if ! command -v python3 >/dev/null 2>&1; then
        error "Python3未安装"
    fi
    
    # 测试Prometheus连接
    if ! curl -s "http://localhost:9090/api/v1/query?query=up" >/dev/null 2>&1; then
        error "无法连接到Prometheus (http://localhost:9090)"
    fi
    
    # 检查关键脚本文件
    if [[ ! -f "$SCRIPT_DIR/scripts/collect_metrics.py" ]]; then
        error "数据采集脚本不存在: $SCRIPT_DIR/scripts/collect_metrics.py"
    fi
    
    # 重新启用调试模式
    set -ex
    
    info "✅ 环境检查通过"
    
    # 注册退出处理器（在环境检查完成后）
    trap cleanup EXIT INT TERM
    
    # 开始构建完整数据集
    build_complete_dataset
}

# 参数处理
case "${1:-}" in
    "test-simple")
        trap cleanup EXIT INT TERM
        test_first_experiment
        ;;
    *)
        main
        ;;
esac