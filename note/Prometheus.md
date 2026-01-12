# 1. Prometheus 路径和版本

path: "/usr/bin/prometheus"
version: 2.31.2+ds1 
service: systemd管理，服务名prometheus.service
config: "/etc/prometheus/prometheus.yml"
port: 9090 (IPv6监听)
status: active (running)


# 2. 配置状态

## 配置文件内容
- global采集间隔：5s
- evaluation间隔：5s  
- 已配置node_exporter：localhost:9100，5s采集间隔
- prometheus自身监控：localhost:9090，5s采集间隔

## 系统硬件信息
- CPU：8核心 (cpu="0" to cpu="7")
- 内存：总内存 8.3GB (node_memory_MemTotal_bytes)
- 磁盘：sda主磁盘，sr0光驱
- 网络：ens33主网卡，lo回环网卡

# 3. 完整系统监控指标清单

## 核心系统资源指标

### CPU指标
- `node_cpu_seconds_total{mode}` - CPU各模式时间统计 (idle/user/system/iowait/irq/nice/softirq/steal)
- `node_load1/5/15` - 1/5/15分钟系统负载 (当前: load1=8.77)
- `node_context_switches_total` - 上下文切换总数
- `node_intr_total` - 中断服务总数
- `node_forks_total` - 进程fork总数
- `node_procs_running` - 运行态进程数 (当前: 11)
- `node_procs_blocked` - 阻塞态进程数
- `node_pressure_cpu_waiting_seconds_total` - CPU压力等待时间

### 内存指标  
- `node_memory_MemTotal_bytes` - 总内存 (8.3GB)
- `node_memory_MemFree_bytes` - 空闲内存
- `node_memory_MemAvailable_bytes` - 可用内存 (约4.6GB)
- `node_memory_Cached_bytes` - 缓存内存
- `node_memory_Buffers_bytes` - 缓冲区内存  
- `node_memory_Active_*_bytes` - 活跃内存 (anon/file)
- `node_memory_Inactive_*_bytes` - 非活跃内存
- `node_memory_SwapTotal_bytes/SwapFree_bytes` - swap分区信息
- `node_memory_Dirty_bytes` - 脏页内存
- `node_pressure_memory_*_seconds_total` - 内存压力指标

### 磁盘I/O指标
- `node_disk_*_bytes_total{device="sda"}` - 读写字节统计
- `node_disk_*_completed_total{device="sda"}` - 读写操作完成次数
- `node_disk_io_time_seconds_total` - I/O总时间
- `node_disk_io_time_weighted_seconds_total` - 加权I/O时间
- `node_disk_io_now` - 当前进行中的I/O数量
- `node_pressure_io_*_seconds_total` - I/O压力指标

### 文件系统指标
- `node_filesystem_size_bytes` - 文件系统总大小
- `node_filesystem_free_bytes` - 可用空间
- `node_filesystem_avail_bytes` - 用户可用空间  
- `node_filesystem_files` - 总inode数
- `node_filesystem_files_free` - 可用inode数
- `node_filesystem_readonly` - 只读状态

### 网络指标
- `node_network_*_bytes_total{device="ens33"}` - 收发字节统计
- `node_network_*_packets_total{device="ens33"}` - 收发包统计  
- `node_network_*_errs_total` - 网络错误统计
- `node_network_*_drop_total` - 丢包统计
- `node_network_up{device}` - 网络接口状态
- `node_network_speed_bytes` - 网络接口速度 (ens33: 125MB/s)

## 高级系统指标

### 调度器指标
- `node_schedstat_running_seconds_total` - CPU运行进程时间
- `node_schedstat_waiting_seconds_total` - 进程等待调度时间
- `node_schedstat_timeslices_total` - 时间片执行次数

### 网络协议栈指标
- `node_netstat_Tcp_*` - TCP协议统计 (连接数/重传/超时等)
- `node_netstat_Udp_*` - UDP协议统计
- `node_netstat_Icmp_*` - ICMP协议统计
- `node_sockstat_*` - 套接字状态统计

### 虚拟内存指标  
- `node_vmstat_pgfault` - 页错误总数
- `node_vmstat_pgmajfault` - 主页错误数
- `node_vmstat_pgpgin/pgpgout` - 页面换入/换出
- `node_vmstat_oom_kill` - OOM杀死进程数

### 系统服务指标
- `node_systemd_unit_state` - systemd服务状态 (active: 338, inactive: 130, failed: 1)
- `node_systemd_units{state}` - 服务状态汇总
- `node_systemd_socket_*_connections_total` - socket连接统计

### 时间同步指标
- `node_time_seconds` - 系统时间
- `node_boot_time_seconds` - 系统启动时间
- `node_timex_sync_status` - 时间同步状态
- `node_timex_offset_seconds` - 时间偏移量

### 硬件相关指标
- `node_cooling_device_*_state` - CPU温控状态 (8个核心)
- `node_power_supply_online` - 电源状态
- `smartmon_device_*` - 磁盘SMART健康状态

# 4. 故障注入监控映射

## ChaosBlade故障 -> Prometheus监控指标

### CPU故障监控查询
- **故障注入**: `blade create cpu fullload --cpu-percent 85 --timeout 30`
- **核心查询指标**:
  - `100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)` - CPU总使用率
  - `node_load1`, `node_load5`, `node_load15` - 系统负载
  - `rate(node_context_switches_total[1m])` - 上下文切换率
  - `node_procs_running`, `node_procs_blocked` - 运行/阻塞进程数

### 内存故障监控查询
- **故障注入**: `blade create mem load --mode ram --mem-percent 70 --timeout 30`
- **核心查询指标**:
  - `(1 - node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes) * 100` - 内存使用率
  - `node_memory_MemFree_bytes`, `node_memory_Cached_bytes`, `node_memory_Buffers_bytes` - 内存分配
  - `(1 - node_memory_SwapFree_bytes/node_memory_SwapTotal_bytes) * 100` - Swap使用率
  - `rate(node_vmstat_pgmajfault[1m])` - 主页错误率

### 磁盘I/O故障监控查询
- **故障注入**: `blade create disk burn --read --write --path /tmp --size 50 --timeout 60`
- **核心查询指标**:
  - `rate(node_disk_io_time_seconds_total{device="sda"}[1m]) * 100` - 磁盘I/O使用率
  - `rate(node_disk_read_bytes_total{device="sda"}[1m])` - 磁盘读取速率
  - `rate(node_disk_written_bytes_total{device="sda"}[1m])` - 磁盘写入速率
  - `node_disk_io_now{device="sda"}` - 当前I/O队列深度
  - `avg(rate(node_cpu_seconds_total{mode="iowait"}[1m])) * 100` - I/O等待时间占比

### 网络故障监控查询
- **故障注入**: `blade create network delay --time 500 --interface ens33 --timeout 30`
- **核心查询指标**:
  - `rate(node_network_receive_bytes_total{device="ens33"}[1m])` - 网络接收速率
  - `rate(node_network_transmit_bytes_total{device="ens33"}[1m])` - 网络发送速率
  - `rate(node_network_receive_errs_total{device="ens33"}[1m])` - 网络接收错误率
  - `rate(node_network_transmit_errs_total{device="ens33"}[1m])` - 网络发送错误率
  - `node_network_up{device="ens33"}` - 网络接口状态

# 5. 完整查询指标列表

## 系统基础指标查询
- `node_time_seconds` - 系统时间戳
- `node_boot_time_seconds` - 系统启动时间
- `rate(node_intr_total[1m])` - 中断频率
- `rate(node_forks_total[1m])` - 进程创建频率

## 文件系统查询
- `(1 - node_filesystem_avail_bytes/node_filesystem_size_bytes) * 100` - 文件系统使用率
- `node_filesystem_files_free/node_filesystem_files * 100` - inode使用率

## 网络协议栈查询
- `node_netstat_Tcp_CurrEstab` - TCP连接数
- `rate(node_netstat_TcpExt_TCPTimeouts[1m])` - TCP超时率
- `rate(node_netstat_Tcp_RetransSegs[1m])` - TCP重传率

## 系统压力指标查询
- `rate(node_pressure_cpu_waiting_seconds_total[1m])` - CPU压力
- `rate(node_pressure_memory_waiting_seconds_total[1m])` - 内存压力  
- `rate(node_pressure_io_waiting_seconds_total[1m])` - I/O压力