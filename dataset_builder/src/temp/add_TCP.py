import pandas as pd
import requests
import json
from datetime import datetime, timedelta

def get_all_tcp_metrics():
    """获取所有可用的TCP相关指标"""
    try:
        response = requests.get("http://localhost:9090/api/v1/label/__name__/values")
        if response.status_code == 200:
            data = response.json()
            # 查找所有TCP相关指标
            tcp_metrics = [metric for metric in data['data'] 
                          if any(keyword in metric.lower() for keyword in 
                               ['tcp', 'retrans', 'connection', 'socket', 'listen'])]
            return sorted(tcp_metrics)
        else:
            print(f"获取指标失败: {response.status_code}")
            return []
    except Exception as e:
        print(f"获取指标出错: {e}")
        return []

def query_tcp_metric(metric_name, start_time, end_time, use_increase=False):
    """查询单个TCP指标"""
    try:
        query = f"increase({metric_name}[15s])" if use_increase else metric_name
        
        response = requests.get(
            "http://localhost:9090/api/v1/query_range",
            params={
                "query": query,
                "start": start_time.timestamp(),
                "end": end_time.timestamp(),
                "step": "15s"
            },
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data['status'] == 'success' and data['data']['result']:
                values = data['data']['result'][0]['values']
                
                # 转换数据
                df = pd.DataFrame(values, columns=['unix_timestamp', metric_name])
                df['unix_timestamp'] = pd.to_numeric(df['unix_timestamp'])
                df['timestamp'] = pd.to_datetime(df['unix_timestamp'], unit='s')
                df['timestamp_str'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
                df[metric_name] = pd.to_numeric(df[metric_name])
                
                return df[['timestamp_str', metric_name]]
            else:
                return None
        else:
            return None
    except Exception as e:
        print(f"查询 {metric_name} 出错: {e}")
        return None

def query_comprehensive_tcp_data():
    """查询综合TCP数据"""
    
    # 时间范围
    start_time_str = "2025-09-22 23:15:10"
    end_time_str = "2025-09-22 23:32:10"
    
    print(f"=== 查询综合TCP数据 ===")
    print(f"时间范围: {start_time_str} 到 {end_time_str}")
    
    start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
    end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
    
    # 获取所有TCP指标
    all_tcp_metrics = get_all_tcp_metrics()
    print(f"\n找到 {len(all_tcp_metrics)} 个TCP相关指标:")
    for i, metric in enumerate(all_tcp_metrics, 1):
        print(f"  {i:2d}. {metric}")
    
    # 重点关注的TCP指标
    priority_metrics = {
        # TCP重传相关
        'node_netstat_Tcp_RetransSegs': {'use_increase': True, 'description': 'TCP重传段数'},
        'node_netstat_TcpExt_TCPSynRetrans': {'use_increase': True, 'description': 'TCP SYN重传'},
        'node_netstat_TcpExt_TCPTimeouts': {'use_increase': True, 'description': 'TCP超时'},
        
        # TCP连接相关
        'node_netstat_Tcp_ActiveOpens': {'use_increase': True, 'description': 'TCP主动打开连接'},
        'node_netstat_Tcp_PassiveOpens': {'use_increase': True, 'description': 'TCP被动打开连接'},
        'node_netstat_Tcp_CurrEstab': {'use_increase': False, 'description': 'TCP当前建立的连接'},
        'node_netstat_Tcp_EstabResets': {'use_increase': True, 'description': 'TCP连接重置'},
        
        # TCP错误相关
        'node_netstat_Tcp_InErrs': {'use_increase': True, 'description': 'TCP输入错误'},
        'node_netstat_Tcp_OutRsts': {'use_increase': True, 'description': 'TCP输出重置'},
        'node_netstat_TcpExt_ListenDrops': {'use_increase': True, 'description': 'TCP监听丢弃'},
        'node_netstat_TcpExt_ListenOverflows': {'use_increase': True, 'description': 'TCP监听溢出'},
        
        # TCP高级特性
        'node_netstat_TcpExt_SyncookiesSent': {'use_increase': True, 'description': 'SYN cookies发送'},
        'node_netstat_TcpExt_SyncookiesRecv': {'use_increase': True, 'description': 'SYN cookies接收'},
        'node_netstat_TcpExt_SyncookiesFailed': {'use_increase': True, 'description': 'SYN cookies失败'},
    }
    
    print(f"\n开始查询 {len(priority_metrics)} 个重点TCP指标...")
    
    # 存储所有数据
    all_data = {}
    successful_metrics = []
    failed_metrics = []
    
    for metric, config in priority_metrics.items():
        print(f"\n查询: {metric} ({config['description']})")
        
        df = query_tcp_metric(metric, start_time, end_time, config['use_increase'])
        
        if df is not None:
            print(f"  ✓ 成功，{len(df)} 个数据点")
            
            # 转换时间为北京时间
            df['timestamp'] = pd.to_datetime(df['timestamp_str']) + timedelta(hours=8)
            df['timestamp_beijing'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
            
            all_data[metric] = df[['timestamp_beijing', metric]].copy()
            successful_metrics.append((metric, config['description']))
            
            # 显示数据统计
            values = df[metric]
            if config['use_increase']:
                non_zero = values[values > 0]
                if len(non_zero) > 0:
                    print(f"    发现 {len(non_zero)} 个非零增量，最大值: {non_zero.max():.2f}")
                else:
                    print(f"    无增量事件")
            else:
                print(f"    数值范围: {values.min():.2f} - {values.max():.2f}")
        else:
            print(f"  ✗ 查询失败或无数据")
            failed_metrics.append((metric, config['description']))
    
    return all_data, successful_metrics, failed_metrics

def merge_and_save_tcp_data(all_data, successful_metrics):
    """合并并保存TCP数据"""
    
    if not all_data:
        print("没有成功获取的数据")
        return
    
    print(f"\n=== 合并数据 ===")
    
    # 以第一个指标的时间戳为基准
    base_metric = list(all_data.keys())[0]
    merged_df = all_data[base_metric].copy()
    merged_df = merged_df.rename(columns={'timestamp_beijing': 'timestamp'})
    
    # 合并其他指标
    for metric in list(all_data.keys())[1:]:
        df_metric = all_data[metric]
        merged_df = merged_df.merge(
            df_metric.rename(columns={'timestamp_beijing': 'timestamp'}),
            on='timestamp',
            how='outer'
        )
    
    # 排序并填充缺失值
    merged_df = merged_df.sort_values('timestamp').fillna(0)
    
    print(f"合并后数据: {len(merged_df)} 行, {len(merged_df.columns)-1} 个指标")
    
    # 保存合并数据
    output_file = "tcp_comprehensive_data.csv"
    merged_df.to_csv(output_file, index=False)
    print(f"✓ 综合数据已保存到: {output_file}")
    
    # 保存各个指标的单独文件
    print(f"\n=== 保存单独指标文件 ===")
    for metric in all_data.keys():
        df_single = all_data[metric]
        filename = f"tcp_{metric.split('_')[-1].lower()}.csv"
        df_single.to_csv(filename, index=False)
        print(f"✓ {metric} -> {filename}")
    
    # 生成数据报告
    generate_tcp_report(merged_df, successful_metrics)
    
    return merged_df

def generate_tcp_report(df, successful_metrics):
    """生成TCP数据报告"""
    
    report_lines = []
    report_lines.append("TCP 数据分析报告")
    report_lines.append("=" * 50)
    report_lines.append(f"时间范围: {df.iloc[0]['timestamp']} 到 {df.iloc[-1]['timestamp']}")
    report_lines.append(f"数据点数: {len(df)}")
    report_lines.append(f"指标数量: {len(successful_metrics)}")
    report_lines.append("")
    
    report_lines.append("成功获取的指标:")
    for i, (metric, desc) in enumerate(successful_metrics, 1):
        report_lines.append(f"  {i:2d}. {metric}")
        report_lines.append(f"      {desc}")
        
        # 计算统计信息
        values = df[metric]
        non_zero = values[values > 0]
        
        report_lines.append(f"      数值范围: {values.min():.2f} - {values.max():.2f}")
        report_lines.append(f"      平均值: {values.mean():.2f}")
        if len(non_zero) > 0:
            report_lines.append(f"      非零事件: {len(non_zero)} 次")
            report_lines.append(f"      非零值总和: {non_zero.sum():.2f}")
        report_lines.append("")
    
    # 重点关注的异常事件
    report_lines.append("重点异常事件:")
    
    # TCP重传事件
    if 'node_netstat_Tcp_RetransSegs' in df.columns:
        retrans = df[df['node_netstat_Tcp_RetransSegs'] > 0]
        if len(retrans) > 0:
            report_lines.append(f"  TCP重传事件: {len(retrans)} 次")
            for _, row in retrans.iterrows():
                report_lines.append(f"    {row['timestamp']}: {row['node_netstat_Tcp_RetransSegs']:.2f} 次重传")
    
    # TCP超时事件
    if 'node_netstat_TcpExt_TCPTimeouts' in df.columns:
        timeouts = df[df['node_netstat_TcpExt_TCPTimeouts'] > 0]
        if len(timeouts) > 0:
            report_lines.append(f"  TCP超时事件: {len(timeouts)} 次")
    
    # TCP错误事件
    if 'node_netstat_Tcp_InErrs' in df.columns:
        errors = df[df['node_netstat_Tcp_InErrs'] > 0]
        if len(errors) > 0:
            report_lines.append(f"  TCP输入错误: {len(errors)} 次")
    
    # 保存报告
    report_file = "tcp_analysis_report.txt"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    
    print(f"✓ 分析报告已保存到: {report_file}")

if __name__ == "__main__":
    print("开始查询综合TCP数据...")
    
    # 1. 查询所有TCP数据
    all_data, successful_metrics, failed_metrics = query_comprehensive_tcp_data()
    
    # 2. 显示结果摘要
    print(f"\n{'='*60}")
    print(f"查询结果摘要:")
    print(f"  成功: {len(successful_metrics)} 个指标")
    print(f"  失败: {len(failed_metrics)} 个指标")
    
    if successful_metrics:
        print(f"\n成功获取的指标:")
        for metric, desc in successful_metrics:
            print(f"  ✓ {desc}")
    
    if failed_metrics:
        print(f"\n失败的指标:")
        for metric, desc in failed_metrics:
            print(f"  ✗ {desc}")
    
    # 3. 合并并保存数据
    if all_data:
        merged_df = merge_and_save_tcp_data(all_data, successful_metrics)
        
        print(f"\n{'='*60}")
        print("文件生成完成:")
        print("1. tcp_comprehensive_data.csv - 所有TCP指标的综合数据")
        print("2. tcp_*.csv - 各个指标的单独文件")
        print("3. tcp_analysis_report.txt - 数据分析报告")
    else:
        print("未能获取任何TCP数据")