"""agent/tools/kb_retrieval.py — KBRetrievalTool implementation."""
from __future__ import annotations

from typing import Any

from agent.tools.base import BaseTool


class KBRetrievalTool(BaseTool):
    name = "KBRetrievalTool"
    description = "查询 Metric KB 或 Fault Pattern Library"

    def __init__(self, metric_kb: list[dict], fpl_entries: list[dict], chroma_collection: Any | None = None):
        self.metric_kb = {m["name"]: m for m in metric_kb if "name" in m}
        self.fpl_entries = fpl_entries
        self.chroma = chroma_collection

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        mode = args.get("mode", "")

        if mode == "metric_lookup":
            metric_name = args.get("metric_name", "")
            hit = self.metric_kb.get(metric_name)
            if hit is not None:
                return {"metric_entries": [hit]}

            # Optional semantic fallback via ChromaDB if available
            if self.chroma is not None and metric_name:
                try:
                    res = self.chroma.query(
                        query_texts=[metric_name],
                        n_results=3,
                    )
                    docs = res.get("metadatas", [[]])[0] if isinstance(res, dict) else []
                    return {"metric_entries": docs}
                except Exception:
                    pass

            return {"metric_entries": []}

        if mode == "pattern_match":
            subsystem = args.get("subsystem", "")
            anomaly_metrics = set(args.get("anomaly_metrics", []))

            # "filesystem" is a sub-domain of "disk"; treat as partial match when querying "disk"
            _SUBSYSTEM_ALIASES: dict[str, set[str]] = {
                "disk": {"disk", "filesystem"},
            }

            hits: list[dict[str, Any]] = []
            for rule in self.fpl_entries:
                sig = rule.get("symptom_signature", {})
                rule_sub = sig.get("leading_subsystem", "")
                required = set(sig.get("required_metrics", []))
                optional = set(sig.get("optional_metrics", []))

                # Sub-system score: 1.0 for exact match, 0.5 for alias match
                if subsystem and subsystem == rule_sub:
                    sub_score = 1.0
                elif subsystem and rule_sub in _SUBSYSTEM_ALIASES.get(subsystem, set()):
                    sub_score = 0.5
                else:
                    sub_score = 0.0

                req_overlap = len(required & anomaly_metrics) / max(len(required), 1)
                opt_overlap = len(optional & anomaly_metrics) / max(len(optional), 1)
                conf = float(rule.get("confidence", 0.0))

                # Weighted match score: required overlap + optional overlap (lower weight)
                match_score = 0.4 * sub_score + 0.3 * req_overlap + 0.1 * opt_overlap + 0.2 * conf
                hits.append(
                    {
                        "pattern_id": rule.get("pattern_id", "unknown"),
                        "fault_type": rule.get("fault_type", "unknown"),
                        "confidence": conf,
                        "match_score": float(match_score),
                    }
                )

            hits.sort(key=lambda x: x["match_score"], reverse=True)
            return {"pattern_hits": hits[:3]}

        return {"error": f"Unsupported mode: {mode}"}

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["metric_lookup", "pattern_match"],
                    },
                    "metric_name": {"type": "string"},
                    "subsystem": {"type": "string"},
                    "anomaly_metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "causal_order": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["mode"],
            },
        }
