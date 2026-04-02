"""Fix two accuracy issues: triage averaging and diagnosis fallback."""

with open("/home/quantum/Eurosys-blade-lab/agent/triage.py", "r") as f:
    content = f.read()

old1a = "    # Convert to averages\n    for k in subsystem_scores:\n        subsystem_scores[k] = subsystem_scores[k] / subsystem_counts[k]"
new1a = "    # Use sum (not average) to avoid small-sample bias"
assert old1a in content, "Fix 1a old string not found!"
content = content.replace(old1a, new1a, 1)

old1b = "    # Average within groups too\n    for k in group_scores:\n        group_scores[k] = group_scores[k] / group_counts[k]"
new1b = "    # Use sum (not average) within groups to avoid small-sample bias"
assert old1b in content, "Fix 1b old string not found!"
content = content.replace(old1b, new1b, 1)

old1c = "    for group in grouped_scores:\n        grouped_scores[group] /= grouped_counts[group]"
new1c = "    # Use sum (not average) to stay consistent with _step2_temporal_ordering"
assert old1c in content, "Fix 1c old string not found!"
content = content.replace(old1c, new1c, 1)

with open("/home/quantum/Eurosys-blade-lab/agent/triage.py", "w") as f:
    f.write(content)
print("triage.py: 3 averaging operations removed")

with open("/home/quantum/Eurosys-blade-lab/agent/diagnosis.py", "r") as f:
    content = f.read()

old2a = '    # Conservative fallback: if no direct-support hypothesis survives, allow\n    # only hypotheses in the triage leading subsystem that were actually\n    # investigated by observational tools.\n    if not qualifying_ids and focus_context is not None:\n        investigated_ids = {\n            hyp_id\n            for e in evidence\n            for hyp_id in e.hypothesis_ids\n            if e.source_tool in observational_tools\n        }\n        qualifying_ids = {\n            h.id\n            for h in hypotheses\n            if h.id in investigated_ids\n            and h.status != "refuted"\n            and h.subsystem == focus_context.leading_subsystem\n        }'
new2a = '    # Conservative fallback: if no direct-support hypothesis survives, allow\n    # any non-refuted hypotheses that were actually investigated by\n    # observational tools (no subsystem restriction).\n    if not qualifying_ids:\n        investigated_ids = {\n            hyp_id\n            for e in evidence\n            for hyp_id in e.hypothesis_ids\n            if e.source_tool in observational_tools\n        }\n        qualifying_ids = {\n            h.id\n            for h in hypotheses\n            if h.id in investigated_ids\n            and h.status != "refuted"\n        }'
assert old2a in content, "Fix 2a old string not found!"
content = content.replace(old2a, new2a, 1)

old2b = '    # Third fallback: non-refuted hypotheses in leading subsystem (no investigation required)\n    if not qualifying_ids and focus_context is not None:\n        qualifying_ids = {\n            h.id for h in hypotheses\n            if h.status != "refuted"\n            and h.subsystem == focus_context.leading_subsystem\n        }'
new2b = '    # Third fallback: single best non-refuted hypothesis by prior_confidence\n    if not qualifying_ids:\n        best = max(\n            (h for h in hypotheses if h.status != "refuted"),\n            key=lambda h: h.prior_confidence,\n            default=None,\n        )\n        if best is not None:\n            qualifying_ids = {best.id}'
assert old2b in content, "Fix 2b old string not found!"
content = content.replace(old2b, new2b, 1)

with open("/home/quantum/Eurosys-blade-lab/agent/diagnosis.py", "w") as f:
    f.write(content)
print("diagnosis.py: 2 fallback fixes applied")
print("\nAll fixes applied successfully!")
