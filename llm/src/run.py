import json
from openai import OpenAI
from datetime import datetime
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

def load_prompt(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        return file.read()
    
def load_fewshot(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        return file.read()
    
def get_in_period_job(metric_content, job_content):
    lines = metric_content.strip().split('\n')
    if len(lines) < 2:
        return ""
    first_metric_time = lines[1].split(',')[0]
    last_metric_time = lines[-1].split(',')[0]

    metric_start = datetime.strptime(first_metric_time, "%Y-%m-%d %H:%M:%S")
    metric_end = datetime.strptime(last_metric_time, "%Y-%m-%d %H:%M:%S")

    job_lines = job_content.strip().split('\n')
    header = job_lines[0]
    filtered_jobs = [header]

    for line in job_lines[1:]:
        if not line.strip(): # 跳过空行
            continue
        parts = line.split('|')
        if len(parts) < 6:
            continue
        job_start_str = parts[4]
        job_end_str = parts[5]
        try:
            job_start = datetime.strptime(job_start_str, "%Y-%m-%dT%H:%M:%S") # e.g. 2025-08-15T14:59:55
            job_end = datetime.strptime(job_end_str, "%Y-%m-%dT%H:%M:%S")
            if job_start <= metric_end and job_end >= metric_start:
                filtered_jobs.append(line)
        except ValueError:
            continue

    return '\n'.join(filtered_jobs)

def get_sample_data():
    metric_columns = "timestamp,cpu_usage_percent,load_1m,load_5m,load_15m,memory_total_bytes,memory_free_bytes,memory_available_bytes,memory_usage_percent,memory_buffers_bytes,memory_cached_bytes,swap_total_bytes,swap_free_bytes,disk_read_bytes_per_sec,disk_write_bytes_per_sec,disk_reads_completed_per_sec,disk_writes_completed_per_sec,disk_io_time_percent,network_receive_bytes_per_sec,network_transmit_bytes_per_sec,network_receive_packets_per_sec,network_transmit_packets_per_sec,network_receive_errors_per_sec,network_transmit_errors_per_sec,filesystem_size_bytes,filesystem_free_bytes,filesystem_avail_bytes,filesystem_usage_percent,context_switches_per_sec,forks_per_sec,processes_running,processes_blocked,system_time_seconds,uptime_seconds,cpu_user_percent,cpu_system_percent,cpu_iowait_percent,cpu_steal_percent,cpu_softirq_percent,cpu_irq_percent,cpu_guest_percent,run_queue_saturation,cpu_logical_cores,cpu_pressure_some_percent,mem_pressure_some_percent,mem_pressure_full_percent,io_pressure_some_percent,io_pressure_full_percent,mem_page_faults_per_sec,mem_major_faults_per_sec,mem_pgpgin_kb_per_sec,mem_pgpgout_kb_per_sec,mem_swap_in_bytes_per_sec,mem_swap_out_bytes_per_sec,mem_dirty_bytes,mem_writeback_bytes,mem_slab_bytes,mem_slab_reclaimable_bytes,disk_read_iops,disk_write_iops,disk_read_latency_ms,disk_write_latency_ms,disk_avg_queue_size,disk_utilization_percent,net_rx_drops_per_sec,net_tx_drops_per_sec,tcp_retrans_segs_per_sec,tcp_out_segs_per_sec,tcp_retrans_percent,tcp_curr_estab,tcp_in_errs_per_sec,tcp_listen_overflows_per_sec,tcp_syn_retrans_per_sec,softirq_net_rx_per_sec,softirq_net_tx_per_sec,softirq_timer_per_sec,softirq_rcu_per_sec,interrupts_per_sec,filesystem_inodes_total,filesystem_inodes_free,filesystem_inodes_used_percent,entropy_bits,time_sync_offset_seconds,system_pressure_combined_score,residual_load,io_wait_to_cpu_ratio,memory_pressure_ratio,disk_latency_product,net_bandwidth_utilization"
    metric_data = "2025-08-15 14:55:53,48.0,1.07,0.76,0.65,8278056960.0,3240484864.0,3681464320.0,56.0,50991104.0,570843136.0,2147479552.0,11173888.0,2457.6,38338.56,0.16,3.6,0.084,1526.4,1327.28,9.44,8.12,0.0,0.0,83826114560.0,27363987456.0,23723950080.0,68.0,1843.6,6.0,14.0,0.0,1755240948.650094,189453.650094,0.3399999999996908,0.5250000000002046,0.025000000000012793,0.0,0.034999999999989484,0.0,,0.005,8.0,0.41744399999970483,,,,,107.03999999999999,0.0,0.0,256.64,0.0,0.0,69632.0,0.0,366198784.0,104308736.0,0.0,9.76,0.0,1.6393442622935912,0.015999999999912688,0.28799999999955617,0.0,0.0,0.0,7.359999999999999,0.0,11.0,0.0,0.0,0.0,,,,,915.2,5210112.0,4504072.0,13.551340163128934,256.0,0.005648908,,-12.93,0.07331378299130634,0.0,0.026229508196554324,0.0022829440000000003"
    system_metric = metric_columns + "\n" + metric_data

    job_columns = "JobID|JobName|NodeList|Submit|Start|End|State|ExitCode|Elapsed"
    job_data = "69|complex_ep_round1|wizhua-virtual-machine|2025-08-15T14:53:54|2025-08-15T14:53:54|2025-08-15T14:54:30|COMPLETED|0:0|00:00:36|"
    job_info = job_columns + "\n" + job_data

    return system_metric, job_info

import argparse

def get_sample_data2(experiment_name="test"):
    # 获取当前脚本所在目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
    
    # 根据实验名称构建路径
    metric_path = os.path.join(project_root, "dataset_builder", "data", experiment_name, "metric.csv")
    jobinfo_path = os.path.join(project_root, "dataset_builder", "data", experiment_name, "jobinfo.csv")
    
    if not os.path.exists(metric_path):
        print(f"Warning: Metric file not found at {metric_path}. Using sample data.")
        return get_sample_data()

    with open(metric_path, 'r', encoding='utf-8') as f:
        system_metric = f.read()
    with open(jobinfo_path, 'r', encoding='utf-8') as f:
        job_info = f.read()
    # fliter_job = get_in_period_job(system_metric, job_info)
    return system_metric, job_info

def split_fewshot(fewshot_text):
    # 按 Expected Output: 分割
    parts = fewshot_text.split("Expected Output:")
    fewshot_input = parts[0].strip()
    fewshot_output = parts[1].strip() if len(parts) > 1 else ""
    return fewshot_input, fewshot_output

def test():
    # ... (keeping test function as is, or removing it if not needed)
    pass

def run_analysis(experiment_name="test"):
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not found in environment variables.")
        return

    client = OpenAI(
      base_url="https://openrouter.ai/api/v1",
      api_key=api_key,
    )
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
    ref_dir = os.path.join(project_root, "llm", "ref")
    
    prompt_path = os.path.join(ref_dir, "prompt.md")
    if not os.path.exists(prompt_path):
        print(f"Error: Prompt file not found at {prompt_path}")
        return

    system_prompt = load_prompt(prompt_path)

    shot1_text = load_fewshot(os.path.join(ref_dir, "fewshot", "shot1.txt"))
    shot2_text = load_fewshot(os.path.join(ref_dir, "fewshot", "shot2.txt"))
    shot3_text = load_fewshot(os.path.join(ref_dir, "fewshot", "shot3.txt"))

    shot1_input, shot1_output = split_fewshot(shot1_text)
    shot2_input, shot2_output = split_fewshot(shot2_text)
    shot3_input, shot3_output = split_fewshot(shot3_text)

    print(f"Loading data for experiment: {experiment_name}")
    system_metric, job_info = get_sample_data2(experiment_name)

    input_data = {
    "system_metrics": system_metric,
    "job_status": job_info
    }
    json_string = json.dumps(input_data, indent=2)
    user_prompt = f"""We are diagnosing abnormal behavior in an HPC cluster.
    The following JSON object contains two types of real-time monitoring data:
    {json_string}
    Please analyze this data and generate a diagnostic report following the rules and format specified in the System Prompt.
    Your output must be a valid JSON array, each object must contain "anomaly", "root_causes", and "solutions". Do not add any explanation or extra text. The output must be directly parsable by Python's json.loads().
    Your output must start with '[' and end with ']'. Do not include any code block markers or extra text.
    """

    print("Sending request to LLM...")
    completion = client.chat.completions.create(
        # model="deepseek/deepseek-chat-v3.1:free",
        # model = "openai/gpt-5",
        model = "anthropic/claude-sonnet-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": shot1_input},
            {"role": "assistant", "content": shot1_output},
            {"role": "user", "content": shot2_input},
            {"role": "assistant", "content": shot2_output},
            {"role": "user", "content": shot3_input},
            {"role": "assistant", "content": shot3_output},
            {"role": "user", "content": user_prompt}
        ]
    )

    print("Response from model:")
    res_content = completion.choices[0].message.content
    try:
        out = json.loads(res_content)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        
        # 保存结果
        result_dir = os.path.join(project_root, "llm", "data", experiment_name)
        os.makedirs(result_dir, exist_ok=True)
        with open(os.path.join(result_dir, "diagnosis_report.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"Report saved to {result_dir}/diagnosis_report.json")
        
    except json.JSONDecodeError:
        print("Failed to parse JSON response:")
        print(res_content)

def main():
    parser = argparse.ArgumentParser(description='Run LLM Diagnosis')
    parser.add_argument('--experiment', type=str, default='test', help='Name of the experiment to analyze')
    args = parser.parse_args()
    
    run_analysis(args.experiment)

if __name__ == "__main__":
    main()
