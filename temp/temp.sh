~/Eurosys-blade-lab (main*) » set -e                                                         quantum@quantum-os

echo "=== 提交测试作业 ==="
JOB_ID=$(sbatch \
  --job-name=blade-lab-test \
  --output=/tmp/slurm-test-%j.out \
  --wrap="echo 'SLURM_OK' && hostname && date" \
  | awk '{print $NF}')

echo "[INFO] 作业已提交，JOB_ID=${JOB_ID}"

echo ""
echo "=== 等待作业完成 ==="
sleep 5
squeue  # 若为空表示作业已完成

echo ""
echo "=== 验证作业输出 ==="
cat /tmp/slurm-test-${JOB_ID}.out && \
  echo "[OK] Slurm 端到端链路验证通过" || \
  echo "[FAIL] 作业未产生输出"
=== 提交测试作业 ===
[INFO] 作业已提交，JOB_ID=1

=== 等待作业完成 ===
             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)

=== 验证作业输出 ===
SLURM_OK
quantum-os
Sat Mar  7 19:33:01 CST 2026
[OK] Slurm 端到端链路验证通过

