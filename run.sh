#!/bin/bash

set -e

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

# 故障配置列表 - 高强度配置，缩短超时时间到60秒
declare -A FAULT_CONFIGS
FAULT_CONFIGS=(
    # 单一故障 - 高强度配置，60秒超时
    ["cpu_fullload"]="cpu fullload --cpu-percent 95 --timeout 60"  # 95% CPU负载
    ["mem_load_ram"]="mem load --mode ram --mem-percent 85 --timeout 60"  # 85% 内存占用
    ["mem_load_cache"]="mem load --mode cache --mem-percent 80 --timeout 60"  # 80% 缓存占用
    ["disk_burn_read"]="disk burn --read --path /tmp --size 2048 --timeout 60"  # 2GB读负载
    ["disk_burn_write"]="disk burn --write --path /tmp --size 2048 --timeout 60"  # 2GB写负载
    ["disk_burn_rw"]="disk burn --read --write --path /tmp --size 1024 --timeout 60"  # 同时读写
    ["disk_fill"]="disk fill --path /tmp --size 3000 --timeout 60"  # 3GB空间填充（降低以加快速度）
    ["network_delay"]="network delay --time 500 --interface ens33 --timeout 60"  # 500ms延迟
    ["network_loss"]="network loss --percent 30 --interface ens33 --timeout 60"  # 30% 丢包率
    ["network_corrupt"]="network corrupt --percent 15 --interface ens33 --timeout 60"  # 15% 包损坏
    
    # 复合故障 - 高强度组合，60秒超时
    ["cpu_mem_composite"]="composite 'cpu|fullload|--cpu-percent|80|--timeout|60' 'mem|load|--mode|ram|--mem-percent|70|--timeout|60'"
    ["cpu_disk_composite"]="composite 'cpu|fullload|--cpu-percent|70|--timeout|60' 'disk|burn|--read|--write|--timeout|60'"
    ["mem_disk_composite"]="composite 'mem|load|--mode|ram|--mem-percent|60|--timeout|60' 'disk|burn|--write|--size|1024|--timeout|60'"
    ["network_cpu_composite"]="composite 'network|delay|--time|300|--interface|ens33|--timeout|60' 'cpu|fullload|--cpu-percent|60|--timeout|60'"
)

# NPB程序列表 - 选择中等规模程序以确保在短时间内运行
NPB_PROGRAMS=(
    # 计算密集型程序 (B级别) - 运行时间适中
    "cg.B.x"    # Conjugate Gradient
    "ep.B.x"    # Embarrassingly Parallel
    "is.B.x"    # Integer Sort
    "ft.B.x"    # Fast Fourier Transform
    "bt.B.x"    # Block Tridiagonal
    "sp.B.x"    # Scalar Pentadiagonal  
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

# 注册退出处理器
trap cleanup EXIT INT TERM

# 批量提交NPB作业以确保整个实验期间都有负载
submit_background_workload() {
    local job_ids=()
    
    log "提交背景工作负载..."
    
    # 提交多个不同类型的NPB作业，设置较长的运行时间
    for program in "${NPB_PROGRAMS[@]:0:4}"; do  # 选择前4个程序
        if [[ -f "$SCRIPT_DIR/npb/NPB3.4.2/NPB3.4-MPI/bin/$program" ]]; then
            local job_id=$(sbatch --parsable --time=00:05:00 "$SCRIPT_DIR/jobs/npb_job.sbatch" "$program" 2>/dev/null)
            if [[ $? -eq 0 && ! -z "$job_id" ]]; then
                job_ids+=("$job_id")
                info "提交作业: $program (JobID: $job_id)"
            else
                warn "作业提交失败: $program"
            fi
            sleep 1  # 避免同时提交造成系统负载峰值
        else
            warn "NPB程序不存在: $program"
        fi
    done
    
    # 等待至少有一个作业开始运行
    if [[ ${#job_ids[@]} -gt 0 ]]; then
        log "等待作业开始运行..."
        local jobs_running=false
        local wait_timeout=90  # 90秒超时
        local wait_count=0
        
        while [[ "$jobs_running" == false && $wait_count -lt $wait_timeout ]]; do
            for job_id in "${job_ids[@]}"; do
                if squeue -j "$job_id" -h -o "%t" 2>/dev/null | grep -q "R"; then
                    jobs_running=true
                    break
                fi
            done
            
            if [[ "$jobs_running" == false ]]; then
                sleep 5
                ((wait_count+=5))
                info "等待作业启动... $wait_count/$wait_timeout 秒"
            fi
        done
        
        if [[ "$jobs_running" == true ]]; then
            log "背景工作负载已开始运行"
            # 显示当前运行的作业状态
            info "当前运行的作业:"
            squeue --user="$(whoami)" --format="%.8i %.9P %.20j %.8u %.2t %.10M %.6D %R" 2>/dev/null || true
        else
            warn "背景工作负载启动超时，但继续进行实验"
        fi
    else
        warn "没有成功提交任何背景作业"
    fi
    
    # 返回作业ID列表（用空格分隔的字符串）
    echo "${job_ids[*]}"
}

run_single_experiment() {
    local fault_name="$1"
    local fault_config="$2"
    local experiment_id="${fault_name}_$(date +%Y%m%d_%H%M%S)"
    local experiment_start_time=$(date +%Y%m%d_%H%M%S)
    
    log "开始实验: $experiment_id"
    info "故障类型: $fault_name"
    info "故障配置: $fault_config"
    
    # 数据文件路径
    local raw_data_file="$DATA_DIR/${experiment_id}.csv"
    local job_info_file="$DATA_DIR/${experiment_id}_job_info.csv"
    
    # 1. 提交背景工作负载
    local background_job_ids_str=$(submit_background_workload)
    IFS=' ' read -ra background_job_ids <<< "$background_job_ids_str"
    info "背景作业IDs: ${background_job_ids[*]}"
    
    # 2. 启动监控数据采集
    log "启动监控数据采集..."
    python3 "$SCRIPT_DIR/scripts/collect_metrics.py" \
        --output "$raw_data_file" \
        --label "$fault_name" \
        --interval 10 > /dev/null 2>&1 &  # 重定向输出避免干扰
    local collector_pid=$!
    info "数据采集进程PID: $collector_pid"
    
    # 等待采集器启动
    sleep 3
    
    # 检查采集器是否正常运行
    if ! kill -0 $collector_pid 2>/dev/null; then
        error "数据采集器启动失败"
    fi
    
    # 3. 基线期（30秒）
    log "=== 阶段1: 基线稳定期开始（30秒）==="
    info "此阶段数据标签: normal"
    sleep 30

    # 4. 注入故障
    log "=== 阶段2: 故障注入期开始（60秒）==="
    info "注入故障: $fault_config"
    info "此阶段数据标签: $fault_name"
    bash "$SCRIPT_DIR/scripts/inject_fault.sh" $fault_config
    
    # 监控故障注入是否成功
    if [[ $? -eq 0 ]]; then
        log "故障注入成功"
    else
        error "故障注入失败"
    fi

    # 5. 故障期（60秒，由--timeout控制）
    log "故障持续期（60秒）..."
    # 每15秒检查一次系统状态
    for i in {1..4}; do
        sleep 15
        info "故障期进度: $((i*15))/60 秒"
        # 显示当前系统负载
        uptime || true
    done
    
    # 6. 恢复期（30秒）
    log "=== 阶段3: 恢复期开始（30秒）==="
    info "此阶段数据标签: recovery"
    
    # 确保故障已经清除
    blade list 2>/dev/null | grep -E "^[a-f0-9-]+.*success" | awk '{print $1}' | while read uid; do
        if [[ ! -z "$uid" ]]; then
            blade destroy "$uid" 2>/dev/null || true
            log "清除故障实验: $uid"
        fi
    done
    
    # 恢复期监控
    for i in {1..2}; do
        sleep 15
        info "恢复期进度: $((i*15))/30 秒"
        uptime || true
    done
    
    # 7. 停止数据采集
    log "停止数据采集..."
    kill $collector_pid 2>/dev/null || true
    wait $collector_pid 2>/dev/null || true
    
    # 8. 清理
    log "清理实验环境..."
    
    # 取消背景作业
    for job_id in "${background_job_ids[@]}"; do
        if [[ ! -z "$job_id" && "$job_id" =~ ^[0-9]+$ ]]; then
            scancel "$job_id" 2>/dev/null || true
            info "取消作业: $job_id"
        fi
    done
    
    # 再次确保所有ChaosBlade实验都被清除
    blade list 2>/dev/null | grep -E "^[a-f0-9-]+.*success" | awk '{print $1}' | while read uid; do
        if [[ ! -z "$uid" ]]; then
            blade destroy "$uid" 2>/dev/null || true
        fi
    done

    # 9. 记录作业信息（实验结束后）
    local experiment_end_time=$(date +%Y%m%d_%H%M%S)
    log "记录实验期间的作业信息..."
    python3 "$SCRIPT_DIR/scripts/record_job_info.py" \
        --output "$job_info_file" \
        --experiment-id "$experiment_id" \
        --start-time "$experiment_start_time" \
        --end-time "$experiment_end_time" \
        --username "$(whoami)" \
        --debug
    
    log "实验 $experiment_id 完成"
    info "数据文件: $raw_data_file"
    
    # 显示数据文件统计
    if [[ -f "$raw_data_file" ]]; then
        local record_count=$(wc -l < "$raw_data_file" 2>/dev/null || echo 0)
        info "采集数据记录数: $((record_count - 1))"  # 减去标题行
    fi
    
    # 实验间隔（让系统完全恢复）- 缩短到30秒
    log "实验间隔等待（30秒）..."
    sleep 30
}

main() {
    log "开始HPC节点异常检测实验"
    info "实验配置数量: ${#FAULT_CONFIGS[@]}"
    info "NPB程序数量: ${#NPB_PROGRAMS[@]}"
    info "每个实验时长: 2分钟 (30秒基线 + 60秒故障 + 30秒恢复)"
    
    # 检查依赖
    command -v blade >/dev/null 2>&1 || error "ChaosBlade未安装或不在PATH中"
    command -v sbatch >/dev/null 2>&1 || error "Slurm未安装或不可用"
    command -v python3 >/dev/null 2>&1 || error "Python3未安装"
    
    # 测试Prometheus连接
    if ! curl -s "http://localhost:9090/api/v1/query?query=up" >/dev/null; then
        error "无法连接到Prometheus (http://localhost:9090)"
    fi
    
    # 检查NPB程序
    local npb_dir="$SCRIPT_DIR/npb/NPB3.4.2/NPB3.4-MPI/bin"
    if [[ ! -d "$npb_dir" ]]; then
        error "NPB目录不存在: $npb_dir"
    fi
    
    local available_programs=0
    for program in "${NPB_PROGRAMS[@]}"; do
        if [[ -f "$npb_dir/$program" ]]; then
            ((available_programs++))
        fi
    done
    
    if [[ $available_programs -eq 0 ]]; then
        error "没有找到可用的NPB程序"
    fi
    
    info "找到 $available_programs 个可用的NPB程序"
    
    local total_experiments=${#FAULT_CONFIGS[@]}
    local completed_experiments=0
    
    log "计划执行 $total_experiments 个故障注入实验"
    info "预计总时长: $((total_experiments * 3)) 分钟 (包含间隔时间)"
    
    # 执行所有实验
    for fault_name in "${!FAULT_CONFIGS[@]}"; do
        ((completed_experiments++))
        info "进度: $completed_experiments/$total_experiments"
        
        run_single_experiment "$fault_name" "${FAULT_CONFIGS[$fault_name]}"
    done
    
    log "所有实验完成！"
    info "原始数据位置: $DATA_DIR"
    info "日志位置: $LOGS_DIR"
    
    # 显示最终统计
    local total_files=$(ls -1 "$DATA_DIR"/*.csv 2>/dev/null | wc -l || echo 0)
    info "生成的数据文件数: $total_files"
}

# 参数处理
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        "single")
            if [[ $# -ne 2 ]]; then
                echo "用法: $0 single <fault_name>"
                echo "可用故障: ${!FAULT_CONFIGS[@]}"
                exit 1
            fi
            fault_name="$2"
            if [[ -z "${FAULT_CONFIGS[$fault_name]:-}" ]]; then
                error "未知故障类型: $fault_name"
            fi
            run_single_experiment "$fault_name" "${FAULT_CONFIGS[$fault_name]}"
            ;;
        "list")
            echo "可用故障配置:"
            for fault_name in "${!FAULT_CONFIGS[@]}"; do
                echo "  $fault_name: ${FAULT_CONFIGS[$fault_name]}"
            done
            echo ""
            echo "可用NPB程序:"
            for program in "${NPB_PROGRAMS[@]}"; do
                echo "  $program"
            done
            ;;
        "test")
            # 测试模式 - 只运行一个简单实验
            log "测试模式：运行单个CPU故障实验（2分钟）"
            run_single_experiment "cpu_fullload" "${FAULT_CONFIGS["cpu_fullload"]}"
            ;;
        *)
            main
            ;;
    esac
fi