"""agent/prompt_utils.py — Prompt rendering helpers."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path


class _SafeDict(defaultdict):
    def __missing__(self, key):
        return "{MISSING}"


def render_prompt(template_path: str | Path, variables: dict) -> str:
    """Render prompt template with safe placeholder substitution.

    Missing variables are replaced with {MISSING} for easier debugging.
    """
    path = Path(template_path)
    text = path.read_text(encoding="utf-8")
    safe_vars = _SafeDict(str)
    safe_vars.update(variables)
    return text.format_map(safe_vars)
