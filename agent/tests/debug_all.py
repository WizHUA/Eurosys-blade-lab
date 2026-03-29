#!/usr/bin/env python3
"""Debug: dump detailed scoring for all 4 smoke test experiments."""
import sys
sys.path.insert(0, "/home/quantum/Eurosys-blade-lab")

import pandas as pd
import numpy as np
import yaml
from pathlib import Path

BASE = Path("/home/quantum/Eurosys-blade-lab/dataset_builder/data/formaltest/extracted_data")
EXPS = {
    "exp_001": BASE / "exp_001_cpu_fullload",
    "exp_005": BASE / "exp_005_network_loss",
    "exp_008": BASE / "exp_008_cpu_fullload_mem_load",
    "exp_029": BASE / "exp_029_disk_burn_disk_fill",
}

with open("/home/quantum/Eurosys-blade-lab/agent/kb/metrics.yaml") as f:
    kb = yaml.safe_load(f)
metric_kb = kb.get("metrics", kb) if isinstance(kb, dict) else kb

from agent.triage import (
    run_triage, _load_metrics, _zscore_anomaly, _resolve_subsystem,
    SUBSYSTEM_GROUP, COMPETING_SUBSYSTEMS,
)
from agent.config import TriageConfig, AblationFlags

cfg = TriageConfig()
abl = AblationFlags()

for name, path in EXPS.items():
    ctx = run_triage(path/"metrics.csv", path/"jobinfo.csv", metric_kb, cfg, abl, name)
    print(f"\n{'='*60}")
    print(f"{name} ({path.name})")
    print(f"  Leading: {ctx.leading_subsystem}")
    print(f"  Subsystem scores: {ctx.subsystem_scores}")
    print(f"  Top 10 metrics:")
    for m in ctx.top_metrics[:10]:
        print(f"    {m.metric}: sub={m.subsystem}, score={m.score:.4f}, dir={m.direction}")

    # Show group scores
    group_scores = {}
    group_counts = {}
    for sub, score in ctx.subsystem_scores.items():
        g = SUBSYSTEM_GROUP.get(sub, sub)
        group_scores[g] = group_scores.get(g, 0.0) + score
        group_counts[g] = group_counts.get(g, 0) + 1
    for k in group_scores:
        group_scores[k] /= group_counts[k]
    competing = {k: v for k, v in group_scores.items() if k in COMPETING_SUBSYSTEMS}
    print(f"  Group scores (avg): {competing}")
