import pandas as pd
import os
from datetime import datetime, timedelta
import subprocess
import json

def parse_chaos_data(chaos_file):
    df = pd.read_csv(chaos_file)
    if df.empty:
        raise ValueError("混沌实验数据文件为空")
    
    df["create_datetime"] = pd.to_datetime(df["create_time"])
    df["end_datetime"] = pd.to_datetime(df["end_time"])

    df = df.sort_values("create_datetime").reset_index(drop=True)

    groups = []
    current_group = []
    
    for _, record in df.iterrows():
        if not current_group:
            current_group.append(record)
        else:
            # 检查与当前组最后一个记录的时间差
            last_time = current_group[-1]['create_datetime']
            current_time = record['create_datetime']
            time_diff = abs((current_time - last_time).total_seconds())
            
            if time_diff <= 2:  # 2秒内认为是同一组
                current_group.append(record)
            else:
                groups.append(current_group)
                current_group = [record]
    
    if current_group:
        groups.append(current_group)
    
    return groups

def get_experiment_center_time(group):
    """计算实验组的中心时间（create_time的平均值 + 30秒）"""
    create_times = [record['create_datetime'] for record in group]
    avg_create_time = create_times[0] + sum((t - create_times[0] for t in create_times[1:]), timedelta()) / len(create_times)
    return avg_create_time + timedelta(seconds=30)  # 注入时间 + 30秒

def extract_metrics_window(center_time, metrics_df):
    """提取指定时间窗口的metrics数据（前后各取4条记录，总共8条）"""
    if metrics_df.empty:
        return metrics_df
    
    # 转换时间戳列
    metrics_df['timestamp_dt'] = pd.to_datetime(metrics_df['timestamp'])
    
    # 找到最接近中心时间的记录索引
    time_diffs = abs(metrics_df['timestamp_dt'] - center_time)
    center_idx = time_diffs.idxmin()
    
    # 往前往后各取4条记录
    start_idx = max(0, center_idx - 4)
    end_idx = min(len(metrics_df), center_idx + 4)
    
    # 确保至少有8条记录（如果数据足够的话）
    if end_idx - start_idx < 8 and len(metrics_df) >= 8:
        if start_idx == 0:
            end_idx = min(len(metrics_df), 8)
        elif end_idx == len(metrics_df):
            start_idx = max(0, len(metrics_df) - 8)
    
    result = metrics_df.iloc[start_idx:end_idx].copy()
    result = result.drop('timestamp_dt', axis=1)  # 删除临时列
    return result

def extract_related_jobinfo(experiment_start, experiment_end, jobinfo_df):
    """提取与实验时间段有交集的作业信息"""
    if jobinfo_df.empty:
        return jobinfo_df
    
    # 处理时间列，使用管道分隔符
    jobinfo_df = jobinfo_df.copy()
    
    # 转换时间格式，处理可能的格式问题
    def safe_parse_time(time_str):
        if pd.isna(time_str) or time_str == '' or time_str == 'Unknown':
            return None
        try:
            return pd.to_datetime(time_str)
        except:
            return None
    
    jobinfo_df['Start_dt'] = jobinfo_df['Start'].apply(safe_parse_time)
    jobinfo_df['End_dt'] = jobinfo_df['End'].apply(safe_parse_time)
    
    # 找到与实验时间段有交集的作业
    mask = (
        (jobinfo_df['Start_dt'].notna() & jobinfo_df['End_dt'].notna()) &
        (
            (jobinfo_df['Start_dt'] <= experiment_end) & 
            (jobinfo_df['End_dt'] >= experiment_start)
        )
    ) | (
        jobinfo_df['Start_dt'].isna() | jobinfo_df['End_dt'].isna()  # 包含时间信息缺失的作业
    )
    
    result = jobinfo_df[mask].copy()
    result = result.drop(['Start_dt', 'End_dt'], axis=1)  # 删除临时列
    return result

def get_group_name(group):
    """根据异常类型生成组名"""
    fault_types = list(set([record['fault_type'] for record in group]))
    fault_types.sort()  # 排序确保一致性
    
    if len(fault_types) == 1:
        return fault_types[0].replace("-", "_")
    else:
        return "_".join([ft.replace("-", "_") for ft in fault_types])

def create_experiment_directory(exp_name, group, metrics_df, jobinfo_df, output_dir):
    """为单个实验组创建完整的数据目录"""
    exp_dir = os.path.join(output_dir, exp_name)
    os.makedirs(exp_dir, exist_ok=True)
    
    # 计算实验时间范围
    start_times = [record['create_datetime'] for record in group]
    end_times = [record['end_datetime'] for record in group]
    
    exp_start = min(start_times)
    exp_end = max(end_times)
    center_time = get_experiment_center_time(group)
    
    # 1. 创建故障信息文件
    with open(os.path.join(exp_dir, 'fault_info.txt'), "w") as f:
        fault_types = [r['fault_type'] for r in group]
        experiment_ids = [r['experiment_id'] for r in group]
        
        f.write(f"Fault Types: {', '.join(fault_types)}\n")
        f.write(f"Experiment IDs: {', '.join(experiment_ids)}\n")
        f.write(f"Injection Time Range: {exp_start.strftime('%Y-%m-%d %H:%M:%S')} - {exp_end.strftime('%Y-%m-%d %H:%M:%S')}\n")
        # f.write(f"Center Time (for metrics): {center_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        f.write("Fault Commands:\n")
        for record in group:
            f.write(f"blade status {record['experiment_id']}\n")
        
        f.write("\nDetailed Fault Information:\n")
        for i, record in enumerate(group, 1):
            f.write(f"{i}. ID: {record['experiment_id']}\n")
            f.write(f"   Type: {record['fault_type']}\n")
            f.write(f"   Create: {record['create_time']}\n")
            f.write(f"   End: {record['end_time']}\n\n")

    # 2. 提取metrics数据
    metrics_window = extract_metrics_window(center_time, metrics_df)
    metrics_window.to_csv(os.path.join(exp_dir, 'metrics.csv'), index=False)
    
    # 3. 提取相关jobinfo
    related_jobs = extract_related_jobinfo(exp_start, exp_end, jobinfo_df)
    related_jobs.to_csv(os.path.join(exp_dir, 'jobinfo.csv'), sep='|', index=False)
    
    print(f"✓ 创建实验目录: {exp_name}")
    print(f"  - 故障类型: {', '.join(set([r['fault_type'] for r in group]))}")
    print(f"  - 异常数量: {len(group)}")
    print(f"  - 时间范围: {exp_start.strftime('%H:%M:%S')} - {exp_end.strftime('%H:%M:%S')}")
    print(f"  - Metrics记录: {len(metrics_window)}")
    print(f"  - 相关作业: {len(related_jobs)}")
    print()

def query_blade_status(experiment_id):
    """查询blade状态（可选功能）"""
    try:
        cmd = ['blade', 'status', experiment_id]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            return {"error": result.stderr}
    except Exception as e:
        return {"error": str(e)}

def validate_data_files(data_dir):
    """验证数据文件是否存在"""
    required_files = ['chaos.csv', 'metric.csv', 'jobinfo.csv']
    missing_files = []
    
    for file in required_files:
        file_path = os.path.join(data_dir, file)
        if not os.path.exists(file_path):
            missing_files.append(file)
    
    if missing_files:
        raise FileNotFoundError(f"缺少必要的数据文件: {', '.join(missing_files)}")
    
    return True

def main():
    # 数据文件路径
    data_dir = "./data/compositetest"
    output_dir = os.path.join(data_dir, "extracted_data")
    
    print("开始处理实验数据...")
    print(f"数据源目录: {data_dir}")
    print(f"输出目录: {output_dir}")
    print("=" * 60)
    
    try:
        # 验证数据文件
        validate_data_files(data_dir)
        
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        # 读取数据文件
        print("读取数据文件...")
        chaos_file = os.path.join(data_dir, 'chaos.csv')
        metrics_file = os.path.join(data_dir, 'metric.csv')
        jobinfo_file = os.path.join(data_dir, 'jobinfo.csv')
        
        # 1. 解析chaos数据并分组
        print("分析异常注入数据...")
        chaos_groups = parse_chaos_data(chaos_file)
        print(f"识别出 {len(chaos_groups)} 个实验组")
        
        if not chaos_groups:
            print("警告: 没有找到有效的异常注入数据")
            return
        
        # 2. 读取metrics和jobinfo数据
        print("读取指标和作业数据...")
        try:
            metrics_df = pd.read_csv(metrics_file)
            print(f"Metrics记录总数: {len(metrics_df)}")
        except Exception as e:
            print(f"警告: 读取metrics文件失败: {e}")
            metrics_df = pd.DataFrame()
        
        try:
            jobinfo_df = pd.read_csv(jobinfo_file, delimiter='|')
            print(f"作业信息记录总数: {len(jobinfo_df)}")
        except Exception as e:
            print(f"警告: 读取jobinfo文件失败: {e}")
            jobinfo_df = pd.DataFrame()
        
        print("\n" + "=" * 60)
        print("开始创建实验数据目录...")
        print("=" * 60)
        
        # 3. 为每个实验组创建数据目录
        for i, group in enumerate(chaos_groups, 1):
            exp_name = f"exp_{i:03d}_{get_group_name(group)}"
            create_experiment_directory(exp_name, group, metrics_df, jobinfo_df, output_dir)
        
        print("=" * 60)
        print(f"✓ 数据处理完成！成功创建了 {len(chaos_groups)} 个实验数据目录")
        print(f"✓ 输出目录: {output_dir}")
        print("\n目录结构:")
        for i, group in enumerate(chaos_groups, 1):
            exp_name = f"exp_{i:03d}_{get_group_name(group)}"
            print(f"  {exp_name}/")
            print(f"    ├── fault_info.txt")
            print(f"    ├── metrics.csv")
            print(f"    └── jobinfo.csv")
        
        # 统计信息
        print(f"\n实验组统计:")
        single_fault_count = sum(1 for group in chaos_groups if len(group) == 1)
        combo_fault_count = len(chaos_groups) - single_fault_count
        print(f"  - 单一异常组: {single_fault_count}")
        print(f"  - 组合异常组: {combo_fault_count}")
        
    except Exception as e:
        print(f"❌ 处理过程中发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()