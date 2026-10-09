"""Baldanders CLI — personal-data firewall for cloud LLMs."""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from baldanders.config import Config, create_default_config, load_config
from baldanders.history import (
    list_sessions, load_session, new_session_id, save_session,
)
from baldanders.mapping import SessionMap
from baldanders.model_manager import is_downloaded, model_dir, download_model
from baldanders.providers.base import BaseProvider
from baldanders.scrubber.engine import ScrubEngine

console = Console()


def _build_provider(name: str, api_key: str | None, base_url: str | None) -> BaseProvider:
    # Consumer-auth providers (no API key needed)
    if name == "claude":
        from baldanders.providers.claude_code import ClaudeCodeProvider
        return ClaudeCodeProvider()
    elif name == "chatgpt":
        from baldanders.providers.codex import CodexProvider
        return CodexProvider()
    elif name == "grok":
        from baldanders.providers.grok import GrokCLIProvider
        return GrokCLIProvider()

    # API-key providers (fallback for providers without a CLI)
    elif name == "anthropic-api":
        from baldanders.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=api_key)
    elif name in ("openai-api", "deepseek", "groq"):
        from baldanders.providers.openai import OpenAIProvider
        url = base_url or {
            "openai-api": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "groq": "https://api.groq.com/openai/v1",
        }.get(name, "https://api.openai.com/v1")
        return OpenAIProvider(api_key=api_key, base_url=url)
    else:
        raise click.BadParameter(f"Unknown provider: {name}")


async def _repl(
    provider: BaseProvider,
    engine: ScrubEngine,
    session_map: SessionMap,
    messages: list[dict[str, str]],
    session_id: str,
    model: str | None,
    verbose: bool = False,
) -> None:
    console.print()
    console.print(Panel(
        "[bold]Baldanders[/bold] — your personal data never leaves this machine.\n"
        f"Session: [dim]{session_id}[/dim]\n"
        f"NER model: {'[green]loaded[/green]' if engine.ner_available else '[yellow]regex-only[/yellow]'}\n"
        "Commands: [dim]/paste  /map  /raw  /clear  /quit[/dim]",
        border_style="blue",
    ))
    console.print()

    raw_mode = False

    while True:
        try:
            prompt_style = "[bold red]Raw:[/bold red] " if raw_mode else "[bold blue]You:[/bold blue] "
            first_line = console.input(prompt_style)
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            break

        # /paste command: collect everything until a line with just "."
        if first_line.strip().lower() == "/paste":
            console.print("[dim]  Paste your text. Type a single . on a line to send.[/dim]")
            lines: list[str] = []
            while True:
                try:
                    line = console.input("[dim]...:[/dim] ")
                except (EOFError, KeyboardInterrupt):
                    break
                if line.strip() == ".":
                    break
                lines.append(line)
            raw = "\n".join(lines).strip()
        else:
            # Check if the first line contains embedded newlines (pasted text)
            if "\n" in first_line:
                raw = first_line.strip()
            else:
                # Collect continuation lines if input looks multi-line
                lines = [first_line]
                if first_line.rstrip().endswith(("(", "{", "[", ",", "\\", ";")):
                    console.print("[dim]  (multi-line: empty line to send)[/dim]")
                    while True:
                        try:
                            line = console.input("[dim]...:[/dim] ")
                        except (EOFError, KeyboardInterrupt):
                            break
                        if line.strip() == "":
                            break
                        lines.append(line)
                raw = "\n".join(lines).strip()

        if not raw:
            continue

        # Commands
        if raw.lower() == "/quit":
            console.print("[dim]Bye.[/dim]")
            break
        if raw.lower() == "/map":
            mapping = session_map.get_map()
            if not mapping:
                console.print("[dim]No personal data mapped yet.[/dim]")
            else:
                for token, real in mapping.items():
                    console.print(f"  {token} → [bold]{real}[/bold]")
            console.print()
            continue
        if raw.lower() == "/raw":
            raw_mode = not raw_mode
            state = "ON" if raw_mode else "OFF"
            console.print(f"[dim]Raw mode {state} — scrubbing {'disabled' if raw_mode else 'enabled'}[/dim]\n")
            continue
        if raw.lower() == "/clear":
            messages.clear()
            console.print("[dim]Conversation cleared.[/dim]\n")
            continue

        # Scrub personal data (unless raw mode)
        if raw_mode:
            scrubbed = raw
        else:
            scrubbed = engine.scrub(raw)
            if scrubbed != raw:
                diff = Text()
                diff.append("  [scrubbed] ", style="dim yellow")
                diff.append(scrubbed, style="yellow")
                console.print(diff)

        if verbose:
            console.print()
            console.print(Panel(
                scrubbed,
                title="[bold red]SENT TO AI (exactly this, nothing else)[/bold red]",
                border_style="red",
            ))
            console.print()

        messages.append({"role": "user", "content": scrubbed})

        # Send to AI
        try:
            with console.status("[dim]Thinking...[/dim]"):
                response = await provider.send(messages, model=model)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}\n")
            messages.pop()
            continue

        if verbose:
            console.print(Panel(
                response,
                title="[bold cyan]RAW RESPONSE FROM AI[/bold cyan]",
                border_style="cyan",
            ))

        messages.append({"role": "assistant", "content": response})

        # Restore personal data in the response
        rehydrated = session_map.rehydrate(response)

        console.print()
        console.print(f"[bold green]Assistant:[/bold green] {rehydrated}")
        console.print()

        # Auto-save after each exchange
        save_session(session_id, session_map, messages)


@click.group(invoke_without_command=True)
@click.option("--provider", "-p", default="claude",
              type=click.Choice(["claude", "chatgpt", "grok", "anthropic-api", "openai-api", "deepseek", "groq"]),
              help="LLM backend. claude/chatgpt/grok use your subscription (no API key).")
@click.option("--model", "-m", default=None, help="Model name override.")
@click.option("--api-key", default=None, help="API key (only needed for direct API providers).")
@click.option("--base-url", default=None, help="Custom API base URL.")
@click.option("--verbose", "-v", is_flag=True, help="Show exact text sent to and received from AI.")
@click.option("--resume", "-r", default=None, help="Resume a previous session by ID.")
@click.pass_context
def main(ctx: click.Context, provider: str, model: str, api_key: str, base_url: str, verbose: bool, resume: str) -> None:
    """Baldanders — local personal-data firewall for cloud LLMs."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        llm = _build_provider(provider, api_key, base_url)
    except (ValueError, RuntimeError, click.BadParameter) as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    # Load or create session
    if resume:
        loaded = load_session(resume)
        if loaded:
            session_map, messages = loaded
            session_id = resume
            console.print(f"[dim]Resumed session {session_id} ({len(messages)} messages)[/dim]")
        else:
            console.print(f"[red]Session '{resume}' not found.[/red]")
            sys.exit(1)
    else:
        session_map = SessionMap()
        messages = []
        session_id = new_session_id()

    create_default_config()
    config = load_config()
    md = model_dir() if is_downloaded() else None
    engine = ScrubEngine(session_map, model_dir=md, config=config)

    try:
        asyncio.run(_repl(llm, engine, session_map, messages, session_id, model, verbose=verbose))
    finally:
        save_session(session_id, session_map, messages)
        if hasattr(llm, "close"):
            llm.close()


@main.command()
def download() -> None:
    """Download the GLiNER model for name and address detection."""
    async def _do() -> None:
        console.print("[dim]Downloading GLiNER name-and-address model...[/dim]")
        path = await download_model()
        console.print(f"[green]Model saved to {path}[/green]")

    asyncio.run(_do())


@main.command()
def sessions() -> None:
    """List saved sessions."""
    saved = list_sessions()
    if not saved:
        console.print("[dim]No saved sessions.[/dim]")
        return
    for s in saved:
        console.print(f"  [bold]{s['id']}[/bold]  {s['message_count']} messages  [dim]{s['updated_at']}[/dim]")
    console.print(f"\n[dim]Resume with: baldanders --resume <id>[/dim]")


if __name__ == "__main__":
    main()
