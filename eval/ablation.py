"""eval/ablation.py — Ablation experiment runner.

Runs 3 ablation configurations (Full, Abl-A, Abl-B) across experiments,
collects results, and produces comparison tables.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from agent.config import AgentConfig, AblationFlags, FORMALTEST_DIR
from eval.evaluate import Evaluator

logger = logging.getLogger(__name__)

ABLATION_IDS = ["Full", "Abl-A", "Abl-B"]


class AblationRunner:
    """Runs ablation experiment matrix and produces comparison tables."""

    def __init__(
        self,
        base_config: AgentConfig,
        data_dir: str | Path,
        output_dir: str | Path,
    ):
        self.base_config = base_config
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.evaluator = Evaluator()

    def run_ablation_suite(
        self,
        experiments: list[str] | None = None,
    ) -> pd.DataFrame:
        """Run all 3 ablation configurations across all experiments.

        Args:
            experiments: List of experiment directory names. If None, auto-detect.

        Returns:
            Summary DataFrame comparing ablation results.
        """
        if experiments is None:
            experiments = self._discover_experiments()

        all_results: dict[str, pd.DataFrame] = {}

        for ablation_id in ABLATION_IDS:
            logger.info("=" * 60)
            logger.info("Running ablation: %s", ablation_id)
            logger.info("=" * 60)

            flags = AgentConfig.from_ablation_id(ablation_id).ablation
            df = self.run_single_ablation(ablation_id, flags, experiments)
            all_results[ablation_id] = df

        # Build comparison summary
        summary = self._build_summary(all_results)

        # Save summary
        summary_path = self.output_dir / "ablation_summary.csv"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(summary_path, index=True)
        logger.info("Ablation summary saved to %s", summary_path)

        return summary

    def run_single_ablation(
        self,
        ablation_id: str,
        flags: AblationFlags,
        experiments: list[str],
    ) -> pd.DataFrame:
        """Run a single ablation configuration across experiments.

        Returns per-experiment evaluation DataFrame.
        """
        from agent.orchestrator import run_diagnosis

        config = AgentConfig.from_ablation_id(ablation_id)
        # Inherit LLM config from base
        config.llm = self.base_config.llm
        ablation_output = self.output_dir / ablation_id

        for exp_name in experiments:
            exp_dir = self.data_dir / exp_name
            metrics_path = exp_dir / "metrics.csv"
            jobinfo_path = exp_dir / "jobinfo.csv"

            if not metrics_path.exists():
                logger.warning("Skipping %s: metrics.csv not found", exp_name)
                continue

            run_id = f"{exp_name}_{ablation_id}"
            exp_output = ablation_output / exp_name
            exp_output.mkdir(parents=True, exist_ok=True)

            try:
                logger.info("  Running %s / %s", ablation_id, exp_name)
                report, trace = run_diagnosis(
                    metrics_path=str(metrics_path),
                    jobinfo_path=str(jobinfo_path),
                    config=config,
                    run_id=run_id,
                )

                # Save report
                report_path = exp_output / "diagnosis_report.json"
                with open(report_path, "w") as f:
                    json.dump(report.model_dump(mode="json"), f, indent=2, default=str)

                # Save trace (execution_trace.json)
                if trace:
                    trace_path = exp_output / "execution_trace.json"
                    with open(trace_path, "w") as f:
                        json.dump(trace, f, indent=2, default=str)

                logger.info("  Done: %s → %s", report.diagnosis_type,
                           [rc.fault_type for rc in report.root_causes])

            except Exception as e:
                logger.error("  Failed %s / %s: %s", ablation_id, exp_name, e)

        # Evaluate batch
        df = self.evaluator.evaluate_batch(
            str(ablation_output),
            str(self.data_dir),
        )
        return df

    def _discover_experiments(self) -> list[str]:
        """Auto-detect experiment directories."""
        return sorted(
            d.name for d in self.data_dir.iterdir()
            if d.is_dir() and d.name.startswith("exp_")
        )

    def _build_summary(
        self,
        all_results: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """Build ablation comparison summary table."""
        rows: list[dict[str, Any]] = []

        for ablation_id, df in all_results.items():
            if df.empty:
                rows.append({"ablation": ablation_id})
                continue

            row: dict[str, Any] = {
                "ablation": ablation_id,
                "n_experiments": len(df),
                "hit_at_1_accuracy": df["hit_at_1"].mean(),
                "hit_at_3_accuracy": df["hit_at_3"].mean(),
                "composite_coverage_mean": df["composite_coverage"].mean(),
                "false_positives_mean": df["false_positives"].mean(),
                "avg_tool_calls": df["tool_calls"].mean(),
                "avg_latency_sec": df["latency_seconds"].mean(),
            }
            rows.append(row)

        summary = pd.DataFrame(rows).set_index("ablation")
        return summary
