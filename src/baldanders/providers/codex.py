"""OpenAI Codex CLI provider — uses the user's ChatGPT subscription, no API key."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from baldanders.providers.base import BaseProvider


class CodexProvider(BaseProvider):
    """Sends prompts via OpenAI Codex CLI.

    Requires: npm install -g @openai/codex
    Auth: `codex login` (browser OAuth, one-time)
    """

    def __init__(self) -> None:
        self._binary = shutil.which("codex")
        if not self._binary:
            raise RuntimeError(
                "Codex CLI not found. Install: npm install -g @openai/codex\n"
                "Then run: codex login"
            )
        self._first_sent = False

    def default_model(self) -> str:
        return ""

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        last_user_msg = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user_msg = m["content"]
                break

        cmd = [self._binary, "-p"]

        if self._first_sent:
            cmd.append("--continue")

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
            raise RuntimeError(f"Codex error: {result.stderr.strip()}")

        self._first_sent = True
        return result.stdout.strip()
