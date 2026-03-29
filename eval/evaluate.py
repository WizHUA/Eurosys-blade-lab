"""eval/evaluate.py — Evaluation framework for HPC diagnosis agent.

Provides:
  - parse_fault_info(): Parse fault_info.txt ground truth files
  - match_fault_type(): Flexible fault type matching with aliases
  - Evaluator: Single and batch evaluation with metrics computation
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.schema import DiagnosisReport

logger = logging.getLogger(__name__)


# ======================================================================
# Fault type alias table (spec §13.4)
# ======================================================================

FAULT_TYPE_ALIASES: dict[str, list[str]] = {
    "mem_load": ["mem_load_ram", "mem_load_buffer", "memory_load"],
    "network_loss": ["net_loss", "packet_loss"],
    "disk_burn": ["disk_io_burn", "io_burn"],
    "disk_fill": ["disk_space_fill", "filesystem_fill"],
}


def _normalize(name: str) -> str:
    """Normalize fault type: lowercase, replace hyphens with underscores."""
    return name.strip().lower().replace("-", "_")


def _get_canonical(name: str) -> str:
    """Get canonical fault type name, resolving aliases."""
    norm = _normalize(name)
    # Check if it's already a canonical name
    if norm in FAULT_TYPE_ALIASES:
        return norm
    # Check if it's an alias
    for canonical, aliases in FAULT_TYPE_ALIASES.items():
        if norm in aliases:
            return canonical
    return norm


def match_fault_type(predicted: str, ground_truth: str) -> bool:
    """Flexible fault type matching with case-insensitivity and alias support."""
    p = _get_canonical(predicted)
    g = _get_canonical(ground_truth)
    return p == g


# ======================================================================
# Ground truth parser
# ======================================================================

def parse_fault_info(path: str) -> list[dict]:
    """Parse fault_info.txt → list[{fault_type, start_time, end_time, duration}].

    Actual format (ChaosBlade output):
        Fault Types: cpu-fullload
        ...
        Detailed Fault Information:
        1. ID: fb581fa9d70b23ae
           Type: cpu-fullload
           Create: 2025-09-20 07:02:13
           End: 2025-09-20 07:03:14
    """
    text = Path(path).read_text(encoding="utf-8")

    # Handle "No fault injected" case
    if "no fault" in text.lower():
        return []

    results: list[dict] = []

    # Parse the "Detailed Fault Information" section
    detail_match = re.search(r"Detailed Fault Information:", text)
    if not detail_match:
        # Fallback: parse from "Fault Types:" header line
        header_match = re.search(r"Fault Types:\s*(.+)", text)
        if header_match:
            types_str = header_match.group(1)
            for ft in types_str.split(","):
                ft = ft.strip()
                if ft:
                    results.append({
                        "fault_type": _normalize(ft),
                        "start_time": None,
                        "end_time": None,
                        "duration": None,
                    })
        return results

    detail_section = text[detail_match.end():]

    # Split by numbered entries: "1. ID:", "2. ID:", etc.
    entries = re.split(r"\n\s*\d+\.\s+ID:", detail_section)

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        fault_type = None
        start_time = None
        end_time = None

        type_match = re.search(r"Type:\s*(.+)", entry)
        if type_match:
            fault_type = _normalize(type_match.group(1))

        create_match = re.search(r"Create:\s*(.+)", entry)
        if create_match:
            try:
                start_time = datetime.strptime(
                    create_match.group(1).strip(), "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                logger.warning("Failed to parse start time: %s", create_match.group(1))

        end_match = re.search(r"End:\s*(.+)", entry)
        if end_match:
            try:
                end_time = datetime.strptime(
                    end_match.group(1).strip(), "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                logger.warning("Failed to parse end time: %s", end_match.group(1))

        if fault_type:
            duration = None
            if start_time and end_time:
                duration = (end_time - start_time).total_seconds()
            results.append({
                "fault_type": fault_type,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
            })

    return results


# ======================================================================
# Evaluator
# ======================================================================

class Evaluator:
    """Evaluation engine for diagnosis reports against ground truth."""

    def evaluate_single(
        self,
        report: DiagnosisReport,
        ground_truth: list[dict],
    ) -> dict[str, Any]:
        """Evaluate a single diagnosis report against ground truth.

        Returns metrics dict with hit_at_1, hit_at_3, composite_coverage,
        false_positives, tool_calls, latency_seconds, etc.
        """
        predicted_types = [rc.fault_type for rc in report.root_causes]
        gt_types = [g["fault_type"] for g in ground_truth]

        # Hit@1: top-1 predicted matches any GT
        hit_at_1 = False
        if predicted_types:
            hit_at_1 = any(
                match_fault_type(predicted_types[0], gt) for gt in gt_types
            )

        # Hit@3: any of top-3 predicted matches any GT
        hit_at_3 = False
        for pred in predicted_types[:3]:
            if any(match_fault_type(pred, gt) for gt in gt_types):
                hit_at_3 = True
                break

        # Composite coverage: fraction of GT faults covered by predictions
        if gt_types:
            matched = sum(
                1 for gt in gt_types
                if any(match_fault_type(pred, gt) for pred in predicted_types)
            )
            composite_coverage = matched / len(gt_types)
        else:
            composite_coverage = 1.0 if not predicted_types else 0.0

        # False positives: predicted types not matching any GT
        false_positives = sum(
            1 for pred in predicted_types
            if not any(match_fault_type(pred, gt) for gt in gt_types)
        )

        # Metadata from trace summary
        ts = report.trace_summary

        return {
            "run_id": report.run_id,
            "hit_at_1": hit_at_1,
            "hit_at_3": hit_at_3,
            "composite_coverage": composite_coverage,
            "false_positives": false_positives,
            "tool_calls": ts.total_tool_calls,
            "audit_tool_calls": len(ts.audit_tools_used),
            "latency_seconds": ts.diagnosis_duration_sec or 0.0,
            "token_usage": {
                "total_in": ts.total_tokens_in,
                "total_out": ts.total_tokens_out,
            },
            "diagnosis_type": report.diagnosis_type,
            "num_root_causes": len(report.root_causes),
            "num_gt_faults": len(gt_types),
        }

    def evaluate_batch(
        self,
        results_dir: str,
        gt_dir: str,
    ) -> "pd.DataFrame":
        """Batch evaluate all experiments in results_dir.

        Args:
            results_dir: Directory with exp_*/diagnosis_report.json
            gt_dir: Directory with exp_*/fault_info.txt (extracted_data)
        """
        import pandas as pd

        results_path = Path(results_dir)
        gt_path = Path(gt_dir)
        rows: list[dict] = []

        for exp_dir in sorted(results_path.iterdir()):
            if not exp_dir.is_dir() or not exp_dir.name.startswith("exp_"):
                continue

            report_file = exp_dir / "diagnosis_report.json"
            if not report_file.exists():
                logger.warning("No report found in %s", exp_dir)
                continue

            # Load report
            with open(report_file) as f:
                report_data = json.load(f)
            report = DiagnosisReport.model_validate(report_data)

            # Find matching GT directory
            gt_exp_dir = _find_gt_dir(gt_path, exp_dir.name)
            if gt_exp_dir is None:
                logger.warning("No GT directory found for %s", exp_dir.name)
                continue

            fault_info_file = gt_exp_dir / "fault_info.txt"
            if not fault_info_file.exists():
                logger.warning("No fault_info.txt in %s", gt_exp_dir)
                continue

            gt = parse_fault_info(str(fault_info_file))
            metrics = self.evaluate_single(report, gt)
            metrics["experiment"] = exp_dir.name
            rows.append(metrics)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        # Compute aggregates
        logger.info("=== Evaluation Summary ===")
        logger.info("Hit@1 Accuracy: %.2f%%", df["hit_at_1"].mean() * 100)
        logger.info("Hit@3 Accuracy: %.2f%%", df["hit_at_3"].mean() * 100)
        logger.info("Composite Coverage: %.2f%%", df["composite_coverage"].mean() * 100)
        logger.info("Avg Tool Calls: %.1f", df["tool_calls"].mean())
        logger.info("Avg Latency: %.1fs", df["latency_seconds"].mean())

        return df


def _find_gt_dir(gt_base: Path, exp_name: str) -> Path | None:
    """Find ground truth directory matching experiment name.

    exp_name might be "exp_001" while GT dir is "exp_001_cpu_fullload".
    """
    # Exact match first
    exact = gt_base / exp_name
    if exact.is_dir():
        return exact

    # Prefix match
    for d in gt_base.iterdir():
        if d.is_dir() and d.name.startswith(exp_name):
            return d

    return None
