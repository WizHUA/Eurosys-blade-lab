"""agent/tests/test_tools.py — Tool unit tests (Phase C, A-type TDD)

Tests for MetricQueryTool, KBRetrievalTool, DataAnalysisTool.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

from agent.config import AgentConfig, FORMALTEST_DIR
from agent.orchestrator import _load_metric_kb, _load_metrics_df
from agent.triage import run_triage

# ===========================================================================
# Shared fixtures
# ===========================================================================

@pytest.fixture
def sample_df():
    """Create a simple DataFrame with known values for tool testing."""
    idx = pd.date_range("2025-01-01 00:00:00", periods=8, freq="15s")
    return pd.DataFrame({
        "cpu_usage_percent": [40, 42, 90, 95, 97, 98, 96, 94],
        "memory_usage_percent": [30, 31, 32, 60, 65, 70, 72, 74],
        "disk_io_usage_percent": [10, 12, 11, 13, 14, 12, 11, 10],
        "network_receive_rate_bytes_per_sec": [1000, 1100, 1050, 1200, 1150, 1000, 950, 1100],
    }, index=idx)

@pytest.fixture
def metric_kb():
    return [
        {"name": "cpu_usage_percent", "subsystem": "cpu",
         "description": "CPU usage", "related_faults": ["cpu_fullload"],
         "normal_range": {"min": 20, "max": 95}, "downstream_effects": []},
        {"name": "memory_usage_percent", "subsystem": "memory",
         "description": "Memory usage", "related_faults": ["mem_load_ram"],
         "normal_range": {"min": 20, "max": 75}, "downstream_effects": []},
        {"name": "disk_io_usage_percent", "subsystem": "disk",
         "description": "Disk IO", "related_faults": ["disk_burn"],
         "normal_range": {"min": 0, "max": 30}, "downstream_effects": []},
    ]

@pytest.fixture
def fpl_entries():
    return [
        {"pattern_id": "fpl_001", "fault_type": "cpu_fullload", "confidence": 0.9,
         "symptom_signature": {"leading_subsystem": "cpu",
                               "required_metrics": ["cpu_usage_percent", "load_1min"],
                               "temporal_pattern": "cpu rises sharply"},
         "verification_steps": ["check cpu > 90%"], "solutions": ["kill process"]},
        {"pattern_id": "fpl_002", "fault_type": "mem_load_ram", "confidence": 0.88,
         "symptom_signature": {"leading_subsystem": "memory",
                               "required_metrics": ["memory_usage_percent", "anon_memory_percent"],
                               "temporal_pattern": "memory rises"},
         "verification_steps": ["check memory > 80%"], "solutions": ["free memory"]},
    ]


# ===========================================================================
# MetricQueryTool tests
# ===========================================================================

class TestMetricQueryTool:
    def test_basic_mean(self, sample_df):
        from agent.tools.metric_query import MetricQueryTool
        tool = MetricQueryTool(sample_df)
        result = tool.execute({
            "metrics": ["cpu_usage_percent"],
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
            "aggregation": "mean",
        })
        assert "results" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["metric"] == "cpu_usage_percent"
        assert isinstance(result["results"][0]["value"], float)

    def test_p95_aggregation(self, sample_df):
        from agent.tools.metric_query import MetricQueryTool
        tool = MetricQueryTool(sample_df)
        result = tool.execute({
            "metrics": ["cpu_usage_percent"],
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
            "aggregation": "p95",
        })
        assert result["results"][0]["value"] >= 90.0

    def test_max_aggregation(self, sample_df):
        from agent.tools.metric_query import MetricQueryTool
        tool = MetricQueryTool(sample_df)
        result = tool.execute({
            "metrics": ["cpu_usage_percent"],
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
            "aggregation": "max",
        })
        assert result["results"][0]["value"] == 98.0

    def test_missing_metric(self, sample_df):
        from agent.tools.metric_query import MetricQueryTool
        tool = MetricQueryTool(sample_df)
        result = tool.execute({
            "metrics": ["nonexistent_metric"],
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
            "aggregation": "mean",
        })
        assert len(result["missing"]) == 1
        assert result["missing"][0]["metric"] == "nonexistent_metric"

    def test_duration_above_threshold(self, sample_df):
        from agent.tools.metric_query import MetricQueryTool
        tool = MetricQueryTool(sample_df)
        result = tool.execute({
            "metrics": ["cpu_usage_percent"],
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
            "aggregation": "duration_above_threshold",
            "threshold_value": 90.0,
        })
        # cpu values = [40,42,90,95,97,98,96,94]. >= 90 = 6 points × 15s = 90s
        assert result["results"][0]["value"] == 90.0

    def test_multiple_metrics(self, sample_df):
        from agent.tools.metric_query import MetricQueryTool
        tool = MetricQueryTool(sample_df)
        result = tool.execute({
            "metrics": ["cpu_usage_percent", "memory_usage_percent"],
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
            "aggregation": "mean",
        })
        assert len(result["results"]) == 2

    def test_window_info(self, sample_df):
        from agent.tools.metric_query import MetricQueryTool
        tool = MetricQueryTool(sample_df)
        result = tool.execute({
            "metrics": ["cpu_usage_percent"],
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
            "aggregation": "mean",
        })
        assert "window_info" in result
        assert result["window_info"]["n_points"] > 0

    def test_handles_datetime64_us_index(self, sample_df):
        from agent.tools.metric_query import MetricQueryTool

        df = sample_df.copy()
        df.index = pd.Index(df.index.to_numpy(dtype="datetime64[us]"))

        tool = MetricQueryTool(df)
        result = tool.execute({
            "metrics": ["cpu_usage_percent"],
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
            "aggregation": "mean",
        })

        assert len(result["results"]) == 1
        assert result["window_info"]["n_points"] > 0

    def test_real_exp001_window_returns_observations(self):
        from agent.tools.metric_query import MetricQueryTool

        config = AgentConfig()
        exp_dir = FORMALTEST_DIR / "exp_001_cpu_fullload"
        focus_context = run_triage(
            metrics_path=exp_dir / "metrics.csv",
            jobinfo_path=exp_dir / "jobinfo.csv",
            metric_kb=_load_metric_kb(),
            config=config.triage,
            ablation=config.ablation,
            run_id="test_exp001_metric_query",
        )
        tool = MetricQueryTool(_load_metrics_df(exp_dir / "metrics.csv"))

        result = tool.execute({
            "metrics": ["cpu_usage_percent"],
            "time_window": {
                "start": focus_context.anomaly_window.start.isoformat(),
                "end": focus_context.anomaly_window.end.isoformat(),
            },
            "aggregation": "mean",
        })

        assert len(result["results"]) == 1
        assert result["window_info"]["n_points"] > 0
        assert result["results"][0]["metric"] == "cpu_usage_percent"


# ===========================================================================
# KBRetrievalTool tests
# ===========================================================================

class TestKBRetrievalTool:
    def test_metric_lookup_hit(self, metric_kb, fpl_entries):
        from agent.tools.kb_retrieval import KBRetrievalTool
        tool = KBRetrievalTool(metric_kb, fpl_entries)
        result = tool.execute({
            "mode": "metric_lookup",
            "metric_name": "cpu_usage_percent",
        })
        assert "metric_entries" in result
        assert len(result["metric_entries"]) > 0
        assert result["metric_entries"][0]["name"] == "cpu_usage_percent"

    def test_metric_lookup_miss(self, metric_kb, fpl_entries):
        from agent.tools.kb_retrieval import KBRetrievalTool
        tool = KBRetrievalTool(metric_kb, fpl_entries)
        result = tool.execute({
            "mode": "metric_lookup",
            "metric_name": "nonexistent_metric",
        })
        assert len(result["metric_entries"]) == 0

    def test_pattern_match_cpu(self, metric_kb, fpl_entries):
        from agent.tools.kb_retrieval import KBRetrievalTool
        tool = KBRetrievalTool(metric_kb, fpl_entries)
        result = tool.execute({
            "mode": "pattern_match",
            "subsystem": "cpu",
            "anomaly_metrics": ["cpu_usage_percent", "load_1min"],
        })
        assert "pattern_hits" in result
        assert len(result["pattern_hits"]) > 0
        assert result["pattern_hits"][0]["fault_type"] == "cpu_fullload"

    def test_pattern_match_no_hit(self, metric_kb, fpl_entries):
        from agent.tools.kb_retrieval import KBRetrievalTool
        tool = KBRetrievalTool(metric_kb, fpl_entries)
        result = tool.execute({
            "mode": "pattern_match",
            "subsystem": "system",
            "anomaly_metrics": ["some_random_metric"],
        })
        # May return hits with low match_score or empty
        for hit in result.get("pattern_hits", []):
            assert hit["match_score"] < 0.5


# ===========================================================================
# DataAnalysisTool tests
# ===========================================================================

class TestDataAnalysisTool:
    def test_correlation(self, sample_df):
        from agent.tools.data_analysis import DataAnalysisTool
        tool = DataAnalysisTool(sample_df)
        result = tool.execute({
            "analysis_type": "correlation",
            "metric_a": "cpu_usage_percent",
            "metric_b": "memory_usage_percent",
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
        })
        assert "findings" in result
        stat_names = {f["statistic_name"] for f in result["findings"]}
        assert "pearson_r" in stat_names
        assert "spearman_rho" in stat_names

    def test_group_compare(self, sample_df):
        from agent.tools.data_analysis import DataAnalysisTool
        tool = DataAnalysisTool(sample_df)
        result = tool.execute({
            "analysis_type": "group_compare",
            "metric": "cpu_usage_percent",
            "split_time": "2025-01-01T00:00:30",
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
        })
        findings = {f["statistic_name"]: f["value"] for f in result["findings"]}
        assert "mean_before" in findings
        assert "mean_after" in findings
        assert findings["mean_after"] > findings["mean_before"]

    def test_changepoint(self, sample_df):
        from agent.tools.data_analysis import DataAnalysisTool
        tool = DataAnalysisTool(sample_df)
        result = tool.execute({
            "analysis_type": "changepoint",
            "metric": "cpu_usage_percent",
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
        })
        assert "findings" in result
        assert "summary" in result

    def test_no_raw_series_in_output(self, sample_df):
        from agent.tools.data_analysis import DataAnalysisTool
        tool = DataAnalysisTool(sample_df)
        result = tool.execute({
            "analysis_type": "correlation",
            "metric_a": "cpu_usage_percent",
            "metric_b": "memory_usage_percent",
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
        })
        # Ensure no raw series data leaked
        result_str = str(result)
        assert "Series" not in result_str
        assert "DataFrame" not in result_str

    def test_invalid_analysis_type(self, sample_df):
        from agent.tools.data_analysis import DataAnalysisTool
        tool = DataAnalysisTool(sample_df)
        result = tool.execute({
            "analysis_type": "invalid_type",
        })
        assert "error" in result

    def test_handles_datetime64_us_index(self, sample_df):
        from agent.tools.data_analysis import DataAnalysisTool

        df = sample_df.copy()
        df.index = pd.Index(df.index.to_numpy(dtype="datetime64[us]"))

        tool = DataAnalysisTool(df)
        result = tool.execute({
            "analysis_type": "correlation",
            "metric_a": "cpu_usage_percent",
            "metric_b": "memory_usage_percent",
            "time_window": {"start": "2025-01-01T00:00:00", "end": "2025-01-01T00:02:00"},
        })

        assert "findings" in result
