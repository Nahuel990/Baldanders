"""Claude Code provider — uses the user's existing auth via `claude -p`."""

from __future__ import annotations

import subprocess
import shutil
from pathlib import Path

from baldanders.providers.base import BaseProvider


class ClaudeCodeProvider(BaseProvider):
    """Sends prompts via `claude -p` with --resume to keep conversation state.

    No API key needed — Claude Code handles auth with the user's
    consumer account. Baldanders never touches credentials.
    """

    def __init__(self) -> None:
        self._binary = shutil.which("claude")
        if not self._binary:
            raise RuntimeError(
                "Claude Code not found. Install it: https://docs.anthropic.com/en/docs/claude-code"
            )
        self._session_id: str | None = None

    def default_model(self) -> str:
        return ""

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        last_user_msg = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user_msg = m["content"]
                break

        cmd = [self._binary, "-p", "--output-format", "json"]

        if self._session_id:
            cmd.extend(["--resume", self._session_id])

        if model:
            cmd.extend(["--model", model])

        cmd.append(last_user_msg)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(Path.home()),
        )

        if result.returncode != 0:
            raise RuntimeError(f"Claude Code error: {result.stderr.strip()}")

        # Parse JSON output to capture session ID
        import json
        for line in result.stdout.strip().splitlines():
            try:
                data = json.loads(line)
                if data.get("type") == "result":
                    if "session_id" in data:
                        self._session_id = data["session_id"]
                    return data.get("result", "")
            except json.JSONDecodeError:
                continue

        # Fallback: plain text
        return result.stdout.strip()

    def close(self) -> None:
        pass
