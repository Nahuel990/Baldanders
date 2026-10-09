"""OpenAI-compatible provider — covers GPT, Grok, DeepSeek, Groq, etc."""

from __future__ import annotations

import os

import httpx

from baldanders.providers.base import BaseProvider


class OpenAIProvider(BaseProvider):

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "No API key. Set OPENAI_API_KEY or pass --api-key."
            )
        self._base_url = base_url.rstrip("/")

    def default_model(self) -> str:
        return "gpt-4o"

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        model = model or self.default_model()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
