"""agent/tools/base.py — Tool base class."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Base class for deterministic tools."""

    name: str
    description: str

    @abstractmethod
    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_schema(self) -> dict[str, Any]:
        raise NotImplementedError
