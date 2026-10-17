"""Tests for the command-line interface."""

from click.testing import CliRunner

from baldanders import __version__
from baldanders.cli import main


def test_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_scrub_argument():
    result = CliRunner().invoke(main, ["scrub", "mail jane@example.com, DNI 20.123.456"])
    assert result.exit_code == 0
    assert result.output.strip() == "mail [EMAIL_1], DNI [NATIONAL_ID_1]"


def test_scrub_stdin_with_map():
    result = CliRunner().invoke(main, ["scrub", "--show-map"], input="IBAN GB29NWBK60161331926819\n")
    assert result.exit_code == 0
    assert "IBAN [IBAN_1]" in result.output
    assert "[IBAN_1] → GB29NWBK60161331926819" in result.output


def test_scrub_json():
    result = CliRunner().invoke(main, ["scrub", "--json", "call +44 7911 123456"])
    assert result.exit_code == 0
    assert '"type": "PHONE"' in result.output


def test_unknown_provider_cli_missing(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = CliRunner().invoke(main, ["-p", "grok"])
    assert result.exit_code == 1
    assert "grok-cli not found" in result.output


def test_provider_names_and_old_aliases(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _: None)
    for name, expected in [("claude-code", "Claude Code not found"), ("claude", "Claude Code not found"),
                           ("codex", "Codex CLI not found"), ("chatgpt", "Codex CLI not found")]:
        result = CliRunner().invoke(main, ["-p", name])
        assert result.exit_code == 1 and expected in result.output, name


def test_no_save_never_touches_the_keychain(monkeypatch):
    """--no-save must not read or create a keychain entry."""
    import shutil
    from baldanders import vault
    from baldanders.providers import claude_code

    calls = []
    monkeypatch.setattr(vault, "available", lambda: calls.append("available") or True)
    monkeypatch.setattr(vault, "_fernet", lambda: calls.append("key") or None)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/true")
    result = CliRunner().invoke(main, ["--no-save"], input="/quit\n")
    assert result.exit_code == 0, result.output
    assert calls == []
