"""OpenAI-compatible provider. Covers OpenAI, DeepSeek, Groq, and any /chat/completions API."""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

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
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, read=600),
            headers={"User-Agent": "baldanders"},
        )

    def default_model(self) -> str:
        return self._default_model

    async def stream(self, messages: list[dict[str, str]], model: str | None = None) -> AsyncIterator[str]:
        model = model or self.default_model()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "stream": True,
            "messages": [{"role": "system", "content": TOKEN_INSTRUCTIONS}, *messages],
        }
        async with self._client.stream(
            "POST", f"{self._base_url}/chat/completions", headers=headers, json=body
        ) as resp:
            if resp.status_code >= 400:
                # Read the error body fully before raising.
                await resp.aread()
                try:
                    detail = resp.json().get("error", {}).get("message") or resp.text
                except ValueError:
                    detail = resp.text
                raise RuntimeError(f"API error ({resp.status_code}): {detail[:500]}")

            async for raw in resp.aiter_lines():
                # SSE lines are prefixed with "data: "; ignore comments and keep-alives.
                if not raw.startswith("data: "):
                    continue
                payload = raw[6:]
                if payload.strip() == "[DONE]":
                    return
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                delta = (event.get("choices") or [{}])[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield text

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model=model)])

    async def aclose(self) -> None:
        await self._client.aclose()
