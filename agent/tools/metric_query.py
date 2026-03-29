"""agent/tools/metric_query.py — MetricQueryTool implementation."""
from __future__ import annotations

from typing import Any

import pandas as pd

from agent.tools.base import BaseTool


class MetricQueryTool(BaseTool):
    name = "MetricQueryTool"
    description = "查询指定指标在指定时间窗口内的聚合统计量"

    def __init__(self, metrics_df: pd.DataFrame):
        self.df = metrics_df.sort_index()

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        metrics = args.get("metrics", [])
        window = args.get("time_window", {})
        aggregation = args.get("aggregation", "mean")
        threshold = args.get("threshold_value")

        start = pd.to_datetime(window.get("start"), errors="coerce")
        end = pd.to_datetime(window.get("end"), errors="coerce")
        if pd.isna(start) or pd.isna(end):
            return {"results": [], "missing": [], "window_info": {"start": None, "end": None, "n_points": 0}}

        window_df = self.df[(self.df.index >= start) & (self.df.index <= end)]

        results: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []

        # Infer sampling interval in seconds (default 15s)
        interval_sec = 15.0
        if len(window_df.index) >= 2:
            interval_sec = float((window_df.index[1] - window_df.index[0]).total_seconds())

        for metric in metrics:
            if metric not in self.df.columns:
                missing.append({"metric": metric, "reason": "column_not_found"})
                continue

            series = window_df[metric].dropna()
            if len(series) == 0:
                missing.append({"metric": metric, "reason": "no_data_in_window"})
                continue

            if aggregation == "mean":
                value = float(series.mean())
            elif aggregation == "p95":
                value = float(series.quantile(0.95))
            elif aggregation == "max":
                value = float(series.max())
            elif aggregation == "duration_above_threshold":
                if threshold is None:
                    missing.append({"metric": metric, "reason": "missing_threshold_value"})
                    continue
                # Spec: count points above threshold × sampling interval
                n_points = int((series >= float(threshold)).sum())
                value = float(n_points * interval_sec)
            else:
                missing.append({"metric": metric, "reason": f"unsupported_aggregation:{aggregation}"})
                continue

            results.append(
                {
                    "metric": metric,
                    "value": value,
                    "unit": "unknown",
                    "aggregation": aggregation,
                }
            )

        return {
            "results": results,
            "missing": missing,
            "window_info": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "n_points": int(len(window_df)),
            },
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "指标名列表",
                    },
                    "time_window": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "string", "description": "ISO8601 开始时间"},
                            "end": {"type": "string", "description": "ISO8601 结束时间"},
                        },
                        "required": ["start", "end"],
                    },
                    "aggregation": {
                        "type": "string",
                        "enum": ["mean", "p95", "max", "duration_above_threshold"],
                    },
                    "threshold_value": {
                        "type": "number",
                        "description": "仅 duration_above_threshold 时需要",
                    },
                },
                "required": ["metrics", "time_window", "aggregation"],
            },
        }
