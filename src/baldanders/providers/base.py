"""Abstract provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """Common interface for all LLM providers."""

    @abstractmethod
    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        """Send messages and return the assistant's response text."""

    @abstractmethod
    def default_model(self) -> str:
        """Return the default model name for this provider."""
