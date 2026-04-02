"""eval/label_mapper.py — LLM-based fault label mapping for evaluation.

This module is INDEPENDENT from the agent pipeline. It takes a DiagnosisReport
(which contains natural-language fault descriptions) and maps each root_cause
to a canonical fault label from a predefined list, for evaluation purposes only.

Design rationale:
  - The agent pipeline must NOT know about canonical fault labels (no leakage)
  - Evaluation needs comparable labels to compute Hit@1/Hit@3/etc.
  - This module bridges that gap with a lightweight LLM call
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from agent.schema import DiagnosisReport

load_dotenv()

logger = logging.getLogger(__name__)

# Canonical fault labels for our experiment set
CANONICAL_FAULT_LABELS = [
    "cpu_fullload",
    "mem_load_ram",
    "mem_load_buffer",
    "network_loss",
    "network_delay",
    "network_corrupt",
    "disk_burn",
    "disk_fill",
    "no_fault",
]

_LABEL_MAPPING_PROMPT = """\
你是一个故障标签分类器。给定一段 HPC 系统的诊断描述，将其映射到最匹配的标准故障标签。

## 标准故障标签列表
{labels}

## 规则
1. 只能从上述标签中选择，不要编造新标签
2. 如果诊断描述明显不属于任何已知标签，使用 "no_fault"
3. 只返回标签名，不要添加任何解释

## 诊断描述
{diagnosis_text}

## 输出格式
返回 JSON：
```json
{{"label": "选择的标签名"}}
```
"""


class LabelMapper:
    """Maps natural-language diagnosis to canonical fault labels via LLM."""

    def __init__(
        self,
        model: str = "deepseek/deepseek-chat",
        base_url: str = "https://openrouter.ai/api/v1",
        api_key: str | None = None,
    ):
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key or os.getenv("OPENROUTER_API_KEY", ""),
            timeout=30,
        )

    def map_single(self, diagnosis_text: str) -> str:
        """Map a single diagnosis description to a canonical fault label."""
        prompt = _LABEL_MAPPING_PROMPT.format(
            labels="\n".join(f"- {l}" for l in CANONICAL_FAULT_LABELS),
            diagnosis_text=diagnosis_text,
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=50,
            )
            content = response.choices[0].message.content or ""
            # Try to extract JSON
            content = content.strip()
            if "{" in content:
                start = content.index("{")
                end = content.rindex("}") + 1
                parsed = json.loads(content[start:end])
                label = parsed.get("label", "").strip()
            else:
                # Fallback: just use the raw text
                label = content.strip().strip('"').strip("'")

            # Validate against canonical list
            if label in CANONICAL_FAULT_LABELS:
                return label

            # Fuzzy match: lowercase, replace hyphens
            normalized = label.lower().replace("-", "_").strip()
            if normalized in CANONICAL_FAULT_LABELS:
                return normalized

            logger.warning("LLM returned unknown label '%s', defaulting to raw", label)
            return label

        except Exception as e:
            logger.error("Label mapping failed: %s", e)
            return "unknown"

    def map_report(self, report: DiagnosisReport) -> list[str]:
        """Map all root causes in a DiagnosisReport to canonical labels.

        For each root_cause, builds a diagnosis text from:
          - fault_type (the agent's natural language description)
          - root_cause description
          - evidence summary
        Then calls LLM to map → canonical label.
        """
        mapped_labels: list[str] = []
        for rc in report.root_causes:
            # Build a concise text for the LLM
            text_parts = [
                f"故障类型: {rc.fault_type}",
                f"根因描述: {getattr(rc, 'description', rc.fault_type)}",
                f"置信度: {rc.confidence}",
            ]
            diagnosis_text = "\n".join(text_parts)
            label = self.map_single(diagnosis_text)
            mapped_labels.append(label)
            logger.debug(
                "Mapped '%s' → '%s'", rc.fault_type, label
            )
        return mapped_labels
