"""agent/tools/data_analysis.py — DataAnalysisTool implementation."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from agent.tools.base import BaseTool


class DataAnalysisTool(BaseTool):
    name = "DataAnalysisTool"
    description = "对时序数据执行统计分析"

    def __init__(self, metrics_df: pd.DataFrame):
        self.df = metrics_df.sort_index()

    def _window(self, start: str, end: str) -> pd.DataFrame:
        s = _normalize_timestamp(start)
        e = _normalize_timestamp(end)
        if pd.isna(s) or pd.isna(e):
            return self.df.iloc[0:0]
        time_index = _normalize_time_index(self.df.index)
        valid_mask = ~time_index.isna()
        window_mask = valid_mask & (time_index >= s) & (time_index <= e)
        return self.df.loc[window_mask]

    def _correlation(self, args: dict[str, Any]) -> dict[str, Any]:
        a = args.get("metric_a")
        b = args.get("metric_b")
        tw = args.get("time_window", {})
        wdf = self._window(tw.get("start"), tw.get("end"))

        if a not in wdf.columns or b not in wdf.columns:
            return {"error": "metric_not_found"}

        pair = wdf[[a, b]].dropna()
        if len(pair) < 3:
            return {"error": "insufficient_data"}

        pearson_r, p1 = stats.pearsonr(pair[a], pair[b])
        spearman_rho, p2 = stats.spearmanr(pair[a], pair[b])

        findings = [
            {"statistic_name": "pearson_r", "value": float(pearson_r), "interpretation": _corr_interp(float(pearson_r))},
            {"statistic_name": "spearman_rho", "value": float(spearman_rho), "interpretation": _corr_interp(float(spearman_rho))},
            {"statistic_name": "p_value", "value": float(min(p1, p2)), "interpretation": "统计显著" if min(p1, p2) < 0.05 else "不显著"},
        ]
        return {"findings": findings, "summary": f"Correlation between {a} and {b}"}

    def _changepoint(self, args: dict[str, Any]) -> dict[str, Any]:
        metric = args.get("metric")
        tw = args.get("time_window", {})
        wdf = self._window(tw.get("start"), tw.get("end"))
        if metric not in wdf.columns:
            return {"error": "metric_not_found"}

        series = wdf[metric].dropna()
        if len(series) < 4:
            return {"error": "insufficient_data"}

        cp_idx = None
        try:
            import ruptures

            signal = series.values.reshape(-1, 1)
            algo = ruptures.Pelt(model="rbf", min_size=3, jump=1).fit(signal)
            bkps = algo.predict(pen=5)
            if len(bkps) > 1:
                cp_idx = min(bkps[0], len(series) - 1)
        except Exception:
            # Rolling-mean-diff fallback
            roll = series.rolling(window=3, min_periods=2).mean()
            diff = roll.diff().abs().fillna(0)
            cp_idx = int(diff.values.argmax())

        if cp_idx is None:
            cp_idx = max(1, len(series) // 2)

        cp_time = series.index[cp_idx]
        before = series.iloc[:cp_idx]
        after = series.iloc[cp_idx:]

        findings = [
            {"statistic_name": "changepoint_time", "value": cp_time.isoformat(), "interpretation": "检测到变化点"},
            {"statistic_name": "mean_before", "value": float(before.mean()) if len(before) else float(series.mean()), "interpretation": "变化点前均值"},
            {"statistic_name": "mean_after", "value": float(after.mean()) if len(after) else float(series.mean()), "interpretation": "变化点后均值"},
        ]
        return {"findings": findings, "summary": f"Changepoint analysis for {metric}"}

    def _group_compare(self, args: dict[str, Any]) -> dict[str, Any]:
        metric = args.get("metric")
        split_time = pd.to_datetime(args.get("split_time"), errors="coerce")
        tw = args.get("time_window", {})
        wdf = self._window(tw.get("start"), tw.get("end"))

        if metric not in wdf.columns or pd.isna(split_time):
            return {"error": "invalid_input"}

        series = wdf[metric].dropna()
        before = series[series.index < split_time]
        after = series[series.index >= split_time]
        if len(before) < 2 or len(after) < 2:
            return {"error": "insufficient_data"}

        t_stat, p_val = stats.ttest_ind(before, after, equal_var=False)
        findings = [
            {"statistic_name": "mean_before", "value": float(before.mean()), "interpretation": "split 前均值"},
            {"statistic_name": "mean_after", "value": float(after.mean()), "interpretation": "split 后均值"},
            {"statistic_name": "t_statistic", "value": float(t_stat), "interpretation": "Welch t-test"},
            {"statistic_name": "p_value", "value": float(p_val), "interpretation": "统计显著" if p_val < 0.05 else "不显著"},
        ]
        return {"findings": findings, "summary": f"Group compare for {metric}"}

    def _lag_analysis(self, args: dict[str, Any]) -> dict[str, Any]:
        a = args.get("metric_a")
        b = args.get("metric_b")
        tw = args.get("time_window", {})
        wdf = self._window(tw.get("start"), tw.get("end"))

        if a not in wdf.columns or b not in wdf.columns:
            return {"error": "metric_not_found"}

        sa = wdf[a].dropna().values
        sb = wdf[b].dropna().values
        n = min(len(sa), len(sb))
        if n < 5:
            return {"error": "insufficient_data"}

        sa = sa[:n] - sa[:n].mean()
        sb = sb[:n] - sb[:n].mean()
        denom = (np.std(sa) * np.std(sb) * n)
        if denom == 0:
            return {"error": "zero_variance"}

        max_lag = min(10, n - 1)
        best_lag = 0
        best_corr = -1.0
        for lag in range(-max_lag, max_lag + 1):
            if lag < 0:
                x = sa[-lag:]
                y = sb[: n + lag]
            elif lag > 0:
                x = sa[: n - lag]
                y = sb[lag:]
            else:
                x = sa
                y = sb
            if len(x) < 3:
                continue
            corr = float(np.corrcoef(x, y)[0, 1])
            if abs(corr) > abs(best_corr):
                best_corr = corr
                best_lag = lag

        # infer interval seconds
        interval_sec = 15
        if len(wdf.index) >= 2:
            interval_sec = int((wdf.index[1] - wdf.index[0]).total_seconds())

        findings = [
            {
                "statistic_name": "best_lag_seconds",
                "value": int(best_lag * interval_sec),
                "interpretation": f"{a} leads {b}" if best_lag < 0 else f"{b} leads {a}" if best_lag > 0 else "同步变化",
            },
            {
                "statistic_name": "max_cross_correlation",
                "value": float(best_corr),
                "interpretation": "强延迟相关" if abs(best_corr) > 0.7 else "弱延迟相关",
            },
        ]
        return {"findings": findings, "summary": f"Lag analysis between {a} and {b}"}

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        at = args.get("analysis_type")
        try:
            if at == "correlation":
                return self._correlation(args)
            if at == "changepoint":
                return self._changepoint(args)
            if at == "group_compare":
                return self._group_compare(args)
            if at == "lag_analysis":
                return self._lag_analysis(args)
            return {"error": f"unsupported_analysis_type:{at}"}
        except Exception as e:
            return {"error": f"analysis_failed:{e}"}

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "analysis_type": {
                        "type": "string",
                        "enum": ["correlation", "changepoint", "group_compare", "lag_analysis"],
                    }
                },
                "required": ["analysis_type"],
            },
        }


def _corr_interp(v: float) -> str:
    av = abs(v)
    if av >= 0.8:
        return "强相关"
    if av >= 0.5:
        return "中等相关"
    if av >= 0.3:
        return "弱相关"
    return "几乎无相关"


def _normalize_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ts
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.tz_convert(None)
    return ts


def _normalize_time_index(index: pd.Index) -> pd.DatetimeIndex:
    dt_index = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce"))
    if getattr(dt_index, "tz", None) is not None:
        dt_index = dt_index.tz_convert(None)
    return dt_index
