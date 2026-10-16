"""Baldanders CLI: personal-data firewall for cloud LLMs."""

from __future__ import annotations

import asyncio
import json
import sys

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.text import Text

from baldanders import __version__
from baldanders.config import Config, create_default_config, load_config
from baldanders.history import (
    delete_session, list_sessions, load_session, new_session_id, save_session,
)
from baldanders.mapping import SessionMap
from baldanders.model_manager import download_model, is_downloaded, model_dir
from baldanders.providers.base import BaseProvider
from baldanders.scrubber.engine import ScrubEngine

console = Console(highlight=False)

PROVIDERS = ["claude", "chatgpt", "grok", "anthropic-api", "openai-api", "deepseek", "groq"]

_OPENAI_COMPATIBLE = {
    # name: (base url, default model, api key env var)
    "openai-api": ("https://api.openai.com/v1", "gpt-4o", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
}

HELP_TEXT = (
    "/paste  multi-line input (end with a single . on its own line)\n"
    "/map    show which token stands for which real value\n"
    "/raw    toggle scrubbing off/on for the next messages\n"
    "/clear  start a fresh conversation (keeps the token map)\n"
    "/help   show this help\n"
    "/quit   exit"
)


def _build_provider(name: str, api_key: str | None, base_url: str | None) -> BaseProvider:
    # Consumer-auth providers (no API key needed)
    if name == "claude":
        from baldanders.providers.claude_code import ClaudeCodeProvider
        return ClaudeCodeProvider()
    if name == "chatgpt":
        from baldanders.providers.codex import CodexProvider
        return CodexProvider()
    if name == "grok":
        from baldanders.providers.grok import GrokCLIProvider
        return GrokCLIProvider()

    # API-key providers
    if name == "anthropic-api":
        from baldanders.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=api_key)
    if name in _OPENAI_COMPATIBLE:
        from baldanders.providers.openai import OpenAIProvider
        url, model, key_env = _OPENAI_COMPATIBLE[name]
        provider = OpenAIProvider(api_key=api_key, base_url=base_url or url, default_model=model, key_env=key_env)
        provider.name = name
        return provider
    raise click.BadParameter(f"Unknown provider: {name}")


def _build_engine(session_map: SessionMap, config: Config) -> ScrubEngine:
    md = model_dir() if is_downloaded() else None
    engine = ScrubEngine(session_map, model_dir=md, config=config)
    for problem in engine.config_errors:
        console.print(f"[yellow]Config:[/yellow] {problem}")
    return engine


def _read_input(raw_mode: bool) -> str | None:
    """Read one message from the terminal. Returns None on EOF/Ctrl-C."""
    prompt = "[bold red]Raw:[/bold red] " if raw_mode else "[bold blue]You:[/bold blue] "
    try:
        first_line = console.input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None

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
        return "\n".join(lines).strip()

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
    return "\n".join(lines).strip()


async def _repl(
    provider: BaseProvider,
    engine: ScrubEngine,
    session_map: SessionMap,
    messages: list[dict[str, str]],
    session_id: str,
    model: str | None,
    verbose: bool = False,
    save: bool = True,
) -> None:
    ner = engine.ner_status
    ner_line = "[green]loaded[/green]" if ner == "loaded" else f"[yellow]regex-only[/yellow] [dim]({ner})[/dim]"
    console.print()
    console.print(Panel(
        f"[bold]Baldanders {__version__}[/bold]: your personal data never leaves this machine.\n"
        f"Provider: [bold]{provider.name}[/bold]   Session: [dim]{session_id}{'' if save else ' (not saved)'}[/dim]\n"
        f"Name/address model: {ner_line}\n"
        "Commands: [dim]/paste  /map  /raw  /clear  /help  /quit[/dim]",
        border_style="blue",
    ))
    console.print()

    raw_mode = False

    def persist() -> None:
        if save:
            save_session(session_id, session_map, messages, provider_state=provider.get_state())

    while True:
        raw = _read_input(raw_mode)
        if raw is None:
            console.print("\n[dim]Bye.[/dim]")
            break
        if not raw:
            continue

        command = raw.lower()
        if command in ("/quit", "/exit"):
            console.print("[dim]Bye.[/dim]")
            break
        if command == "/help":
            console.print(Text(HELP_TEXT, style="dim"))
            console.print()
            continue
        if command == "/map":
            mapping = session_map.get_map()
            if not mapping:
                console.print("[dim]No personal data mapped yet.[/dim]")
            for token, real in mapping.items():
                line = Text("  ")
                line.append(token)
                line.append(" → ")
                line.append(real, style="bold")
                console.print(line)
            console.print()
            continue
        if command == "/raw":
            raw_mode = not raw_mode
            if raw_mode:
                console.print("[bold red]Raw mode ON: messages are sent exactly as typed, with no scrubbing.[/bold red]\n")
            else:
                console.print("[dim]Raw mode OFF: scrubbing enabled.[/dim]\n")
            continue
        if command == "/clear":
            messages.clear()
            provider.reset()
            persist()
            console.print("[dim]Conversation cleared.[/dim]\n")
            continue

        # Scrub personal data (unless raw mode)
        if raw_mode:
            scrubbed = raw
        else:
            scrubbed = engine.scrub(raw)
            if scrubbed != raw:
                diff = Text("  [scrubbed] ", style="dim yellow")
                diff.append(scrubbed, style="yellow")
                console.print(diff)

        if verbose:
            console.print()
            console.print(Panel(
                Text(scrubbed),
                title="[bold red]SENT TO AI (exactly this, nothing else)[/bold red]",
                border_style="red",
            ))
            console.print()

        messages.append({"role": "user", "content": scrubbed})

        try:
            with console.status("[dim]Thinking...[/dim]"):
                response = await provider.send(messages, model=model)
        except KeyboardInterrupt:
            console.print("[dim]Cancelled.[/dim]\n")
            messages.pop()
            continue
        except Exception as e:
            console.print(Text.assemble(("Error: ", "red"), str(e)))
            console.print()
            messages.pop()
            continue

        if verbose:
            console.print(Panel(
                Text(response),
                title="[bold cyan]RAW RESPONSE FROM AI[/bold cyan]",
                border_style="cyan",
            ))

        messages.append({"role": "assistant", "content": response})

        # Restore personal data in the response
        rehydrated = session_map.rehydrate(response)

        console.print()
        console.print("[bold green]Assistant:[/bold green]")
        console.print(Markdown(rehydrated))
        console.print()

        persist()


@click.group(invoke_without_command=True)
@click.option("--provider", "-p", default="claude", type=click.Choice(PROVIDERS),
              help="LLM backend. claude/chatgpt/grok use your subscription (no API key).")
@click.option("--model", "-m", default=None, help="Model name override.")
@click.option("--api-key", default=None, envvar="BALDANDERS_API_KEY",
              help="API key (only needed for direct API providers).")
@click.option("--base-url", default=None, help="Custom API base URL (OpenAI-compatible providers).")
@click.option("--verbose", "-v", is_flag=True, help="Show exact text sent to and received from AI.")
@click.option("--resume", "-r", default=None, help="Resume a previous session by ID.")
@click.option("--no-save", is_flag=True, help="Don't write this session to disk.")
@click.version_option(__version__, prog_name="baldanders")
@click.pass_context
def main(ctx: click.Context, provider: str, model: str | None, api_key: str | None,
         base_url: str | None, verbose: bool, resume: str | None, no_save: bool) -> None:
    """Baldanders: local personal-data firewall for cloud LLMs."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        llm = _build_provider(provider, api_key, base_url)
    except (ValueError, RuntimeError, click.BadParameter) as e:
        console.print(Text(str(e), style="red"))
        sys.exit(1)

    create_default_config()
    config = load_config()
    save = config.save_sessions and not no_save

    if resume:
        loaded = load_session(resume)
        if not loaded:
            console.print(Text(f"Session '{resume}' not found.", style="red"))
            sys.exit(1)
        session_map, messages, provider_state = loaded
        llm.set_state(provider_state)
        session_id = resume
        console.print(f"[dim]Resumed session {session_id} ({len(messages)} messages)[/dim]")
    else:
        session_map = SessionMap()
        messages = []
        session_id = new_session_id()

    engine = _build_engine(session_map, config)

    async def run() -> None:
        try:
            await _repl(llm, engine, session_map, messages, session_id, model, verbose=verbose, save=save)
        finally:
            if save and messages:
                save_session(session_id, session_map, messages, provider_state=llm.get_state())
            await llm.aclose()

    asyncio.run(run())


@main.command()
def download() -> None:
    """Download the multilingual name-and-address model (~320 MB)."""
    try:
        import gliner  # noqa: F401
    except ImportError:
        console.print('[yellow]Note:[/yellow] the model needs the NER extra: pip install "baldanders[ner]"')

    with Progress(
        TextColumn("[dim]{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
        console=console,
    ) as progress:
        tasks: dict[str, int] = {}

        def on_progress(name: str, done: int, total: int | None) -> None:
            if name not in tasks:
                tasks[name] = progress.add_task(name, total=total)
            progress.update(tasks[name], completed=done)

        try:
            path = asyncio.run(download_model(progress=on_progress))
        except Exception as e:
            console.print(Text(f"Download failed: {e}", style="red"))
            sys.exit(1)
    console.print(f"[green]Model ready in {path}[/green]")


@main.command()
@click.argument("text", required=False)
@click.option("--show-map", is_flag=True, help="Also print the token → value mapping (to stderr).")
@click.option("--json", "as_json", is_flag=True, help="Print detected spans as JSON instead.")
def scrub(text: str | None, show_map: bool, as_json: bool) -> None:
    """Scrub TEXT (or stdin) and print the result. Nothing is sent anywhere."""
    if text is None:
        text = sys.stdin.read()
    session_map = SessionMap()
    engine = _build_engine(session_map, load_config())
    if as_json:
        spans = engine.detect(text)
        click.echo(json.dumps(
            [{"start": s.start, "end": s.end, "type": s.entity_type, "text": s.text} for s in spans],
            ensure_ascii=False, indent=2,
        ))
        return
    click.echo(engine.scrub(text), nl=not text.endswith("\n"))
    if show_map:
        for token, real in session_map.get_map().items():
            click.echo(f"{token} → {real}", err=True)


@main.command()
@click.option("--delete", "delete_id", default=None, metavar="ID", help="Delete a saved session.")
def sessions(delete_id: str | None) -> None:
    """List saved sessions."""
    if delete_id:
        if delete_session(delete_id):
            console.print(f"[dim]Deleted session {delete_id}.[/dim]")
        else:
            console.print(Text(f"Session '{delete_id}' not found.", style="red"))
        return
    saved = list_sessions()
    if not saved:
        console.print("[dim]No saved sessions.[/dim]")
        return
    for s in saved:
        provider = f"  {s['provider']}" if s.get("provider") else ""
        console.print(f"  [bold]{s['id']}[/bold]  {s['message_count']} messages{provider}  [dim]{s['updated_at']}[/dim]")
    console.print("\n[dim]Resume with: baldanders --resume <id>[/dim]")


if __name__ == "__main__":
    main()
