Input Data:
We are diagnosing abnormal behavior in an HPC cluster.
The following JSON object contains two types of real-time monitoring data:
{
  "system_metrics": "timestamp,node,cpu_usage,mem_usage,disk_io,temp\n12:00,node01,85,95,120,78\n12:01,node01,88,97,130,79",
  "job_status": "job_id|user|node|start_time|end_time|status|exit_code\n12873|alice|node01|11:50|12:01|FAILED|137\n12874|bob|node02|11:55|12:10|COMPLETED|0"
}
Please analyze this data and generate a diagnostic report following the rules and format specified in the System Prompt.  

Expected Output:
[
  {
    "anomaly": "Critical memory load on Node01 leading to resource exhaustion.",
    "root_causes": "A memory-intensive application or potential memory leak.",
    "solutions": [
      "Immediately check system logs on Node01 for Out-Of-Memory (OOM) killer events using 'dmesg | grep -i oom'.",
      "Analyze the failed Slurm job 12873; an exit code of 137 strongly suggests it was terminated due to memory exhaustion.",
      "Use 'top' or 'htop' sorted by memory usage to identify the responsible process."
    ]
  },
  {
    "anomaly": "Sustained high CPU load on Node01.",
    "root_causes": "A computationally intensive process is monopolizing CPU resources.",
    "solutions": [
      "Use 'top' or 'htop' sorted by CPU usage to identify the specific process.",
      "Check if the process causing high CPU is the same one causing the high memory load to understand the full resource profile of the application."
    ]
  },
  {
    "anomaly": "Elevated disk I/O load on Node01.",
    "root_causes": "Possible memory swapping (thrashing) due to extreme memory pressure.",
    "solutions": [
      "Use 'vmstat 1' or 'free -h' to check for active memory swapping (non-zero 'si'/'so' columns in vmstat).",
      "Resolving the primary memory exhaustion issue is the most likely way to fix this secondary symptom."
    ]
  }
]