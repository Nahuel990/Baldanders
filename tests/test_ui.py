"""Tests for the terminal interface (non-interactive paths and rendering)."""

import asyncio

from rich.console import Console

from baldanders.ui import COMMANDS, TerminalUI


def make_ui(**kw):
    console = Console(record=True, width=80, force_terminal=False, color_system=None)
    ui = TerminalUI(console, provider="claude-code", session_id="s1", saved=True,
                    ner_status="loaded", version="9.9", interactive=False, **kw)
    return ui, console


def test_header_shows_provider_and_model():
    ui, console = make_ui()
    ui.header()
    out = console.export_text()
    assert "Baldanders 9.9" in out and "provider: claude-code" in out and "names model: loaded" in out


def test_protected_line_counts_items():
    ui, console = make_ui()
    ui.protected_line(["[PERSON_1]", "[EMAIL_1]"])
    ui.protected_line(["[PHONE_1]"])
    assert ui.protected == 3
    assert "Protected 2: [PERSON_1], [EMAIL_1]" in console.export_text()


def test_assistant_renders_markdown_with_bullet():
    ui, console = make_ui()
    ui.assistant("Hola **Ana**\n\n- uno\n- dos")
    out = console.export_text()
    assert "● Hola Ana" in out and "uno" in out and "**" not in out


def test_markup_in_replies_is_not_interpreted():
    ui, console = make_ui()
    ui.assistant("Use [bold]tags[/bold] and [PERSON_1] literally")
    assert "[bold]tags[/bold]" in console.export_text()


def test_token_map_and_help():
    ui, console = make_ui()
    ui.token_map({"[PERSON_1]": "Ana Pérez"})
    ui.help()
    out = console.export_text()
    assert "[PERSON_1]  Ana Pérez" in out
    assert all(cmd in out for cmd in COMMANDS)


def test_plain_read_from_pipe(monkeypatch):
    ui, console = make_ui()
    inputs = iter(["  hola  "])
    monkeypatch.setattr(console, "input", lambda prompt="": next(inputs))
    assert asyncio.run(ui.read()) == "hola"


def test_plain_read_eof_exits(monkeypatch):
    ui, console = make_ui()

    def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr(console, "input", eof)
    assert asyncio.run(ui.read()) is None
