# 1. blade 路径和版本

path: "/usr/local/bin/blade"

version: 1.7.2

# 2. blade 基础命令使用方式

- `blade create` - 创建故障注入实验
- `blade list` - 查询当前实验
- `blade destroy` - 销毁实验
- `blade status` - 查看实验状态

# 3. HPC 场景的故障注入

## CPU 故障
- `cpu fullload` - CPU满负载
  - `--cpu-percent` - CPU占用百分比（0-100）
  - `--cpu-count` - 指定CPU核心数量
  - `--cpu-list` - 指定CPU核心列表（如0,3或1-3）
  - `--climb-time` - 负载爬升时间（秒）
  - `--timeout` - 持续时间

## 内存故障  
- `mem load` - 内存负载
  - `--mode ram|cache` - 内存模式（物理内存或缓存）
  - `--mem-percent` - 内存占用百分比（0-100）
  - `--reserve` - 保留内存大小（MB）
  - `--rate` - 内存消耗速率（M/S，仅ram模式）
  - `--timeout` - 持续时间
  - `--avoid-being-killed` - 避免被OOM killer杀死
  - `--include-buffer-cache` - 包含buffer/cache

## 网络故障（分布式HPC）
- `network delay` - 网络延迟
  - `--time` - 延迟时间（毫秒，必需）
  - `--offset` - 延迟偏移时间（毫秒）
  - `--interface` - 网络接口（如eth0，必需）
  - `--local-port` - 本地端口（如80,8000-8080）
  - `--remote-port` - 远程端口
  - `--destination-ip` - 目标IP（支持掩码和多IP）
  - `--exclude-port` - 排除端口
  - `--protocol` - 协议（tcp/udp/icmp）

- `network loss` - 网络丢包
  - `--percent` - 丢包率（0-100，必需）
  - `--interface` - 网络接口（必需）
  - `--local-port` - 本地端口
  - `--remote-port` - 远程端口
  - `--destination-ip` - 目标IP
  - `--exclude-port` - 排除端口
  - `--protocol` - 协议

## 磁盘I/O故障
- `disk burn` - 磁盘I/O负载
  - `--read` - 读I/O负载（创建600M文件）
  - `--write` - 写I/O负载
  - `--path` - 目标路径（默认/）
  - `--size` - 块大小（MB，默认10）
  - `--timeout` - 持续时间

- `disk fill` - 磁盘空间填充
  - `--path` - 填充路径（默认/）
  - `--size` - 填充大小（MB）
  - `--percent` - 填充百分比（优先级最高）
  - `--reserve` - 保留空间（MB，优先级中等）
  - `--retain-handle` - 保留文件句柄
  - `--timeout` - 持续时间

## 进程故障
- `process kill` - 杀死进程
  - `--process` - 进程名关键字
  - `--process-cmd` - 命令名
  - `--pid` - 进程ID
  - `--local-port` - 本地端口
  - `--signal` - 信号（如9,15）
  - `--count` - 限制数量
  - `--ignore-not-found` - 忽略未找到进程

- `process stop` - 进程假死
  - `--process` - 进程名关键字
  - `--process-cmd` - 命令名
  - `--pid` - 进程ID
  - `--local-port` - 本地端口
  - `--count` - 限制数量
  - `--ignore-not-found` - 忽略未找到进程

# 4. Python脚本集成方式

## 命令输出格式
- `blade create` 成功返回：`{"code":200,"success":true,"result":"experiment_uid"}`
- `blade status` 返回详细实验信息JSON格式，包含：
  - Uid: 实验ID
  - Command/SubCommand: 实验类型
  - Flag: 执行参数
  - Status: 实验状态（Running/Destroyed等）
  - CreateTime/UpdateTime: 时间信息
- `blade destroy` 返回销毁确认信息

## 实验生命周期
- 创建后自动执行（如timeout=5秒后自动结束）
- Status显示"Destroyed"表示实验已结束
- 销毁命令可清理实验记录

## Python封装关键点
```python
import subprocess
import json

class ChaosBladeController:
    def _run_command(self, cmd):
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Command failed: {result.stderr}")
        return json.loads(result.stdout)
    
    def create_experiment(self, target, action, **params):
        # 构建参数字符串并执行blade create
        # 返回experiment_uid
        pass
    
    def check_status(self, uid):
        # 解析status JSON返回实验状态信息
        pass
```

# 5. 实验用故障注入命令

## 命令

### CPU故障
```bash
# CPU满负载 - 85%负载，30秒，默认使用所有CPU核心
blade create cpu fullload --cpu-percent 85 --timeout 30
```

### 内存故障
```bash
# 内存RAM负载 - 70%物理内存占用，30秒
blade create mem load --mode ram --mem-percent 70 --timeout 30

# 内存Cache负载 - 60%缓存占用，30秒  
blade create mem load --mode cache --mem-percent 60 --timeout 30
```

### 磁盘I/O故障
```bash
# 高强度磁盘I/O负载 - 同时读写，50块（*100MB）大小，60秒
blade create disk burn --read --write --path /tmp --size 50 --timeout 60
```

### 网络故障（分布式HPC场景）
```bash
# 网络延迟注入 - 500ms延迟，30秒
blade create network delay --time 500 --interface ens33 --timeout 30
```

## 期望的系统表现

- **CPU故障**：系统负载显著上升，top显示CPU使用率达到85%以上，所有核心参与
- **内存故障**：内存使用率上升至70%，可能触发swap，free命令显示可用内存大幅减少  
- **磁盘I/O故障**：iotop显示高I/O活动，iowait增加，磁盘队列长度上升
- **网络故障**：ping显示延迟增加500ms，影响分布式通信


# 还原检查点前的补充：

关于我们刚才的ChaosBlade工作，需要补充到note中的关键点：

补充内容（简述）：

测试验证完成：通过 chaos.py 成功验证了CPU、内存、磁盘、网络等故障注入类型的有效性

网络接口确认：系统使用 ens33 作为活跃网络接口，网络延迟注入测试成功，网络丢包因tc冲突暂不使用

最终命令集：确定了5条高强度故障注入命令（CPU 85%、内存RAM 70%/Cache 60%、磁盘I/O 50MB块、网络延迟500ms），参数调优以确保监控指标中现象明显

Python集成方案：基于subprocess调用blade命令，解析JSON输出，已有ChaosBladeController类框架

去除磁盘填充：专注于能反映I/O性能指标的disk burn，避免只影响存储空间的fill操作

这样在还原检查点后，您只需要告诉我"ChaosBlade部分已完成，现在开始Prometheus监控配置"，我就能快速理解当前进展。