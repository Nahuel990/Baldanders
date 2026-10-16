"""OpenAI-compatible provider — covers OpenAI, DeepSeek, Groq, and any /chat/completions API."""

from __future__ import annotations

import os

import httpx

from baldanders.providers.base import BaseProvider, TOKEN_INSTRUCTIONS


class OpenAIProvider(BaseProvider):

    name = "openai-api"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o",
        key_env: str = "OPENAI_API_KEY",
    ) -> None:
        self._api_key = api_key or os.environ.get(key_env, "")
        if not self._api_key:
            raise ValueError(
                f"No API key. Set {key_env} or pass --api-key."
            )
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=600))

    def default_model(self) -> str:
        return self._default_model

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        model = model or self.default_model()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "system", "content": TOKEN_INSTRUCTIONS}, *messages],
        }
        resp = await self._client.post(f"{self._base_url}/chat/completions", headers=headers, json=body)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("message") or resp.text
            except ValueError:
                detail = resp.text
            raise RuntimeError(f"API error ({resp.status_code}): {detail[:500]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""

    async def aclose(self) -> None:
        await self._client.aclose()
