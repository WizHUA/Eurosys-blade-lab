Input Data:
We are diagnosing abnormal behavior in an HPC cluster.
The following JSON object contains two types of real-time monitoring data:
{
  "system_metrics": "timestamp,cpu_usage_percent,load_1m,load_5m,load_15m,memory_total_bytes,memory_free_bytes,memory_available_bytes,memory_usage_percent,memory_buffers_bytes,memory_cached_bytes,swap_total_bytes,swap_free_bytes,disk_read_bytes_per_sec,disk_write_bytes_per_sec,disk_reads_completed_per_sec,disk_writes_completed_per_sec,disk_io_time_percent,network_receive_bytes_per_sec,network_transmit_bytes_per_sec,network_receive_packets_per_sec,network_transmit_packets_per_sec,network_receive_errors_per_sec,network_transmit_errors_per_sec,filesystem_size_bytes,filesystem_free_bytes,filesystem_avail_bytes,filesystem_usage_percent,context_switches_per_sec,forks_per_sec,processes_running,processes_blocked,system_time_seconds,uptime_seconds,cpu_user_percent,cpu_system_percent,cpu_iowait_percent,cpu_steal_percent,cpu_softirq_percent,cpu_irq_percent,cpu_guest_percent,run_queue_saturation,cpu_logical_cores,cpu_pressure_some_percent,mem_pressure_some_percent,mem_pressure_full_percent,io_pressure_some_percent,io_pressure_full_percent,mem_page_faults_per_sec,mem_major_faults_per_sec,mem_pgpgin_kb_per_sec,mem_pgpgout_kb_per_sec,mem_swap_in_bytes_per_sec,mem_swap_out_bytes_per_sec,mem_dirty_bytes,mem_writeback_bytes,mem_slab_bytes,mem_slab_reclaimable_bytes,disk_read_iops,disk_write_iops,disk_read_latency_ms,disk_write_latency_ms,disk_avg_queue_size,disk_utilization_percent,net_rx_drops_per_sec,net_tx_drops_per_sec,tcp_retrans_segs_per_sec,tcp_out_segs_per_sec,tcp_retrans_percent,tcp_curr_estab,tcp_in_errs_per_sec,tcp_listen_overflows_per_sec,tcp_syn_retrans_per_sec,softirq_net_rx_per_sec,softirq_net_tx_per_sec,softirq_timer_per_sec,softirq_rcu_per_sec,interrupts_per_sec,filesystem_inodes_total,filesystem_inodes_free,filesystem_inodes_used_percent,entropy_bits,time_sync_offset_seconds,system_pressure_combined_score,residual_load,io_wait_to_cpu_ratio,memory_pressure_ratio,disk_latency_product,net_bandwidth_utilization\n
                                   2025-08-15 15:12:03,94.0,9.82,6.95,4.64,8278056960.0,182947840.0,1809510400.0,78.0,24043520.0,1742233600.0,2147479552.0,2465792.0,54485.262605,10015351.797194,2.102454,19.892451,88.242429,422.755024,446.528929,4.204908,4.285772,0.0,0.0,83826114560.0,4709384192.0,1069346816.0,94.0,2566.409251,2.870659,12.0,6.0,1755241918.654904,190423.654904,0.3100000000004002,0.4849999999999,0.00999999999999801,0.0,0.03500000000000724,0.0,,0.01,8.0,0.38912400000026537,,,,,112.16,0.0,0.0,151.67999999999998,0.0,0.0,208896.0,0.0,366465024.0,104484864.0,0.0,7.76,0.0,1.752577319588379,0.013559999999997673,0.22400000000016004,0.0,0.0,0.0,6.959999999999999,0.0,11.0,0.0,0.0,0.0,,,,,897.12,5210112.0,4504065.0,13.551474517246465,256.0,0.004457629,,-8.18,0.03215434083596509,0.0,0.023764948453614338,0.0006954271624\n
                                   2025-08-15 15:12:18,96.0,9.56,7.03,4.7,8278056960.0,176721920.0,1794678784.0,78.0,22507520.0,1734643712.0,2147479552.0,278528.0,98304.0,9050030.08,6.72,27.0,79.38,467.36,449.2,4.52,4.28,0.0,0.0,83826114560.0,4394778624.0,754741248.0,94.0,2317.4,1.96,13.0,2.0,1755241933.676184,190438.676184,0.2799999999999727,0.46499999999991815,0.0,0.0,0.04499999999999992,0.0,,0.0075,8.0,0.3945800000001327,,,,,124.35999999999999,0.0,0.0,167.67999999999998,0.0,0.0,278528.0,0.0,366469120.0,104484864.0,0.0,8.0,0.0,1.5499999999974534,0.012360000000044237,0.22399999999925058,0.0,0.0,0.0,6.52,0.0,11.0,0.0,0.0,0.0,,,,,884.44,5210112.0,4504065.0,13.551474517246465,256.0,0.004441332,,-5.4399999999999995,0.0,0.0,0.01915800000003709,0.000733248\n
                                   2025-08-15 15:12:33,94.0,9.83,7.0,4.66,8278056960.0,173977600.0,1839177728.0,78.0,23691264.0,1784397824.0,2147479552.0,557056.0,68643.435524,9358443.379124,3.303388,25.419973,90.037465,379.889618,405.067881,3.786811,3.786811,0.0,0.0,83826114560.0,4667219968.0,1027182592.0,94.0,2250.69492,2.618539,17.0,5.0,1755241923.94164,190428.94164,0.3099999999994907,0.4650000000001455,0.005000000000002559,0.0,0.04499999999999815,0.0,,0.01,8.0,0.3792360000006738,,,,,108.35999999999999,0.0,0.0,165.76000000000002,0.0,0.0,98304.0,0.0,366469120.0,104484864.0,0.0,8.04,0.0,1.2537313432856092,0.010040000000008148,0.1959999999999127,0.0,0.0,0.0,7.319999999999999,0.0,11.0,0.0,0.0,0.0,,,,,904.4,5210112.0,4504065.0,13.551474517246465,256.0,0.00445219,,-12.17,0.01607717041804099,0.0,0.012587462686597732,0.0006279659991999999\n
                                   2025-08-15 15:12:48,94.0,9.35,7.03,4.71,8278056960.0,147570688.0,1795014656.0,78.0,22609920.0,1771995136.0,2147479552.0,0.0,312162.184744,29014036.156432,30.699395,75.987714,65.408506,481.971545,404.9098,4.609384,3.938115,0.0,0.0,83826114560.0,4066197504.0,426160128.0,96.0,1848.049854,0.984529,16.0,0.0,1755241938.666154,190443.666154,0.3100000000001728,0.47000000000002723,0.005000000000002559,0.0,0.04499999999998572,0.0,,0.0075,8.0,0.3760480000000825,,,,,123.96,0.0,0.0,183.67999999999998,0.0,0.0,180224.0,0.0,366469120.0,104484864.0,0.0,9.0,0.0,1.6177777777759021,0.01455999999991036,0.25600000000031287,0.0,0.0,0.0,6.64,0.0,11.0,0.0,0.0,0.0,,,,,891.2,5210112.0,4504065.0,13.551474517246465,256.0,0.004435913,,-6.65,0.016077170418005726,0.0,0.023554844444272117,0.000709505076\n
                                   2025-08-15 15:13:03,94.0,9.6,7.0,4.68,8278056960.0,190357504.0,1804677120.0,78.0,21778432.0,1737302016.0,2147479552.0,53248.0,64225.28,7832862.72,4.2,24.8,84.672,450.0,423.88,4.44,4.12,0.0,0.0,83826114560.0,4607459328.0,967421952.0,94.0,2265.8,1.96,14.0,0.0,1755241928.714889,190433.714889,0.28003360403245664,0.46505580669683555,0.005000600072011199,0.0,0.04000480057606473,0.0,,0.00875,8.0,0.38785854302510286,,,,,114.13369604352523,0.0,0.0,166.41997039644758,0.0,0.0,16384.0,0.0,366469120.0,104484864.0,0.0,7.960955314637757,0.0,1.5527638190918942,0.012361483378049603,0.22402688322614722,0.0,0.0,0.0,6.960835300236028,0.0,11.0,0.0,0.0,0.0,,,,,879.2655118614234,5210112.0,4504065.0,13.551474517246465,256.0,0.004446758,,-4.4,0.017793601904751856,0.0,0.019194464139741272,0.000699104\n
                                   2025-08-15 15:13:18,96.0,9.35,7.03,4.71,8278056960.0,147570688.0,1795014656.0,78.0,22609920.0,1771995136.0,2147479552.0,0.0,269113.950382,25012901.2039,26.465843,65.508748,56.388449,415.506018,349.071351,3.973734,3.395035,0.0,0.0,83826114560.0,4066197504.0,426160128.0,96.0,1593.19745,0.848759,16.0,0.0,1755241938.666154,190443.666154,0.32999999999992724,0.47000000000002723,0.005000000000002559,0.0,0.03999999999999382,0.0,,0.00625,8.0,0.3953680000004169,,,,,117.64,0.0,0.0,147.84,0.0,0.0,319488.0,0.0,366469120.0,104484864.0,0.0,6.76,0.0,1.544378698227349,0.01043999999994412,0.1959999999999127,0.0,0.0,0.0,6.76,0.0,11.0,0.0,0.0,0.0,,,,,905.0400000000001,5210112.0,4504065.0,13.551474517246465,256.0,0.004430501,,-6.65,0.015105740181279933,0.0,0.016123313609407226,0.0006116618952000001",
  "job_status": "JobID|JobName|NodeList|State|ExitCode\n
                74|io_mem_stress|wizhua-virtual-machine|RUNNING|-"
}
Please analyze this data and generate a diagnostic report following the rules and format specified in the System Prompt.

Expected Output:
[
  {
    "anomaly": "CPU utilization is saturated near 95% and system load is critically high.",
    "root_causes": "CPU resource load from one or more computationally intensive processes.",
    "solutions": [
      "Use 'top' or 'htop' sorted by CPU usage to identify the specific processes causing saturation.",
      "The system load (load_1m > 9.0) is extremely high for an 8-core system, indicating severe resource contention.",
      "Review the running job 'io_mem_stress' as a likely source of the high CPU load."
    ]
  },
  {
    "anomaly": "Node is under extreme memory pressure, leading to swap space exhaustion.",
    "root_causes": "Memory resource load exceeding available physical RAM.",
    "solutions": [
      "The 'swap_free_bytes' metric drops to zero, which is a critical indicator of memory exhaustion and heavy swapping.",
      "Identify memory-heavy processes using 'ps aux --sort=-%mem'.",
      "The significant drop in 'memory_available_bytes' confirms that the system is running out of usable memory."
    ]
  },
  {
    "anomaly": "Extremely high disk I/O wait time, indicating a severe storage bottleneck.",
    "root_causes": "Disk I/O load, likely caused by a combination of application I/O and memory swapping.",
    "solutions": [
      "The 'disk_io_time_percent' is consistently above 80% for a significant period, which is a critical state.",
      "Use 'iotop' to identify which processes are responsible for the intense disk read/write activity.",
      "Resolving the primary memory pressure issue is the most effective way to reduce I/O load caused by swapping."
    ]
  }
]