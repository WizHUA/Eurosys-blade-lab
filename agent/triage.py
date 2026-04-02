"""agent/triage.py — Stage 1: Deterministic Triage

纯 Python 确定性计算，不调用 LLM。
从 metrics.csv + jobinfo.csv 中提取异常上下文，
输出 FocusContext 供 Stage 2 Diagnosis Agent 使用。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agent.config import AblationFlags, TriageConfig
from agent.schema import (
    AnomalyWindow,
    FocusContext,
    JobOverlap,
    NodeSeverity,
    TopMetric,
)

logger = logging.getLogger(__name__)

# ===========================================================================
# 指标名 → 子系统映射（兜底规则）
# ===========================================================================
SUBSYSTEM_PREFIX_MAP: dict[str, str] = {
    "cpu_": "cpu",
    "load_": "cpu",
    "context_switches": "cpu",
    "interrupts": "cpu",
    "cpu_pressure": "cpu",
    "schedstat_": "cpu",
    "process_": "system",
    "processes_": "system",
    "memory_": "memory",
    "cache_": "memory",
    "buffer_": "memory",
    "anon_memory": "memory",
    "memory_pressure": "memory",
    "swap_": "memory",
    "page_": "memory",
    "vm_": "memory",
    "disk_": "disk",
    "io_pressure": "disk",
    "filesystem_": "disk",
    "filefd_": "disk",
    "network_": "network",
    "tcp_": "network",
    "udp_": "network",
    "icmp_": "network",
    "sockets_": "network",
    "softnet_": "network",
    "arp_": "network",
    "node_netstat_": "network",
    "systemd_": "system",
    "system_": "system",
    "time_": "system",
    "entropy_": "system",
}

# 4 大 subsystem（参与 leading_subsystem 竞争）
COMPETING_SUBSYSTEMS = {"cpu", "memory", "network", "disk"}

# 子类 → 父类聚合映射 (spec §4.6)
SUBSYSTEM_GROUP: dict[str, str] = {
    "cpu": "cpu", "load": "cpu", "schedstat": "cpu",
    "memory": "memory", "swap": "memory", "cache": "memory",
    "buffer": "memory", "page": "memory", "vm": "memory",
    "vmstat": "memory",
    "network": "network", "tcp": "network", "udp": "network",
    "icmp": "network", "sockets": "network", "softnet": "network",
    "arp": "network",
    "disk": "disk", "filesystem": "disk", "io_pressure": "disk",
    "system": "system", "systemd": "system", "time": "system",
    "entropy": "system", "process": "system", "processes": "system",
}

CORE_SUBSYSTEM_METRICS: dict[str, list[str]] = {
    "cpu": ["cpu_usage_percent", "load_1min", "cpu_user_percent", "context_switches_rate"],
    "memory": ["memory_usage_percent", "anon_memory_percent", "memory_available_bytes", "page_faults_rate"],
    "network": ["network_transmit_drop_rate", "network_receive_drop_rate", "network_transmit_rate_bytes_per_sec", "network_receive_packets_rate"],
    "disk": ["filesystem_avail_bytes", "filesystem_free_bytes", "disk_io_usage_percent", "disk_read_iops", "disk_write_iops"],
}


def _resolve_subsystem(metric_name: str, metric_kb: list[dict]) -> str:
    """Resolve metric name to subsystem. KB takes priority, then prefix map."""
    # KB lookup first
    for entry in metric_kb:
        if entry.get("name") == metric_name:
            return entry["subsystem"]

    # Prefix rule fallback
    for prefix, subsystem in SUBSYSTEM_PREFIX_MAP.items():
        if metric_name.startswith(prefix):
            return subsystem

    return "unknown"


# ===========================================================================
# 数据加载
# ===========================================================================

def _is_monotonic(series: pd.Series, tolerance: float = 0.01) -> bool:
    """Check if series is monotonically non-decreasing (cumulative counter)."""
    diffs = series.diff().dropna()
    if len(diffs) == 0:
        return False
    n_decreasing = (diffs < -tolerance).sum()
    return n_decreasing / len(diffs) < 0.05  # Allow up to 5% noise


def _is_cumulative_name(col: str) -> bool:
    """Heuristic: column names ending in _total or starting with node_netstat_ are cumulative."""
    return col.endswith("_total") or col.startswith("node_netstat_")


def _load_metrics(metrics_path: str | Path) -> pd.DataFrame:
    """Load metrics.csv, set timestamp as DatetimeIndex, drop constant and cumulative columns."""
    df = pd.read_csv(metrics_path, parse_dates=["timestamp"], index_col="timestamp")
    # Keep only numeric columns
    df = df.select_dtypes(include=[np.number])
    # Drop constant columns (std == 0)
    stds = df.std()
    df = df.loc[:, stds > 0]
    # Drop monotonically increasing columns (cumulative counters)
    mono_cols = [c for c in df.columns if _is_monotonic(df[c]) or _is_cumulative_name(c)]
    df = df.drop(columns=mono_cols)
    return df


# ===========================================================================
# 基线计算
# ===========================================================================

def _compute_baseline(series: pd.Series, n_baseline: int = 5) -> tuple[float, float]:
    """Compute baseline mean and std from first n_baseline points."""
    baseline = series.iloc[:n_baseline]
    mean = float(baseline.mean())
    std = float(baseline.std(ddof=0))
    if std < 1e-10:
        std = max(abs(mean) * 0.01, 1e-10)
    return mean, std


# ===========================================================================
# Z-score 异常评分
# ===========================================================================

def _zscore_anomaly(
    df: pd.DataFrame,
    n_baseline: int,
    z_threshold: float,
    persistence_ratio: float,
) -> list[dict]:
    """Score each column by z-score anomaly. Return sorted by score desc."""
    results: list[dict] = []
    n_total = len(df)
    if n_total <= n_baseline:
        return results

    # Adaptive baseline: don't use more than 1/3 of data for baseline  
    effective_baseline = min(n_baseline, max(2, n_total // 3))

    # Cap z-scores to prevent extreme values from near-zero baselines
    z_cap = 100.0

    for col in df.columns:
        series = df[col]
        mean, std = _compute_baseline(series, effective_baseline)

        z_scores = (series - mean) / std
        # Cap z-scores for persistence/direction detection
        z_capped = z_scores.clip(-z_cap, z_cap)

        # Count points exceeding threshold (absolute)
        exceed_mask = z_capped.abs() > z_threshold
        n_exceed = int(exceed_mask.sum())

        # Persistence filter
        if n_exceed / n_total < persistence_ratio:
            continue

        # Direction: majority of exceeding points
        exceed_values = z_capped[exceed_mask]
        n_positive = int((exceed_values > 0).sum())
        direction = "+" if n_positive >= len(exceed_values) / 2 else "-"

        # Score: log-compressed Cohen's d effect size
        pre = series.iloc[:effective_baseline]
        post = series.iloc[effective_baseline:]
        pre_var = float(pre.var(ddof=0))
        post_var = float(post.var(ddof=0))
        n_pre, n_post = len(pre), len(post)
        pooled_std = np.sqrt(
            (pre_var * n_pre + post_var * n_post) / (n_pre + n_post)
        )
        # Robust floor: only clamp when pooled_std is suspiciously small
        # relative to the metric's variation range. This prevents Cohen's d
        # explosion for near-zero-variance metrics (e.g., tcp_resets_sent_rate
        # going from 0.018 → 0) without penalizing normally-distributed ones.
        range_val = float(series.max() - series.min())
        if range_val > 0 and pooled_std < range_val * 0.01:
            pooled_std = range_val * 0.1
        elif pooled_std < 1e-10:
            pooled_std = max(abs(mean), 1e-6) * 0.01
        cohen_d = abs(float(post.mean()) - float(pre.mean())) / pooled_std
        score = float(np.log1p(cohen_d))

        results.append({
            "metric": col,
            "score": score,
            "direction": direction,
            "z_scores": z_capped,
        })

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ===========================================================================
# 变化点检测
# ===========================================================================

def _detect_onset(
    series: pd.Series,
    z_scores: pd.Series,
    z_threshold: float,
) -> tuple[datetime, datetime | None]:
    """Detect onset time and optional change point."""
    # t_onset: first time z exceeds threshold
    exceed_mask = z_scores.abs() > z_threshold
    exceed_indices = z_scores.index[exceed_mask]
    if len(exceed_indices) == 0:
        t_onset = series.index[0]
    else:
        t_onset = exceed_indices[0]

    # Change point detection via ruptures
    change_point: datetime | None = None
    try:
        import ruptures
        signal = series.values.reshape(-1, 1)
        algo = ruptures.Pelt(model="rbf", min_size=3, jump=1).fit(signal)
        bkps = algo.predict(pen=5)
        # bkps contains indices (1-based ending), last is always len(signal)
        for bp_idx in bkps[:-1]:
            bp_time = series.index[min(bp_idx, len(series) - 1)]
            if bp_time <= t_onset:
                change_point = bp_time
                break
    except Exception:
        # Fallback: no change_point if ruptures unavailable or fails
        pass

    return t_onset, change_point


def _build_top_metrics(
    candidates: list[dict],
    df: pd.DataFrame,
    metric_kb: list[dict],
    z_threshold: float,
) -> list[TopMetric]:
    """Build TopMetric models from scored candidate metrics."""
    metrics_with_onset: list[tuple[dict, datetime, datetime | None]] = []
    for cand in candidates:
        t_onset, change_point = _detect_onset(
            df[cand["metric"]], cand["z_scores"], z_threshold
        )
        metrics_with_onset.append((cand, t_onset, change_point))

    metrics_with_onset.sort(key=lambda x: x[1])

    top_metrics: list[TopMetric] = []
    for rank, (cand, t_onset, change_point) in enumerate(metrics_with_onset, start=1):
        subsystem = _resolve_subsystem(cand["metric"], metric_kb)
        top_metrics.append(
            TopMetric(
                metric=cand["metric"],
                subsystem=subsystem,
                direction=cand["direction"],
                score=cand["score"],
                t_onset=t_onset,
                onset_rank=rank,
                change_point=change_point,
            )
        )

    top_metrics.sort(key=lambda m: m.score, reverse=True)
    return top_metrics


def _ensure_metric_coverage(
    df: pd.DataFrame,
    metric_kb: list[dict],
    top_metrics: list[TopMetric],
    leading_subsystem: str,
    subsystem_scores: dict[str, float],
    config: TriageConfig,
) -> list[TopMetric]:
    """Ensure leading and near-leading subsystems retain direct anomaly signals.

    Pure Top-K ranking can hide direct CPU/Memory metrics behind many secondary
    drops. That hurts downstream hypothesis generation and recall. This helper
    supplements a small number of additional metrics from the leading subsystem.
    """
    if not top_metrics:
        return top_metrics

    grouped_scores: dict[str, float] = {}
    grouped_counts: dict[str, int] = {}
    for subsystem, score in subsystem_scores.items():
        group = SUBSYSTEM_GROUP.get(subsystem, subsystem)
        if group not in COMPETING_SUBSYSTEMS:
            continue
        grouped_scores[group] = grouped_scores.get(group, 0.0) + score
        grouped_counts[group] = grouped_counts.get(group, 0) + 1
    for group in grouped_scores:
        grouped_scores[group] /= grouped_counts[group]

    target_groups = [leading_subsystem]

    def _group_of(metric: TopMetric) -> str:
        return SUBSYSTEM_GROUP.get(metric.subsystem, metric.subsystem)

    def _count_in_group(metrics: list[TopMetric], group: str) -> int:
        return sum(1 for metric in metrics if _group_of(metric) == group)

    if all(
        _count_in_group(top_metrics, group) >= (2 if group == leading_subsystem else 1)
        for group in target_groups
    ):
        return top_metrics

    all_candidates = _zscore_anomaly(
        df,
        n_baseline=config.baseline_window_points,
        z_threshold=config.z_score_threshold,
        persistence_ratio=config.persistence_ratio,
    )
    kb_metric_names = {entry.get("name") for entry in metric_kb if entry.get("name")}
    core_metric_priority = {
        metric: idx
        for idx, metric in enumerate(CORE_SUBSYSTEM_METRICS.get(leading_subsystem, []))
    }
    ordered_candidates = sorted(
        all_candidates,
        key=lambda cand: (
            core_metric_priority.get(cand["metric"], 10_000),
            cand["metric"] not in kb_metric_names,
            -cand["score"],
        ),
    )
    selected_metrics = {metric.metric for metric in top_metrics}
    extras: list[dict] = []

    for cand in ordered_candidates:
        if cand["metric"] in selected_metrics:
            continue
        subsystem = _resolve_subsystem(cand["metric"], metric_kb)
        group = SUBSYSTEM_GROUP.get(subsystem, subsystem)
        if group not in target_groups:
            continue
        desired_count = 2 if group == leading_subsystem else 1
        if _count_in_group(top_metrics, group) + sum(
            1
            for extra in extras
            if SUBSYSTEM_GROUP.get(_resolve_subsystem(extra["metric"], metric_kb), _resolve_subsystem(extra["metric"], metric_kb)) == group
        ) >= desired_count:
            continue
        extras.append(cand)
        if len(extras) >= 4:
            break

    if not extras:
        return top_metrics

    extra_metrics = _build_top_metrics(
        extras,
        df,
        metric_kb,
        config.z_score_threshold,
    )
    combined = top_metrics + extra_metrics
    sorted_by_onset = sorted(combined, key=lambda metric: metric.t_onset)
    onset_ranks = {
        metric.metric: rank
        for rank, metric in enumerate(sorted_by_onset, start=1)
    }
    rebuilt = [
        metric.model_copy(update={"onset_rank": onset_ranks[metric.metric]})
        for metric in combined
    ]
    rebuilt.sort(key=lambda metric: metric.score, reverse=True)
    return rebuilt


# ===========================================================================
# Step 1: 异常指标筛选
# ===========================================================================

def _step1_anomaly_scoring(
    df: pd.DataFrame,
    metric_kb: list[dict],
    config: TriageConfig,
) -> list[TopMetric]:
    """Score anomalous metrics, detect onset times, assign subsystems."""
    candidates = _zscore_anomaly(
        df,
        n_baseline=config.baseline_window_points,
        z_threshold=config.z_score_threshold,
        persistence_ratio=config.persistence_ratio,
    )

    # Take top-K
    top_candidates = candidates[: config.top_k]
    return _build_top_metrics(
        top_candidates,
        df,
        metric_kb,
        config.z_score_threshold,
    )


# ===========================================================================
# Step 2: 时序因果排序
# ===========================================================================

def _step2_temporal_ordering(
    top_metrics: list[TopMetric],
    ablation: AblationFlags,
) -> tuple[list[str], dict[str, float], str]:
    """Compute causal order, subsystem scores, and leading subsystem."""
    # causal_order: by t_onset ascending
    sorted_by_onset = sorted(top_metrics, key=lambda m: m.t_onset)
    causal_order = [m.metric for m in sorted_by_onset]

    # subsystem_scores: average score per subsystem (raw subsystem names)
    subsystem_scores: dict[str, float] = {}
    subsystem_counts: dict[str, int] = {}
    for m in top_metrics:
        subsystem_scores[m.subsystem] = subsystem_scores.get(m.subsystem, 0.0) + m.score
        subsystem_counts[m.subsystem] = subsystem_counts.get(m.subsystem, 0) + 1
    # Convert to averages
    for k in subsystem_scores:
        subsystem_scores[k] = subsystem_scores[k] / subsystem_counts[k]

    # Aggregate into 4 competing groups for leading_subsystem selection
    group_scores: dict[str, float] = {}
    group_counts: dict[str, int] = {}
    for sub, score in subsystem_scores.items():
        group = SUBSYSTEM_GROUP.get(sub, sub)
        group_scores[group] = group_scores.get(group, 0.0) + score
        group_counts[group] = group_counts.get(group, 0) + 1
    # Average within groups too
    for k in group_scores:
        group_scores[k] = group_scores[k] / group_counts[k]

    # leading_subsystem: max score among competing subsystems only
    competing = {
        k: v for k, v in group_scores.items() if k in COMPETING_SUBSYSTEMS
    }
    if competing:
        leading_subsystem = max(competing, key=competing.get)  # type: ignore[arg-type]
    elif subsystem_scores:
        leading_subsystem = max(subsystem_scores, key=subsystem_scores.get)  # type: ignore[arg-type]
    else:
        leading_subsystem = "unknown"

    return causal_order, subsystem_scores, leading_subsystem


# ===========================================================================
# Step 3: FocusContext 构建
# ===========================================================================

def _parse_jobinfo(jobinfo_path: str | Path) -> pd.DataFrame:
    """Parse jobinfo.csv (pipe-delimited)."""
    df = pd.read_csv(jobinfo_path, sep="|")
    # Parse time columns
    for col in ["Submit", "Start", "End"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _step3_build_context(
    run_id: str,
    top_metrics: list[TopMetric],
    causal_order: list[str],
    subsystem_scores: dict[str, float],
    leading_subsystem: str,
    df: pd.DataFrame,
    jobinfo_path: str | Path,
    config: TriageConfig,
) -> FocusContext:
    """Build FocusContext from triage results."""
    # anomaly_window
    if top_metrics:
        start = min(m.t_onset for m in top_metrics)
    else:
        start = df.index[0]
    end = df.index[-1]
    anomaly_window = AnomalyWindow(start=start, end=end)

    # nodes: single-node setup, severity based on top subsystem score
    max_score = max(subsystem_scores.values()) if subsystem_scores else 0.0
    if max_score > 8.0:
        severity = "high"
    elif max_score > 4.0:
        severity = "medium"
    else:
        severity = "low"
    nodes = [NodeSeverity(node="node-0", severity=severity)]

    # jobs: parse jobinfo and compute overlap
    jobs: list[JobOverlap] = []
    try:
        job_df = _parse_jobinfo(jobinfo_path)
        for _, row in job_df.iterrows():
            job_id = str(row.get("JobID", ""))
            # Skip sub-jobs (e.g., "276.batch")
            if "." in job_id:
                continue
            job_start = row.get("Start")
            job_end = row.get("End")
            if pd.isna(job_start) or pd.isna(job_end):
                continue
            # Compute overlap with anomaly_window
            overlap_start = max(job_start, anomaly_window.start)
            overlap_end = min(job_end, anomaly_window.end)
            if overlap_start >= overlap_end:
                continue
            job_duration = (job_end - job_start).total_seconds()
            if job_duration <= 0:
                continue
            overlap_duration = (overlap_end - overlap_start).total_seconds()
            overlap_ratio = min(1.0, overlap_duration / job_duration)
            node_list = str(row.get("NodeList", "")).split(",")
            jobs.append(
                JobOverlap(
                    job_id=job_id,
                    overlap_ratio=round(overlap_ratio, 4),
                    node_set=node_list,
                )
            )
    except Exception as e:
        logger.warning("Failed to parse jobinfo: %s", e)

    # triage_confidence
    if top_metrics:
        top_score = top_metrics[0].score
        signal_ratio = min(1.0, len(top_metrics) / config.top_k)
        triage_confidence = min(1.0, top_score / 10.0) * signal_ratio
        triage_confidence = min(1.0, max(0.0, triage_confidence))
    else:
        triage_confidence = 0.0

    return FocusContext(
        run_id=run_id,
        anomaly_window=anomaly_window,
        top_metrics=top_metrics,
        causal_order=causal_order,
        subsystem_scores=subsystem_scores,
        leading_subsystem=leading_subsystem,
        nodes=nodes,
        jobs=jobs,
        triage_confidence=triage_confidence,
    )


# ===========================================================================
# Fallback FocusContext (Abl-A)
# ===========================================================================

def _build_fallback_context(
    run_id: str,
    metrics_path: str | Path,
    jobinfo_path: str | Path,
    metric_kb: list[dict],
    config: TriageConfig,
) -> FocusContext:
    """Full-summary mode when triage is disabled (Abl-A)."""
    df = _load_metrics(metrics_path)

    # Top 20 by std, score = std, direction = "+"
    stds = df.std().sort_values(ascending=False)
    top_cols = stds.head(20)

    top_metrics: list[TopMetric] = []
    t_start = df.index[0]
    for rank, (col, std_val) in enumerate(top_cols.items(), start=1):
        subsystem = _resolve_subsystem(col, metric_kb)
        top_metrics.append(
            TopMetric(
                metric=col,
                subsystem=subsystem,
                direction="+",
                score=float(std_val),
                t_onset=t_start,
                onset_rank=rank,
                change_point=None,
            )
        )

    causal_order = [m.metric for m in top_metrics]

    # subsystem_scores: evenly distributed
    subsystem_scores: dict[str, float] = {}
    for m in top_metrics:
        subsystem_scores[m.subsystem] = subsystem_scores.get(m.subsystem, 0.0) + m.score

    leading_subsystem = max(subsystem_scores, key=subsystem_scores.get) if subsystem_scores else "unknown"  # type: ignore[arg-type]

    return FocusContext(
        run_id=run_id,
        anomaly_window=AnomalyWindow(start=df.index[0], end=df.index[-1]),
        top_metrics=top_metrics,
        causal_order=causal_order,
        subsystem_scores=subsystem_scores,
        leading_subsystem=leading_subsystem,
        nodes=[NodeSeverity(node="node-0", severity="low")],
        jobs=[],
        triage_confidence=0.1,
    )


# ===========================================================================
# 主入口
# ===========================================================================

def run_triage(
    metrics_path: str | Path,
    jobinfo_path: str | Path,
    metric_kb: list[dict],
    config: TriageConfig,
    ablation: AblationFlags,
    run_id: str,
) -> FocusContext:
    """Stage 1 entry point. Pure deterministic Python, no LLM calls.
    
    消融行为:
    - ablation.enable_triage = False: 直接构建 fallback FocusContext
    """
    if not ablation.enable_triage:
        logger.info("[Triage] Ablation skip → fallback context for %s", run_id)
        return _build_fallback_context(run_id, metrics_path, jobinfo_path, metric_kb, config)

    logger.info("[Triage] Running for %s", run_id)

    # Step 0: Load data
    df = _load_metrics(metrics_path)
    logger.debug("[Triage] Loaded %d rows × %d cols", len(df), len(df.columns))

    # Step 1: Anomaly scoring
    top_metrics = _step1_anomaly_scoring(df, metric_kb, config)
    logger.info("[Triage] Found %d anomalous metrics", len(top_metrics))

    # Step 2: Temporal ordering
    causal_order, subsystem_scores, leading_subsystem = _step2_temporal_ordering(
        top_metrics, ablation
    )

    # Supplement direct signals for the leading subsystem so that downstream
    # diagnosis sees at least one direct root-cause metric. This augmentation is
    # for context only and must not change the deterministic leading decision.
    top_metrics = _ensure_metric_coverage(
        df,
        metric_kb,
        top_metrics,
        leading_subsystem,
        subsystem_scores,
        config,
    )
    causal_order = [metric.metric for metric in sorted(top_metrics, key=lambda metric: metric.t_onset)]
    logger.info("[Triage] Leading subsystem: %s", leading_subsystem)

    # Step 3: Build context
    ctx = _step3_build_context(
        run_id, top_metrics, causal_order, subsystem_scores,
        leading_subsystem, df, jobinfo_path, config,
    )

    logger.info(
        "[Triage] Done: confidence=%.3f, leading=%s, top_metrics=%d",
        ctx.triage_confidence, ctx.leading_subsystem, len(ctx.top_metrics),
    )
    return ctx
