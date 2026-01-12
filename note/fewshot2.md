Input Data:
We are diagnosing abnormal behavior in an HPC cluster.
The following JSON object contains two types of real-time monitoring data:
{
  "system_metrics": "timestamp,cpu_usage_percent,load_1m,memory_usage_percent,swap_free_bytes
                                   2025-08-15 14:58:13,50.0,4.35,64.0,11546624.0\n
                                   2025-08-15 14:58:28,50.0,4.4,64.0,11546624.0\n
                                   2025-08-15 14:58:43,50.0,4.45,64.0,11546624.0\n
                                   2025-08-15 14:58:58,50.0,4.5,64.0,11546624.0\n
                                   2025-08-15 14:59:13,44.0,4.45,64.0,11575296.0\n
                                   2025-08-15 14:59:28,42.0,4.40,64.0,11581248.0",
  "job_status": "JobID|JobName|NodeList|State|ExitCode\n
                71|complex_cg_intensive|wizhua-virtual-machine|COMPLETED|0:0"
}
Please analyze this data and generate a diagnostic report following the rules and format specified in the System Prompt.

Expected Output:
[
  {
    "anomaly": "Sustained high system load, disproportionate to the moderate CPU usage.",
    "root_causes": "CPU resource load from a high number of processes or background tasks.",
    "solutions": [
      "Use 'top' or 'htop' to identify the specific processes contributing to the high system load.",
      "Check system logs (e.g., /var/log/syslog) for any post-job cleanup or maintenance tasks that might be consuming CPU resources.",
      "Analyze the process states; a high number of processes in 'D' (disk wait) state could point to an I/O bottleneck causing the load."
    ]
  },
  {
    "anomaly": "Significant and persistent memory pressure on the node.",
    "root_causes": "Memory resource load, indicated by high usage and near-exhausted swap space.",
    "solutions": [
      "Use 'free -h' and 'vmstat 1' to confirm the extent of swap utilization and check for ongoing swapping activity.",
      "Identify memory-intensive processes using 'top' or 'ps aux --sort=-%mem'.",
      "If a specific application is identified, review its configuration for potential memory leaks or inefficient resource management."
    ]
  }
]