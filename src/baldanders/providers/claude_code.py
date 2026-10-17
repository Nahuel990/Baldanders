"""Claude Code provider. Uses the user's existing login via `claude -p`."""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any, AsyncIterator

from baldanders.providers.base import (
    BaseProvider, LoginStatus, TOKEN_INSTRUCTIONS, last_user_message, run_cli, workdir,
)

# Without this, the model sometimes writes out imaginary tool calls and results.
NO_TOOLS_NOTE = (
    "In this conversation you have no tools: you cannot read files, run commands "
    "or browse. Never write out pretend tool calls or their output; answer from "
    "what the user has told you."
)

_STDERR_KEEP = 4000


class ClaudeCodeProvider(BaseProvider):
    """Keeps one `claude -p` process open for the session and streams replies.

    No API key needed: Claude Code handles auth with the user's own account.
    Baldanders never touches credentials.

    All tools and MCP servers are disabled, and it runs in an empty directory,
    so the model can't read local files that never went through the scrubber.
    """

    name = "claude-code"
    tool = "Claude Code"
    login_command = ["claude", "auth", "login"]

    def __init__(self) -> None:
        self._binary = shutil.which("claude")
        if not self._binary:
            raise RuntimeError(
                "Claude Code not found. Install it: https://docs.anthropic.com/en/docs/claude-code"
            )
        self._session_id: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._model: str | None = None
        self._stderr = ""
        self._stderr_task: asyncio.Task | None = None

    def default_model(self) -> str:
        return ""

    def get_state(self) -> dict[str, Any]:
        return {"provider": self.name, "session_id": self._session_id}

    def set_state(self, state: dict[str, Any]) -> None:
        if state.get("provider") in (self.name, "claude"):  # "claude" in 0.1.0 sessions
            self._session_id = state.get("session_id")

    # --- Login ------------------------------------------------------------

    async def check_login(self) -> LoginStatus:
        status = LoginStatus(ok=None, tool=self.tool, login_command=list(self.login_command))
        try:
            code, out, _ = await run_cli([self._binary, "auth", "status", "--json"], timeout=30)
            data = json.loads(out)
        except (OSError, ValueError, asyncio.TimeoutError):
            return status  # older Claude Code without `auth status`: find out on first message
        status.ok = bool(data.get("loggedIn"))
        status.detail = str(data.get("authMethod") or "")
        return status

    # --- Process ----------------------------------------------------------

    async def _start(self, model: str | None) -> None:
        cmd = [
            self._binary, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--tools", "",
            "--strict-mcp-config",
            "--append-system-prompt", TOKEN_INSTRUCTIONS + " " + NO_TOOLS_NOTE,
        ]
        if self._session_id:
            cmd.extend(["--resume", self._session_id])
        if model:
            cmd.extend(["--model", model])
        self._stderr = ""
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir()),
            limit=16 * 1024 * 1024,
        )
        self._model = model
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        # Keep reading so a chatty stderr can never fill the pipe and stall the process.
        assert proc.stderr is not None
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            self._stderr = (self._stderr + chunk.decode("utf-8", "replace"))[-_STDERR_KEEP:]

    async def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.returncode is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None

    def _died(self, reason: str) -> RuntimeError:
        detail = self._stderr.strip().splitlines()[-1] if self._stderr.strip() else reason
        return RuntimeError(f"Claude Code error: {detail}")

    # --- Conversation -----------------------------------------------------

    async def stream(self, messages: list[dict[str, str]], model: str | None = None) -> AsyncIterator[str]:
        if self._proc is None or self._proc.returncode is not None or model != self._model:
            await self._stop()
            await self._start(model)
        proc = self._proc
        assert proc is not None and proc.stdin is not None and proc.stdout is not None

        line = json.dumps({"type": "user", "message": {"role": "user", "content": last_user_message(messages)}})
        try:
            proc.stdin.write(line.encode("utf-8") + b"\n")
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            await asyncio.sleep(0.2)  # let stderr arrive
            await self._stop()
            raise self._died("it stopped unexpectedly") from None

        streamed = False
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    await asyncio.sleep(0.2)
                    await self._stop()
                    raise self._died("it stopped unexpectedly")
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("session_id"):
                    self._session_id = event["session_id"]
                kind = event.get("type")
                if kind == "stream_event":
                    inner = event.get("event") or {}
                    delta = inner.get("delta") or {}
                    if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            streamed = True
                            yield text
                elif kind == "result":
                    if event.get("is_error") or event.get("subtype", "success") != "success":
                        raise RuntimeError(f"Claude Code error: {event.get('result') or event.get('subtype')}")
                    if not streamed and event.get("result"):
                        yield str(event["result"])
                    return
        except (asyncio.CancelledError, GeneratorExit):
            # Interrupted mid-reply: this process is still generating, so drop
            # it. The next message resumes the same conversation.
            await self._stop()
            raise

    async def send(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        return "".join([piece async for piece in self.stream(messages, model=model)])

    def reset(self) -> None:
        """Start a brand-new conversation on the next message."""
        self._session_id = None
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        self._proc = None

    async def reconnect(self) -> None:
        await self._stop()

    async def warm_up(self, model: str | None = None) -> None:
        """Start Claude Code now, so the first message doesn't wait for it."""
        if self._proc is None or self._proc.returncode is not None:
            await self._start(model)

    async def aclose(self) -> None:
        await self._stop()
