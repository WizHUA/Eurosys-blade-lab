import subprocess
import yaml
import os
import logging
from typing import Optional
from datetime import datetime, timedelta
import threading
import time
import pandas as pd

RUNTAG = False

def greenPrint(msg: str):
    if RUNTAG:
        return
    print(f"\033[92m{msg}\033[0m")

def redPrint(msg: str):
    if RUNTAG:
        return
    print(f"\033[91m{msg}\033[0m")

def bluePrint(msg: str):
    if RUNTAG:
        return
    print(f"\033[94m{msg}\033[0m")


class WorkloadManager:

    def __init__(self, config_path: str = "config/workloads.yaml"):
        self.config = self._load_config(config_path)
        self.slurm_config = self.config["slurm"]
        self.job_info_config = self.config["job_info"]
        self.npb_programs = self.config['npb_programs']
        self.workload_plans = self.config['workload_plans']
        self.npb_config = self.config['npb_config']
        # 运行时状态
        self.submitted_jobs = [] # id
        self.experiment_start_time = None
        self.logger = logging.getLogger(__name__)

        self._validate_npb_paths()

    def _load_config(self, path: str) -> dict:
        with open(path, 'r') as file:
            return yaml.safe_load(file)
    
    def _validate_npb_paths(self):
        base_path = self.npb_config["base_path"]
        sbatch_script = self.npb_config["sbatch_template"]

        if not os.path.exists(base_path):
            redPrint(f"NPB base path does not exist: {base_path}")
            raise FileNotFoundError(f"NPB base path does not exist: {base_path}")
        if not os.path.exists(sbatch_script):
            redPrint(f"NPB sbatch script does not exist: {sbatch_script}")
            raise FileNotFoundError(f"NPB sbatch script does not exist: {sbatch_script}")

        greenPrint("Checking NPB workloads")
        for program_name, program_config in self.npb_programs.items():
            exe_path = os.path.join(base_path, program_config["executable"])
            if not os.path.exists(exe_path):
                redPrint(f"Executable for {program_name} does not exist: {exe_path}")
                raise FileNotFoundError(f"Executable for {program_name:>10} does not exist: {exe_path}")
        greenPrint("All NPB workloads are valid.")
    
    def check_slurm_status(self) -> bool:
        greenPrint("Checking SLURM status...")
        try:
            # slurm existence check
            res = subprocess.run(["squeue", "--version"],
                                 capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                greenPrint(f"SLURM is operational: {res.stdout.strip()}")
            else :
                redPrint(f"SLURM check failed: {res.stderr.strip()}")
                return False
            # partition check
            if self.slurm_config["partition"]:
                res = subprocess.run(["sinfo", "-p", self.slurm_config["partition"]],
                                    capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    greenPrint(f'''Partition "{self.slurm_config['partition']}" is available.''')
                else:
                    redPrint(f'''Partition "{self.slurm_config['partition']}" is not available: {res.stderr.strip()}''')
                    return False
            # check node status
            cmd = ["sinfo", "--noheader", "--state=idle"]
            if self.slurm_config["partition"]:
                cmd.extend(["-p", self.slurm_config["partition"]])

            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                greenPrint(f"idle Node status: {res.stdout.strip()}")
            else:
                redPrint(f"Failed to get node status: {res.stderr.strip()}")
                return False
            
            return True

        except Exception as e:
            redPrint(f"Error checking SLURM status: {e}")
            return False
        
    def submit_job(self, program_name:str, job_name:str,
                   submit_time: Optional[datetime] = None) -> Optional[str]: # return job_id
        if program_name not in self.npb_programs:
            redPrint(f"Program {program_name} not found in configuration.")
            return None
        program_config = self.npb_programs[program_name]
        resources = program_config["resource_requirements"]

        cmd = [
            "sbatch",
            "--job-name", job_name,
            "--nodes", str(self.slurm_config.get("nodes", 1)),
            "--ntasks", str(resources.get("ntasks", 1)),
            "--time", self.slurm_config.get("time_limit", self.slurm_config["default_time_limit"]),
            "--output", f"/dev/null",  # 避免文件系统负载
            "--error", f"/dev/null"
        ]

        if self.slurm_config["partition"]:
            cmd.extend(["-p", self.slurm_config["partition"]])
        if self.slurm_config["account"]:
            cmd.extend(["-A", self.slurm_config["account"]])
        if submit_time:
            tims_str = submit_time.strftime("%Y-%m-%dT%H:%M:%S")
            cmd.extend(["--begin", tims_str])

        # sbatch脚本
        cmd.extend([self.npb_config["sbatch_template"], program_config["executable"]])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                # e.g. "Submitted batch job 123456"
                redPrint(f"Job submitted successfully: {res.stdout.strip()}") # 调试用
                job_id = res.stdout.strip().split()[-1]
                self.submitted_jobs.append(job_id)
                # print(job_id)
                greenPrint(f"Submitted job {job_id}: {job_name} ({program_name})")
                return job_id
            else:
                redPrint(f"Job submission failed: {res.stderr}")
                return None
        except Exception as e:
            redPrint(f"Error submitting job: {e}")
            return None
    
    def execute_workload_plan(self, plan_name: str, experiment_start: Optional[datetime]):
        if plan_name not in self.workload_plans:
            redPrint(f"Workload plan {plan_name} not found.")
            return
        
        plan = self.workload_plans[plan_name]
        self.experiment_start_time = experiment_start or datetime.now()

        greenPrint(f"Executing workload plan: {plan_name}")
        bluePrint(f"Description: {plan.get('description', 'No description') :>20}")

        submit_threads = []

        for i, job_spec in enumerate(plan['jobs']):
            submit_delay = job_spec.get('submit_delay', 0)
            submit_time = self.experiment_start_time + timedelta(seconds=submit_delay)

            thread = threading.Thread(
                target=self._delayed_submit,
                args=(job_spec, submit_time, i+1, len(plan['jobs']))
            )
            submit_threads.append(thread)
            thread.start()
        
        for thread in submit_threads:
            thread.join()
    
    def _delayed_submit(self, job_spec: dict, submit_time: datetime,
                      job_index: int, total_jobs: int):
        now = datetime.now()
        if submit_time > now:
            delay = (submit_time - now).total_seconds()
            bluePrint(f"[{job_index}/{total_jobs}] Waiting {delay:.1f}s to submit job '{job_spec['job_name']}'...")
            time.sleep(delay)

        # self.submit_job(job_spec['program'], job_spec['job_name'], submit_time)
        self.submit_job(job_spec['program'], job_spec['job_name'], submit_time=None) # 立即提交
    
    def wait_for_all_jobs(self, timeout: int = 600) -> bool:
        if not self.submitted_jobs:
            bluePrint("No jobs have been submitted.")
            return True
        greenPrint(f"Waiting for {len(self.submitted_jobs) :>3} jobs to complete (timeout: {timeout :>4}s)")
        
        start_time = time.time() # 适合计算
        while time.time() - start_time < timeout:
            running_jobs = self._get_running_jobs()
            if not running_jobs:
                greenPrint("All jobs have completed.")
                return True
            bluePrint(f"{len(running_jobs) :>3} jobs still running: {', '.join(running_jobs)}")
            time.sleep(10)
        
        redPrint("Timeout reached while waiting for jobs to complete.")
        return False

    def _get_running_jobs(self) -> list[str]:
        # state=RUNNING or PENDING
        try:
            cmd = ["squeue", "--noheader", "--format=%i"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if res.returncode == 0:
                active_jobs = set(res.stdout.strip().split())
                running_jobs = [job_id for job_id in self.submitted_jobs if job_id in active_jobs]
                return running_jobs
        except Exception as e:
            redPrint(f"Error checking running jobs: {e}")
        
        return []
    
    def cancel_all_jobs(self):
        if not self.submitted_jobs:
            bluePrint("No jobs to cancel.")
            return
        greenPrint(f"Cancelling {len(self.submitted_jobs)} jobs...")
        for job_id in self.submitted_jobs:
            try:
                subprocess.run(["scancel", job_id], capture_output=True, text=True, timeout=10)
            except Exception as e:
                redPrint(f"Error cancelling job {job_id}: {e}")
        self.submitted_jobs.clear()
    
    def get_job_info(self, start_time: datetime,
                     end_time: datetime) -> pd.DataFrame:
        cmd = [
            "sacct",
            "--format", self.job_info_config['query_format'],
            "--starttime", start_time.strftime('%Y-%m-%dT%H:%M:%S'),
            "--endtime", end_time.strftime('%Y-%m-%dT%H:%M:%S')
        ]

        # 输出格式
        cmd.extend(["--parsable2", "--noheader"])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                lines = res.stdout.strip().split('\n')
                if not lines or lines == [""]:
                    redPrint("No job data retrieved.")
                    return pd.DataFrame()  # No data
                data = []
                col = self.job_info_config['query_format'].replace(" ", "").split(",") # 防止手贱打空格
                for line in lines:
                    if line.strip() == "":
                        continue
                    values = line.split("|")
                    if len(values) == len(col):
                        data.append(values)
                if data:
                    df = pd.DataFrame(data, columns=col)
                    return df
                else:
                    redPrint("No valid job data found.")
                    return pd.DataFrame()
            else:
                redPrint(f"Failed to retrieve job info: {res.stderr.strip()}")
                return pd.DataFrame()

        except Exception as e:
            redPrint(f"Error retrieving job info: {e}")
            return pd.DataFrame()
    
    def export_job_info(self, output_path: str = "temp/job_info.csv", start_time: Optional[datetime] = None,
                        end_time: Optional[datetime] = None):
        if start_time is None or end_time is None:
            if self.experiment_start_time:
                start_time = self.experiment_start_time
                end_time = datetime.now()
                bluePrint(f"Using experiment time range: {start_time} to {end_time}")
            else:
                end_time = datetime.now()
                start_time = end_time - timedelta(hours=1)
                redPrint("Experiment start time is not set. Please provide start_time and end_time.")

        bluePrint(f"Exporting job info from {start_time} to {end_time} to {output_path}")
        df = self.get_job_info(start_time, end_time)
        if not df.empty:
            delimiter = self.job_info_config.get('output_delimiter', '|')
            df.to_csv(output_path, index=False, sep=delimiter)
            greenPrint(f"Job info exported to {output_path} ({len(df)} records).")
        else:
            redPrint("No job info to export.")

    def cleanup(self):
        bluePrint("Cleaning up WorkloadManager state...")
        self.cancel_all_jobs()
        self.submitted_jobs.clear()
        self.experiment_start_time = None
        greenPrint("WorkloadManager state has been reset.")

def main():
    wm = WorkloadManager()
    if not wm.check_slurm_status():
        redPrint("SLURM is not operational. Exiting.")
        return
    wm.execute_workload_plan("light_background", None)
    bluePrint("Simulating workload execution...")

    wm.wait_for_all_jobs(timeout=900)
    wm.export_job_info(output_path="temp/job_info.csv")
    wm.cleanup()


if __name__ == "__main__":
    main()