"""Tests for providers, including the real subprocess path via fake CLIs."""

import asyncio
import json
import shutil
import sys
import textwrap

import pytest

from baldanders.providers.codex import _find_thread_id, _last_agent_message, _parse_events
from baldanders.providers.grok import _extract_text, _transcript

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake CLIs are shell scripts")


def test_claude_code_not_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    from baldanders.providers.claude_code import ClaudeCodeProvider
    with pytest.raises(RuntimeError, match="Claude Code not found"):
        ClaudeCodeProvider()


def test_codex_not_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    from baldanders.providers.codex import CodexProvider
    with pytest.raises(RuntimeError, match="Codex CLI not found"):
        CodexProvider()


def test_grok_not_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    from baldanders.providers.grok import GrokCLIProvider
    with pytest.raises(RuntimeError, match="grok-cli not found"):
        GrokCLIProvider()


def test_openai_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from baldanders.providers.openai import OpenAIProvider
    with pytest.raises(ValueError, match="No API key"):
        OpenAIProvider(api_key="")


def test_anthropic_builds_with_key():
    pytest.importorskip("anthropic")
    from baldanders.providers.anthropic import AnthropicProvider
    p = AnthropicProvider(api_key="sk-ant-test")
    assert p.default_model() == "claude-opus-5"
    asyncio.run(p.aclose())


def _fake_cli(tmp_path, monkeypatch, name, body):
    """Put an executable shell script called `name` first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    return tmp_path / "log"


FAKE_CLAUDE = """
if [ "$1" = "auth" ]; then echo '{"loggedIn": %(logged_in)s, "authMethod": "claude.ai"}'; exit 0; fi
echo "start" >> %(log)s/starts
printf '%%s\\n' "$@" > %(log)s/args
while IFS= read -r line; do
  printf '%%s\\n' "$line" >> %(log)s/stdin
  %(reply)s
done
"""

OK_REPLY = (
    """echo '{"type":"system","session_id":"sess-123"}'; """
    """echo '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hola [PER"}}}'; """
    """echo '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"SON_1]"}}}'; """
    """echo '{"type":"result","subtype":"success","is_error":false,"result":"Hola [PERSON_1]","session_id":"sess-123"}'"""
)


def _claude(tmp_path, monkeypatch, reply=OK_REPLY, logged_in="true"):
    return _fake_cli(tmp_path, monkeypatch, "claude", FAKE_CLAUDE % {"log": tmp_path, "reply": reply, "logged_in": logged_in})


@posix_only
def test_claude_code_streams_and_keeps_one_process(tmp_path, monkeypatch):
    _claude(tmp_path, monkeypatch)
    from baldanders.providers.claude_code import ClaudeCodeProvider

    async def run():
        p = ClaudeCodeProvider()
        first = [piece async for piece in p.stream([{"role": "user", "content": "--soy [PERSON_1]"}])]
        second = await p.send([{"role": "user", "content": "again"}])
        state = p.get_state()
        await p.aclose()
        return first, second, state

    first, second, state = asyncio.run(run())
    assert first == ["Hola [PER", "SON_1]"]  # streamed in pieces
    assert second == "Hola [PERSON_1]"
    assert state == {"provider": "claude-code", "session_id": "sess-123"}
    assert (tmp_path / "starts").read_text().count("start") == 1  # one process for both messages
    args = (tmp_path / "args").read_text().splitlines()
    assert "--tools" in args and args[args.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in args and "stream-json" in args
    sent = [json.loads(line)["message"]["content"] for line in (tmp_path / "stdin").read_text().splitlines()]
    assert sent == ["--soy [PERSON_1]", "again"]  # over stdin, never as arguments


@posix_only
def test_claude_code_resumes_after_reconnect(tmp_path, monkeypatch):
    _claude(tmp_path, monkeypatch)
    from baldanders.providers.claude_code import ClaudeCodeProvider

    async def run():
        p = ClaudeCodeProvider()
        await p.send([{"role": "user", "content": "one"}])
        await p.reconnect()
        await p.send([{"role": "user", "content": "two"}])
        await p.aclose()

    asyncio.run(run())
    args = (tmp_path / "args").read_text().splitlines()
    assert args[args.index("--resume") + 1] == "sess-123"


@posix_only
def test_claude_code_error_result(tmp_path, monkeypatch):
    _claude(tmp_path, monkeypatch, reply="""echo '{"type":"result","subtype":"success","is_error":true,"result":"Invalid API key · Please run /login"}'""")
    from baldanders.providers.base import is_auth_error
    from baldanders.providers.claude_code import ClaudeCodeProvider

    async def run():
        p = ClaudeCodeProvider()
        try:
            await p.send([{"role": "user", "content": "hi"}])
        finally:
            await p.aclose()

    with pytest.raises(RuntimeError, match="Please run /login") as e:
        asyncio.run(run())
    assert is_auth_error(str(e.value))


@posix_only
def test_claude_code_login_check(tmp_path, monkeypatch):
    _claude(tmp_path, monkeypatch, logged_in="false")
    from baldanders.providers.claude_code import ClaudeCodeProvider
    status = asyncio.run(ClaudeCodeProvider().check_login())
    assert status.ok is False and status.login_command == ["claude", "auth", "login"]


@posix_only
def test_codex_end_to_end(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, "codex", f"""
        printf '%s\\n' "$@" > {tmp_path}/args
        cat > {tmp_path}/stdin
        out=""
        prev=""
        for a in "$@"; do
          if [ "$prev" = "--output-last-message" ]; then out="$a"; fi
          prev="$a"
        done
        echo '{{"type":"thread.started","thread_id":"th-42"}}'
        echo '{{"type":"item.completed","item":{{"type":"agent_message","text":"Listo [EMAIL_1]"}}}}'
        printf 'Listo [EMAIL_1]' > "$out"
    """)
    from baldanders.providers.codex import CodexProvider
    p = CodexProvider()
    assert asyncio.run(p.send([{"role": "user", "content": "mail [EMAIL_1]"}])) == "Listo [EMAIL_1]"
    args = (tmp_path / "args").read_text().splitlines()
    assert args[0] == "exec" and "--skip-git-repo-check" in args and args[-1] == "-"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "placeholders" in (tmp_path / "stdin").read_text()  # instructions on first turn

    asyncio.run(p.send([{"role": "user", "content": "second"}]))
    args = (tmp_path / "args").read_text().splitlines()
    assert args[-3:] == ["resume", "th-42", "-"]
    assert (tmp_path / "stdin").read_text() == "second"


@posix_only
def test_grok_end_to_end(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, "grok-cli", f"""
        printf '%s\\n' "$@" > {tmp_path}/args
        echo '{{"ok":true,"command":"chat","data":{{"text":"Hi [PERSON_1]"}}}}'
    """)
    from baldanders.providers.grok import GrokCLIProvider
    reply = asyncio.run(GrokCLIProvider().send([{"role": "user", "content": "I am [PERSON_1]"}]))
    assert reply == "Hi [PERSON_1]"
    args = (tmp_path / "args").read_text()
    assert "--no-web-search" in args and "--json" in args


@posix_only
def test_grok_error_envelope(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, "grok-cli", """
        echo '{"ok":false,"command":"chat","error":{"code":"auth_missing","message":"Run grok-cli login"}}'
        exit 1
    """)
    from baldanders.providers.grok import GrokCLIProvider
    with pytest.raises(RuntimeError, match="grok-cli login"):
        asyncio.run(GrokCLIProvider().send([{"role": "user", "content": "hi"}]))


def test_codex_event_parsing_legacy_format():
    out = "\n".join([
        "not json",
        json.dumps({"id": "0", "msg": {"type": "session_configured", "session_id": "old-1"}}),
        json.dumps({"id": "1", "msg": {"type": "agent_message", "message": "hello"}}),
    ])
    events = _parse_events(out)
    assert _find_thread_id(events) == "old-1"
    assert _last_agent_message(events) == "hello"


def test_grok_transcript_and_extract():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]
    t = _transcript(msgs)
    assert "User: hi" in t and "Assistant: hello" in t and t.endswith("bye")
    assert _transcript([{"role": "user", "content": "solo"}]) == "solo"
    assert _extract_text({"output": [{"content": [{"text": "deep"}]}]}) == ""  # unknown shape
    assert _extract_text({"response": {"text": "nested"}}) == "nested"


def test_old_session_provider_names_still_restore(monkeypatch):
    """Sessions saved by 0.1.0 used "claude" and "chatgpt"."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    from baldanders.providers.claude_code import ClaudeCodeProvider
    from baldanders.providers.codex import CodexProvider
    c = ClaudeCodeProvider()
    c.set_state({"provider": "claude", "session_id": "old"})
    assert c.get_state()["session_id"] == "old"
    x = CodexProvider()
    x.set_state({"provider": "chatgpt", "thread_id": "th"})
    assert x.get_state()["thread_id"] == "th"
