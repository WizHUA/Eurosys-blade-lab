"""agent/config.py — 全局配置常量"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ===========================================================================
# 路径常量
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
KB_DIR = PROJECT_ROOT / "agent" / "kb"
METRICS_YAML = KB_DIR / "metrics.yaml"
FPL_JSONL = KB_DIR / "fpl.jsonl"
CHROMA_DIR = KB_DIR / "chroma_db"
FORMALTEST_DIR = PROJECT_ROOT / "dataset_builder" / "data" / "formaltest" / "extracted_data"
OUTPUT_DIR = PROJECT_ROOT / "agent" / "data"


# ===========================================================================
# LLM 配置
# ===========================================================================
@dataclass
class LLMConfig:
    base_url: str = "https://openrouter.ai/api/v1"
    model_name: str = "deepseek/deepseek-chat"
    api_key: str = ""                    # 从 .env 加载，不硬编码
    temperature: float = 0.3
    max_tokens: int = 4096
    retry_max: int = 3
    retry_delay_sec: float = 2.0
    timeout_sec: float = 120.0


# ===========================================================================
# Budget 配置
# ===========================================================================
@dataclass
class BudgetConfig:
    # Diagnosis Agent
    tool_calls_limit: int = 15
    max_react_iterations: int = 15   # 超过后强制 conclude
    # Audit Agent
    audit_tool_calls_limit: int = 3
    audit_max_rounds: int = 2
    # Orchestrator
    max_rehyp: int = 1               # 最多 1 次重新假设
    max_orchestrator_rounds: int = 3  # Diagnosis-Audit 最大交互轮次


# ===========================================================================
# Triage 超参数
# ===========================================================================
@dataclass
class TriageConfig:
    baseline_window_points: int = 5    # 前 N 个采样点作为基线
    z_score_threshold: float = 3.0     # Z-score 异常阈值
    persistence_ratio: float = 0.3     # 连续超阈值比例
    top_k: int = 15                    # 保留 Top-K 异常指标
    min_anomaly_duration_sec: int = 30 # 最短异常持续时间


# ===========================================================================
# 消融开关
# ===========================================================================
@dataclass
class AblationFlags:
    """每个 flag 为 True 时表示启用该组件（默认全启用 = Full 版本）"""
    enable_triage: bool = True         # Abl-A: False 时跳过 Triage
    enable_audit: bool = True          # Abl-B: False 时跳过 Audit Agent


# ===========================================================================
# 综合配置
# ===========================================================================
@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    triage: TriageConfig = field(default_factory=TriageConfig)
    ablation: AblationFlags = field(default_factory=AblationFlags)
    output_dir: Path = OUTPUT_DIR

    @classmethod
    def from_ablation_id(cls, ablation_id: str) -> "AgentConfig":
        """根据消融实验 ID 构建配置"""
        flags_map = {
            "Full": AblationFlags(),
            "Abl-A": AblationFlags(enable_triage=False),
            "Abl-B": AblationFlags(enable_audit=False),
        }
        if ablation_id not in flags_map:
            raise ValueError(
                f"Unknown ablation_id: {ablation_id}, must be one of {list(flags_map)}"
            )
        return cls(ablation=flags_map[ablation_id])
