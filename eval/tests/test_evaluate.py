"""eval/tests/test_evaluate.py — A-type TDD tests for evaluation framework.

Tests parse_fault_info, match_fault_type, Evaluator.evaluate_single.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from eval.evaluate import Evaluator, match_fault_type, parse_fault_info


# ======================================================================
# Fixtures
# ======================================================================

SINGLE_FAULT_TXT = """\
Fault Types: cpu-fullload
Experiment IDs: fb581fa9d70b23ae
Injection Time Range: 2025-09-20 07:02:13 - 2025-09-20 07:03:14
Fault Commands:
blade status fb581fa9d70b23ae

Detailed Fault Information:
1. ID: fb581fa9d70b23ae
   Type: cpu-fullload
   Create: 2025-09-20 07:02:13
   End: 2025-09-20 07:03:14
"""

COMPOSITE_FAULT_TXT = """\
Fault Types: mem-load, cpu-fullload
Experiment IDs: b42609e6da44c717, ca3248e485509d0b
Injection Time Range: 2025-09-20 07:16:13 - 2025-09-20 07:17:14
Fault Commands:
blade status b42609e6da44c717
blade status ca3248e485509d0b

Detailed Fault Information:
1. ID: b42609e6da44c717
   Type: mem-load
   Create: 2025-09-20 07:16:13
   End: 2025-09-20 07:17:14

2. ID: ca3248e485509d0b
   Type: cpu-fullload
   Create: 2025-09-20 07:16:13
   End: 2025-09-20 07:17:14
"""


def _make_report(fault_types: list[str], diagnosis_type: str = "single_fault", run_id: str = "test"):
    """Helper to build a minimal DiagnosisReport for testing."""
    from agent.schema import DiagnosisReport, RootCause, TraceSummary

    root_causes = [
        RootCause(
            cause=f"{ft} detected",
            fault_type=ft,
            confidence=0.9 - i * 0.1,
            evidence_ids=[f"e{i}"],
        )
        for i, ft in enumerate(fault_types)
    ]
    trace = TraceSummary(
        triage_leading_subsystem="cpu",
        triage_confidence=0.8,
        main_tools_used=["MetricQueryTool"],
        audit_tools_used=[],
        total_tool_calls=5,
        main_iterations=3,
        audit_iterations=1,
        total_tokens_in=1000,
        total_tokens_out=500,
        diagnosis_duration_sec=10.0,
    )
    return DiagnosisReport(
        run_id=run_id,
        anomaly_summary="test anomaly",
        diagnosis_type=diagnosis_type,
        root_causes=root_causes,
        solutions=[],
        uncertainties=[],
        trace_summary=trace,
        generated_at=datetime.now(),
    )


# ======================================================================
# §1: parse_fault_info tests
# ======================================================================

class TestParseFaultInfo:

    def test_single_fault(self, tmp_path):
        p = tmp_path / "fault_info.txt"
        p.write_text(SINGLE_FAULT_TXT)
        result = parse_fault_info(str(p))
        assert len(result) == 1
        assert result[0]["fault_type"] == "cpu_fullload"

    def test_composite_fault(self, tmp_path):
        p = tmp_path / "fault_info.txt"
        p.write_text(COMPOSITE_FAULT_TXT)
        result = parse_fault_info(str(p))
        assert len(result) == 2
        types = {r["fault_type"] for r in result}
        assert types == {"mem_load", "cpu_fullload"}

    def test_has_time_fields(self, tmp_path):
        p = tmp_path / "fault_info.txt"
        p.write_text(SINGLE_FAULT_TXT)
        result = parse_fault_info(str(p))
        assert "start_time" in result[0]
        assert "end_time" in result[0]

    def test_hyphen_to_underscore(self, tmp_path):
        """fault_info.txt uses hyphens but our system uses underscores."""
        p = tmp_path / "fault_info.txt"
        p.write_text(SINGLE_FAULT_TXT)
        result = parse_fault_info(str(p))
        assert "_" in result[0]["fault_type"]
        assert "-" not in result[0]["fault_type"]

    def test_real_exp_001(self):
        path = Path("dataset_builder/data/formaltest/extracted_data/exp_001_cpu_fullload/fault_info.txt")
        if not path.exists():
            pytest.skip("Real data not available")
        result = parse_fault_info(str(path))
        assert len(result) == 1
        assert result[0]["fault_type"] == "cpu_fullload"

    def test_real_exp_008(self):
        path = Path("dataset_builder/data/formaltest/extracted_data/exp_008_cpu_fullload_mem_load/fault_info.txt")
        if not path.exists():
            pytest.skip("Real data not available")
        result = parse_fault_info(str(path))
        assert len(result) == 2
        types = {r["fault_type"] for r in result}
        assert "cpu_fullload" in types
        assert "mem_load" in types


# ======================================================================
# §2: match_fault_type tests
# ======================================================================

class TestMatchFaultType:

    def test_exact_match(self):
        assert match_fault_type("cpu_fullload", "cpu_fullload") is True

    def test_case_insensitive(self):
        assert match_fault_type("CPU_Fullload", "cpu_fullload") is True

    def test_no_match(self):
        assert match_fault_type("cpu_fullload", "mem_load") is False

    def test_alias_mem_load(self):
        assert match_fault_type("mem_load_ram", "mem_load") is True
        assert match_fault_type("mem_load", "mem_load_ram") is True

    def test_alias_network_loss(self):
        assert match_fault_type("net_loss", "network_loss") is True
        assert match_fault_type("packet_loss", "network_loss") is True

    def test_alias_disk_burn(self):
        assert match_fault_type("disk_io_burn", "disk_burn") is True
        assert match_fault_type("io_burn", "disk_burn") is True

    def test_alias_disk_fill(self):
        assert match_fault_type("disk_space_fill", "disk_fill") is True
        assert match_fault_type("filesystem_fill", "disk_fill") is True

    def test_hyphen_underscore_equivalent(self):
        assert match_fault_type("cpu-fullload", "cpu_fullload") is True
        assert match_fault_type("disk-burn", "disk_burn") is True


# ======================================================================
# §3: Evaluator.evaluate_single tests
# ======================================================================

class TestEvaluateSingle:

    def setup_method(self):
        self.evaluator = Evaluator()

    def test_hit_at_1_single(self):
        report = _make_report(["cpu_fullload"], "single_fault")
        gt = [{"fault_type": "cpu_fullload"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["hit_at_1"] is True
        assert result["hit_at_3"] is True
        assert result["composite_coverage"] == 1.0
        assert result["false_positives"] == 0

    def test_hit_at_1_miss(self):
        report = _make_report(["disk_burn"], "single_fault")
        gt = [{"fault_type": "cpu_fullload"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["hit_at_1"] is False
        assert result["false_positives"] == 1

    def test_hit_at_3_match(self):
        report = _make_report(["disk_burn", "mem_load", "cpu_fullload"], "composite_fault")
        gt = [{"fault_type": "cpu_fullload"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["hit_at_1"] is False
        assert result["hit_at_3"] is True

    def test_composite_coverage(self):
        report = _make_report(["cpu_fullload", "mem_load"], "composite_fault")
        gt = [{"fault_type": "cpu_fullload"}, {"fault_type": "mem_load"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["composite_coverage"] == 1.0
        assert result["false_positives"] == 0

    def test_partial_coverage(self):
        report = _make_report(["cpu_fullload"], "single_fault")
        gt = [{"fault_type": "cpu_fullload"}, {"fault_type": "mem_load"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["composite_coverage"] == 0.5

    def test_false_positives(self):
        report = _make_report(["cpu_fullload", "network_loss", "disk_burn"], "composite_fault")
        gt = [{"fault_type": "cpu_fullload"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["false_positives"] == 2

    def test_inconclusive_report(self):
        from agent.schema import DiagnosisReport, TraceSummary
        trace = TraceSummary(
            triage_leading_subsystem="cpu",
            triage_confidence=0.8,
            total_tool_calls=5,
            main_iterations=3,
            audit_iterations=1,
            total_tokens_in=1000,
            total_tokens_out=500,
        )
        report = DiagnosisReport(
            run_id="test",
            anomaly_summary="test",
            diagnosis_type="inconclusive",
            root_causes=[],
            solutions=[],
            uncertainties=["not sure"],
            trace_summary=trace,
            generated_at=datetime.now(),
        )
        gt = [{"fault_type": "cpu_fullload"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["hit_at_1"] is False
        assert result["composite_coverage"] == 0.0
        assert result["diagnosis_type"] == "inconclusive"

    def test_alias_matching_in_evaluate(self):
        report = _make_report(["mem_load_ram"], "single_fault")
        gt = [{"fault_type": "mem_load"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["hit_at_1"] is True

    def test_tool_calls_and_metadata(self):
        report = _make_report(["cpu_fullload"], "single_fault")
        gt = [{"fault_type": "cpu_fullload"}]
        result = self.evaluator.evaluate_single(report, gt)
        assert result["tool_calls"] == 5
        assert result["diagnosis_type"] == "single_fault"
        assert "latency_seconds" in result
