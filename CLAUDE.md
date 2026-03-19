# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An **HPC Anomaly Diagnosis** research platform with two phases:
1. **Dataset Builder** (`dataset_builder/`) — injects faults into HPC clusters and collects system metrics
2. **LLM Diagnosis** (`llm/`) — uses LLMs to diagnose root causes from collected data

The current development goal is upgrading the LLM module from a single-call LLM interaction to a multi-step **Agent** (see `design/` for target architecture).

## Running the Project

### Phase 1: Data Collection
```bash
cd dataset_builder
python run.py --experiment formaltest
```
Requires live HPC cluster with Slurm, Prometheus, ChaosBlade, and NPB binaries.

### Phase 2: LLM Diagnosis
```bash
cd llm
python run.py --experiment formaltest
```
Requires `OPENROUTER_API_KEY` in a `.env` file (see `.env.example`).

### Dependencies
```bash
pip install -r dataset_builder/requirements.txt
```

## Architecture

### Dataset Builder (`dataset_builder/src/`)

Three-component concurrent model orchestrated by `ExperimentManager`:

| Component | File | Role |
|-----------|------|------|
| `ExperimentManager` | `experiment.py` | Orchestrator; loads YAML config, coordinates threads, exports CSV |
| `WorkloadManager` | `workload.py` | Submits NPB jobs via Slurm, tracks via `squeue` |
| `ChaosManager` | `chaos.py` | Wraps ChaosBlade CLI for fault injection (CPU/mem/net/disk) |
| `PrometheusMonitor` | `monitor.py` | Polls 150+ metrics via PromQL at configurable intervals |

All three managers run as concurrent threads. Experiment lifecycle: prepare → execute → recover → cleanup. Outputs `metrics.csv`, `jobinfo.csv`, `chaos.csv` to `dataset_builder/data/<experiment_name>/`.

Configuration is YAML-driven (`config/experiments.yaml`, `chaos.yaml`, `workloads.yaml`, `metrics.yaml`).

### LLM Diagnosis (`llm/src/`)

Current (pre-agent) pipeline in `main.py`:
1. Load `metrics.csv` + `jobinfo.csv`
2. Construct prompt: system prompt (`ref/prompt.md`) + few-shot examples (`ref/fewshot/shot*.txt`) + serialized data
3. Single LLM call via OpenRouter (OpenAI-compatible API)
4. Parse JSON response → `diagnosis_report.json`

Uses `openai` SDK pointed at OpenRouter. Model selection is configured in `run.py`.

### Target Agent Architecture (`design/`)

The upgrade target is documented in `design/v3.md` and `design/v3_detail.md`:

- **Single unified agent** with a reasoning loop: Hypothesize → Gather Evidence → Verify → Finalize
- **Deterministic triage** runs first (fast, reproducible anomaly detection + context compression)
- **Evidence gating**: conclusions require minimum evidence thresholds before finalizing
- **4 tools**: `TriageTool`, `MetricQueryTool`, `KBRetrievalTool`, `DataAnalysisTool`
- **3 knowledge bases**: Metric KB (semantic definitions), Baseline Profile (normal ranges), Fault Pattern Library (symptom → root cause mappings)
- **LangGraph** state machine for formal execution semantics and audit trails

Read `design/v3_detail.md` for the full specification before implementing any agent components.

## Key Data Paths

- Experiment raw data: `dataset_builder/data/<experiment_name>/`
- LLM results: `llm/data/<experiment_name>/`
- Few-shot examples: `llm/ref/fewshot/shot*.txt`
- System prompt: `llm/ref/prompt.md`
- 29+ pre-collected formal test scenarios: `dataset_builder/data/formaltest/`

## External System Dependencies

The dataset builder requires a real HPC cluster. For development/testing of the LLM/agent module only, the pre-collected data in `dataset_builder/data/` can be used directly without a live cluster.

Required external systems (dataset builder only): Slurm 20.02+, Prometheus 2.0+, Node Exporter, ChaosBlade 1.7.0+, OpenMPI/MPICH, NPB 3.4.2 MPI binaries.
