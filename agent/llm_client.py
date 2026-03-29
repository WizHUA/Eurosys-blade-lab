"""agent/llm_client.py — LLM 调用封装 + TokenTracker

封装 OpenRouter API (OpenAI-compatible)，提供重试、token 计量和结构化输出。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel

from agent.config import LLMConfig

load_dotenv()

logger = logging.getLogger(__name__)


class LLMCallError(Exception):
    """Raised when LLM call fails after all retries."""


class TokenTracker:
    """Per-category token accounting for ablation analysis."""

    CATEGORIES = (
        "diagnosis_hypothesize",
        "diagnosis_think",
        "audit_think",
        "finalize",
        "reflect",
    )

    def __init__(self) -> None:
        self.categories: dict[str, dict[str, int]] = {
            cat: {"prompt": 0, "completion": 0} for cat in self.CATEGORIES
        }

    def record(self, category: str, usage: dict[str, int]) -> None:
        if category not in self.categories:
            self.categories[category] = {"prompt": 0, "completion": 0}
        self.categories[category]["prompt"] += usage.get("prompt_tokens", 0)
        self.categories[category]["completion"] += usage.get("completion_tokens", 0)

    def summary(self) -> dict[str, dict[str, int]]:
        return {
            k: {**v, "total": v["prompt"] + v["completion"]}
            for k, v in self.categories.items()
        }

    def total(self) -> dict[str, int]:
        prompt = sum(v["prompt"] for v in self.categories.values())
        completion = sum(v["completion"] for v in self.categories.values())
        return {"prompt_tokens": prompt, "completion_tokens": completion, "total": prompt + completion}


def _extract_json_from_text(text: str) -> str:
    """Extract JSON from LLM response text (fallback parser).

    Strategy:
    1. Try entire text as JSON
    2. Match ```json ... ``` fenced block
    3. Match first { ... } or [ ... ] block
    """
    text = text.strip()

    # 1. Direct parse
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # 2. Fenced code block
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # 3. First balanced { ... } or [ ... ]
    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_char:
                depth += 1
            elif text[i] == close_char:
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"Could not extract JSON from LLM response: {text[:200]}")


class LLMClient:
    """Unified LLM calling interface with retry and token tracking."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()
        api_key = self.config.api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.client = OpenAI(
            base_url=self.config.base_url,
            api_key=api_key,
            timeout=self.config.timeout_sec,
        )
        self.tracker = TokenTracker()

    def call(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> tuple[str, dict[str, int]]:
        """Make an LLM API call with retries.

        Returns: (content_string, usage_dict)
        Raises: LLMCallError after retries exhausted.
        """
        temp = temperature if temperature is not None else self.config.temperature
        max_tok = max_tokens or self.config.max_tokens

        kwargs: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_err: Exception | None = None
        for attempt in range(self.config.retry_max):
            try:
                response = self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                }
                logger.debug(
                    "[LLM] OK model=%s tokens=%s",
                    self.config.model_name,
                    usage,
                )
                return content, usage

            except RateLimitError as e:
                last_err = e
                wait = self.config.retry_delay_sec * (2**attempt)
                logger.warning("[LLM] Rate limited, retrying in %.1fs (attempt %d)", wait, attempt + 1)
                time.sleep(wait)

            except (APIConnectionError, APITimeoutError) as e:
                last_err = e
                wait = self.config.retry_delay_sec * (2**attempt)
                logger.warning("[LLM] Connection error: %s, retrying in %.1fs", e, wait)
                time.sleep(wait)

            except Exception as e:
                raise LLMCallError(f"Non-retryable LLM error: {e}") from e

        raise LLMCallError(f"LLM call failed after {self.config.retry_max} retries: {last_err}")

    def call_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        temperature: float | None = None,
        category: str | None = None,
    ) -> tuple[BaseModel, dict[str, int]]:
        """Call LLM and parse response into a Pydantic model.

        Tries JSON mode first, falls back to text extraction.
        Records token usage under the given category.
        """
        content, usage = self.call(
            messages, temperature=temperature, json_mode=True
        )

        if category:
            self.tracker.record(category, usage)

        try:
            json_str = _extract_json_from_text(content)
            data = json.loads(json_str)
            parsed = schema.model_validate(data)
            return parsed, usage
        except Exception as first_err:
            logger.warning("[LLM] Structured parse failed (%s), retrying with hint", first_err)

            # Retry with format hint appended
            hint_msg = {
                "role": "user",
                "content": (
                    f"Your previous response could not be parsed. "
                    f"Please respond with ONLY valid JSON matching this schema:\n"
                    f"{schema.model_json_schema()}"
                ),
            }
            content2, usage2 = self.call(
                messages + [{"role": "assistant", "content": content}, hint_msg],
                temperature=0.0,
                json_mode=True,
            )
            if category:
                self.tracker.record(category, usage2)

            # Merge usage
            total_usage = {
                "prompt_tokens": usage["prompt_tokens"] + usage2["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"] + usage2["completion_tokens"],
            }
            json_str2 = _extract_json_from_text(content2)
            data2 = json.loads(json_str2)
            parsed2 = schema.model_validate(data2)
            return parsed2, total_usage

    def call_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        category: str | None = None,
    ) -> tuple[dict | list, dict[str, int]]:
        """Call LLM and parse response as raw JSON (dict or list)."""
        content, usage = self.call(
            messages, temperature=temperature, json_mode=True
        )
        if category:
            self.tracker.record(category, usage)

        json_str = _extract_json_from_text(content)
        return json.loads(json_str), usage
