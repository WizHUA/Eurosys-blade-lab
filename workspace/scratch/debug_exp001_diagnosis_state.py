import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.config import AgentConfig, FORMALTEST_DIR
from agent.orchestrator import _load_fpl, _load_metric_kb
from agent.tools.registry import create_tools
from agent.triage import run_triage
from agent.llm_client import LLMClient
from agent.diagnosis import build_diagnosis_graph, _build_conclusion_proposal
from agent.schema import DiagnosisState


def main() -> None:
    exp_id = "exp_001_cpu_fullload"
    base = FORMALTEST_DIR / exp_id
    metrics_path = base / "metrics.csv"
    jobinfo_path = base / "jobinfo.csv"

    config = AgentConfig()
    metric_kb = _load_metric_kb()
    fpl_entries = _load_fpl()

    focus_context = run_triage(
        metrics_path=metrics_path,
        jobinfo_path=jobinfo_path,
        metric_kb=metric_kb,
        config=config.triage,
        ablation=config.ablation,
        run_id="debug_exp001",
    )

    print("FOCUS", focus_context.leading_subsystem, focus_context.triage_confidence)
    print("TOP")
    for top_metric in focus_context.top_metrics[:10]:
        print(
            " ",
            top_metric.metric,
            top_metric.subsystem,
            round(top_metric.score, 3),
            top_metric.direction,
        )

    metrics_df = pd.read_csv(metrics_path)
    if "timestamp" in metrics_df.columns:
        metrics_df["timestamp"] = pd.to_datetime(metrics_df["timestamp"])
        metrics_df = metrics_df.set_index("timestamp")
    pd.read_csv(jobinfo_path, sep="|")

    tools = create_tools(
        metrics_df=metrics_df,
        metric_kb=metric_kb,
        fpl_entries=fpl_entries,
    )
    llm_client = LLMClient(config.llm)
    diagnosis_graph = build_diagnosis_graph(tools=tools, llm_client=llm_client, config=config)

    state = DiagnosisState(
        run_id="debug_exp001",
        focus_context=focus_context,
        hypotheses=[],
        evidence=[],
        react_trace=[],
        gate_hint=None,
        budget={
            "tool_calls_used": 0,
            "tool_calls_limit": config.budget.tool_calls_limit,
        },
        rehyp_count=0,
    )

    result = diagnosis_graph.invoke(state)
    proposal = _build_conclusion_proposal(result)

    print("\nHYPOTHESES")
    for hypothesis in result.get("hypotheses", []):
        print(
            hypothesis.id,
            hypothesis.fault_type,
            hypothesis.subsystem,
            hypothesis.status,
            hypothesis.current_confidence,
        )
        for verification in hypothesis.required_verifications:
            print(
                "  V",
                verification.description,
                verification.required_metrics,
                verification.status,
                verification.evidence_id,
            )

    print("\nEVIDENCE")
    for evidence in result.get("evidence", []):
        print(evidence.id, evidence.type, evidence.source_tool, evidence.hypothesis_ids)
        print("  ", evidence.query_summary)
        print("  ", evidence.result_digest)

    print("\nTRACE")
    for step in result.get("react_trace", []):
        tool_name = step.tool_call.tool if step.tool_call else None
        tool_args = step.tool_call.args if step.tool_call else None
        print(step.step_id, step.action_type, tool_name, tool_args)
        print("  thought=", step.thought)

    print("\nPROPOSAL", proposal.proposed_diagnosis_type)
    for root_cause in proposal.proposed_root_causes:
        print("  RC", root_cause.fault_type, root_cause.confidence, root_cause.evidence_ids)


if __name__ == "__main__":
    main()
