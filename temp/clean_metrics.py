"""Temporary script to clean fault_type leakage from metrics.yaml."""
import yaml
import re

METRICS_PATH = "agent/kb/metrics.yaml"

fault_patterns = {
    "mem_load": "内存负载异常",
    "cpu_fullload": "CPU 满载",
    "disk_burn": "磁盘 I/O 压力",
    "disk_fill": "磁盘空间不足",
    "network_loss": "网络丢包",
    "network_delay": "网络延迟",
    "network_corrupt": "网络报文异常",
}

def clean_description(desc):
    if not isinstance(desc, str):
        return desc
    for fault_label, neutral in fault_patterns.items():
        desc = re.sub(
            r"(?<!\w)" + re.escape(fault_label) + r"(?!\w)",
            neutral,
            desc,
        )
    return desc

with open(METRICS_PATH, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)

for metric in data:
    if "related_faults" in metric:
        del metric["related_faults"]
    if "description" in metric:
        metric["description"] = clean_description(metric["description"])
    if "common_misconceptions" in metric:
        metric["common_misconceptions"] = clean_description(
            metric["common_misconceptions"]
        )

with open(METRICS_PATH, "w", encoding="utf-8") as f:
    yaml.dump(
        data,
        f,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    )

print("Done. Cleaned metrics.yaml.")
