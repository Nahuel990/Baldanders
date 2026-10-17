"""Grok provider. Uses grok-cli with a SuperGrok / X Premium+ login, no API key."""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from baldanders.providers.base import BaseProvider, LoginStatus, TOKEN_INSTRUCTIONS, run_cli


class GrokCLIProvider(BaseProvider):
    """Sends prompts via `grok-cli chat`.

    Requires: grok-cli (https://github.com/Moore-developers/grok-cli)
    Auth: `grok-cli login` (browser OAuth, one-time)

    grok-cli keeps no conversation state, so the scrubbed transcript is sent
    each turn. Web search is turned off so prompts stay between you and Grok.
    """

    name = "grok"
    tool = "grok-cli"
    login_command = ["grok-cli", "login"]

    def __init__(self) -> None:
        self._binary = shutil.which("grok-cli")
        if not self._binary:
            raise RuntimeError(
                "grok-cli not found. See: https://github.com/Moore-developers/grok-cli\n"
                "Then run: grok-cli login"
            )

    def default_model(self) -> str:
        return ""

    async def check_login(self) -> LoginStatus:
        status = LoginStatus(ok=None, tool=self.tool, login_command=list(self.login_command))
        try:
            code, out, _ = await run_cli([self._binary, "status", "--json"], timeout=30)
            envelope = json.loads(out)
        except (OSError, ValueError, asyncio.TimeoutError):
            return status
        status.ok = bool(envelope.get("ok"))
        error = envelope.get("error") or {}
        status.detail = str(error.get("message", "")) if isinstance(error, dict) else str(error)
        return status

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        cmd = [
            self._binary, "chat",
            "--json",
            "--no-web-search",
            "--system", TOKEN_INSTRUCTIONS,
            "--prompt", _transcript(messages),
        ]
        if model:
            cmd.extend(["--model", model])

        code, out, err = await run_cli(cmd)
        try:
            envelope = json.loads(out)
        except json.JSONDecodeError:
            if code != 0:
                raise RuntimeError(f"Grok error: {(err or out).strip()}")
            return out.strip()

        if not envelope.get("ok", code == 0):
            error = envelope.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"Grok error: {message or err.strip()}")
        return _extract_text(envelope.get("data"))


def _transcript(messages: list[dict[str, str]]) -> str:
    if len(messages) == 1:
        return messages[0]["content"]
    lines = ["Conversation so far:"]
    for m in messages[:-1]:
        speaker = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"\n{speaker}: {m['content']}")
    lines.append(f"\n\nReply to the user's latest message:\n{messages[-1]['content']}")
    return "\n".join(lines)


def _extract_text(data: Any) -> str:
    """Pull the reply out of grok-cli's `data` payload."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "output_text", "content", "message", "response", "answer"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, (dict, list)):
                found = _extract_text(value)
                if found:
                    return found
    if isinstance(data, list):
        parts = [_extract_text(item) for item in data]
        return "\n".join(p for p in parts if p)
    return ""
