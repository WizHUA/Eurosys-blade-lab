#!/bin/bash

# NPB基准测试运行时间统计脚本
# 设置颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 设置MPI环境变量
export OMPI_ALLOW_RUN_AS_ROOT=1
export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1

# 测试参数
NPB_DIR="/opt/exp/npb/NPB3.4.2/NPB3.4-MPI/bin"
NP=4  # MPI进程数
RESULT_FILE="/opt/exp/note/npb_timing_results.csv"

# 初始化结果文件
echo "程序,Class,实际运行时间(秒),运行时间(分:秒),计算类型,适用场景" > $RESULT_FILE

echo -e "${BLUE}=== NPB基准测试运行时间统计 ===${NC}"
echo -e "${YELLOW}MPI进程数: $NP${NC}"
echo -e "${YELLOW}测试目录: $NPB_DIR${NC}"
echo ""

# 切换到NPB目录
cd $NPB_DIR

# 定义测试程序列表和特性
declare -A programs=(
    ["ep.A.x"]="CPU密集,无通信"
    ["ep.B.x"]="CPU密集,无通信" 
    ["ep.C.x"]="CPU密集,无通信"
    ["ep.D.x"]="CPU密集,无通信"
    ["is.A.x"]="整数排序,低通信"
    ["is.B.x"]="整数排序,低通信"
    ["is.C.x"]="整数排序,低通信"
    ["is.D.x"]="整数排序,低通信"
    ["mg.B.x"]="多重网格,中等通信"
    ["mg.C.x"]="多重网格,中等通信"
    ["cg.A.x"]="共轭梯度,中等通信"
    ["cg.B.x"]="共轭梯度,中等通信"
    ["cg.C.x"]="共轭梯度,中等通信"
    ["cg.D.x"]="共轭梯度,中等通信"
    ["ft.B.x"]="FFT计算,高通信"
    ["ft.C.x"]="FFT计算,高通信"
    ["bt.B.x"]="块三对角,高通信"
    ["lu.B.x"]="LU分解,高通信"
    ["sp.B.x"]="标量五对角,高通信"
)

# 测试函数
test_program() {
    local prog=$1
    local class=$(echo $prog | grep -o '[A-D]')
    local type=${programs[$prog]}
    
    echo -e "${GREEN}测试: $prog${NC}"
    
    # 运行测试并记录时间
    start_time=$(date +%s.%N)
    timeout 600s mpirun -np $NP ./$prog > /dev/null 2>&1
    exit_code=$?
    end_time=$(date +%s.%N)
    
    if [ $exit_code -eq 124 ]; then
        echo -e "${RED}  超时 (>10分钟)${NC}"
        duration="TIMEOUT"
        formatted_time="TIMEOUT"
    elif [ $exit_code -ne 0 ]; then
        echo -e "${RED}  执行失败${NC}"
        duration="FAILED"
        formatted_time="FAILED"
    else
        duration=$(echo "$end_time - $start_time" | bc)
        minutes=$(echo "$duration / 60" | bc)
        seconds=$(echo "$duration % 60" | bc)
        formatted_time=$(printf "%d:%05.2f" $minutes $seconds)
        echo -e "${GREEN}  完成: ${formatted_time}${NC}"
    fi
    
    # 写入结果文件
    echo "$prog,$class,$duration,$formatted_time,$type,测试" >> $RESULT_FILE
}

# 按类别测试程序
echo -e "${BLUE}=== 测试EP程序(CPU密集) ===${NC}"
for prog in ep.A.x ep.B.x ep.C.x; do
    [[ -f $prog ]] && test_program $prog
done

echo -e "${BLUE}=== 测试IS程序(整数排序) ===${NC}"  
for prog in is.A.x is.B.x is.C.x; do
    [[ -f $prog ]] && test_program $prog
done

echo -e "${BLUE}=== 测试MG程序(多重网格) ===${NC}"
for prog in mg.B.x mg.C.x; do
    [[ -f $prog ]] && test_program $prog
done

echo -e "${BLUE}=== 测试CG程序(共轭梯度) ===${NC}"
for prog in cg.A.x cg.B.x cg.C.x; do
    [[ -f $prog ]] && test_program $prog
done

echo -e "${BLUE}=== 测试FT程序(FFT) ===${NC}"
for prog in ft.B.x ft.C.x; do
    [[ -f $prog ]] && test_program $prog
done

echo -e "${BLUE}=== 测试其他程序 ===${NC}"
for prog in bt.B.x lu.B.x sp.B.x; do
    [[ -f $prog ]] && test_program $prog
done

echo -e "${GREEN}=== 测试完成 ===${NC}"
echo -e "${YELLOW}结果已保存到: $RESULT_FILE${NC}"

# 生成汇总表格
echo ""
echo -e "${BLUE}=== 运行时间汇总 ===${NC}"
column -t -s',' $RESULT_FILE