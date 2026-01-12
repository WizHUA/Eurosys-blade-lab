import pandas as pd
import json
import argparse
import os
from datetime import datetime
import glob

class ExperimentProcessor:
    def __init__(self, raw_data_dir, job_info_file, output_file):
        self.raw_data_dir = raw_data_dir
        self.job_info_file = job_info_file
        self.output_file = output_file
        
        # 加载作业信息
        self.job_info = pd.read_csv(job_info_file, sep='|')
        self.job_info['Start'] = pd.to_datetime(self.job_info['Start'])
        self.job_info['End'] = pd.to_datetime(self.job_info['End'])
        
    def get_job_info_for_timestamp(self, timestamp):
        """根据时间戳获取当时运行的作业信息"""
        ts = pd.to_datetime(timestamp)
        
        # 查找该时间点正在运行的作业
        running_jobs = self.job_info[
            (self.job_info['Start'] <= ts) & 
            (self.job_info['End'] >= ts)
        ]
        
        if len(running_jobs) > 0:
            job = running_jobs.iloc[0]
            return {
                'job_id': int(job['JobID']),
                'node_list': job['NodeList'].split(','),
                'tag': job['Tag']
            }
        else:
            return {
                'job_id': None,
                'node_list': [],
                'tag': 'no_job'
            }
    
    def determine_anomaly_phase(self, df, experiment_start_time):
        """确定每个时间点的异常阶段"""
        df['timestamp_dt'] = pd.to_datetime(df['timestamp'])
        start_time = pd.to_datetime(experiment_start_time)
        
        # 计算相对时间（分钟）
        df['relative_time'] = (df['timestamp_dt'] - start_time).dt.total_seconds() / 60
        
        def classify_phase(relative_time, original_label):
            if relative_time < 3:  # 前3分钟
                return 'normal'
            elif 3 <= relative_time < 8:  # 3-8分钟：故障期
                return original_label
            else:  # 8分钟后：恢复期
                return 'recovery'
        
        df['ground_truth_anomaly'] = df.apply(
            lambda row: classify_phase(row['relative_time'], row['label']), 
            axis=1
        )
        
        return df
    
    def process_single_file(self, csv_file):
        """处理单个原始数据文件"""
        print(f"处理文件: {csv_file}")
        
        try:
            df = pd.read_csv(csv_file)
            
            if len(df) == 0:
                print(f"警告: 文件 {csv_file} 为空")
                return []
            
            # 获取实验开始时间（第一条记录的时间）
            experiment_start_time = df.iloc[0]['timestamp']
            
            # 确定异常阶段
            df = self.determine_anomaly_phase(df, experiment_start_time)
            
            records = []
            
            for _, row in df.iterrows():
                # 获取作业信息
                job_info = self.get_job_info_for_timestamp(row['timestamp'])
                
                # 构造指标字典
                metrics = {}
                for col in df.columns:
                    if col not in ['timestamp', 'label', 'timestamp_dt', 'relative_time', 'ground_truth_anomaly']:
                        value = row[col]
                        # 处理NaN值
                        if pd.isna(value):
                            metrics[col] = None
                        else:
                            metrics[col] = float(value)
                
                # 构造最终记录
                record = {
                    'timestamp': row['timestamp'],
                    'metrics': metrics,
                    'job_info': job_info,
                    'ground_truth_anomaly': row['ground_truth_anomaly'],
                    'experiment_file': os.path.basename(csv_file)
                }
                
                records.append(record)
            
            print(f"从 {csv_file} 处理了 {len(records)} 条记录")
            return records
            
        except Exception as e:
            print(f"处理文件 {csv_file} 时出错: {e}")
            return []
    
    def process_all_files(self):
        """处理所有原始数据文件"""
        csv_files = glob.glob(os.path.join(self.raw_data_dir, "*.csv"))
        
        if not csv_files:
            print(f"在 {self.raw_data_dir} 中未找到CSV文件")
            return
        
        print(f"找到 {len(csv_files)} 个原始数据文件")
        
        all_records = []
        
        for csv_file in csv_files:
            records = self.process_single_file(csv_file)
            all_records.extend(records)
        
        print(f"总共处理了 {len(all_records)} 条记录")
        
        # 保存到JSONL文件
        with open(self.output_file, 'w') as f:
            for record in all_records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        
        print(f"数据已保存到: {self.output_file}")
        
        # 打印统计信息
        self.print_statistics(all_records)
    
    def print_statistics(self, records):
        """打印数据集统计信息"""
        print("\n=== 数据集统计 ===")
        
        # 按异常类型统计
        anomaly_counts = {}
        for record in records:
            anomaly = record['ground_truth_anomaly']
            anomaly_counts[anomaly] = anomaly_counts.get(anomaly, 0) + 1
        
        print("按异常类型统计:")
        for anomaly, count in sorted(anomaly_counts.items()):
            print(f"  {anomaly}: {count} 条")
        
        # 按作业统计
        job_counts = {}
        for record in records:
            job_id = record['job_info']['job_id']
            job_counts[job_id] = job_counts.get(job_id, 0) + 1
        
        print("\n按作业ID统计:")
        for job_id, count in sorted(job_counts.items()):
            print(f"  Job {job_id}: {count} 条")

def main():
    parser = argparse.ArgumentParser(description='处理实验原始数据')
    parser.add_argument('--raw-data-dir', required=True, help='原始数据目录')
    parser.add_argument('--job-info-file', default='/opt/exp/data/ref/Job', help='作业信息文件')
    parser.add_argument('--output', required=True, help='输出JSONL文件路径')
    
    args = parser.parse_args()
    
    processor = ExperimentProcessor(
        args.raw_data_dir,
        args.job_info_file,
        args.output
    )
    
    processor.process_all_files()

if __name__ == "__main__":
    main()