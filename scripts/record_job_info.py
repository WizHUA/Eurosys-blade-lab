import csv
import os
import subprocess
import time
from datetime import datetime, timedelta
import argparse

def get_jobs_during_experiment(experiment_start_time, experiment_end_time, username=None):
    """获取实验期间运行的所有作业信息"""
    jobs = []
    
    try:
        # 格式化时间为sacct所需的格式
        start_time_str = experiment_start_time.strftime('%Y-%m-%dT%H:%M:%S')
        end_time_str = experiment_end_time.strftime('%Y-%m-%dT%H:%M:%S')
        
        print(f"查询时间范围: {start_time_str} 到 {end_time_str}")
        
        # 使用sacct查询指定时间范围内的作业 - 移除不兼容的选项
        cmd = [
            'sacct',
            '--noheader',
            '--parsable2',  # 使用管道分隔符
            '--format=JobID,JobName,NodeList,Start,End,State,ExitCode,User,Submit,Elapsed',
            '--starttime', start_time_str,
            '--endtime', end_time_str,
            '--state=CANCELLED,COMPLETED,FAILED,NODE_FAIL,PREEMPTED,RUNNING,SUSPENDED,TIMEOUT'  # 替代--alljobs
        ]
        
        if username:
            cmd.extend(['--user', username])
        
        print(f"执行命令: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        print(f"sacct 原始输出:")
        print(result.stdout)
        
        if result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                if line.strip() and not line.endswith('.batch') and not line.endswith('.extern'):
                    parts = line.split('|')
                    if len(parts) >= 10:
                        job_id, job_name, node_list, start_time, end_time, state, exit_code, user, submit_time, elapsed = parts[:10]
                        
                        # 过滤出实验相关的作业
                        if (job_name.lower().find('npb') != -1 or 
                            job_name.lower().find('experiment') != -1 or
                            job_id.strip().isdigit()):  # 包含数字作业ID
                            
                            jobs.append({
                                'JobID': job_id.strip(),
                                'JobName': job_name.strip(),
                                'NodeList': node_list.strip() if node_list.strip() not in ['None', ''] else 'wizhua-virtual-machine',
                                'Start': start_time.strip() if start_time.strip() not in ['None', 'Unknown', ''] else submit_time.strip(),
                                'End': end_time.strip() if end_time.strip() not in ['None', 'Unknown', ''] else experiment_end_time.strftime('%Y-%m-%dT%H:%M:%S'),
                                'State': state.strip(),
                                'ExitCode': exit_code.strip(),
                                'User': user.strip(),
                                'Elapsed': elapsed.strip()
                            })
                            print(f"找到作业: {job_id} - {job_name} ({state}) - ExitCode: {exit_code}")
        
        # 如果sacct没有找到作业，尝试从squeue获取当前运行的作业
        if not jobs:
            print("sacct未找到作业，尝试从squeue获取当前作业...")
            try:
                squeue_cmd = ['squeue', '--noheader', '--format=%i|%j|%N|%S|%T|%u']
                if username:
                    squeue_cmd.extend(['--user', username])
                
                squeue_result = subprocess.run(squeue_cmd, capture_output=True, text=True, check=True)
                
                for line in squeue_result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = line.split('|')
                        if len(parts) >= 6:
                            job_id, job_name, node_list, start_time, state, user = parts[:6]
                            
                            if user.strip() == (username or user.strip()):
                                jobs.append({
                                    'JobID': job_id.strip(),
                                    'JobName': job_name.strip(),
                                    'NodeList': node_list.strip(),
                                    'Start': start_time.strip() if start_time.strip() != 'N/A' else datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                                    'End': experiment_end_time.strftime('%Y-%m-%dT%H:%M:%S'),
                                    'State': state.strip(),
                                    'ExitCode': 'RUNNING' if state.strip() == 'R' else 'UNKNOWN',
                                    'User': user.strip(),
                                    'Elapsed': '00:00:00'
                                })
                                print(f"从squeue找到作业: {job_id} - {job_name} ({state})")
            except subprocess.CalledProcessError as e:
                print(f"squeue查询也失败: {e}")
        
        print(f"总共找到 {len(jobs)} 个相关作业")
        return jobs
        
    except subprocess.CalledProcessError as e:
        print(f"查询作业历史失败: {e}")
        if e.stderr:
            print(f"stderr: {e.stderr}")
        
        # 如果sacct失败，尝试简化的查询
        print("尝试简化的sacct查询...")
        try:
            simple_cmd = ['sacct', '--noheader', '--format=JobID,JobName,State,ExitCode']
            if username:
                simple_cmd.extend(['--user', username])
            
            result = subprocess.run(simple_cmd, capture_output=True, text=True, check=True)
            print(f"简化查询结果:\n{result.stdout}")
            
        except subprocess.CalledProcessError as e2:
            print(f"简化查询也失败: {e2}")
        
        return []

def record_experiment_jobs(output_file, experiment_id, experiment_start_time, experiment_end_time, username=None):
    """记录实验期间的作业信息"""
    print(f"记录实验 {experiment_id} 的作业信息")
    
    # 解析时间字符串
    if isinstance(experiment_start_time, str):
        try:
            experiment_start_time = datetime.strptime(experiment_start_time, '%Y%m%d_%H%M%S')
        except ValueError:
            # 如果格式不对，使用当前时间前5分钟
            experiment_start_time = datetime.now() - timedelta(minutes=5)
    
    if isinstance(experiment_end_time, str):
        try:
            experiment_end_time = datetime.strptime(experiment_end_time, '%Y%m%d_%H%M%S')
        except ValueError:
            experiment_end_time = datetime.now()
    
    jobs = get_jobs_during_experiment(experiment_start_time, experiment_end_time, username)
    
    # 确保目录存在
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    # 写入作业信息
    with open(output_file, 'w') as f:
        f.write('JobID|NodeList|Start|End|Tag|ExperimentID|ExitCode|State|Elapsed\n')
        
        if jobs:
            for job in jobs:
                # 确定标签
                if 'npb' in job['JobName'].lower():
                    tag = 'npb_workload'
                else:
                    tag = 'experiment'
                
                f.write(f"{job['JobID']}|{job['NodeList']}|{job['Start']}|{job['End']}|{tag}|{experiment_id}|{job['ExitCode']}|{job['State']}|{job['Elapsed']}\n")
        else:
            # 如果没有找到作业，创建一个占位记录
            current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            f.write(f"0|wizhua-virtual-machine|{current_time}|{current_time}|no_job|{experiment_id}|0|PLACEHOLDER|00:00:00\n")
    
    print(f"记录了 {len(jobs)} 个作业信息到 {output_file}")
    return len(jobs)

def main():
    parser = argparse.ArgumentParser(description='记录实验期间的Slurm作业信息')
    parser.add_argument('--output', required=True, help='输出文件路径')
    parser.add_argument('--experiment-id', required=True, help='实验ID')
    parser.add_argument('--start-time', required=True, help='实验开始时间 (YYYYMMDD_HHMMSS)')
    parser.add_argument('--end-time', help='实验结束时间 (YYYYMMDD_HHMMSS)，默认为当前时间')
    parser.add_argument('--username', help='用户名（可选）')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    
    args = parser.parse_args()
    
    end_time = args.end_time if args.end_time else datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if args.debug:
        print("=== 调试模式 ===")
        print("测试sacct命令兼容性:")
        
        # 测试基本sacct命令
        try:
            result = subprocess.run(['sacct', '--help'], capture_output=True, text=True, timeout=10)
            print("sacct命令可用")
        except Exception as e:
            print(f"sacct命令不可用: {e}")
        
        # 测试squeue命令
        try:
            result = subprocess.run(['squeue', '--help'], capture_output=True, text=True, timeout=10)
            print("squeue命令可用")
        except Exception as e:
            print(f"squeue命令不可用: {e}")
        
        print("最近的squeue输出:")
        subprocess.run(['squeue'], check=False)
        
        print("最近的sacct输出:")
        subprocess.run(['sacct', '--format=JobID,JobName,State,ExitCode', '-S', 'now-2hours'], check=False)
        print("================")
    
    record_experiment_jobs(args.output, args.experiment_id, args.start_time, end_time, args.username)

if __name__ == "__main__":
    main()