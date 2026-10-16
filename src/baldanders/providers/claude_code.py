"""Claude Code provider — uses the user's existing auth via `claude -p`."""

from __future__ import annotations

import json
import shutil
from typing import Any

from baldanders.providers.base import BaseProvider, TOKEN_INSTRUCTIONS, last_user_message, run_cli


# Without this, the model sometimes writes out imaginary tool calls and results.
NO_TOOLS_NOTE = (
    "In this conversation you have no tools: you cannot read files, run commands "
    "or browse. Never write out pretend tool calls or their output; answer from "
    "what the user has told you."
)


class ClaudeCodeProvider(BaseProvider):
    """Sends prompts via `claude -p` with --resume to keep conversation state.

    No API key needed — Claude Code handles auth with the user's
    consumer account. Baldanders never touches credentials.

    All tools and MCP servers are disabled, and it runs in an empty directory,
    so the model can't read local files that never went through the scrubber.
    """

    name = "claude"

    def __init__(self) -> None:
        self._binary = shutil.which("claude")
        if not self._binary:
            raise RuntimeError(
                "Claude Code not found. Install it: https://docs.anthropic.com/en/docs/claude-code"
            )
        self._session_id: str | None = None

    def default_model(self) -> str:
        return ""

    def reset(self) -> None:
        self._session_id = None

    def get_state(self) -> dict[str, Any]:
        return {"provider": self.name, "session_id": self._session_id}

    def set_state(self, state: dict[str, Any]) -> None:
        if state.get("provider") == self.name:
            self._session_id = state.get("session_id")

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        cmd = [
            self._binary, "-p",
            "--output-format", "json",
            "--tools", "",
            "--strict-mcp-config",
            "--append-system-prompt", TOKEN_INSTRUCTIONS + " " + NO_TOOLS_NOTE,
        ]
        if self._session_id:
            cmd.extend(["--resume", self._session_id])
        if model:
            cmd.extend(["--model", model])

        # Prompt goes over stdin: no argv length limit, not visible in `ps`,
        # and a message starting with "-" can't be parsed as a flag.
        code, out, err = await run_cli(cmd, stdin=last_user_message(messages))

        data = _parse_result(out)
        if data is None:
            if code != 0:
                raise RuntimeError(f"Claude Code error: {(err or out).strip()}")
            return out.strip()

        if data.get("session_id"):
            self._session_id = data["session_id"]
        if data.get("is_error") or code != 0:
            raise RuntimeError(f"Claude Code error: {data.get('result') or err.strip()}")
        return str(data.get("result", ""))


def _parse_result(out: str) -> dict[str, Any] | None:
    """Find the `result` object in `claude -p --output-format json` output."""
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            results = [d for d in data if isinstance(d, dict) and d.get("type") == "result"]
            return results[-1] if results else None
    except json.JSONDecodeError:
        pass
    for line in reversed(out.strip().splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            return data
    return None
