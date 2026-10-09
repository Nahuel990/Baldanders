"""Tests for provider initialization and error handling."""

import pytest
import shutil


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


def test_anthropic_no_key():
    from baldanders.providers.anthropic import AnthropicProvider
    with pytest.raises(ValueError, match="No Anthropic API key"):
        AnthropicProvider(api_key="")


def test_openai_no_key():
    from baldanders.providers.openai import OpenAIProvider
    with pytest.raises(ValueError, match="No API key"):
        OpenAIProvider(api_key="")
