"""agent/reflect.py — Stage 3: Reflect + FPL writeback.

规则提炼 + 去重 + FPL 写回。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.config import AgentConfig, FPL_JSONL
from agent.llm_client import LLMClient, _extract_json_from_text
from agent.prompt_utils import render_prompt
from agent.schema import DiagnosisReport, FocusContext

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


# =====================================================================
# Trigger condition
# =====================================================================

def should_reflect(report: DiagnosisReport, config: AgentConfig) -> bool:
    """Determine if Reflect should be triggered.

    Triggers when:
    1. diagnosis_type not "inconclusive"
    2. At least 1 root_cause with confidence > 0.5
    """
    if report.diagnosis_type == "inconclusive":
        return False
    return len([rc for rc in report.root_causes if rc.confidence > 0.5]) > 0


# =====================================================================
# FPL rule validation (anti-contamination)
# =====================================================================

def _validate_reflected_rule(rule: dict, next_id: str) -> dict:
    """Validate and normalize a reflected FPL rule.

    Forces:
    - confidence = 0.5
    - status = "active"
    - source = "reflected"
    - pattern_id = system-assigned
    """
    rule["confidence"] = 0.5
    rule["status"] = "active"
    rule["source"] = "reflected"
    rule["pattern_id"] = next_id
    rule.setdefault("version", 1)
    rule.setdefault("symptom_signature", {})
    rule.setdefault("verification_steps", [])
    rule.setdefault("solutions", [])
    rule.setdefault("provenance_exp_ids", [])
    return rule


# =====================================================================
# Deduplication
# =====================================================================

def _compute_metric_overlap(existing_metrics: list[str], new_metrics: list[str]) -> float:
    """Compute overlap ratio between two metric lists."""
    if not existing_metrics and not new_metrics:
        return 1.0
    if not existing_metrics or not new_metrics:
        return 0.0
    intersection = set(existing_metrics) & set(new_metrics)
    union = set(existing_metrics) | set(new_metrics)
    return len(intersection) / len(union) if union else 0.0


def _dedup_rule(
    candidate: dict,
    existing_fpl: list[dict],
) -> tuple[str, dict | None]:
    """Check if candidate is a duplicate.

    Returns:
    - ("new", None): completely new rule
    - ("update", existing_rule): should update existing rule
    """
    candidate_ft = candidate.get("fault_type", "")

    for existing in existing_fpl:
        if existing.get("fault_type") != candidate_ft:
            continue

        # Compare symptom_signature required_metrics
        ex_metrics = existing.get("symptom_signature", {}).get("required_metrics", [])
        cand_metrics = candidate.get("symptom_signature", {}).get("required_metrics", [])
        overlap = _compute_metric_overlap(ex_metrics, cand_metrics)

        if overlap > 0.8:
            return ("update", existing)

    return ("new", None)


# =====================================================================
# Main reflect function
# =====================================================================

def run_reflect(
    report: DiagnosisReport,
    focus_context: FocusContext,
    existing_fpl: list[dict],
    llm_client: LLMClient,
    config: AgentConfig,
) -> list[dict]:
    """Run Reflect: extract FPL rules from confirmed diagnoses.

    Returns updated fpl_entries list.
    """
    logger.info("[Reflect] Starting reflection for %s", report.run_id)

    confirmed_causes = [rc for rc in report.root_causes if rc.confidence > 0.5]
    if not confirmed_causes:
        logger.info("[Reflect] No confirmed causes, skipping")
        return existing_fpl

    # Compute next pattern_id
    max_id = 0
    for entry in existing_fpl:
        pid = entry.get("pattern_id", "")
        if pid.startswith("fpl_"):
            try:
                num = int(pid.replace("fpl_", ""))
                max_id = max(max_id, num)
            except ValueError:
                pass
    next_counter = max_id + 1

    new_entries = []
    for rc in confirmed_causes:
        # Build evidence summary
        evidence_summary = "无关联证据"
        # (In full implementation, we'd cross-reference evidence IDs here)

        top_metrics_str = ", ".join(
            f"{tm.metric}({tm.direction}{tm.score:.1f})" for tm in focus_context.top_metrics[:5]
        )

        variables = {
            "run_id": report.run_id,
            "root_cause": rc.cause,
            "fault_type": rc.fault_type,
            "confidence": f"{rc.confidence:.2f}",
            "evidence_summary": evidence_summary,
            "leading_subsystem": focus_context.leading_subsystem,
            "top_metrics": top_metrics_str or "无",
        }
        prompt = render_prompt(PROMPTS_DIR / "reflect.md", variables)

        messages = [
            {"role": "system", "content": "你是 HPC 故障模式知识库维护专家。"},
            {"role": "user", "content": prompt},
        ]

        try:
            content, usage = llm_client.call(messages, temperature=0.2)
            llm_client.tracker.record("reflect", usage)

            json_str = _extract_json_from_text(content)
            candidate = json.loads(json_str)
        except Exception as e:
            logger.warning("[Reflect] Failed to generate rule for %s: %s", rc.fault_type, e)
            continue

        # Add provenance
        candidate["provenance_exp_ids"] = [report.run_id]
        candidate["fault_type"] = rc.fault_type  # Ensure consistency

        # Validate (anti-contamination)
        next_id = f"fpl_{next_counter:03d}"
        candidate = _validate_reflected_rule(candidate, next_id)

        # Dedup check
        action, existing = _dedup_rule(candidate, existing_fpl)

        if action == "update" and existing:
            # Update existing rule
            existing["version"] = existing.get("version", 1) + 1
            provenance = set(existing.get("provenance_exp_ids", []))
            provenance.add(report.run_id)
            existing["provenance_exp_ids"] = list(provenance)
            logger.info("[Reflect] Updated existing rule %s", existing.get("pattern_id"))
        else:
            new_entries.append(candidate)
            next_counter += 1
            logger.info("[Reflect] Created new rule %s for %s", next_id, rc.fault_type)

    # Write back to fpl.jsonl
    if new_entries:
        _write_fpl(existing_fpl + new_entries)

    updated_fpl = existing_fpl + new_entries
    logger.info("[Reflect] Done: %d new rules", len(new_entries))
    return updated_fpl


def _write_fpl(entries: list[dict], path: Path | None = None) -> None:
    """Write FPL entries back to fpl.jsonl."""
    p = path or FPL_JSONL
    try:
        with open(p, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("[Reflect] Wrote %d entries to %s", len(entries), p)
    except Exception as e:
        logger.warning("[Reflect] Failed to write FPL: %s", e)
