import os
import sys
import yaml
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
import signal
import traceback

from monitor import PrometheusMonitor
from workload import WorkloadManager
from chaos import ChaosManager

def greenPrint(text: str):
    print(f"\033[92m{text}\033[0m")

def redPrint(text: str):
    print(f"\033[91m{text}\033[0m")

def bluePrint(text: str):
    print(f"\033[94m{text}\033[0m")

def yellowPrint(text: str):
    print(f"\033[93m{text}\033[0m")

class ExperimentController:
    """实验总控制器"""
    
    def __init__(self, config_path: str = "config/experiments.yaml"):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.global_settings = self.config.get('global_settings', {})
        
        # 组件实例
        self.monitor = None
        self.workload = None 
        self.chaos = None
        
        # 实验状态
        self.current_experiment = None
        self.experiment_start_time = None
        self.is_running = False
        
        # 设置日志
        self._setup_logging()
        
        # 注册信号处理器
        self._setup_signal_handlers()
        
        self.logger = logging.getLogger(__name__)
    
    def _load_config(self, config_path: str) -> Dict:
        """加载实验配置文件"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            redPrint(f"Failed to load experiment config: {e}")
            raise
    
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
            self._emergency_cleanup()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def _initialize_components(self, experiment_config: Dict):
        """根据实验配置初始化组件"""
        component_configs = experiment_config['component_configs']
        
        try:
            greenPrint("Initializing experiment components...")
            
            # 初始化监控组件
            self.monitor = PrometheusMonitor(component_configs['monitor'])
            bluePrint("  ✅ Monitor component initialized")
            
            # 初始化负载管理组件
            self.workload = WorkloadManager(component_configs['workload'])
            bluePrint("  ✅ Workload component initialized")
            
            # 初始化混沌工程组件
            self.chaos = ChaosManager(component_configs['chaos'])
            bluePrint("  ✅ Chaos component initialized")
            
            greenPrint("All components initialized successfully")
            
        except Exception as e:
            redPrint(f"Failed to initialize components: {e}")
            raise
    
    def _validate_environment(self) -> bool:
        """验证实验环境"""
        greenPrint("Validating experiment environment...")
        
        try:
            # 检查监控系统
            if not self.monitor.check_connectivity():
                redPrint("❌ Prometheus monitoring system not available")
                return False
            
            # 检查作业调度系统  
            if not self.workload.check_slurm_status():
                redPrint("❌ SLURM workload system not available")
                return False
            
            # 检查故障注入工具
            self.chaos.check_fault_types()
            
            greenPrint("✅ Environment validation completed")
            return True
            
        except Exception as e:
            redPrint(f"Environment validation failed: {e}")
            return False
    
    def run_experiment(self, experiment_name: str):
        """运行指定的实验"""
        # 查找实验配置
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
        
        # 创建输出目录
        output_dir = experiment_config['output_dir']
        os.makedirs(output_dir, exist_ok=True)
        
        greenPrint("="*80)
        greenPrint(f"🚀 Starting Experiment: {experiment_name}")
        greenPrint(f"   Description: {experiment_config['description']}")
        greenPrint(f"   Output Directory: {output_dir}")
        greenPrint(f"   Start Time: {experiment_start.strftime('%Y-%m-%d %H:%M:%S')}")
        greenPrint("="*80)
        
        try:
            # 初始化组件
            self._initialize_components(experiment_config)
            
            # 验证环境
            if not self._validate_environment():
                raise RuntimeError("Environment validation failed")
            
            # 执行实验流程
            success = self._execute_experiment_phases(experiment_config)
            
            if success:
                greenPrint("✅ Experiment completed successfully")
                return True
            else:
                redPrint("❌ Experiment failed")
                return False
                
        except Exception as e:
            redPrint(f"❌ Experiment failed with exception: {e}")
            self.logger.error(f"Experiment failed: {traceback.format_exc()}")
            
            if self.global_settings.get('cleanup_on_error', True):
                self._emergency_cleanup()
            
            return False
        
        finally:
            self.is_running = False
            self.current_experiment = None
    
    def _execute_experiment_phases(self, experiment_config: Dict) -> bool:
        """执行实验的各个阶段"""
        experiment_plan = experiment_config['experiment_plan']
        monitoring_config = experiment_config['monitoring']
        output_dir = experiment_config['output_dir']
        durations = experiment_plan['duration']
        
        try:
            # ===== 阶段1: 准备阶段 =====
            greenPrint("\n📋 Phase 1: Preparation")
            self._phase_preparation(experiment_plan, durations['preparation'])
            
            # ===== 阶段2: 基线期 =====
            greenPrint("\n📊 Phase 2: Baseline Monitoring")
            baseline_start = datetime.now()
            self._phase_baseline(monitoring_config, durations['baseline'])
            
            # ===== 阶段3: 故障注入期 =====
            greenPrint("\n💥 Phase 3: Chaos Injection")
            chaos_start = datetime.now()
            chaos_end_time = self._phase_chaos_injection(experiment_plan, chaos_start)
            
            # ===== 阶段4: 恢复期 =====
            greenPrint("\n🔄 Phase 4: Recovery Monitoring")
            recovery_start = datetime.now()
            self._phase_recovery(monitoring_config, durations['recovery'])
            
            # ===== 阶段5: 数据收集和清理 =====
            greenPrint("\n📁 Phase 5: Data Collection & Cleanup")
            self._phase_data_collection(output_dir, baseline_start, recovery_start + timedelta(seconds=durations['recovery']))
            
            self._phase_cleanup(durations['cleanup'])
            
            return True
            
        except Exception as e:
            redPrint(f"Experiment phase failed: {e}")
            self.logger.error(f"Experiment phase failed: {traceback.format_exc()}")
            return False
    
    def _phase_preparation(self, experiment_plan: Dict, duration: int):
        """准备阶段：启动背景负载"""
        bluePrint(f"  Starting background workload: {experiment_plan['workload_plan']}")
        
        self.workload.execute_workload_plan(
            experiment_plan['workload_plan'], 
            self.experiment_start_time
        )
        
        bluePrint(f"  Waiting {duration}s for workload to stabilize...")
        time.sleep(duration)
    
    def _phase_baseline(self, monitoring_config: Dict, duration: int):
        """基线期：开始监控收集基线数据"""
        bluePrint(f"  Starting monitoring (interval: {monitoring_config['interval']}s)")
        
        self.monitor.start_realtime_monitoring(monitoring_config['interval'])
        
        bluePrint(f"  Collecting baseline data for {duration}s...")
        time.sleep(duration)
    
    def _phase_chaos_injection(self, experiment_plan: Dict, chaos_start: datetime):
        """故障注入期：执行混沌实验"""
        chaos_plan = experiment_plan['chaos_plan']
        bluePrint(f"  Executing chaos plan: {chaos_plan}")
        
        # 执行混沌计划，返回预期结束时间
        chaos_end_time = self.chaos.execute_chaos_plan(chaos_plan, chaos_start)
        
        if chaos_end_time:
            # 等待所有故障结束
            remaining_time = (chaos_end_time - datetime.now()).total_seconds()
            if remaining_time > 0:
                bluePrint(f"  Waiting {remaining_time:.0f}s for chaos to complete...")
                time.sleep(remaining_time + 5)  # 额外等待5秒确保完全结束
        
        return chaos_end_time
    
    def _phase_recovery(self, monitoring_config: Dict, duration: int):
        """恢复期：继续监控系统恢复"""
        bluePrint(f"  Monitoring system recovery for {duration}s...")
        time.sleep(duration)
    
    def _phase_data_collection(self, output_dir: str, data_start: datetime, data_end: datetime):
        """数据收集阶段"""
        bluePrint("  Stopping real-time monitoring...")
        self.monitor.stop_realtime_monitoring()
        
        bluePrint("  Collecting and exporting data...")
        
        # 导出监控数据
        metrics_file = os.path.join(output_dir, "metrics.csv")
        self.monitor.export_monitored_data(
            data_start, data_end, 
            output_path=metrics_file,
            step=self.current_experiment['monitoring']['step']
        )
        
        # 导出作业信息
        jobs_file = os.path.join(output_dir, "jobs.csv")
        self.workload.export_job_info(
            output_path=jobs_file,
            start_time=self.experiment_start_time,
            end_time=data_end
        )
        
        # 导出故障信息
        chaos_file = os.path.join(output_dir, "chaos.csv")
        self.chaos.export_success_faults(chaos_file)
        
        # 导出实验配置
        config_file = os.path.join(output_dir, "experiment_config.yaml")
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(self.current_experiment, f, default_flow_style=False, allow_unicode=True)
        
        greenPrint(f"  📊 Data exported to {output_dir}/")
        greenPrint(f"     - Metrics: {metrics_file}")
        greenPrint(f"     - Jobs: {jobs_file}")
        greenPrint(f"     - Chaos: {chaos_file}")
        greenPrint(f"     - Config: {config_file}")
    
    def _phase_cleanup(self, duration: int):
        """清理阶段"""
        bluePrint("  Cleaning up experiment resources...")
        
        # 清理故障注入
        if self.chaos:
            self.chaos.clean_up()
        
        # 清理作业
        if self.workload:
            self.workload.cleanup()
        
        bluePrint(f"  Final cleanup wait: {duration}s...")
        time.sleep(duration)
        
        greenPrint("✅ Cleanup completed")
    
    def _emergency_cleanup(self):
        """紧急清理：在异常情况下清理所有资源"""
        yellowPrint("🚨 Performing emergency cleanup...")
        
        try:
            if self.monitor and self.monitor._monitoring:
                self.monitor.stop_realtime_monitoring()
            
            if self.chaos:
                self.chaos.destory_all_faults()
            
            if self.workload:
                self.workload.cancel_all_jobs()
                
            greenPrint("✅ Emergency cleanup completed")
            
        except Exception as e:
            redPrint(f"Emergency cleanup failed: {e}")
    
    def run_experiment_suite(self, experiment_names: Optional[list] = None):
        """运行实验套件"""
        if experiment_names is None:
            experiment_names = [exp['name'] for exp in self.config['experiments']]
        
        greenPrint(f"🎯 Running experiment suite: {len(experiment_names)} experiments")
        
        results = {}
        
        for i, exp_name in enumerate(experiment_names, 1):
            greenPrint(f"\n{'='*20} Experiment {i}/{len(experiment_names)} {'='*20}")
            
            success = self.run_experiment(exp_name)
            results[exp_name] = success
            
            if success:
                greenPrint(f"✅ Experiment '{exp_name}' completed successfully")
            else:
                redPrint(f"❌ Experiment '{exp_name}' failed")
            
            # 实验间休息时间
            if i < len(experiment_names):
                rest_time = 60  # 60秒间隔
                bluePrint(f"   Resting {rest_time}s before next experiment...")
                time.sleep(rest_time)
        
        # 输出总结
        greenPrint("\n" + "="*80)
        greenPrint("🎯 Experiment Suite Summary")
        greenPrint("="*80)
        
        successful = sum(results.values())
        total = len(results)
        
        for exp_name, success in results.items():
            status = "✅ SUCCESS" if success else "❌ FAILED"
            print(f"  {exp_name:<30} {status}")
        
        greenPrint(f"\nOverall: {successful}/{total} experiments successful")
        
        return results

# 主程序入口
def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='HPC Anomaly Detection Dataset Builder')
    parser.add_argument('--config', '-c', default='config/experiments.yaml',
                       help='Path to experiment configuration file')
    parser.add_argument('--experiment', '-e', 
                       help='Name of specific experiment to run')
    parser.add_argument('--list', '-l', action='store_true',
                       help='List available experiments')
    parser.add_argument('--all', '-a', action='store_true',
                       help='Run all experiments')
    
    args = parser.parse_args()
    
    try:
        controller = ExperimentController(args.config)
        
        if args.list:
            # 列出所有可用实验
            greenPrint("Available experiments:")
            for exp in controller.config['experiments']:
                print(f"  - {exp['name']}: {exp['description']}")
            return
        
        if args.experiment:
            # 运行指定实验
            success = controller.run_experiment(args.experiment)
            sys.exit(0 if success else 1)
        
        elif args.all:
            # 运行所有实验
            results = controller.run_experiment_suite()
            success_count = sum(results.values())
            sys.exit(0 if success_count == len(results) else 1)
        
        else:
            parser.print_help()
    
    except Exception as e:
        redPrint(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()