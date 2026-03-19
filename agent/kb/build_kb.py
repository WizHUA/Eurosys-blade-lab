#!/usr/bin/env python3
"""
agent/kb/build_kb.py
HPC-Diagnosis Agent v4 — Phase A: Offline Knowledge Base Builder

Builds three offline artifacts:
  1. Validates metrics.yaml (Metric KB)
  2. Validates fpl.jsonl (Fault Pattern Library)
  3. Builds baseline profiles from pre-fault experiment windows
  4. Builds ChromaDB vector index from metrics.yaml for semantic lookup

Run from project root:
  /home/quantum/miniconda3/envs/blade-lab/bin/python agent/kb/build_kb.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KB_DIR = Path(__file__).resolve().parent
BASELINE_DIR = KB_DIR / "baseline"
CHROMA_DIR = KB_DIR / "chroma_db"
METRICS_YAML = KB_DIR / "metrics.yaml"
FPL_JSONL = KB_DIR / "fpl.jsonl"
FORMALTEST_DIR = PROJECT_ROOT / "dataset_builder" / "data" / "formaltest" / "extracted_data"

# Look-back window before fault injection for baseline extraction (seconds)
BASELINE_WINDOW_SEC = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# A-2: Validate metrics.yaml
# ===========================================================================

REQUIRED_METRIC_FIELDS = [
    "name", "subsystem", "description", "prometheus_query",
    "metric_type", "unit", "normal_range", "strength_thresholds",
    "related_faults", "downstream_effects", "common_misconceptions",
]
VALID_METRIC_TYPES = {"counter", "gauge", "derived_rate"}
VALID_SUBSYSTEMS = {"cpu", "memory", "swap", "vmstat", "disk", "filesystem",
                    "network", "load", "processes", "system"}
VALID_STRENGTH_LEVELS = {"weak", "medium", "strong"}


def validate_metrics_yaml() -> list[dict]:
    """Load and validate agent/kb/metrics.yaml. Returns the list of metric entries."""
    logger.info("Validating metrics.yaml ...")
    if not METRICS_YAML.exists():
        raise FileNotFoundError(f"metrics.yaml not found: {METRICS_YAML}")

    with open(METRICS_YAML, encoding="utf-8") as f:
        metrics = yaml.safe_load(f)

    if not isinstance(metrics, list):
        raise ValueError("metrics.yaml must be a YAML list at the top level")

    errors: list[str] = []
    for i, entry in enumerate(metrics):
        name = entry.get("name", f"<entry[{i}]>")
        for field in REQUIRED_METRIC_FIELDS:
            if field not in entry:
                errors.append(f"{name}: missing required field '{field}'")

        mt = entry.get("metric_type", "")
        if mt not in VALID_METRIC_TYPES:
            errors.append(f"{name}: invalid metric_type '{mt}' (must be one of {VALID_METRIC_TYPES})")

        sub = entry.get("subsystem", "")
        if sub not in VALID_SUBSYSTEMS:
            errors.append(f"{name}: invalid subsystem '{sub}' (must be one of {VALID_SUBSYSTEMS})")

        st = entry.get("strength_thresholds", {})
        for level in VALID_STRENGTH_LEVELS:
            if level not in st:
                errors.append(f"{name}: strength_thresholds missing level '{level}'")
            else:
                if "condition" not in st[level]:
                    errors.append(f"{name}: strength_thresholds.{level} missing 'condition'")
                if "min_duration_sec" not in st[level]:
                    errors.append(f"{name}: strength_thresholds.{level} missing 'min_duration_sec'")

    if errors:
        logger.error("metrics.yaml validation FAILED:")
        for err in errors:
            logger.error("  - %s", err)
        raise ValueError(f"metrics.yaml has {len(errors)} validation error(s)")

    logger.info("  OK: %d metrics validated", len(metrics))
    # Report subsystem breakdown
    from collections import Counter
    subsystem_counts = Counter(m["subsystem"] for m in metrics)
    for sub, count in sorted(subsystem_counts.items()):
        logger.info("    %s: %d metrics", sub, count)

    return metrics


# ===========================================================================
# A-4: Validate fpl.jsonl
# ===========================================================================

REQUIRED_FPL_FIELDS = [
    "pattern_id", "fault_type", "version", "status", "source",
    "confidence", "symptom_signature", "verification_steps", "solutions",
]
REQUIRED_SYMPTOM_FIELDS = [
    "leading_subsystem", "required_metrics", "temporal_pattern",
]
VALID_FPL_STATUSES = {"confirmed", "active", "candidate", "deprecated"}


def validate_fpl_jsonl() -> list[dict]:
    """Load and validate agent/kb/fpl.jsonl. Returns the list of FPL rules."""
    logger.info("Validating fpl.jsonl ...")
    if not FPL_JSONL.exists():
        raise FileNotFoundError(f"fpl.jsonl not found: {FPL_JSONL}")

    rules: list[dict] = []
    errors: list[str] = []

    with open(FPL_JSONL, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rule = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"Line {lineno}: JSON parse error: {exc}")
                continue
            rules.append(rule)

            pid = rule.get("pattern_id", f"<line {lineno}>")

            for field in REQUIRED_FPL_FIELDS:
                if field not in rule:
                    errors.append(f"{pid}: missing required field '{field}'")

            conf = rule.get("confidence", -1)
            if not (0.0 <= conf <= 1.0):
                errors.append(f"{pid}: confidence={conf} out of range [0, 1]")

            status = rule.get("status", "")
            if status not in VALID_FPL_STATUSES:
                errors.append(f"{pid}: invalid status '{status}'")

            sig = rule.get("symptom_signature", {})
            for sf in REQUIRED_SYMPTOM_FIELDS:
                if sf not in sig:
                    errors.append(f"{pid}: symptom_signature missing field '{sf}'")

            vs = rule.get("verification_steps", [])
            if not isinstance(vs, list) or len(vs) == 0:
                errors.append(f"{pid}: verification_steps must be a non-empty list")

    if errors:
        logger.error("fpl.jsonl validation FAILED:")
        for err in errors:
            logger.error("  - %s", err)
        raise ValueError(f"fpl.jsonl has {len(errors)} validation error(s)")

    logger.info("  OK: %d FPL rules validated", len(rules))
    for rule in rules:
        logger.info(
            "    %s: %s (confidence=%.2f, status=%s)",
            rule["pattern_id"], rule["fault_type"], rule["confidence"], rule["status"]
        )

    return rules


# ===========================================================================
# A-3: Build Baseline Profiles
# ===========================================================================

def _parse_fault_start_time(fault_info_path: Path) -> datetime | None:
    """Parse the fault injection start time from fault_info.txt."""
    pattern = re.compile(
        r"Injection Time Range:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"
    )
    try:
        text = fault_info_path.read_text(encoding="utf-8")
        match = pattern.search(text)
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        logger.warning("  Could not parse fault_info.txt at %s: %s", fault_info_path, exc)
    return None


def _compute_stats(series: pd.Series) -> dict[str, Any]:
    """Compute summary statistics for a numeric series."""
    clean = series.dropna()
    if len(clean) == 0:
        return {"mean": None, "std": None, "p5": None, "p25": None,
                "p50": None, "p75": None, "p95": None, "max": None,
                "sample_count": 0}
    return {
        "mean": round(float(clean.mean()), 6),
        "std": round(float(clean.std()), 6),
        "p5": round(float(np.percentile(clean, 5)), 6),
        "p25": round(float(np.percentile(clean, 25)), 6),
        "p50": round(float(np.percentile(clean, 50)), 6),
        "p75": round(float(np.percentile(clean, 75)), 6),
        "p95": round(float(np.percentile(clean, 95)), 6),
        "max": round(float(clean.max()), 6),
        "sample_count": int(len(clean)),
    }


# Key metrics to include in baseline profiles (subset of all ~170 cols)
BASELINE_METRICS = [
    "cpu_usage_percent", "cpu_iowait_percent", "context_switches_rate",
    "load_1min", "load_5min",
    "processes_running", "processes_blocked",
    "memory_usage_percent", "swap_usage_percent", "page_major_faults_rate",
    "anon_memory_percent", "memory_dirty_bytes", "cache_usage_percent", "buffer_usage_percent",
    "disk_io_usage_percent", "disk_read_rate_bytes_per_sec", "disk_write_rate_bytes_per_sec",
    "filesystem_usage_percent", "filesystem_files_free",
    "network_receive_rate_bytes_per_sec", "network_transmit_rate_bytes_per_sec",
    "network_receive_drop_rate", "network_transmit_drop_rate",
    "network_receive_errors_rate",
    "filefd_usage_percent",
]


def build_baseline_profiles() -> dict:
    """
    Build per-experiment pre-fault baseline profiles and a global aggregated profile.

    For each experiment under FORMALTEST_DIR:
      1. Read fault_info.txt to get fault injection start time.
      2. Read metrics.csv, filter rows within [fault_start - BASELINE_WINDOW_SEC, fault_start).
      3. Compute mean/std/p5/.../p95 for each key metric.
      4. Write individual JSON to baseline/exp_{xxx}_pre_fault.json.

    Then aggregate all pre-fault windows into baseline/global.json.
    """
    logger.info("Building baseline profiles (window=%ds before fault injection) ...", BASELINE_WINDOW_SEC)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    exp_dirs = sorted(FORMALTEST_DIR.iterdir()) if FORMALTEST_DIR.exists() else []
    if not exp_dirs:
        logger.warning("No experiment directories found under %s", FORMALTEST_DIR)
        return {}

    all_pre_fault_frames: list[pd.DataFrame] = []
    per_exp_summaries: dict[str, dict] = {}

    for exp_dir in exp_dirs:
        if not exp_dir.is_dir():
            continue
        exp_id = exp_dir.name

        fault_info_path = exp_dir / "fault_info.txt"
        metrics_path = exp_dir / "metrics.csv"

        if not fault_info_path.exists() or not metrics_path.exists():
            logger.debug("  Skipping %s: missing fault_info.txt or metrics.csv", exp_id)
            continue

        fault_start = _parse_fault_start_time(fault_info_path)
        if fault_start is None:
            logger.warning("  Skipping %s: could not parse fault start time", exp_id)
            continue

        try:
            df = pd.read_csv(metrics_path, parse_dates=["timestamp"])
        except Exception as exc:
            logger.warning("  Skipping %s: failed to read metrics.csv: %s", exp_id, exc)
            continue

        if "timestamp" not in df.columns:
            logger.warning("  Skipping %s: no 'timestamp' column", exp_id)
            continue

        # Ensure timestamps are tz-naive for comparison
        if hasattr(df["timestamp"].dtype, "tz") and df["timestamp"].dt.tz is not None:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)
        fault_start_naive = fault_start.replace(tzinfo=None) if fault_start.tzinfo else fault_start

        window_start = fault_start_naive - timedelta(seconds=BASELINE_WINDOW_SEC)
        pre_fault = df[
            (df["timestamp"] >= window_start) & (df["timestamp"] < fault_start_naive)
        ].copy()

        if len(pre_fault) == 0:
            logger.warning(
                "  Skipping %s: no data in pre-fault window [%s, %s)",
                exp_id, window_start.strftime("%H:%M:%S"), fault_start_naive.strftime("%H:%M:%S")
            )
            continue

        logger.info(
            "  %s: fault_start=%s, pre-fault rows=%d",
            exp_id, fault_start_naive.strftime("%Y-%m-%d %H:%M:%S"), len(pre_fault)
        )

        # Compute per-experiment stats for key metrics
        metric_stats: dict[str, Any] = {}
        present_metrics = []
        for metric in BASELINE_METRICS:
            if metric in pre_fault.columns:
                stats = _compute_stats(pre_fault[metric])
                stats["window_size_sec"] = BASELINE_WINDOW_SEC
                metric_stats[metric] = stats
                present_metrics.append(metric)

        exp_profile = {
            "profile_id": f"pre_fault_{exp_id}",
            "experiment_id": exp_id,
            "fault_start_time": fault_start_naive.isoformat(),
            "computation_window": {
                "description": f"Pre-fault window: [{window_start.isoformat()}, {fault_start_naive.isoformat()})",
                "window_size_sec": BASELINE_WINDOW_SEC,
                "sample_count": len(pre_fault),
            },
            "created_at": datetime.utcnow().isoformat() + "Z",
            "metrics": metric_stats,
        }

        out_path = BASELINE_DIR / f"{exp_id}_pre_fault.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(exp_profile, f, indent=2, ensure_ascii=False)

        per_exp_summaries[exp_id] = exp_profile
        all_pre_fault_frames.append(pre_fault[["timestamp"] + [m for m in BASELINE_METRICS if m in pre_fault.columns]])

    # ---- Build global profile ----
    if not all_pre_fault_frames:
        logger.warning("No valid pre-fault windows found; skipping global profile")
        return per_exp_summaries

    global_df = pd.concat(all_pre_fault_frames, ignore_index=True)
    global_metrics: dict[str, Any] = {}
    for metric in BASELINE_METRICS:
        if metric in global_df.columns:
            stats = _compute_stats(global_df[metric])
            stats["window_size_sec"] = BASELINE_WINDOW_SEC
            stats["source_experiments"] = len(all_pre_fault_frames)
            global_metrics[metric] = stats

    global_profile = {
        "profile_id": "global",
        "description": "Aggregated pre-fault baseline from all formaltest experiments",
        "source_experiment_count": len(all_pre_fault_frames),
        "source_experiments": [p["profile_id"] for p in per_exp_summaries.values()],
        "total_sample_rows": len(global_df),
        "computation_window": {
            "description": "Union of all pre-fault windows (5 min before each fault injection)",
            "window_size_sec": BASELINE_WINDOW_SEC,
        },
        "created_at": datetime.utcnow().isoformat() + "Z",
        "metrics": global_metrics,
    }

    global_out = BASELINE_DIR / "global.json"
    with open(global_out, "w", encoding="utf-8") as f:
        json.dump(global_profile, f, indent=2, ensure_ascii=False)

    logger.info(
        "Global baseline profile: %d experiments, %d total rows, saved to %s",
        len(all_pre_fault_frames), len(global_df), global_out
    )

    # Print key metric summary
    key_display = ["cpu_usage_percent", "memory_usage_percent",
                   "disk_io_usage_percent", "network_receive_drop_rate"]
    logger.info("  Key metrics in global baseline:")
    for m in key_display:
        if m in global_metrics and global_metrics[m]["mean"] is not None:
            s = global_metrics[m]
            logger.info(
                "    %-40s mean=%.3f  std=%.3f  p95=%.3f",
                m, s["mean"], s["std"], s["p95"]
            )

    return per_exp_summaries


# ===========================================================================
# A-5: Build ChromaDB Vector Index (local TF-IDF embedding, no internet needed)
# ===========================================================================

class _LocalTFIDFEmbeddingFunction:
    """
    Pure-numpy TF-IDF embedding function for ChromaDB.
    Fits a vocabulary on the document corpus, then represents each document
    as an L2-normalised TF-IDF vector.  No external model or internet required.

    This is intentionally lightweight: the KB has only ~22 documents and the
    primary use-case is keyword-level semantic retrieval (fault_type → metric).
    If a neural embedding becomes available, replace with it.
    """

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._idf: np.ndarray | None = None
        self._corpus_vectors: np.ndarray | None = None
        self._corpus_docs: list[str] = []

    # ---- tokenizer ----
    @staticmethod
    def _tokenize(text: str) -> list[str]:
        # Lowercase, split on non-alphanumeric, drop empty tokens
        tokens = re.split(r"[^a-z0-9_]+", text.lower())
        return [t for t in tokens if t]

    def _build_vocab(self, documents: list[str]) -> None:
        vocab: set[str] = set()
        for doc in documents:
            vocab.update(self._tokenize(doc))
        self._vocab = {tok: i for i, tok in enumerate(sorted(vocab))}

    def _tf(self, tokens: list[str]) -> np.ndarray:
        vec = np.zeros(len(self._vocab), dtype=np.float32)
        for tok in tokens:
            if tok in self._vocab:
                vec[self._vocab[tok]] += 1.0
        if vec.sum() > 0:
            vec /= vec.sum()
        return vec

    def _compute_idf(self, documents: list[str]) -> np.ndarray:
        n = len(documents)
        df = np.zeros(len(self._vocab), dtype=np.float32)
        for doc in documents:
            tokens_set = set(self._tokenize(doc))
            for tok in tokens_set:
                if tok in self._vocab:
                    df[self._vocab[tok]] += 1.0
        # Smooth IDF: log((1+n)/(1+df)) + 1  (sklearn-style)
        idf = np.log((1.0 + n) / (1.0 + df)) + 1.0
        return idf

    def _embed(self, text: str) -> np.ndarray:
        tokens = self._tokenize(text)
        tf_vec = self._tf(tokens)
        tfidf_vec = tf_vec * self._idf  # type: ignore[operator]
        norm = np.linalg.norm(tfidf_vec)
        if norm > 0:
            tfidf_vec /= norm
        return tfidf_vec.tolist()

    def fit(self, documents: list[str]) -> None:
        """Fit vocabulary and IDF weights on a corpus of documents."""
        self._build_vocab(documents)
        self._idf = self._compute_idf(documents)

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        if self._idf is None:
            raise RuntimeError("Call fit() before __call__()")
        return [self._embed(doc) for doc in input]


def build_chroma_index(metrics: list[dict]) -> None:
    """
    Vectorize all metric KB entries and persist to ChromaDB.

    Uses a local pure-numpy TF-IDF embedding function (no internet required).
    Collection name: 'metric_kb'
    Persisted to: agent/kb/chroma_db/
    """
    logger.info("Building ChromaDB vector index (local TF-IDF, no network) ...")
    try:
        import chromadb
    except ImportError:
        logger.error("chromadb not installed. Run: pip install chromadb")
        raise

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    # Build document corpus
    docs: list[str] = []
    ids: list[str] = []
    metadatas: list[dict] = []

    for entry in metrics:
        name = entry["name"]
        description = entry.get("description", "")
        common_misconceptions = entry.get("common_misconceptions", "")
        related_faults = ", ".join(entry.get("related_faults", []))

        doc = (
            f"Metric: {name} "
            f"Subsystem: {entry.get('subsystem', '')} "
            f"Description: {description} "
            f"Related faults: {related_faults} "
            f"Common misconceptions: {common_misconceptions}"
        )
        docs.append(doc)
        ids.append(name)
        metadatas.append({
            "name": name,
            "subsystem": entry.get("subsystem", ""),
            "metric_type": entry.get("metric_type", ""),
            "unit": entry.get("unit", ""),
            "related_faults": related_faults,
        })

    # Fit TF-IDF on corpus
    ef = _LocalTFIDFEmbeddingFunction()
    ef.fit(docs)
    embeddings = ef(docs)

    # Build ChromaDB collection
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Delete existing collection to allow rebuild
    try:
        client.delete_collection("metric_kb")
        logger.debug("  Deleted existing 'metric_kb' collection")
    except Exception:
        pass

    collection = client.create_collection(
        name="metric_kb",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    collection.add(documents=docs, ids=ids, metadatas=metadatas, embeddings=embeddings)

    logger.info(
        "  ChromaDB index built: %d documents in collection 'metric_kb', persisted to %s",
        len(docs), CHROMA_DIR
    )

    # Smoke-test: query for CPU-related metrics
    query_text = "CPU overload high usage fullload"
    query_embedding = ef([query_text])
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=3,
    )
    logger.info("  Smoke-test query '%s' → top-3 results:", query_text)
    for idx, doc_id in enumerate(results["ids"][0]):
        dist = results["distances"][0][idx] if results.get("distances") else "N/A"
        logger.info("    [%d] %s (cosine_distance=%.4f)", idx + 1, doc_id, float(dist))

    # Second smoke-test: memory-related query
    query_text2 = "memory leak anon high RAM pressure swap"
    query_embedding2 = ef([query_text2])
    results2 = collection.query(
        query_embeddings=query_embedding2,
        n_results=3,
    )
    logger.info("  Smoke-test query '%s' → top-3 results:", query_text2)
    for idx, doc_id in enumerate(results2["ids"][0]):
        dist = results2["distances"][0][idx] if results2.get("distances") else "N/A"
        logger.info("    [%d] %s (cosine_distance=%.4f)", idx + 1, doc_id, float(dist))


# ===========================================================================
# Main
# ===========================================================================

def main() -> None:
    logger.info("=" * 60)
    logger.info("HPC-Diagnosis Agent v4 — Phase A: KB Builder")
    logger.info("Project root: %s", PROJECT_ROOT)
    logger.info("=" * 60)

    # Step 1: Validate Metric KB
    try:
        metrics = validate_metrics_yaml()
        logger.info("Step 1/4 PASSED: metrics.yaml (%d entries)\n", len(metrics))
    except Exception as exc:
        logger.error("Step 1/4 FAILED: %s", exc)
        sys.exit(1)

    # Step 2: Validate Fault Pattern Library
    try:
        fpl_rules = validate_fpl_jsonl()
        logger.info("Step 2/4 PASSED: fpl.jsonl (%d rules)\n", len(fpl_rules))
    except Exception as exc:
        logger.error("Step 2/4 FAILED: %s", exc)
        sys.exit(1)

    # Step 3: Build Baseline Profiles
    try:
        per_exp = build_baseline_profiles()
        logger.info("Step 3/4 PASSED: %d per-experiment profiles + global.json\n", len(per_exp))
    except Exception as exc:
        logger.error("Step 3/4 FAILED: %s", exc)
        sys.exit(1)

    # Step 4: Build ChromaDB Index
    try:
        build_chroma_index(metrics)
        logger.info("Step 4/4 PASSED: ChromaDB index built\n")
    except Exception as exc:
        logger.error("Step 4/4 FAILED: %s", exc)
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Phase A Knowledge Base build COMPLETE")
    logger.info("  Metric KB:      %s", METRICS_YAML)
    logger.info("  FPL:            %s", FPL_JSONL)
    logger.info("  Baseline:       %s", BASELINE_DIR)
    logger.info("  ChromaDB:       %s", CHROMA_DIR)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
