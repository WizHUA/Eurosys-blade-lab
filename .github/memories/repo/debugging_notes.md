# Agent Debugging Notes

## 2026-04-01: Evidence classification causes 0-candidate problem
- `_determine_evidence_type()` needs `query_metrics & hyp_metrics` non-empty
- LLM often leaves `required_metrics` empty in hypotheses
- Fix: subsystem-based fallback when `hyp_metrics` is empty
- Third fallback must restrict to leading_subsystem to avoid cross-subsystem false positives
- OrchestratorState has NO hypotheses/evidence; use ConclusionProposal instead
