"""Grok CLI provider — uses SuperGrok/X Premium+ subscription, no API key."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from baldanders.providers.base import BaseProvider


class GrokCLIProvider(BaseProvider):
    """Sends prompts via grok-cli.

    Requires: grok-cli installed (see https://github.com/Moore-developers/grok-cli)
    Auth: `grok login` (browser OAuth, one-time)
    """

    def __init__(self) -> None:
        self._binary = shutil.which("grok")
        if not self._binary:
            raise RuntimeError(
                "grok-cli not found. See: https://github.com/Moore-developers/grok-cli\n"
                "Then run: grok login"
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

        cmd = [self._binary, "chat", "--message", last_user_msg]

        if model:
            cmd.extend(["--model", model])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(Path.home()),
        )

        if result.returncode != 0:
            raise RuntimeError(f"Grok error: {result.stderr.strip()}")

        self._first_sent = True
        return result.stdout.strip()
