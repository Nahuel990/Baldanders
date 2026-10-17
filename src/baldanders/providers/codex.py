"""OpenAI Codex CLI provider. Uses the user's ChatGPT plan login, no API key."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from baldanders.providers.base import (
    BaseProvider, TOKEN_INSTRUCTIONS, last_user_message, run_cli, workdir,
)


class CodexProvider(BaseProvider):
    """Sends prompts via `codex exec`, resuming the same thread each turn.

    Requires: npm install -g @openai/codex
    Auth: `codex login` (browser OAuth, one-time)

    Runs read-only in an empty directory so the agent has nothing local to read.
    """

    name = "codex"

    def __init__(self) -> None:
        self._binary = shutil.which("codex")
        if not self._binary:
            raise RuntimeError(
                "Codex CLI not found. Install: npm install -g @openai/codex\n"
                "Then run: codex login"
            )
        self._thread_id: str | None = None

    def default_model(self) -> str:
        return ""

    def reset(self) -> None:
        self._thread_id = None

    def get_state(self) -> dict[str, Any]:
        return {"provider": self.name, "thread_id": self._thread_id}

    def set_state(self, state: dict[str, Any]) -> None:
        if state.get("provider") in (self.name, "chatgpt"):  # "chatgpt" in 0.1.0 sessions
            self._thread_id = state.get("thread_id")

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        prompt = last_user_message(messages)
        if not self._thread_id:
            prompt = f"{TOKEN_INSTRUCTIONS}\n\n{prompt}"

        fd, last_message_file = tempfile.mkstemp(prefix="codex-", suffix=".txt", dir=workdir())
        os.close(fd)
        try:
            cmd = [
                self._binary, "exec",
                "--json",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--cd", str(workdir()),
                "--output-last-message", last_message_file,
            ]
            if model:
                cmd.extend(["--model", model])
            if self._thread_id:
                cmd.extend(["resume", self._thread_id])
            cmd.append("-")  # read the prompt from stdin

            code, out, err = await run_cli(cmd, stdin=prompt)
            events = _parse_events(out)

            thread_id = _find_thread_id(events)
            if thread_id:
                self._thread_id = thread_id

            text = Path(last_message_file).read_text(encoding="utf-8").strip()
            if not text:
                text = _last_agent_message(events)
            error = _find_error(events)
            if code != 0 or (not text and error):
                raise RuntimeError(f"Codex error: {error or err.strip() or out.strip()}")
            return text
        finally:
            Path(last_message_file).unlink(missing_ok=True)


def _parse_events(out: str) -> list[dict[str, Any]]:
    events = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _find_thread_id(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        for container in (event, event.get("msg") or {}):
            if not isinstance(container, dict):
                continue
            for key in ("thread_id", "session_id", "conversation_id"):
                value = container.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _last_agent_message(events: list[dict[str, Any]]) -> str:
    text = ""
    for event in events:
        item = event.get("item") or event.get("msg") or {}
        if isinstance(item, dict) and item.get("type") in ("agent_message", "assistant_message"):
            text = item.get("text") or item.get("message") or text
    return text


def _find_error(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("type") in ("error", "turn.failed"):
            err = event.get("error")
            if isinstance(err, dict):
                return str(err.get("message", err))
            return str(event.get("message") or err or "")
    return ""
