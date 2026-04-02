import json, os, glob, sys
sys.path.insert(0, "/home/quantum/Eurosys-blade-lab")
from eval.evaluate import parse_fault_info, match_fault_type

gt_dir = "dataset_builder/data/formaltest/extracted_data"
results_dir = "agent/data"
gt_dirs = sorted(glob.glob(os.path.join(gt_dir, "exp_*")))

hit1 = hit3 = total = 0
for gd in gt_dirs:
    exp_name = os.path.basename(gd)
    result_dir = os.path.join(results_dir, exp_name)
    report_file = os.path.join(result_dir, "diagnosis_report.json")
    fault_info_file = os.path.join(gd, "fault_info.txt")
    if not os.path.exists(report_file) or not os.path.exists(fault_info_file):
        continue
    gt_faults = parse_fault_info(fault_info_file)
    gt_types = [f["fault_type"] for f in gt_faults]
    with open(report_file) as f:
        report = json.load(f)
    predicted = [rc.get("fault_type", "") for rc in report.get("root_causes", [])]
    h1 = any(match_fault_type(predicted[0], gt) for gt in gt_types) if predicted else False
    h3 = any(match_fault_type(p, gt) for p in predicted[:3] for gt in gt_types) if predicted else False
    hit1 += int(h1); hit3 += int(h3); total += 1
    m1 = "V" if h1 else "X"; m3 = "V" if h3 else "X"
    print(f"{exp_name}: GT={gt_types} Pred={predicted[:3]} [{m1}/{m3}]")
print(f"\nHit@1: {hit1}/{total} ({100*hit1/total:.1f}%)")
print(f"Hit@3: {hit3}/{total} ({100*hit3/total:.1f}%)")
