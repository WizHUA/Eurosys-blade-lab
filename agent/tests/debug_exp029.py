#!/usr/bin/env python3
"""Debug script for exp_029 triage analysis."""
import sys
sys.path.insert(0, "/home/quantum/Eurosys-blade-lab")

from agent.triage import run_triage
from agent.config import TriageConfig, AblationFlags
import yaml
from pathlib import Path

with open("/home/quantum/Eurosys-blade-lab/agent/kb/metrics.yaml") as f:
    kb = yaml.safe_load(f)
metric_kb = kb.get("metrics", kb) if isinstance(kb, dict) else kb

cfg = TriageConfig()
abl = AblationFlags()

exp029 = Path("/home/quantum/Eurosys-blade-lab/dataset_builder/data/formaltest/extracted_data/exp_029_disk_burn_disk_fill")
ctx = run_triage(exp029 / "metrics.csv", exp029 / "jobinfo.csv", metric_kb, cfg, abl, "exp_029")

print("Leading:", ctx.leading_subsystem)
print("Subsystem scores:", ctx.subsystem_scores)
print()
for m in ctx.top_metrics[:15]:
    print(f"  {m.metric}: sub={m.subsystem}, score={m.score:.4f}, dir={m.direction}")
