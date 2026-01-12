import pandas as pd
import requests
import json
import time
import argparse
import sys
import os
from datetime import datetime
import signal

class PrometheusCollector:
    def __init__(self, prometheus_url="http://localhost:9090", metrics_file="/opt/exp/note/metric.csv"):
        self.prometheus_url = prometheus_url
        self.running = True
        
        # 检查指标文件是否存在
        if not os.path.exists(metrics_file):
            raise FileNotFoundError(f"指标文件不存在: {metrics_file}")
        
        try:
            # 读取指标文件
            self.metrics_df = pd.read_csv(metrics_file)
            print(f"原始指标文件包含 {len(self.metrics_df)} 行")
            
            # 过滤出有效的指标（排除分组标题行和空行）
            self.metrics_df = self.metrics_df[
                (self.metrics_df['name'].notna()) & 
                (self.metrics_df['query'].notna()) &
                (self.metrics_df['name'].str.len() > 0) &
                (self.metrics_df['query'].str.len() > 0) &
                (~self.metrics_df['name'].str.startswith('---', na=False))
            ].copy()
            
            print(f"过滤后加载了 {len(self.metrics_df)} 个有效监控指标")
            
            if len(self.metrics_df) == 0:
                raise ValueError("没有找到有效的监控指标")
                
        except Exception as e:
            print(f"读取指标文件失败: {e}")
            raise
        
    def query_prometheus(self, query):
        """查询Prometheus API"""
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={'query': query},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if data['status'] == 'success' and data['data']['result']:
                # 返回第一个结果的值
                value = data['data']['result'][0]['value'][1]
                # 确保返回的是数字类型
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return None
            else:
                return None
        except Exception as e:
            print(f"查询失败 '{query}': {e}")
            return None
    
    def collect_single_snapshot(self):
        """采集一次完整的指标快照"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        snapshot = {'timestamp': timestamp}
        
        for _, metric in self.metrics_df.iterrows():
            metric_name = str(metric['name']).strip()
            query = str(metric['query']).strip()
            
            value = self.query_prometheus(query)
            # 处理None值
            snapshot[metric_name] = value if value is not None else ""
            
        return snapshot
    
    def start_collection(self, output_file, label, interval=15):
        """开始数据采集"""
        print(f"开始采集数据，输出到: {output_file}")
        print(f"故障标签: {label}")
        print(f"采集间隔: {interval}秒")
        
        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        # 创建CSV文件并写入头部
        columns = ['timestamp', 'label'] + [str(name).strip() for name in self.metrics_df['name']]
        
        try:
            with open(output_file, 'w') as f:
                f.write(','.join(columns) + '\n')
        except Exception as e:
            print(f"创建输出文件失败: {e}")
            raise
        
        collection_count = 0
        
        while self.running:
            try:
                snapshot = self.collect_single_snapshot()
                snapshot['label'] = label
                
                # 追加到CSV文件
                with open(output_file, 'a') as f:
                    row = []
                    for col in columns:
                        value = snapshot.get(col, '')
                        # 确保值是字符串类型，处理特殊情况
                        if value is None:
                            row.append('')
                        elif isinstance(value, (int, float)):
                            if str(value).lower() in ['nan', 'inf', '-inf']:
                                row.append('')
                            else:
                                row.append(str(value))
                        else:
                            row.append(str(value))
                    f.write(','.join(row) + '\n')
                
                collection_count += 1
                print(f"已采集 {collection_count} 条记录，时间: {snapshot['timestamp']}")
                
                time.sleep(interval)
                
            except KeyboardInterrupt:
                print("\n收到停止信号，正在退出...")
                break
            except Exception as e:
                print(f"采集过程中出错: {e}")
                time.sleep(interval)
        
        print(f"数据采集完成，共采集 {collection_count} 条记录")

def signal_handler(signum, frame):
    print(f"\n收到信号 {signum}，准备停止采集...")
    global collector
    if collector:
        collector.running = False

def main():
    global collector
    collector = None
    
    parser = argparse.ArgumentParser(description='Prometheus指标采集工具')
    parser.add_argument('--output', required=True, help='输出CSV文件路径')
    parser.add_argument('--label', required=True, help='故障标签')
    parser.add_argument('--interval', type=int, default=15, help='采集间隔（秒）')
    parser.add_argument('--prometheus-url', default='http://localhost:9090', help='Prometheus URL')
    parser.add_argument('--metrics-file', default='/opt/exp/note/metric.csv', help='指标配置文件')
    
    args = parser.parse_args()
    
    # 注册信号处理器
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # 测试Prometheus连接
        test_response = requests.get(f"{args.prometheus_url}/api/v1/query?query=up", timeout=5)
        if test_response.status_code != 200:
            raise Exception(f"无法连接到Prometheus: {args.prometheus_url}")
        
        collector = PrometheusCollector(args.prometheus_url, args.metrics_file)
        collector.start_collection(args.output, args.label, args.interval)
        
    except FileNotFoundError as e:
        print(f"文件未找到: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"采集器启动失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()