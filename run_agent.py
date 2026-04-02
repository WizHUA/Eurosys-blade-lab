"""run_agent.py — 总入口脚本

Usage:
    python run_agent.py --experiment exp_001
    python run_agent.py --experiment exp_001 --ablation Abl-A
    python run_agent.py --all
    python run_agent.py --all --evaluate
    python run_agent.py --ablation-suite
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from agent.config import AgentConfig, FORMALTEST_DIR, OUTPUT_DIR


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def find_experiment_dir(exp_name: str) -> Path:
    """Find experiment directory by name or prefix."""
    for d in sorted(FORMALTEST_DIR.iterdir()):
        if d.is_dir() and d.name.startswith(exp_name):
            return d
    raise FileNotFoundError(f"No experiment directory matching '{exp_name}' in {FORMALTEST_DIR}")


def run_single(
    exp_name: str,
    ablation_id: str = "Full",
    output_dir: Path | None = None,
) -> None:
    """Run diagnosis on a single experiment."""
    from agent.orchestrator import run_diagnosis

    exp_dir = find_experiment_dir(exp_name)
    metrics_path = exp_dir / "metrics.csv"
    jobinfo_path = exp_dir / "jobinfo.csv"

    if not metrics_path.exists():
        logger.error("metrics.csv not found in %s", exp_dir)
        sys.exit(1)

    config = AgentConfig.from_ablation_id(ablation_id)
    run_id = f"{exp_name}_{ablation_id}"

    logger.info("=" * 60)
    logger.info("Running %s with %s", exp_name, ablation_id)
    logger.info("=" * 60)

    report, trace, focus_context = run_diagnosis(
        metrics_path=str(metrics_path),
        jobinfo_path=str(jobinfo_path),
        config=config,
        run_id=run_id,
    )

    logger.info("Diagnosis type: %s", report.diagnosis_type)
    for rc in report.root_causes:
        logger.info("  Root cause: %s (%s, confidence=%.2f)", rc.cause, rc.fault_type, rc.confidence)
    logger.info("Total tool calls: %d", report.trace_summary.total_tool_calls)

    # Save output
    if output_dir:
        exp_output = output_dir / ablation_id / exp_dir.name
        exp_output.mkdir(parents=True, exist_ok=True)
        report_path = exp_output / "diagnosis_report.json"
        with open(report_path, "w") as f:
            json.dump(report.model_dump(mode="json"), f, indent=2, default=str)
        if trace:
            trace_path = exp_output / "execution_trace.json"
            with open(trace_path, "w") as f:
                json.dump(trace, f, indent=2, default=str)
        if focus_context is not None:
            fc_path = exp_output / "focus_context.json"
            fc_data = focus_context.model_dump(mode="json") if hasattr(focus_context, "model_dump") else focus_context
            with open(fc_path, "w") as f:
                json.dump(fc_data, f, indent=2, ensure_ascii=False, default=str)
        logger.info("Output saved to %s", exp_output)


def run_all(ablation_id: str = "Full", output_dir: Path | None = None) -> None:
    """Run diagnosis on all experiments."""
    for exp_dir in sorted(FORMALTEST_DIR.iterdir()):
        if not exp_dir.is_dir():
            continue
        exp_name = exp_dir.name.split("_")[0] + "_" + exp_dir.name.split("_")[1]
        try:
            run_single(exp_name, ablation_id, output_dir)
        except Exception as e:
            logger.error("Failed on %s: %s", exp_name, e)


def run_evaluate(ablation_id: str, output_dir: Path) -> None:
    """Evaluate results against ground truth."""
    from eval.evaluate import Evaluator

    evaluator = Evaluator(use_label_mapper=False)
    results_dir = output_dir / ablation_id
    df = evaluator.evaluate_batch(str(results_dir), str(FORMALTEST_DIR))
    if not df.empty:
        print("\n" + df.to_string(index=False))
    else:
        logger.warning("No results to evaluate")


def run_ablation_suite(output_dir: Path) -> None:
    """Run full ablation experiment matrix."""
    from eval.ablation import AblationRunner

    config = AgentConfig()
    runner = AblationRunner(config, str(FORMALTEST_DIR), str(output_dir))
    summary = runner.run_ablation_suite()
    print("\n=== Ablation Summary ===")
    print(summary.to_string())


def main():
    parser = argparse.ArgumentParser(description="HPC Diagnosis Agent")
    parser.add_argument("--experiment", "-e", type=str, help="Experiment name (e.g., exp_001)")
    parser.add_argument("--ablation", "-a", type=str, default="Full",
                        choices=["Full", "Abl-A", "Abl-B"],
                        help="Ablation configuration")
    parser.add_argument("--all", action="store_true", help="Run all experiments")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate results after running")
    parser.add_argument("--ablation-suite", action="store_true",
                        help="Run full ablation experiment matrix (Full + Abl-A + Abl-B)")
    parser.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR),
                        help="Output directory for results")
    parser.add_argument("--model", type=str, default=None,
                        help="LLM model name (e.g., deepseek/deepseek-chat)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    output_dir = Path(args.output_dir)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.ablation_suite:
        run_ablation_suite(output_dir)
    elif args.all:
        run_all(args.ablation, output_dir)
        if args.evaluate:
            run_evaluate(args.ablation, output_dir)
    elif args.experiment:
        run_single(args.experiment, args.ablation, output_dir)
        if args.evaluate:
            run_evaluate(args.ablation, output_dir)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
