import requests
import yaml
import pandas as pd
import time
from datetime import datetime, timedelta
import logging
from typing import Optional
import threading
import queue

RUNTAG = True

def greenPrint(text: str):
    if RUNTAG:
        return
    print(f"\033[92m{text}\033[0m")

def redPrint(text: str):
    if RUNTAG:
        return
    print(f"\033[91m{text}\033[0m")

class PrometheusMonitor:
    
    def __init__(self, config_path: str = "config/metrics.yaml"):
        self.config = self._load_config(config_path)
        self.prometheus_url = self.config['config']['prometheus_url']
        self.default_interval = self.config['config']['default_interval']
        self.query_timeout = self.config['config']['query_timeout']
        # 监控指标
        self.metrics = self._extract_metrics()
        self.logger = logging.getLogger(__name__)
        self._monitoring = False
        self._monitoring_thread = None
        self._data_queue = queue.Queue()
        
    def _load_config(self, config_path: str) -> dict:
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)

    def _extract_metrics(self) -> dict[str, dict]:
        metrics = {}
        for category, metric_group in self.config.items():
            if category == 'config':
                continue

            for metric_name, metric_info in metric_group.items():
                metrics[metric_name] = metric_info
            
            for metric_name, metric_info in metric_group.items():
                metrics[metric_name] = {
                    "category": category,
                    "description": metric_info.get("description", ""),
                    "query": metric_info.get("query", ""),
                }
        return metrics
    
    def check_connectivity(self) -> bool:
        # 用绿色字输出函数提示
        greenPrint("Checking connectivity to Prometheus server...")
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query", 
                params={"query": "up"},
                timeout=self.query_timeout
            )
            if response.status_code == 200:
                greenPrint("Successfully connected to Prometheus server.")
                return True
            else:
                redPrint(f"Failed to connect to Prometheus server. Status code: {response.status_code}")
                return False
        except Exception as e:
            redPrint(f"Failed to connect to Prometheus server: {e}")
            return False
    
    def check_metrics(self) -> dict[str, bool]:
        greenPrint("Checking availability of configured metrics...")
        results = {}
        for metric_name, metric_info in self.metrics.items():
            query = metric_info['query']
            try:
                response = requests.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={"query": query},
                    timeout=self.query_timeout
                )
                if response.status_code == 200:
                    data = response.json()
                    print(response.json())
                    if data['status'] == 'success' and data['data']['result']:
                        greenPrint(f"Metric '{metric_name}' is available.")
                        results[metric_name] = True
                    else:
                        redPrint(f"Metric '{metric_name}' is not available or returned no data.")
                        results[metric_name] = False
                else:
                    redPrint(f"Failed to query metric '{metric_name}'. Status code: {response.status_code}")
                    results[metric_name] = False
            except Exception as e:
                redPrint(f"Error querying metric '{metric_name}': {e}")
                results[metric_name] = False
        return results

    def query_instant(self, metric_name: str, timestamp: Optional[datetime] = None) -> Optional[float]:
        if metric_name not in self.metrics:
            redPrint(f"Metric '{metric_name}' is not configured.")
            raise ValueError(f"Metric '{metric_name}' is not configured.")
        query = self.metrics[metric_name]['query']
        params = {"query": query}

        if timestamp:
            params["time"] = timestamp.timestamp()
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params=params,
                timeout=self.query_timeout
            )
            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success' and data['data']['result']:
                    value = float(data['data']['result'][0]['value'][1])
                    return value
                else:
                    redPrint(f"No data returned for metric '{metric_name}' at the specified time.")
                    return None
        except Exception as e:
            redPrint(f"Error querying metric '{metric_name}': {e}")
            return None
    
    def query_range(self, metric_name: str, start_time: datetime, 
                    end_time: datetime, step: str = '10s') -> pd.DataFrame:
        if metric_name not in self.metrics:
            redPrint(f"Metric '{metric_name}' is not configured.")
            raise ValueError(f"Metric '{metric_name}' is not configured.")
        query = self.metrics[metric_name]['query']
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query_range",
                params={
                    "query": query,
                    "start": start_time.timestamp(),
                    "end": end_time.timestamp(),
                    "step": step
                },
                timeout=self.query_timeout
            )
            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success' and data['data']['result']:
                    # 转换为DataFrame
                    result = data['data']['result'][0]
                    df = pd.DataFrame(result['values'], columns=['timestamp', metric_name])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
                    df[metric_name] = df[metric_name].astype(float)
                    return df
            return pd.DataFrame()
        except Exception as e:
            redPrint(f"Error querying metric '{metric_name}' over range: {e}")
            return pd.DataFrame()
        
    def collect_all_metrics(self, timestamp: Optional[datetime] = None) -> dict[str, float]:
        results = {}
        for metric_name in self.metrics.keys():
            value = self.query_instant(metric_name, timestamp)
            results[metric_name] = value if value is not None else float('nan')
        return results

    def collet_all_metrics_range(self, start_time: datetime, 
                              end_time: datetime, step: str = '10s') -> pd.DataFrame:
        all_data = []
        for metric_name in self.metrics.keys():
            df = self.query_range(metric_name, start_time, end_time, step)
            if not df.empty:
                all_data.append(df)
        
        if all_data:
            result_df = all_data[0]
            for df in all_data[1:]:
                result_df = pd.merge(result_df, df, on='timestamp', how='outer')
            result_df.sort_values('timestamp', inplace=True)
            return result_df
        else:
            return pd.DataFrame()
    
    # 格式化输出查询结果
    def _Print_result_for_test(self):
        start_time = datetime.now() - timedelta(minutes=1)
        end_time = datetime.now()
        step = "3s"
        results = self.collet_all_metrics_range(start_time, end_time, step)
        # 格式化保存到./temp/monitor.tmp种
        results.to_csv("./temp/monitor.tmp", index=False)


    """以下为实时监控"""
    def start_realtime_monitoring(self, interval: Optional[int] = None):
        if self._monitoring:
            self.logger.warning("monitoring is already running.")
            return

        interval = interval or self.default_interval

        self._monitoring = True
        self._monitoring_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval)
        )
        self._monitoring_thread.start()
        self.logger.info("Started real-time monitoring.")
    
    def _monitor_loop(self, interval: int):
        while self._monitoring:
            timastamp = datetime.now()
            data_point = {"timestamp": timastamp}

            for metric_name in self.metrics.keys():
                value = self.query_instant(metric_name)
                data_point[metric_name] = value if value is not None else float('nan')
            
            self._data_queue.put(data_point)
            time.sleep(interval)
    
    def stop_realtime_monitoring(self):
        if not self._monitoring:
            self.logger.warning("Monitoring is not running.")
            return
        
        self._monitoring = False
        if self._monitoring_thread:
            self._monitoring_thread.join()
            self._monitoring_thread = None
        self.logger.info("Stopped real-time monitoring.")
    
    def get_collected_data(self) -> pd.DataFrame:
        data_points = []
        while not self._data_queue.empty():
            data_points.append(self._data_queue.get())
        
        if data_points:
            df = pd.DataFrame(data_points)
            # df.sort_values('timestamp', inplace=True)
            return df
        else:
            return pd.DataFrame()

    """以下为实时监控"""
    def export_monitored_data(self, start_time: datetime, end_time: datetime,
                              output_path: str = "temp/metrics.csv", step: str = "15s"):
        all_data = pd.DataFrame()
        greenPrint(f"Exporting monitored data \n \t from {start_time} to {end_time}...")

        for i, (metric_name, _) in enumerate(self.metrics.items()):
            print(f"Exporting metric {i+1:>2}/{len(self.metrics):<2}: {metric_name:<25}")
            df = self.query_range(metric_name, start_time, end_time, step)
            if not df.empty:
                if all_data.empty:
                    all_data = df
                else:
                    all_data = pd.merge(all_data, df, on='timestamp', how='outer')

        if not all_data.empty:
            # 这里的时间有8小时时差，都需要处理
            all_data['timestamp'] = pd.to_datetime(all_data['timestamp']) + pd.Timedelta(hours=8)
            # 将timestamp的格式固定为 "2025-09-17 08:18:59"，精确到秒即可
            all_data['timestamp'] = all_data['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
            all_data.sort_values('timestamp', inplace=True)
            all_data.to_csv(output_path, index=False)
            greenPrint(f"Export completed. Data saved to {output_path}")
        else:
            redPrint("No data collected for the specified time range.")
        

def test():
    """just check metrics"""
    a = PrometheusMonitor()
    a.check_connectivity()
    results = a.check_metrics()
    for metric, status in results.items():
        print(f"{metric}: {'Available' if status else 'Not Available'}")
    # 输出not available的指标
    not_available = [metric for metric, status in results.items() if not status]
    greenPrint(str(len(results)-len(not_available)) + " available metrics.")
    if not_available:
        redPrint(f"Not available metrics:")
        for metric in not_available:
            redPrint(f" - {metric}")

def main():
    a = PrometheusMonitor()
    end_time = datetime.now()
    start_time = end_time - timedelta(minutes=1)
    a.export_monitored_data(start_time, end_time, step="5s")

if __name__ == "__main__":
    test()