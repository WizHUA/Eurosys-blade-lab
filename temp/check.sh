set -e
cd /home/quantum/Eurosys-blade-lab

echo "=== [1/5] tmux 会话确认 ==="
[ -n "$TMUX" ] && echo "[OK] 已在 tmux 会话中: $TMUX" || echo "[WARNING] 未在 tmux 中！"

echo ""
echo "=== [2/5] 启动所有服务 ==="
./start-lab.sh start

echo ""
echo "=== [3/5] conda 环境确认 ==="
echo "Python: $(python --version)"
echo "Env:    ${CONDA_DEFAULT_ENV:-<未激活>}"

echo ""
echo "=== [4/5] 磁盘空间安全检查 ==="
df -h / | awk 'NR==2 {
    used=$3; avail=$4; pct=$5
    print "[INFO] 根分区: used="used", avail="avail", use%="pct
    if (substr(pct,1,length(pct)-1)+0 > 70)
        print "[WARNING] 磁盘使用率超过70%，disk_fill实验风险较高"
    else
        print "[OK] 磁盘空间充足"
}'

echo ""
echo "=== [5/5] Slurm 节点状态 ==="
sinfo