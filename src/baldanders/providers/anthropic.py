"""Claude API provider — official Anthropic SDK, API key or `ant auth login` profile."""

from __future__ import annotations

from baldanders.providers.base import BaseProvider, TOKEN_INSTRUCTIONS

DEFAULT_MODEL = "claude-opus-5"


class AnthropicProvider(BaseProvider):

    name = "anthropic-api"

    def __init__(self, api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                'The Anthropic SDK is not installed. Run: pip install "baldanders[anthropic]"'
            ) from None
        self._anthropic = anthropic
        # With no key the SDK falls back to ANTHROPIC_API_KEY or an `ant auth login` profile.
        self._client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()

    def default_model(self) -> str:
        return DEFAULT_MODEL

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        model = model or self.default_model()
        kwargs = {}
        if model == DEFAULT_MODEL:
            # If Opus declines, let the API retry on a suitable fallback model.
            kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            async with self._client.beta.messages.stream(
                model=model,
                max_tokens=64000,
                system=TOKEN_INSTRUCTIONS,
                messages=messages,
                **kwargs,
            ) as stream:
                response = await stream.get_final_message()
        except self._anthropic.AuthenticationError:
            raise RuntimeError(
                "Anthropic authentication failed. Set ANTHROPIC_API_KEY, pass --api-key, or run `ant auth login`."
            ) from None
        except self._anthropic.APIStatusError as e:
            raise RuntimeError(f"Anthropic API error ({e.status_code}): {e.message}") from None
        except self._anthropic.APIConnectionError:
            raise RuntimeError("Could not reach the Anthropic API. Check your connection.") from None

        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to answer this request.")
        return "".join(block.text for block in response.content if block.type == "text")

    async def aclose(self) -> None:
        await self._client.close()
