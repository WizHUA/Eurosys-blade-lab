#!/usr/bin/env bash
# ==============================================================================
# start-lab.sh — Eurosys-blade-lab 实验环境服务管理脚本
# 用法:
#   ./start-lab.sh start   启动所有服务
#   ./start-lab.sh stop    停止所有服务
#   ./start-lab.sh status  查看状态
#   ./start-lab.sh restart 重启所有服务
# ==============================================================================
set -euo pipefail

# ── 颜色输出 ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
header()  { echo -e "\n${BOLD}=== $* ===${NC}"; }

# ── 配置 ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMETHEUS_PORT=9090
NODE_EXPORTER_PORT=9100
PROMETHEUS_DATA_DIR="${SCRIPT_DIR}/temp/prometheus_data"
PROMETHEUS_LOG="${SCRIPT_DIR}/temp/prometheus.log"
NODE_EXPORTER_LOG="${SCRIPT_DIR}/temp/node_exporter.log"
PROMETHEUS_CFG="${SCRIPT_DIR}/temp/prometheus.yml"
PROMETHEUS_PID="${SCRIPT_DIR}/temp/prometheus.pid"
NODE_EXPORTER_PID="${SCRIPT_DIR}/temp/node_exporter.pid"

# ── 工具函数 ──────────────────────────────────────────────────────────────────
check_command() {
    command -v "$1" &>/dev/null
}

is_port_listening() {
    ss -tlnp 2>/dev/null | grep -q ":$1 " || \
    nc -z localhost "$1" 2>/dev/null
}

is_running_by_pid() {
    local pidfile="$1"
    [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

wait_for_port() {
    local port="$1" timeout="$2" name="$3"
    local elapsed=0
    while ! is_port_listening "$port"; do
        sleep 1; elapsed=$((elapsed + 1))
        [[ $elapsed -ge $timeout ]] && { error "${name} 在 ${timeout}s 内未能在端口 ${port} 就绪"; return 1; }
    done
    ok "${name} 端口 ${port} 就绪 (${elapsed}s)"
}

# ── Prometheus 配置生成（若不存在）────────────────────────────────────────────
ensure_prometheus_config() {
    mkdir -p "${SCRIPT_DIR}/temp" "${PROMETHEUS_DATA_DIR}"
    if [[ ! -f "$PROMETHEUS_CFG" ]]; then
        info "生成 Prometheus 配置文件..."
        cat > "$PROMETHEUS_CFG" <<'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "node"
    static_configs:
      - targets: ["localhost:9100"]
EOF
        ok "已生成 ${PROMETHEUS_CFG}"
    fi
}

# ── START ─────────────────────────────────────────────────────────────────────
cmd_start() {
    header "启动 Eurosys-blade-lab 实验环境"

    # 1. Munge
    header "1/4  Munge 认证服务"
    if check_command munge; then
        if systemctl is-active --quiet munge 2>/dev/null; then
            ok "munge 已在运行"
        else
            sudo systemctl start munge
            sleep 1
            systemctl is-active --quiet munge && ok "munge 启动成功" || { error "munge 启动失败"; exit 1; }
        fi
    else
        warn "munge 未安装，跳过"
    fi

    # 2. Slurm
    header "2/4  Slurm 调度服务"
    if check_command slurmctld; then
        # slurmctld
        if systemctl is-active --quiet slurmctld 2>/dev/null; then
            ok "slurmctld 已在运行"
        else
            sudo systemctl start slurmctld
            sleep 2
            systemctl is-active --quiet slurmctld && ok "slurmctld 启动成功" || { error "slurmctld 启动失败"; sudo journalctl -u slurmctld -n 5 --no-pager; exit 1; }
        fi
        # slurmd
        if systemctl is-active --quiet slurmd 2>/dev/null; then
            ok "slurmd 已在运行"
        else
            sudo systemctl start slurmd
            sleep 2
            systemctl is-active --quiet slurmd && ok "slurmd 启动成功" || { error "slurmd 启动失败"; sudo journalctl -u slurmd -n 5 --no-pager; exit 1; }
        fi
        # 恢复节点状态（drain/down 时自动恢复）
        NODE_STATE=$(sinfo -h -o "%T" 2>/dev/null | head -1)
        if [[ "$NODE_STATE" == "drain" || "$NODE_STATE" == "down" ]]; then
            warn "节点状态为 ${NODE_STATE}，正在恢复..."
            sudo scontrol update NodeName="$(hostname -s)" State=RESUME
            sleep 1
        fi
        SINFO_OUT=$(sinfo 2>/dev/null)
        ok "Slurm 就绪:\n${SINFO_OUT}"
    else
        warn "Slurm 未安装，跳过"
    fi

    # 3. Node Exporter
    header "3/4  Node Exporter (Prometheus 数据采集)"
    if check_command node_exporter; then
        if is_port_listening "$NODE_EXPORTER_PORT"; then
            ok "node_exporter 已在端口 ${NODE_EXPORTER_PORT} 运行"
        else
            info "启动 node_exporter..."
            nohup node_exporter \
                --web.listen-address=":${NODE_EXPORTER_PORT}" \
                > "$NODE_EXPORTER_LOG" 2>&1 &
            echo $! > "$NODE_EXPORTER_PID"
            wait_for_port "$NODE_EXPORTER_PORT" 10 "node_exporter"
        fi
    else
        warn "node_exporter 未安装 — metrics 采集将失败"
        warn "安装: https://github.com/prometheus/node_exporter/releases"
    fi

    # 4. Prometheus
    header "4/4  Prometheus 监控服务"
    if check_command prometheus; then
        if is_port_listening "$PROMETHEUS_PORT"; then
            ok "prometheus 已在端口 ${PROMETHEUS_PORT} 运行"
        else
            ensure_prometheus_config
            info "启动 prometheus..."
            nohup prometheus \
                --config.file="$PROMETHEUS_CFG" \
                --storage.tsdb.path="$PROMETHEUS_DATA_DIR" \
                --web.listen-address=":${PROMETHEUS_PORT}" \
                --log.level=warn \
                > "$PROMETHEUS_LOG" 2>&1 &
            echo $! > "$PROMETHEUS_PID"
            wait_for_port "$PROMETHEUS_PORT" 15 "prometheus"
        fi
        # 验证 API 可达
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
            "http://localhost:${PROMETHEUS_PORT}/api/v1/query?query=up" 2>/dev/null || echo "000")
        [[ "$HTTP_CODE" == "200" ]] && ok "Prometheus API 响应正常 (HTTP ${HTTP_CODE})" \
                                    || warn "Prometheus API 响应异常 (HTTP ${HTTP_CODE})"
    else
        warn "prometheus 未安装 — 监控数据采集将失败"
    fi

    header "启动完成 — 环境状态摘要"
    cmd_status
}

# ── STOP ──────────────────────────────────────────────────────────────────────
cmd_stop() {
    header "停止 Eurosys-blade-lab 实验环境"

    # 停止 Prometheus
    if is_running_by_pid "$PROMETHEUS_PID"; then
        kill "$(cat "$PROMETHEUS_PID")" && rm -f "$PROMETHEUS_PID"
        ok "prometheus 已停止"
    else
        info "prometheus 未运行或非本脚本启动（跳过）"
    fi

    # 停止 Node Exporter
    if is_running_by_pid "$NODE_EXPORTER_PID"; then
        kill "$(cat "$NODE_EXPORTER_PID")" && rm -f "$NODE_EXPORTER_PID"
        ok "node_exporter 已停止"
    else
        info "node_exporter 未运行或非本脚本启动（跳过）"
    fi

    # 停止 Slurm（不停止 munge，其他服务可能依赖）
    if check_command slurmd; then
        sudo systemctl stop slurmd   2>/dev/null && ok "slurmd 已停止"   || true
        sudo systemctl stop slurmctld 2>/dev/null && ok "slurmctld 已停止" || true
    fi

    ok "所有实验服务已停止"
}

# ── STATUS ────────────────────────────────────────────────────────────────────
cmd_status() {
    echo ""
    printf "  %-20s %s\n" "服务" "状态"
    printf "  %-20s %s\n" "--------------------" "------"

    # munge
    systemctl is-active --quiet munge 2>/dev/null \
        && printf "  %-20s ${GREEN}%s${NC}\n" "munge" "running" \
        || printf "  %-20s ${RED}%s${NC}\n"   "munge" "stopped"

    # slurmctld
    systemctl is-active --quiet slurmctld 2>/dev/null \
        && printf "  %-20s ${GREEN}%s${NC}\n" "slurmctld" "running" \
        || printf "  %-20s ${RED}%s${NC}\n"   "slurmctld" "stopped"

    # slurmd
    systemctl is-active --quiet slurmd 2>/dev/null \
        && printf "  %-20s ${GREEN}%s${NC}\n" "slurmd" "running" \
        || printf "  %-20s ${RED}%s${NC}\n"   "slurmd" "stopped"

    # node_exporter
    is_port_listening "$NODE_EXPORTER_PORT" \
        && printf "  %-20s ${GREEN}%s${NC}\n" "node_exporter" "running (:${NODE_EXPORTER_PORT})" \
        || printf "  %-20s ${RED}%s${NC}\n"   "node_exporter" "stopped"

    # prometheus
    is_port_listening "$PROMETHEUS_PORT" \
        && printf "  %-20s ${GREEN}%s${NC}\n" "prometheus" "running (:${PROMETHEUS_PORT})" \
        || printf "  %-20s ${RED}%s${NC}\n"   "prometheus" "stopped"

    # slurm 节点状态
    if check_command sinfo 2>/dev/null; then
        echo ""
        sinfo 2>/dev/null || true
    fi
    echo ""
}

# ── 入口 ──────────────────────────────────────────────────────────────────────
case "${1:-status}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    restart) cmd_stop; sleep 2; cmd_start ;;
    status)  cmd_status  ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac