#!/usr/bin/env python3
"""Check KB metric names vs actual metrics.csv columns."""
import sys
sys.path.insert(0, "/home/quantum/Eurosys-blade-lab")
import yaml
import pandas as pd
from pathlib import Path

# Load KB
with open("/home/quantum/Eurosys-blade-lab/agent/kb/metrics.yaml") as f:
    kb = yaml.safe_load(f)
kb_names = {m["name"] for m in kb}

# Load one metrics.csv
df = pd.read_csv(
    "/home/quantum/Eurosys-blade-lab/dataset_builder/data/formaltest/extracted_data/exp_001_cpu_fullload/metrics.csv"
)
csv_cols = set(df.columns) - {"timestamp"}

matched = kb_names & csv_cols
unmatched_kb = kb_names - csv_cols
unmatched_csv_sample = list(csv_cols - kb_names)[:20]

print(f"KB entries: {len(kb_names)}")
print(f"CSV columns: {len(csv_cols)}")
print(f"Matched: {len(matched)}")
print(f"\nKB entries NOT in CSV ({len(unmatched_kb)}):")
for n in sorted(unmatched_kb):
    print(f"  {n}")
print(f"\nCSV columns not in KB (sample of {len(unmatched_csv_sample)}):")
for n in sorted(unmatched_csv_sample):
    print(f"  {n}")
