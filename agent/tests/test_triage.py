"""agent/tests/test_triage.py — Triage 模块 TDD 测试 (Phase B)

分为两类测试:
1. 单元测试 — 测试内部函数逻辑
2. 集成测试 — 在真实实验数据上验证验收标准
"""

import pytest
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

# ===========================================================================
# 路径常量
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORMALTEST_DIR = PROJECT_ROOT / "dataset_builder" / "data" / "formaltest" / "extracted_data"

EXP_001 = FORMALTEST_DIR / "exp_001_cpu_fullload"
EXP_005 = FORMALTEST_DIR / "exp_005_network_loss"
EXP_008 = FORMALTEST_DIR / "exp_008_cpu_fullload_mem_load"
EXP_029 = FORMALTEST_DIR / "exp_029_disk_burn_disk_fill"


def _load_metric_kb() -> list[dict]:
    """加载 metrics.yaml 作为 list[dict]"""
    import yaml
    kb_path = PROJECT_ROOT / "agent" / "kb" / "metrics.yaml"
    with open(kb_path) as f:
        return yaml.safe_load(f)


# ===========================================================================
# §1. 单元测试: _load_metrics
# ===========================================================================

class TestLoadMetrics:
    def test_returns_dataframe_with_datetime_index(self):
        from agent.triage import _load_metrics
        df = _load_metrics(EXP_001 / "metrics.csv")
        assert isinstance(df, pd.DataFrame)
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_drops_constant_columns(self):
        from agent.triage import _load_metrics
        df = _load_metrics(EXP_001 / "metrics.csv")
        # All remaining columns should have std > 0
        assert (df.std() > 0).all()

    def test_timestamp_not_in_columns(self):
        from agent.triage import _load_metrics
        df = _load_metrics(EXP_001 / "metrics.csv")
        assert "timestamp" not in df.columns


# ===========================================================================
# §2. 单元测试: _compute_baseline
# ===========================================================================

class TestComputeBaseline:
    def test_returns_mean_std_tuple(self):
        from agent.triage import _compute_baseline
        s = pd.Series([1.0, 2.0, 3.0, 10.0, 20.0, 100.0, 200.0])
        mean, std = _compute_baseline(s, n_baseline=3)
        assert abs(mean - 2.0) < 1e-6
        assert std > 0

    def test_zero_std_fallback(self):
        from agent.triage import _compute_baseline
        s = pd.Series([5.0, 5.0, 5.0, 5.0, 100.0])
        mean, std = _compute_baseline(s, n_baseline=3)
        assert abs(mean - 5.0) < 1e-6
        # std should use fallback: abs(mean) * 0.01
        assert abs(std - 0.05) < 1e-6

    def test_zero_mean_zero_std_fallback(self):
        from agent.triage import _compute_baseline
        s = pd.Series([0.0, 0.0, 0.0, 0.0, 100.0])
        mean, std = _compute_baseline(s, n_baseline=3)
        # Both mean and std from data are ~0; std should be at least 1e-10
        assert std >= 1e-10


# ===========================================================================
# §3. 单元测试: _zscore_anomaly
# ===========================================================================

class TestZscoreAnomaly:
    def test_detects_anomaly_in_synthetic_data(self):
        from agent.triage import _zscore_anomaly
        # Create synthetic dataframe: 5 baseline points + 20 anomaly points
        normal = np.ones((5, 2)) * 10.0
        anomaly = np.ones((20, 2)) * 100.0
        anomaly[:, 1] = 10.0  # second column stays normal
        data = np.vstack([normal, anomaly])
        df = pd.DataFrame(data, columns=["metric_a", "metric_b"])
        
        results = _zscore_anomaly(df, n_baseline=5, z_threshold=3.0, persistence_ratio=0.3)
        
        # metric_a should be detected, metric_b should not
        detected = {r["metric"] for r in results}
        assert "metric_a" in detected
        assert "metric_b" not in detected

    def test_direction_positive_for_increase(self):
        from agent.triage import _zscore_anomaly
        normal = np.ones((5, 1)) * 10.0
        anomaly = np.ones((20, 1)) * 100.0
        data = np.vstack([normal, anomaly])
        df = pd.DataFrame(data, columns=["metric_a"])
        
        results = _zscore_anomaly(df, n_baseline=5, z_threshold=3.0, persistence_ratio=0.3)
        assert results[0]["direction"] == "+"

    def test_direction_negative_for_decrease(self):
        from agent.triage import _zscore_anomaly
        normal = np.ones((5, 1)) * 100.0
        anomaly = np.ones((20, 1)) * 1.0
        data = np.vstack([normal, anomaly])
        df = pd.DataFrame(data, columns=["metric_a"])
        
        results = _zscore_anomaly(df, n_baseline=5, z_threshold=3.0, persistence_ratio=0.3)
        assert results[0]["direction"] == "-"


# ===========================================================================
# §4. 单元测试: subsystem 映射
# ===========================================================================

class TestSubsystemMapping:
    def test_prefix_map_cpu(self):
        from agent.triage import _resolve_subsystem
        assert _resolve_subsystem("cpu_usage_percent", []) == "cpu"

    def test_prefix_map_network(self):
        from agent.triage import _resolve_subsystem
        assert _resolve_subsystem("network_receive_bytes_total", []) == "network"

    def test_prefix_map_memory(self):
        from agent.triage import _resolve_subsystem
        assert _resolve_subsystem("memory_free_bytes", []) == "memory"

    def test_prefix_map_disk(self):
        from agent.triage import _resolve_subsystem
        assert _resolve_subsystem("disk_read_bytes_total", []) == "disk"

    def test_kb_takes_priority(self):
        from agent.triage import _resolve_subsystem
        kb = [{"name": "cpu_usage_percent", "subsystem": "custom_sub"}]
        assert _resolve_subsystem("cpu_usage_percent", kb) == "custom_sub"

    def test_unknown_fallback(self):
        from agent.triage import _resolve_subsystem
        assert _resolve_subsystem("xyz_unknown_metric", []) == "unknown"


# ===========================================================================
# §5. 单元测试: _step2_temporal_ordering
# ===========================================================================

class TestTemporalOrdering:
    def test_causal_order_by_onset(self):
        from agent.triage import _step2_temporal_ordering
        from agent.schema import TopMetric
        from agent.config import AblationFlags
        
        t1 = datetime(2025, 1, 1, 0, 0, 0)
        t2 = datetime(2025, 1, 1, 0, 1, 0)
        
        metrics = [
            TopMetric(metric="b", subsystem="cpu", direction="+", score=5.0, t_onset=t2, onset_rank=2),
            TopMetric(metric="a", subsystem="network", direction="+", score=10.0, t_onset=t1, onset_rank=1),
        ]
        
        causal, sub_scores, leading = _step2_temporal_ordering(metrics, AblationFlags())
        
        assert causal == ["a", "b"]
        assert leading in ("cpu", "network")
        assert "cpu" in sub_scores
        assert "network" in sub_scores

    def test_leading_subsystem_is_highest_score(self):
        from agent.triage import _step2_temporal_ordering
        from agent.schema import TopMetric
        from agent.config import AblationFlags

        t = datetime(2025, 1, 1, 0, 0, 0)
        metrics = [
            TopMetric(metric="a", subsystem="cpu", direction="+", score=10.0, t_onset=t, onset_rank=1),
            TopMetric(metric="b", subsystem="cpu", direction="+", score=8.0, t_onset=t, onset_rank=2),
            TopMetric(metric="c", subsystem="network", direction="+", score=5.0, t_onset=t, onset_rank=3),
        ]
        
        causal, sub_scores, leading = _step2_temporal_ordering(metrics, AblationFlags())
        assert leading == "cpu"
        assert sub_scores["cpu"] == 9.0  # average of 10.0 + 8.0


# ===========================================================================
# §6. 集成测试: 验收标准 (Phase B)
# ===========================================================================

class TestTriageAcceptance:
    """Phase B 验收标准 — 真实数据集。"""

    @pytest.fixture(scope="class")
    def metric_kb(self):
        return _load_metric_kb()

    @pytest.fixture(scope="class")
    def triage_config(self):
        from agent.config import TriageConfig
        return TriageConfig()

    @pytest.fixture(scope="class")
    def ablation_full(self):
        from agent.config import AblationFlags
        return AblationFlags()

    def test_exp_001_cpu_fullload(self, metric_kb, triage_config, ablation_full):
        """exp_001: leading_subsystem == 'cpu'"""
        from agent.triage import run_triage
        ctx = run_triage(
            metrics_path=EXP_001 / "metrics.csv",
            jobinfo_path=EXP_001 / "jobinfo.csv",
            metric_kb=metric_kb,
            config=triage_config,
            ablation=ablation_full,
            run_id="exp_001",
        )
        assert ctx.leading_subsystem == "cpu"
        # Should have cpu metrics in top_metrics
        cpu_metrics = [m for m in ctx.top_metrics if m.subsystem == "cpu"]
        assert len(cpu_metrics) > 0
        # Must retain at least one direct CPU-full-load signal, not only side effects
        metric_names = {m.metric for m in ctx.top_metrics}
        assert {"cpu_usage_percent", "load_1min"} & metric_names

    def test_exp_005_network_loss(self, metric_kb, triage_config, ablation_full):
        """exp_005: leading_subsystem == 'network'"""
        from agent.triage import run_triage
        ctx = run_triage(
            metrics_path=EXP_005 / "metrics.csv",
            jobinfo_path=EXP_005 / "jobinfo.csv",
            metric_kb=metric_kb,
            config=triage_config,
            ablation=ablation_full,
            run_id="exp_005",
        )
        assert ctx.leading_subsystem == "network"

    def test_exp_008_cpu_mem_composite(self, metric_kb, triage_config, ablation_full):
        """exp_008: top_metrics 同时包含 cpu 和 memory 指标"""
        from agent.triage import run_triage
        ctx = run_triage(
            metrics_path=EXP_008 / "metrics.csv",
            jobinfo_path=EXP_008 / "jobinfo.csv",
            metric_kb=metric_kb,
            config=triage_config,
            ablation=ablation_full,
            run_id="exp_008",
        )
        subsystems = {m.subsystem for m in ctx.top_metrics}
        assert "cpu" in subsystems
        assert "memory" in subsystems

    def test_exp_029_disk_fault(self, metric_kb, triage_config, ablation_full):
        """exp_029: disk_burn + disk_fill → disk must appear in top metrics
        and be in the top-3 competing subsystems.
        Note: Triage is context-focusing, not final diagnosis. The Diagnosis
        Agent will refine from FocusContext. Disk signals in this experiment
        are weak due to pre-existing high I/O load (79% baseline).
        """
        from agent.triage import run_triage
        ctx = run_triage(
            metrics_path=EXP_029 / "metrics.csv",
            jobinfo_path=EXP_029 / "jobinfo.csv",
            metric_kb=metric_kb,
            config=triage_config,
            ablation=ablation_full,
            run_id="exp_029",
        )
        # Disk metrics must be present in top_metrics
        subsystems_in_top = {m.subsystem for m in ctx.top_metrics}
        assert "disk" in subsystems_in_top or "filesystem" in subsystems_in_top
        # Disk should be in top-3 competing subsystem scores
        from agent.triage import SUBSYSTEM_GROUP, COMPETING_SUBSYSTEMS
        group_scores: dict[str, float] = {}
        group_counts: dict[str, int] = {}
        for sub, score in ctx.subsystem_scores.items():
            g = SUBSYSTEM_GROUP.get(sub, sub)
            if g in COMPETING_SUBSYSTEMS:
                group_scores[g] = group_scores.get(g, 0.0) + score
                group_counts[g] = group_counts.get(g, 0) + 1
        for k in group_scores:
            group_scores[k] /= group_counts[k]
        sorted_groups = sorted(group_scores.items(), key=lambda x: x[1], reverse=True)
        top3 = {g for g, _ in sorted_groups[:3]}
        assert "disk" in top3, f"disk not in top-3 subsystems: {sorted_groups}"

    def test_runtime_under_2s(self, metric_kb, triage_config, ablation_full):
        """所有实验的 triage 应在 2 秒内完成。"""
        import time
        from agent.triage import run_triage

        start = time.monotonic()
        for exp_dir in [EXP_001, EXP_005, EXP_008, EXP_029]:
            run_triage(
                metrics_path=exp_dir / "metrics.csv",
                jobinfo_path=exp_dir / "jobinfo.csv",
                metric_kb=metric_kb,
                config=triage_config,
                ablation=ablation_full,
                run_id=exp_dir.name,
            )
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"Triage took {elapsed:.2f}s for 4 experiments, should be < 2s"


# ===========================================================================
# §7. Fallback 测试 (Abl-A)
# ===========================================================================

class TestTriageFallback:
    def test_ablation_a_returns_low_confidence(self):
        """Abl-A: enable_triage=False → triage_confidence 低。"""
        from agent.triage import run_triage
        from agent.config import TriageConfig, AblationFlags

        ctx = run_triage(
            metrics_path=EXP_001 / "metrics.csv",
            jobinfo_path=EXP_001 / "jobinfo.csv",
            metric_kb=_load_metric_kb(),
            config=TriageConfig(),
            ablation=AblationFlags(enable_triage=False),
            run_id="exp_001_fallback",
        )
        assert ctx.triage_confidence <= 0.2
        # Should still have some top_metrics (full summary mode)
        assert len(ctx.top_metrics) > 0


# ===========================================================================
# §8. run_triage 返回类型校验
# ===========================================================================

class TestTriageReturnType:
    def test_returns_focus_context(self):
        from agent.triage import run_triage
        from agent.schema import FocusContext
        from agent.config import TriageConfig, AblationFlags

        ctx = run_triage(
            metrics_path=EXP_001 / "metrics.csv",
            jobinfo_path=EXP_001 / "jobinfo.csv",
            metric_kb=_load_metric_kb(),
            config=TriageConfig(),
            ablation=AblationFlags(),
            run_id="exp_001",
        )
        assert isinstance(ctx, FocusContext)
        assert ctx.run_id == "exp_001"
        assert ctx.anomaly_window is not None
        assert ctx.triage_confidence >= 0.0
        assert ctx.triage_confidence <= 1.0
