import subprocess
import yaml
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging
import os
import re
from time import sleep
import pandas as pd

# print(os.environ)

RUNTAG = True

def greenPrint(text: str):
    if RUNTAG:
        return
    print(f"\033[92m{text}\033[0m")

def redPrint(text: str):
    if RUNTAG:
        return
    print(f"\033[91m{text}\033[0m")

def bluePrint(text: str):
    if RUNTAG:
        return
    print(f"\033[94m{text}\033[0m")

def yellowPrint(text: str):
    if RUNTAG:
        return
    print(f"\033[93m{text}\033[0m")

class ChaosManager:

    def __init__(self, config_path: str = "config/chaos.yaml"):
        self.config = self._load_config(config_path)
        self.chaosblade_config = self.config["chaosblade"]
        self.fault_types = self.config["fault_types"]
        self.chaos_plans = self.config["chaos_plans"]

        # running state
        self.active_faults= {} # 指激活过的作业
        self.experiment_start_time = None
        self.inject_threads = [] 

        self.logger = logging.getLogger(__name__)
        self._setup_logging()

        self._validate_chaosblade()

    def _load_config(self, config_path: str) -> dict:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
        
    def _setup_logging(self):
        """设置日志"""
        log_config = self.config.get('logging', {})
        log_file = log_config.get('log_file', 'logs/chaos.log')
        
        # 确保日志目录存在
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        
        # 配置日志格式
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # 文件处理器
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        self.logger.setLevel(getattr(logging, log_config.get('level', 'INFO')))

    def _validate_chaosblade(self):
        greenPrint("Validating ChaosBlade avbailability...")
        cmd = [self.chaosblade_config["command"], 'version']
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if res.returncode == 0:
            version = res.stdout.strip()
            greenPrint(f"ChaosBlade is available. Version: {version}")
        else:
            redPrint(f"ChaosBlade validation failed: {res.stderr}")
            raise RuntimeError("ChaosBlade is not available. Please check the installation.")

    def check_fault_types(self) -> dict[str, bool]:
        greenPrint("Checking configured fault types...")
        results = {}

        for fault_name, fault_config in self.fault_types.items():
            try:
                bluePrint(f"  Testing fault type: {fault_name:<20} | {fault_config['description']}")
                test_params = fault_config['default_params'].copy()
                
                # 设置较短的测试时间 (5秒)
                test_params['timeout'] = 5
                # 对于某些故障类型，使用较温和的参数进行测试
                # 如果fault_name中包含"cpu"字样
                if 'cpu' in fault_name:
                    test_params['cpu-percent'] = 50  # 降低CPU占用
                elif 'mem' in fault_name:
                    test_params['mem-percent'] = 30  # 降低内存占用
                elif fault_name == 'disk_burn':
                    test_params['size'] = 100  # 降低磁盘负载
                elif 'network' in fault_name:
                    test_params['percent'] = 5  # 降低网络影响
                
                # 注入短时故障进行测试
                experiment_id = self.inject_fault(fault_name, custom_params=test_params)
                
                if experiment_id:
                    time.sleep(1)
                    
                    destroy_success = self.destroy_fault(experiment_id)
                    
                    if destroy_success:
                        results[fault_name] = True
                    else:
                        results[fault_name] = False
                else:
                    results[fault_name] = False
                    
            except Exception as e:
                results[fault_name] = False
                redPrint(f"Error testing fault type '{fault_name}': {e}")
                self.logger.error(f"Error testing fault type {fault_name}: {e}")
        
        # 统计结果
        available = sum(results.values())
        total = len(results)
        if available == total:
            greenPrint(f"All fault types available: {available}/{total}")
        else:
            yellowPrint(f"Fault types check: {available}/{total} available")
        
        return results
    
    def _build_fault_command(self, fault_type: str, custom_params: Optional[dict] = None) -> List[str]:
        if fault_type not in self.fault_types:
            raise ValueError(f"Unknow fault type: {fault_type}")
        fault_config = self.fault_types[fault_type]
        cmd = [self.chaosblade_config["command"], "create"]
        cmd.extend([fault_config['type'], fault_config['subtype']])
        # params
        params = fault_config['default_params'].copy()
        if custom_params:
            params.update(custom_params)
        
        for key, value in params.items():
            if isinstance(value, bool):
                if value:
                    cmd.append(f"--{key}")
            else:
                cmd.extend([f"--{key}", str(value)])
        
        return cmd
    
    def inject_fault(self, fault_type: str, inject_time: Optional[datetime] = None,
                     custom_params: Optional[dict] = None,
                     target_node: Optional[str] = None) -> Optional[str]:
        if fault_type not in self.fault_types:
            redPrint(f"Unknown fault type: {fault_type}")
            return None
        
        fault_config = self.fault_types[fault_type]

        if inject_time and inject_time > datetime.now():
            delay = (inject_time - datetime.now()).total_seconds()
            bluePrint(f"Waiting {delay:.1f}s to inject fault '{fault_type}'")
            time.sleep(delay)
        
        try:
            cmd = self._build_fault_command(fault_type, custom_params)
            bluePrint(f"Injecting fault: {fault_type}")
            self.logger.info(f"Executing: {' '.join(cmd)}")

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.chaosblade_config.get("timeout", 60),
                env=os.environ,
                # shell=True
            )

            if res.returncode == 0:
                experiment_id = self._extract_experiment_id(res.stdout)
                if experiment_id:
                    fault_info = {
                        'fault_type': fault_type,
                        'experiment_id': experiment_id,
                        'inject_time': datetime.now(),
                        'custom_params': custom_params or {}, #! custom but not all
                        'target_node': target_node,
                        'description': fault_config['description'],
                        'command': ' '.join(cmd),
                        'status': 'active'
                    }

                    self.active_faults[experiment_id] = fault_info

                    greenPrint(f"Fault injected successfully: {experiment_id:>15}")
                    self.logger.info(f"Fault injected: {fault_info}")
                    return experiment_id
                else:
                    redPrint(f"Failed to extract experiment ID from: {res.stdout}")
                    return None
            else:
                redPrint(f"Fault injection failed: {res.stderr}")
                self.logger.error(f"Fault injection failed: {res.stderr}")
                return None

        except Exception as e:
            redPrint(f"Exception during fault injection: {str(e)}")
            self.logger.error(f"Exception during fault injection: {str(e)}")
            return None

    def _extract_experiment_id(self, stdout: str) -> Optional[str]:
        """从ChaosBlade输出中提取实验ID"""
        try:
            # ChaosBlade返回JSON格式: {"code":200,"success":true,"result":"experiment_id"}
            data = json.loads(stdout.strip())
            
            if data.get('success') and data.get('result'):
                return data['result']
                
        except json.JSONDecodeError:
            raise ValueError("Failed to parse ChaosBlade output as JSON.")
        
        return None
    
    def _update_fault_status(self, experiment_id: str) -> bool:
        """检查并更新单个故障的状态"""
        if experiment_id not in self.active_faults:
            raise ValueError(f"Experiment ID not found: {experiment_id}")
        
        fault_info = self.active_faults[experiment_id]
        
        # 如果已经标记为非活跃状态，直接返回
        if fault_info['status'] != 'active':
            return False
        
        return self._verify_fault_active(experiment_id)

    def _verify_fault_active(self, experiment_id: str) -> bool:
        """通过ChaosBlade命令验证故障是否还在运行"""
        cmd = [self.chaosblade_config['command'], 'status', experiment_id]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            data = json.loads(res.stdout.strip())
            if data.get("success") and data.get("result"):
                status = data["result"].get("Status","")

                if status.lower() in ["success", "created", "running"]: # 可能的活跃状态
                    return True
                else:
                    self.active_faults[experiment_id]['status'] = status
                    return False
        else:
            raise RuntimeError(f"Failed to check status for {experiment_id}: {res.stderr}")

    def update_all_fault_status(self):
        """更新所有故障的状态"""
        active_before = {exp_id: info for exp_id, info in self.active_faults.items() 
             if info['status'] == 'active'}
        active_count_before = len(active_before)
        
        for experiment_id in list(self.active_faults.keys()):
            self._update_fault_status(experiment_id)
        
        active_after = {exp_id: info for exp_id, info in active_before.items() 
             if info['status'] == 'active'}
        active_count_after = len(active_after)
        
        if active_count_before != active_count_after:
            expired_count = active_count_before - active_count_after
            bluePrint(f"Updated fault status: {expired_count} faults marked as expired")
    
    def get_active_faults(self) -> Dict[str, dict]:
        greenPrint("-"*60)
        greenPrint("get active faults:")
        self.update_all_fault_status()
        active = {exp_id: info for exp_id, info in self.active_faults.items() 
             if info['status'] == 'active'}
        return active
    
    def destroy_fault(self, experiment_id: str) -> bool:
        if experiment_id not in self.active_faults:
            redPrint(f"Experiment ID not found: {experiment_id}")
            return False
        try:
            cmd = [self.chaosblade_config['command'], 'destroy', experiment_id]
            bluePrint(f"Destroying fault: {experiment_id}")
            self.logger.info(f"Destroying fault with command: {' '.join(cmd)}")

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.chaosblade_config.get("timeout", 60)
            )
            if res.returncode == 0:
                if experiment_id in self.active_faults:
                    self.active_faults[experiment_id]['status'] = "Destroyed"
                    self.active_faults[experiment_id]['destroy_time'] = datetime.now() # todo
                
                greenPrint(f"Fault destroyed successfully: {experiment_id:>15}")
                self.logger.info(f"Fault destroyed: {experiment_id}")
                return True
            else:
                redPrint(f"Failed to destroy fault: {res.stderr}")
                self.logger.error(f"Failed to destroy fault {experiment_id}: {res.stderr}")
                return False
            
        except Exception as e:
            redPrint(f"Exception during fault destruction: {str(e)}")
            self.logger.error(f"Exception during fault destruction: {str(e)}")
            return False
    
    def destory_all_faults(self):
        bluePrint("Destroying all active faults...")
        active_faults = self.get_active_faults()
        for experiment_id in list(active_faults.keys()):
            self.destroy_fault(experiment_id)
        greenPrint("All active faults destroyed.")

    def execute_chaos_plan(self, plan_name: str, experiment_start: Optional[datetime] = None):
        if plan_name not in self.chaos_plans:
            redPrint(f"Unknown chaos plan: {plan_name}")
            raise ValueError(f"Unknown chaos plan: {plan_name}")
        plan = self.chaos_plans[plan_name]
        self.experiment_start_time = experiment_start or datetime.now()
        bluePrint(f"Executing chaos plan: {plan_name}")

        inject_threads = []
        expect_end_time = None


        for i, fault_spec in enumerate(plan['faults']):
            inject_delay = fault_spec.get('inject_delay', 0)
            inject_time = self.experiment_start_time + timedelta(seconds=inject_delay)

            duration = fault_spec["duration"]
            end_time = inject_time + timedelta(seconds=duration)

            expect_end_time = max(expect_end_time, end_time) if expect_end_time else end_time

            thread = threading.Thread(
                target = self._delayed_inject,
                args = (fault_spec, inject_time, i+1, len(plan['faults']))
            )
            inject_threads.append(thread)
            thread.start()

        self.inject_threads.extend(inject_threads)

        for thread in inject_threads:
            thread.join()
        
        return expect_end_time
        
    def _delayed_inject(self, fault_spec: dict, inject_time: datetime,
                        fault_index: int, total_faults: int):
        fault_type = fault_spec['fault_type']
        duration = fault_spec['duration']
        custom_params = fault_spec.get('custom_params', {})
        target_node = fault_spec.get('target_node', None)

        if "timeout" not in custom_params:
            custom_params['timeout'] = duration
        
        experiment_id = self.inject_fault(
            fault_type,
            inject_time=inject_time,
            custom_params=custom_params,
            target_node=target_node
        )

        if experiment_id:
            bluePrint(f"[{fault_index}/{total_faults}] Fault {fault_type} will run for {duration}s")

    def export_success_faults(self, outpath: Optional[str] = "temp/chaos.csv"):
        # 虽然一般都会在destory之后使用
        self.update_all_fault_status()
        target_faults_id = list(self.active_faults.keys())
        # export expid, Command-SubCommand, CreateTime, EndTime
        """
        e.g.
        $ blade status b975893986e2cbee                           
        {
                "code": 200,
                "success": true,
                "result": {
                        "Uid": "b975893986e2cbee",
                        "Command": "cpu",
                        "SubCommand": "fullload",
                        "Flag": " --cpu-percent=20 --timeout=60",
                        "Status": "Destroyed",
                        "Error": "",
                        "CreateTime": "2025-09-18T20:33:01.232213923+08:00",
                        "UpdateTime": "2025-09-18T20:34:01.775680194+08:00"
                }
        }
        """
        all_data = []
        for id in target_faults_id:
            cmd = [self.chaosblade_config['command'], 'status', id]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode == 0:
                data = json.loads(res.stdout.strip())
                if not data.get("success"):
                    continue
                if data.get("result"):
                    result = data["result"]
                    status = result.get('Status','')
                    if status.lower() != "destroyed":
                        continue
                    experiment_id = id
                    # 时间格式： 2025-09-17 08:18:59，精确到秒
                    create_time = pd.to_datetime(result.get('CreateTime','')).strftime('%Y-%m-%d %H:%M:%S')
                    update_time = pd.to_datetime(result.get('UpdateTime','')).strftime('%Y-%m-%d %H:%M:%S')
                    r = {
                        'experiment_id': id,
                        'fault_type': f"{result.get('Command','')}-{result.get('SubCommand','')}",
                        'create_time': create_time,
                        'end_time': update_time
                    }
                    all_data.append(r)
        all_data = pd.DataFrame(all_data)
        all_data.sort_values('create_time', inplace=True)
        all_data.to_csv(outpath, index=False)

    def clean_up(self):
        self.destory_all_faults()



def test1():
    a = ChaosManager()
    # a.check_fault_types()

    # blade create cpu fullload --cpu-percent 20 --timeout 600
    id = a.inject_fault("cpu_fullload", custom_params={"cpu-percent":20, "timeout":600})
    a.inject_fault("cpu_fullload", custom_params={"cpu-percent":20, "timeout":600})
    a.inject_fault("cpu_fullload", custom_params={"cpu-percent":20, "timeout":600})

    active = a.get_active_faults()
    # 仅输出id
    print("Active faults:", list(active.keys()))

    sleep(5)
    a.destroy_fault(id)
    active = a.get_active_faults()
    print("Active faults after destroy:", list(active.keys()))


    sleep(10)
    a.destory_all_faults()

def test2():
    """测试chaos plan"""
    a = ChaosManager()
    end_time = a.execute_chaos_plan("formaltest")
    if not end_time:
        print("No faults to inject, exiting.")
        return
    sleep_time = end_time - datetime.now() + timedelta(seconds=10)
    print(f"Experiment will end at {end_time}, sleeping for {int(sleep_time.total_seconds())+1:>3}s ...")
    sleep(sleep_time.total_seconds())
                                                             
    a.clean_up()
    a.export_success_faults()
    
def main():
    test2()

def temp():
    a = ChaosManager()
    a.check_fault_types()
    
if __name__ == "__main__":
    # main()
    temp()