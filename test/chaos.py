#!/usr/bin/env python3
import subprocess
import json
import time
import sys

class ChaosBladeController:
    def _run_command(self, cmd):
        """执行命令并返回JSON结果"""
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"命令执行失败: {cmd}")
                print(f"错误信息: {result.stderr}")
                return None
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            print(f"JSON解析失败: {result.stdout}")
            return None
        except Exception as e:
            print(f"执行异常: {e}")
            return None
    
    def create_experiment(self, target, action, **params):
        """创建故障注入实验"""
        param_str = " ".join([f"--{k} {v}" for k, v in params.items() if v != ""])
        cmd = f"blade create {target} {action} {param_str}"
        print(f"创建实验: {cmd}")
        
        result = self._run_command(cmd)
        if result and result.get("success"):
            uid = result.get("result")
            print(f"实验创建成功，UID: {uid}")
            return uid
        else:
            print(f"实验创建失败: {result}")
            return None
    
    def check_status(self, uid):
        """检查实验状态"""
        cmd = f"blade status {uid}"
        result = self._run_command(cmd)
        if result and result.get("success"):
            return result.get("result")
        return None
    
    def destroy_experiment(self, uid):
        """销毁实验"""
        cmd = f"blade destroy {uid}"
        result = self._run_command(cmd)
        return result and result.get("success")

def test_chaos_experiments():
    """测试各种故障注入类型"""
    controller = ChaosBladeController()
    
    # 完整的测试用例列表
    test_cases = [
        # CPU故障测试
        {
            "name": "CPU满负载测试",
            "target": "cpu",
            "action": "fullload",
            "params": {"cpu-percent": 30, "timeout": 5}
        },
        {
            "name": "CPU指定核心负载测试",
            "target": "cpu",
            "action": "fullload",
            "params": {"cpu-percent": 50, "cpu-count": 1, "timeout": 5}
        },
        
        # 内存故障测试
        {
            "name": "内存RAM负载测试",
            "target": "mem",
            "action": "load",
            "params": {"mode": "ram", "mem-percent": 20, "timeout": 5}
        },
        {
            "name": "内存Cache负载测试",
            "target": "mem",
            "action": "load", 
            "params": {"mode": "cache", "mem-percent": 15, "timeout": 5}
        },
        
        # 磁盘I/O故障测试
        {
            "name": "磁盘读I/O负载测试",
            "target": "disk",
            "action": "burn",
            "params": {"read": "", "path": "/tmp", "timeout": 5}
        },
        {
            "name": "磁盘写I/O负载测试", 
            "target": "disk",
            "action": "burn",
            "params": {"write": "", "path": "/tmp", "timeout": 5}
        },
        {
            "name": "磁盘空间填充测试",
            "target": "disk",
            "action": "fill",
            "params": {"path": "/tmp", "size": "100", "timeout": 5}
        },
        
        # 进程故障测试
        {
            "name": "进程假死测试",
            "target": "process",
            "action": "stop",
            "params": {"process": "sleep", "timeout": 5}
        }
    ]
    
    results = []
    
    for test_case in test_cases:
        print(f"\n{'='*60}")
        print(f"测试: {test_case['name']}")
        print('='*60)
        
        # 创建实验
        uid = controller.create_experiment(
            test_case["target"], 
            test_case["action"], 
            **test_case["params"]
        )
        
        if uid:
            # 等待实验执行
            print("等待实验执行...")
            time.sleep(3)
            
            # 检查状态
            status = controller.check_status(uid)
            if status:
                print(f"实验状态: {status.get('Status')}")
                print(f"命令: {status.get('Command')} {status.get('SubCommand')}")
                print(f"参数: {status.get('Flag')}")
                
            results.append({
                "test": test_case["name"],
                "uid": uid,
                "success": True,
                "status": status.get('Status') if status else 'Unknown'
            })
        else:
            results.append({
                "test": test_case["name"],
                "uid": None,
                "success": False,
                "status": 'Failed'
            })
    
    # 输出测试结果汇总
    print(f"\n{'='*60}")
    print("测试结果汇总")
    print('='*60)
    for result in results:
        status_str = "✓" if result["success"] else "✗"
        print(f"{status_str} {result['test']}: {result['status']}")

if __name__ == "__main__":
    test_chaos_experiments()