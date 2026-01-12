#!/bin/bash

#
# 该脚本用于查询ChaosBlade中与HPC节点异常相关的各类故障注入命令的详细帮助信息。
# 它会遍历预定义的故障目标（target）及其动作（action），
# 并将每个目标的帮助信息输出到 'note/chaoshelp/' 目录下的独立日志文件中。
#

# 定义颜色以便更好地区分输出信息
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 定义日志输出的基础目录
LOG_DIR="note/chaoshelp"

# 使用关联数组定义需要查询的故障目标及其对应的所有子命令
declare -A CHAOS_COMMANDS
CHAOS_COMMANDS=(
    ["cpu"]="fullload"
    ["mem"]="load"
    ["disk"]="burn fill"
    ["network"]="corrupt delay dns dns_down drop duplicate loss occupy reorder"
    ["process"]="kill stop"
    ["file"]="add append chmod delete move"
    ["script"]="delay exit"
    ["time"]="travel"
    ["systemd"]="stop"
)

# 确保日志目录存在，如果不存在则创建
mkdir -p "$LOG_DIR"
echo -e "${GREEN}日志目录 '$LOG_DIR' 已准备就绪。${NC}"

# 遍历所有定义的故障目标
for target in "${!CHAOS_COMMANDS[@]}"; do
    LOG_FILE="$LOG_DIR/$target.log"
    
    # 清空旧的日志文件，准备写入新内容
    > "$LOG_FILE"
    
    echo -e "\n${YELLOW}正在查询目标: ${target} ...${NC}"
    echo "详细信息将输出到: $LOG_FILE"

    # 遍历该目标下的所有子命令
    for action in ${CHAOS_COMMANDS[$target]}; do
        COMMAND="blade create $target $action -h"
        
        # 打印分隔符和当前执行的命令到日志文件
        echo "======================================================================" >> "$LOG_FILE"
        echo "\$ $COMMAND" >> "$LOG_FILE"
        echo "======================================================================" >> "$LOG_FILE"
        
        # 执行命令并将标准输出和标准错误都追加到日志文件中
        $COMMAND >> "$LOG_FILE" 2>&1
        
        # 在命令之间添加换行符以提高可读性
        echo -e "\n\n" >> "$LOG_FILE"
    done
    echo -e "${GREEN}目标 ${target} 的帮助信息已成功写入。${NC}"
done

echo -e "\n${GREEN}所有查询已完成。${NC}"