"""agent/tools/registry.py — Tool registry factory."""
from __future__ import annotations

from typing import Any

import pandas as pd

from agent.tools.base import BaseTool
from agent.tools.data_analysis import DataAnalysisTool
from agent.tools.kb_retrieval import KBRetrievalTool
from agent.tools.metric_query import MetricQueryTool


def create_tools(
    metrics_df: pd.DataFrame,
    metric_kb: list[dict],
    fpl_entries: list[dict],
    chroma_collection: Any | None = None,
) -> dict[str, BaseTool]:
    return {
        "MetricQueryTool": MetricQueryTool(metrics_df),
        "KBRetrievalTool": KBRetrievalTool(metric_kb, fpl_entries, chroma_collection),
        "DataAnalysisTool": DataAnalysisTool(metrics_df),
    }
