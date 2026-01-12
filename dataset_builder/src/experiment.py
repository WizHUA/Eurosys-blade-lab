import os
import sys
import yaml
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
import signal
import traceback
import threading
from time import sleep

from monitor import PrometheusMonitor
from chaos import ChaosManager
from workload import WorkloadManager

def greenPrint(text: str):
    print(f"\033[92m{text}\033[0m")

def redPrint(text: str):
    print(f"\033[91m{text}\033[0m")

def bluePrint(text: str):
    print(f"\033[94m{text}\033[0m")

def yellowPrint(text: str):
    print(f"\033[93m{text}\033[0m")

class ExperimentManager:

    def __init__(self, config_path: str = "config/experiments.yaml"):
        self.config = self._load_config(config_path)
        self.global_settings = self.config.get("global_settings", {})

        self.monitor = None
        self.chaos = None
        self.workload = None # 后续在initial

        self.current_experiment = None
        self.experiment_start_time = None
        self.is_running = False

        # 静态
        # 设置日志
        self._setup_logging()
        self._setup_signal_handlers()
        self.logger = logging.getLogger(__name__)

        # 共享变量
        self.shared_data = {"expect_end_time": None}
        
    def _load_config(self, config_path: str):
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
        
    def _setup_logging(self):
        """设置全局日志配置"""
        log_level = self.global_settings.get('log_level', 'INFO')
        
        # 创建logs目录
        os.makedirs('logs', exist_ok=True)
        
        # 配置根日志记录器
        logging.basicConfig(
            level=getattr(logging, log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('logs/experiment_controller.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        
        # 为各组件设置独立的日志文件
        self._setup_component_logging()
    
    def _setup_component_logging(self):
        """为各组件设置独立的日志文件"""
        component_loggers = {
            'monitor': 'logs/monitor.log',
            'workload': 'logs/workload.log', 
            'chaos': 'logs/chaos.log'
        }
        
        for component, log_file in component_loggers.items():
            logger = logging.getLogger(component)
            logger.setLevel(logging.INFO)
            
            # 清除默认处理器，避免重复输出
            logger.handlers.clear()
            logger.propagate = False
            
            # 添加文件处理器
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ))
            logger.addHandler(file_handler)
    
    def _setup_signal_handlers(self):
        """设置信号处理器，用于优雅关闭"""
        def signal_handler(signum, frame):
            yellowPrint(f"\nReceived signal {signum}, initiating graceful shutdown...")
            try:
                self._clean_all()
            except Exception as e:
                redPrint(f"Error during clean up: {e}")
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _initialize_components(self, experiment_config: Dict):
        component_configs = experiment_config["component_configs"]
        greenPrint("Initializing components...")
        self.monitor = PrometheusMonitor(component_configs['monitor'])
        bluePrint("Monitor component initialized")
        self.chaos = ChaosManager(component_configs['chaos'])
        bluePrint("Chaos component initialized")
        self.workload = WorkloadManager(component_configs['workload'])
        bluePrint("Workload component initialized")
    
    def _validate_environment(self) -> bool:
        greenPrint("Validating experiment environment...")
        try:
            # 检查监控系统
            if not self.monitor.check_connectivity():
                redPrint("Prometheus monitoring system not available")
                return False
            # 检查作业调度系统  
            if not self.workload.check_slurm_status():
                redPrint("SLURM workload system not available")
                return False
            # 检查故障注入工具

            # 程序写得太屎了，每次手动check吧，不然有bug
            # self.chaos.check_fault_types() #! todo
            
            greenPrint("Environment validation completed")
            return True
            
        except Exception as e:
            redPrint(f"Environment validation failed: {e}")
            return False

    def run(self, experiment_name: str):
        # 配置文件写得太拉了所以这样找
        experiment_config = None
        for exp in self.config['experiments']:
            if exp['name'] == experiment_name:
                experiment_config = exp
                break
        
        if not experiment_config:
            redPrint(f"Experiment '{experiment_name}' not found in configuration")
            return False
        
        self.current_experiment = experiment_config
        experiment_start = datetime.now()
        self.experiment_start_time = experiment_start
        self.is_running = True

        output_dir = experiment_config["output_dir"]
        os.makedirs(output_dir, exist_ok=True)

        greenPrint("="*80)
        greenPrint(f"   Starting Experiment: {experiment_name}")
        greenPrint(f"   Description: {experiment_config['description']}")
        greenPrint(f"   Output Directory: {output_dir}")
        greenPrint(f"   Run function Start Time: {experiment_start.strftime('%Y-%m-%d %H:%M:%S')}")
        greenPrint("="*80)

        # initialize 
        self._initialize_components(experiment_config)
        
        # check
        if not self._validate_environment():
            redPrint("Experiment environment validation failed. Aborting.")
            raise RuntimeError("Environment validation failed")
        
        # execute 并且监视 workload和chaos结束
        success = self._execute_experiment_phases(experiment_config)

        greenPrint("="*80)
        greenPrint(f"   Result:")
        if success:
            greenPrint(f"Experiment completed successfully")
            greenPrint("="*80)
            return True
        else:
            redPrint(f"Experiment failed")
            greenPrint("="*80)
            return False

    def _execute_experiment_phases(self, experiment_config: dict) -> bool:
        experiment_plan = experiment_config['experiment_plan']
        monitoring_config = experiment_config['monitoring']
        output_dir = experiment_config['output_dir']
        durations = experiment_plan['duration']
        
        timeline_start = datetime.now()
        self.experiment_start_time = timeline_start

        greenPrint(f"Timeline Start: {timeline_start.strftime('%Y-%m-%d %H:%M:%S')}")

        total_duration = sum(durations.values())
        """
        e.g.
        duration:
            preparation: xx
            baseline: xx
            recovery: xx
            cleanup: xx
        """
        # todo

        bluePrint("\nStarting workload and chaos plan injecting")

        # 等待一下线程创建的过程，然后开始计时，便于后续时间对齐
        timeline_start += timedelta(seconds=durations['preparation'])

        workload_thread = threading.Thread(
            target=self._execute_workload_plan,
            args=(experiment_plan, timeline_start)
        )
        workload_thread.start()

        chaos_thread = threading.Thread(
            target=self._execute_chaos_plan,
            args=(experiment_plan, timeline_start)
        )
        chaos_thread.start()

        workload_thread.join()
        chaos_thread.join()
        
        # 预留异常注入结束时间
        expect_end_time = self.shared_data.get('expect_end_time') + timedelta(seconds=durations['recovery'])

        sleep_time = (expect_end_time - datetime.now()).total_seconds()
        # 等待异常注入结束
        sleep(sleep_time)
        bluePrint("Chaos expected end time reached")

        # 取消负载
        self.workload.cancel_all_jobs()
        bluePrint("All workload jobs cancelled")

        # 清理所有资源
        self._clean_all()
        self._export_all()

        return True

    def _execute_workload_plan(self, experiment_plan: dict, timeline_start: datetime):
        # todo ： 时间未手动对齐
        workload_plan = experiment_plan['workload_plan']
        bluePrint(f"Workload plan: {workload_plan}")
        start = datetime.now()
        self.workload.execute_workload_plan(workload_plan, start)
        self.logger.info(f"Workload timeline execution completed for plan: {workload_plan}")
        return

    def _execute_chaos_plan(self, experiment_plan: dict, timeline_start: datetime):
        # todo ： 时间未手动对齐
        chaos_plan = experiment_plan['chaos_plan']
        bluePrint(f"Chaos plan: {chaos_plan}")
        expect_end_time = self.chaos.execute_chaos_plan(chaos_plan)
        self.logger.info(f"Chaos timeline execution completed for plan: {chaos_plan}")
        self.shared_data['expect_end_time'] = expect_end_time
        pass

    def _clean_all(self):
        bluePrint("Cleaning up all resources...")
        self.chaos.clean_up()
        self.workload.cancel_all_jobs()
        bluePrint("All resources cleaned up.")

    def _export_all(self):
        # 异常，性能监控，作业
        start_time = self.experiment_start_time
        end_time = datetime.now()
        bluePrint("Exporting all data...")
        
        bluePrint("Exporting monitored data...")
        monitor_path = os.path.join(self.current_experiment['output_dir'], 'metric.csv')
        self.monitor.export_monitored_data(
            start_time=start_time, 
            end_time=end_time, 
            output_path=monitor_path)

        bluePrint("Exporting workload data...")
        workload_path = os.path.join(self.current_experiment['output_dir'], 'jobinfo.csv')
        self.workload.export_job_info(
            start_time=start_time, 
            end_time=end_time, 
            output_path=workload_path)

        bluePrint("Exporting chaos data...")
        chaos_path = os.path.join(self.current_experiment['output_dir'], 'chaos.csv')
        self.chaos.export_success_faults(outpath=chaos_path)


def main():
    redPrint("Note: You should run this after check your all chaos to inject")
    config_path = "config/experiments.yaml"
    a = ExperimentManager()
    a.run("compositetest")

if __name__ == "__main__":
    main()

