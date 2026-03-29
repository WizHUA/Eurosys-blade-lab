#!/usr/bin/env python3
"""Verify ground truth from fault_info.txt for all 29 experiments."""
from pathlib import Path

BASE = Path("/home/quantum/Eurosys-blade-lab/dataset_builder/data/formaltest/extracted_data")

for d in sorted(BASE.iterdir()):
    if not d.is_dir() or not d.name.startswith("exp_"):
        continue
    fi = d / "fault_info.txt"
    if fi.exists():
        content = fi.read_text().strip()
        # Extract fault types line
        for line in content.split("\n"):
            if "Fault Types:" in line or "No fault" in line.lower() or line.startswith("Fault Type:"):
                print(f"{d.name}: {line.strip()}")
                break
        else:
            print(f"{d.name}: (no fault type line found)")
            print(f"  Content preview: {content[:100]}")
    else:
        print(f"{d.name}: NO fault_info.txt")
