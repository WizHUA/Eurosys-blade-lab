"""agent/tests/test_config.py — Config 模块 TDD 测试 (Phase B)"""

import pytest
from pathlib import Path


def test_path_constants_exist():
    """PROJECT_ROOT / KB_DIR / FORMALTEST_DIR 等路径常量应存在。"""
    from agent.config import PROJECT_ROOT, KB_DIR, METRICS_YAML, FPL_JSONL, CHROMA_DIR, FORMALTEST_DIR, OUTPUT_DIR
    assert isinstance(PROJECT_ROOT, Path)
    assert isinstance(KB_DIR, Path)
    assert KB_DIR == PROJECT_ROOT / "agent" / "kb"
    assert METRICS_YAML == KB_DIR / "metrics.yaml"
    assert FPL_JSONL == KB_DIR / "fpl.jsonl"
    assert CHROMA_DIR == KB_DIR / "chroma_db"
    assert FORMALTEST_DIR == PROJECT_ROOT / "dataset_builder" / "data" / "formaltest" / "extracted_data"
    assert OUTPUT_DIR == PROJECT_ROOT / "agent" / "data"


def test_llm_config_defaults():
    """LLMConfig 应有合理默认值且 api_key 为空。"""
    from agent.config import LLMConfig
    cfg = LLMConfig()
    assert cfg.base_url == "https://openrouter.ai/api/v1"
    assert cfg.model_name == "deepseek/deepseek-chat"
    assert cfg.api_key == ""
    assert cfg.temperature == 0.3
    assert cfg.max_tokens == 4096
    assert cfg.retry_max == 3


def test_budget_config_defaults():
    """BudgetConfig 默认值与实现规格一致。"""
    from agent.config import BudgetConfig
    cfg = BudgetConfig()
    assert cfg.tool_calls_limit == 15
    assert cfg.max_react_iterations == 15
    assert cfg.audit_tool_calls_limit == 3
    assert cfg.audit_max_rounds == 2
    assert cfg.max_rehyp == 1
    assert cfg.max_orchestrator_rounds == 3


def test_triage_config_defaults():
    """TriageConfig 默认值与实现规格一致。"""
    from agent.config import TriageConfig
    cfg = TriageConfig()
    assert cfg.baseline_window_points == 5
    assert cfg.z_score_threshold == 3.0
    assert cfg.persistence_ratio == 0.3
    assert cfg.top_k == 15
    assert cfg.min_anomaly_duration_sec == 30


def test_ablation_flags_default_full():
    """AblationFlags 默认全启用 (Full 配置)。"""
    from agent.config import AblationFlags
    flags = AblationFlags()
    assert flags.enable_triage is True
    assert flags.enable_audit is True


def test_ablation_flags_abl_a():
    """Abl-A: 禁用 triage。"""
    from agent.config import AblationFlags
    flags = AblationFlags(enable_triage=False)
    assert flags.enable_triage is False
    assert flags.enable_audit is True


def test_ablation_flags_abl_b():
    """Abl-B: 禁用 audit。"""
    from agent.config import AblationFlags
    flags = AblationFlags(enable_audit=False)
    assert flags.enable_triage is True
    assert flags.enable_audit is False


def test_agent_config_defaults():
    """AgentConfig 应组合所有子配置。"""
    from agent.config import AgentConfig, LLMConfig, BudgetConfig, TriageConfig, AblationFlags, OUTPUT_DIR
    cfg = AgentConfig()
    assert isinstance(cfg.llm, LLMConfig)
    assert isinstance(cfg.budget, BudgetConfig)
    assert isinstance(cfg.triage, TriageConfig)
    assert isinstance(cfg.ablation, AblationFlags)
    assert cfg.output_dir == OUTPUT_DIR


def test_from_ablation_id_full():
    """from_ablation_id('Full') 全启用。"""
    from agent.config import AgentConfig
    cfg = AgentConfig.from_ablation_id("Full")
    assert cfg.ablation.enable_triage is True
    assert cfg.ablation.enable_audit is True


def test_from_ablation_id_abl_a():
    """from_ablation_id('Abl-A') 禁用 triage。"""
    from agent.config import AgentConfig
    cfg = AgentConfig.from_ablation_id("Abl-A")
    assert cfg.ablation.enable_triage is False
    assert cfg.ablation.enable_audit is True


def test_from_ablation_id_abl_b():
    """from_ablation_id('Abl-B') 禁用 audit。"""
    from agent.config import AgentConfig
    cfg = AgentConfig.from_ablation_id("Abl-B")
    assert cfg.ablation.enable_triage is True
    assert cfg.ablation.enable_audit is False


def test_from_ablation_id_invalid():
    """无效 ablation_id 应抛 ValueError。"""
    from agent.config import AgentConfig
    with pytest.raises(ValueError, match="Unknown ablation_id"):
        AgentConfig.from_ablation_id("Abl-C")


def test_agent_config_is_dataclass():
    """AgentConfig 应是 dataclass，支持 field override。"""
    from agent.config import AgentConfig, LLMConfig
    cfg = AgentConfig(llm=LLMConfig(temperature=0.7))
    assert cfg.llm.temperature == 0.7
