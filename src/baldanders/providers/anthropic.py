"""Claude provider — calls Anthropic Messages API via httpx."""

from __future__ import annotations

import os

import httpx

from baldanders.providers.base import BaseProvider

API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(BaseProvider):

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "No Anthropic API key. Set ANTHROPIC_API_KEY or pass --api-key."
            )

    def default_model(self) -> str:
        return "claude-sonnet-4-20250514"

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        model = model or self.default_model()
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(API_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data["content"][0]["text"]
