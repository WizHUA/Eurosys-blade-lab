#!/usr/bin/env python3
"""Deep analysis of exp_029 data to understand scoring issues."""
import sys
sys.path.insert(0, "/home/quantum/Eurosys-blade-lab")

import pandas as pd
import numpy as np
from pathlib import Path

exp029 = Path("/home/quantum/Eurosys-blade-lab/dataset_builder/data/formaltest/extracted_data/exp_029_disk_burn_disk_fill")
df = pd.read_csv(exp029 / "metrics.csv", parse_dates=["timestamp"], index_col="timestamp")
df = df.select_dtypes(include=[np.number])

print(f"Shape: {df.shape}")
print(f"Rows: {len(df)}")
print()

# Check key columns  
key_cols = [c for c in df.columns if any(k in c for k in ['tcp_resets', 'disk_io', 'filesystem', 'cpu_iowait', 'disk_read', 'disk_write', 'io_pressure'])]
print("Key disk/tcp columns:")
for c in key_cols:
    vals = df[c].values
    print(f"  {c}: {vals}")
    print(f"    mean_pre2={np.mean(vals[:2]):.4f}, mean_post={np.mean(vals[2:]):.4f}")
print()

# Check fault_info
fault_info = exp029 / "fault_info.txt"
if fault_info.exists():
    print("=== fault_info.txt ===")
    print(fault_info.read_text())
