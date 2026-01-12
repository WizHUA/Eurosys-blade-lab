#!/bin/bash
# filepath: /opt/exp/scripts/inject_fault.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/tmp/fault_injection.log"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"
    exit 1
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"
}

# 单一故障注入函数
inject_single_fault() {
    local fault_type="$1"
    local fault_action="$2"
    shift 2
    local params="$@"
    
    log "注入单一故障: ${fault_type}_${fault_action}"
    log "参数: $params"
    
    case "${fault_type}_${fault_action}" in
        "cpu_fullload")
            blade create cpu fullload $params
            ;;
        "mem_load")
            blade create mem load $params
            ;;
        "disk_burn")
            blade create disk burn $params
            ;;
        "disk_fill")
            blade create disk fill $params
            ;;
        "network_delay")
            blade create network delay $params
            ;;
        "network_loss")
            blade create network loss $params
            ;;
        "network_corrupt")
            blade create network corrupt $params
            ;;
        "process_kill")
            blade create process kill $params
            ;;
        "process_stop")
            blade create process stop $params
            ;;
        "file_delete")
            blade create file delete $params
            ;;
        "file_chmod")
            blade create file chmod $params
            ;;
        *)
            error "不支持的故障类型: ${fault_type}_${fault_action}"
            ;;
    esac
}

# 复合故障注入函数
inject_composite_fault() {
    local fault1="$1"
    local fault2="$2"
    
    log "注入复合故障: $fault1 + $fault2"
    
    # 解析第一个故障
    IFS='|' read -ra FAULT1_PARTS <<< "$fault1"
    inject_single_fault ${FAULT1_PARTS[@]}
    
    # 等待3秒后注入第二个故障
    sleep 3
    
    # 解析第二个故障
    IFS='|' read -ra FAULT2_PARTS <<< "$fault2"
    inject_single_fault ${FAULT2_PARTS[@]}
}

# 主函数
main() {
    if [[ $# -lt 2 ]]; then
        echo "用法:"
        echo "  单一故障: $0 <fault_type> <fault_action> [参数...]"
        echo "  复合故障: $0 composite '<fault1>' '<fault2>'"
        echo ""
        echo "示例:"
        echo "  $0 cpu fullload --cpu-percent 80 --timeout 300"
        echo "  $0 composite 'cpu|fullload|--cpu-percent|50|--timeout|300' 'disk|burn|--read|--timeout|300'"
        exit 1
    fi
    
    local command="$1"
    shift
    
    case "$command" in
        "composite")
            if [[ $# -ne 2 ]]; then
                error "复合故障需要两个故障参数"
            fi
            inject_composite_fault "$1" "$2"
            ;;
        *)
            inject_single_fault "$command" "$@"
            ;;
    esac
    
    log "故障注入完成"
}

main "$@"