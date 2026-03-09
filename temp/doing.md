~/Eurosys-blade-lab (main*) » which slurmctld && slurmctld --version                         quantum@quantum-os
/usr/sbin/slurmctld
Usage: slurmctld [OPTIONS]
  -c            Do not recover state from last checkpoint.
  -D            Run daemon in foreground, with logging copied to stdout.
  -f file       Use specified file for slurmctld configuration.
  -h            Print this help message.
  -i            Ignore errors found while reading in state files on startup.
  -L logfile    Log messages to the specified file.
  -n value      Run the daemon at the specified nice value.
  -R            Recover full state from last checkpoint.
  -s            Change working directory to SlurmctldLogFile/StateSaveLocation.
  -v            Verbose mode. Multiple -v's increase verbosity.
  -V            Print version information and exit.
(base) ---------------------------------------------------------------------------------------------------------
~/Eurosys-blade-lab (main*) » slurmctld -V                                               1 ↵ quantum@quantum-os
slurm-wlm 21.08.5
(base) ---------------------------------------------------------------------------------------------------------
~/Eurosys-blade-lab (main*) » which slurmd && slurmd -V                                      quantum@quantum-os
/usr/sbin/slurmd
slurm-wlm 21.08.5
(base) ---------------------------------------------------------------------------------------------------------
~/Eurosys-blade-lab (main*) » which sbatch && sbatch -V                                      quantum@quantum-os
/usr/bin/sbatch
sbatch: error: resolve_ctls_from_dns_srv: res_nsearch error: Unknown host
sbatch: error: fetch_config: DNS SRV lookup failed
sbatch: error: _establish_config_source: failed to fetch config
sbatch: fatal: Could not establish a configuration source
(base) ---------------------------------------------------------------------------------------------------------
~/Eurosys-blade-lab (main*) » sbatch --version                                           1 ↵ quantum@quantum-os
sbatch: error: resolve_ctls_from_dns_srv: res_nsearch error: Unknown host
sbatch: error: fetch_config: DNS SRV lookup failed
sbatch: error: _establish_config_source: failed to fetch config
sbatch: fatal: Could not establish a configuration source
(base) ---------------------------------------------------------------------------------------------------------
~/Eurosys-blade-lab (main*) » which munge     && munge --version                         1 ↵ quantum@quantum-os
/usr/bin/munge
munge-0.5.14 (2020-01-14)
(base) ---------------------------------------------------------------------------------------------------------
~/Eurosys-blade-lab (main*) » set -e                                                         quantum@quantum-os

echo "=== 生成 Munge 密钥 ==="
# Ubuntu apt 安装后密钥可能已存在，检查后决定是否生成
if [ ! -f /etc/munge/munge.key ]; then
  sudo dd if=/dev/urandom bs=1 count=1024 > /tmp/munge.key
  sudo mv /tmp/munge.key /etc/munge/munge.key
  sudo chown munge:munge /etc/munge/munge.key
  sudo chmod 400 /etc/munge/munge.key
  echo "[OK] Munge 密钥已生成"
else
  echo "[INFO] Munge 密钥已存在，跳过生成"
  sudo chmod 400 /etc/munge/munge.key
fi
=== 生成 Munge 密钥 ===
[sudo] password for quantum: 
1024+0 records in
1024+0 records out
1024 bytes (1.0 kB, 1.0 KiB) copied, 0.00414835 s, 247 kB/s
[OK] Munge 密钥已生成
(base) ---------------------------------------------------------------------------------------------------------
~/Eurosys-blade-lab (main*) » echo "=== 启动 Munge ==="                                      quantum@quantum-os
sudo systemctl enable munge
sudo systemctl start munge
sleep 1
=== 启动 Munge ===
Synchronizing state of munge.service with SysV service script with /lib/systemd/systemd-sysv-install.
Executing: /lib/systemd/systemd-sysv-install enable munge
(base) ---------------------------------------------------------------------------------------------------------
~/Eurosys-blade-lab (main*) » munge -n | unmunge && echo "[OK] Munge 认证链路正常" || echo "[FAIL] Munge 异常"
STATUS:          Success (0)
ENCODE_HOST:     quantum-os (127.0.1.1)
ENCODE_TIME:     2026-03-07 18:28:52 +0800 (1772879332)
DECODE_TIME:     2026-03-07 18:28:52 +0800 (1772879332)
TTL:             300
CIPHER:          aes128 (4)
MAC:             sha256 (5)
ZIP:             none (0)
UID:             quantum (1000)
GID:             quantum (1000)
LENGTH:          0

[OK] Munge 认证链路正常

