import pandas as pd
from datetime import datetime

# 读取两个CSV文件
metric_df = pd.read_csv('metric.csv')
tcp_df = pd.read_csv('tcp_comprehensive_data.csv')

# 转换时间戳为datetime格式以便匹配
metric_df['timestamp'] = pd.to_datetime(metric_df['timestamp'])
tcp_df['timestamp'] = pd.to_datetime(tcp_df['timestamp'])

# 基于时间戳合并数据
merged_df = pd.merge(metric_df, tcp_df, on='timestamp', how='left')

# 保存合并后的数据
merged_df.to_csv('metric_with_tcp.csv', index=False)

print("已成功将TCP指标数据合并到metric.csv中")
print(f"原始metric数据行数: {len(metric_df)}")
print(f"TCP数据行数: {len(tcp_df)}")
print(f"合并后数据行数: {len(merged_df)}")
print(f"新增列数: {len(tcp_df.columns) - 1}")  # 减1是因为timestamp列重复

# 显示新增的TCP列名
tcp_columns = [col for col in tcp_df.columns if col != 'timestamp']
print("\n新增的TCP指标列:")
for col in tcp_columns:
    print(f"  - {col}")

# 检查合并后的数据结构
print(f"\n合并后数据总列数: {len(merged_df.columns)}")
print("\n前几行TCP数据示例:")
print(merged_df[['timestamp'] + tcp_columns[:5]].head())