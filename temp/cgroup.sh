set -e

# 检查 cgroup 版本（Ubuntu 22.04 默认为 cgroup v2）
CGROUP_VERSION=$(stat -fc %T /sys/fs/cgroup/)
echo "[INFO] cgroup 类型: ${CGROUP_VERSION}"

if [ "$CGROUP_VERSION" = "cgroup2fs" ]; then
  echo "[INFO] 检测到 cgroup v2，写入对应配置"
  sudo tee /etc/slurm/cgroup.conf > /dev/null <<EOF
CgroupPlugin=autodetect
EnableControllers=yes
IgnoreSystemd=no
EOF
else
  echo "[INFO] 检测到 cgroup v1，写入对应配置"
  sudo tee /etc/slurm/cgroup.conf > /dev/null <<EOF
CgroupAutomount=yes
ConstrainCores=yes
ConstrainRAMSpace=yes
EOF
fi

echo "[OK] cgroup.conf 已写入"