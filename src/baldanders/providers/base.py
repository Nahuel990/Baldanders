"""Abstract provider interface and shared helpers."""

from __future__ import annotations

import asyncio
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from baldanders.config import BASE_DIR

# Sent as a system prompt so the model keeps placeholders intact, which is
# what lets Baldanders restore the real values in the reply.
TOKEN_INSTRUCTIONS = (
    "Some details in the user's messages have been replaced with placeholders "
    "such as [PERSON_1], [EMAIL_2] or [IBAN_1] to protect their privacy. Treat each "
    "placeholder as the real value it stands for. Whenever you refer to one, write "
    "the placeholder exactly as given, brackets included, so it can be restored "
    "on the user's side. Do not ask for or guess the real values."
)


@dataclass
class LoginStatus:
    """Result of a provider's local login check (nothing is sent to the AI)."""

    ok: bool | None  # None: couldn't tell, carry on
    tool: str = ""
    login_command: list[str] = field(default_factory=list)
    detail: str = ""


# Error text from a CLI that means "not signed in" or "sign-in expired".
_AUTH_ERROR = re.compile(
    r"\b401\b|unauthori[sz]ed|not logged in|not signed in|log ?in again|sign ?in again|"
    r"run /login|please (run )?(/)?login|invalid api key|auth_missing|relogin|"
    r"refresh token|token (has )?expired|session expired|authentication (failed|required|error)",
    re.IGNORECASE,
)


def is_auth_error(message: str) -> bool:
    return bool(_AUTH_ERROR.search(message or ""))


class BaseProvider(ABC):
    """Common interface for all LLM providers."""

    name: str = ""
    tool: str = ""                  # human name of the CLI, e.g. "Claude Code"
    login_command: list[str] = []   # how to sign in, for CLI providers

    @abstractmethod
    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        """Send the conversation and return the assistant's response text.

        `messages` holds the full scrubbed history; the last entry is the new
        user message.
        """

    async def stream(self, messages: list[dict[str, str]], model: str | None = None) -> AsyncIterator[str]:
        """Yield the reply in pieces as it is generated. Default: all at once."""
        yield await self.send(messages, model=model)

    async def check_login(self) -> LoginStatus:
        """Check locally whether the user is signed in. Default: can't tell."""
        return LoginStatus(ok=None, tool=self.tool, login_command=list(self.login_command))

    async def reconnect(self) -> None:
        """Drop any running process so the next message starts fresh (keeps the conversation)."""

    @abstractmethod
    def default_model(self) -> str:
        """Return the default model name for this provider."""

    def reset(self) -> None:
        """Forget any conversation state kept on the provider's side."""

    def get_state(self) -> dict[str, Any]:
        """State to save with the session so --resume can continue it."""
        return {}

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore state saved by get_state()."""

    async def aclose(self) -> None:
        """Release network resources."""


def last_user_message(messages: list[dict[str, str]]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"]
    return ""


def workdir() -> Path:
    """Empty private directory the CLI agents run in, so they see no local files."""
    d = BASE_DIR / "workdir"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


async def run_cli(
    cmd: list[str],
    stdin: str | None = None,
    timeout: float = 600,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Run a CLI without blocking the event loop. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd or workdir()),
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode("utf-8") if stdin is not None else None),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
