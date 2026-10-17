"""Baldanders CLI: personal-data firewall for cloud LLMs."""

from __future__ import annotations

import asyncio
import json
import re
import sys

import click
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.text import Text

from baldanders import __version__
from baldanders.config import Config, create_default_config, load_config
from baldanders import vault
from baldanders.history import (
    delete_session, list_sessions, load_session, migrate_plaintext, new_session_id, save_session,
)
from baldanders.mapping import SessionMap
from baldanders.model_manager import download_model, is_downloaded, model_dir
from baldanders.providers.base import BaseProvider
from baldanders.scrubber.engine import ScrubEngine
from baldanders.ui import ORANGE, TerminalUI

console = Console(highlight=False)

PROVIDERS = ["claude-code", "codex", "grok", "anthropic-api", "openai-api", "deepseek", "groq"]

# Names used by 0.1.0, still accepted but not shown in --help.
_ALIASES = {"claude": "claude-code", "chatgpt": "codex"}


class _ProviderChoice(click.Choice):
    def convert(self, value, param, ctx):
        return super().convert(_ALIASES.get(value, value), param, ctx)

_OPENAI_COMPATIBLE = {
    # name: (base url, default model, api key env var)
    "openai-api": ("https://api.openai.com/v1", "gpt-4o", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
}

def _build_provider(name: str, api_key: str | None, base_url: str | None) -> BaseProvider:
    # Consumer-auth providers (no API key needed)
    name = _ALIASES.get(name, name)
    if name == "claude-code":
        from baldanders.providers.claude_code import ClaudeCodeProvider
        return ClaudeCodeProvider()
    if name == "codex":
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


_TOKEN = re.compile(r"\[[A-Z_]+_\d+\]")


async def _repl(
    provider: BaseProvider,
    engine: ScrubEngine,
    session_map: SessionMap,
    messages: list[dict[str, str]],
    session_id: str,
    model: str | None,
    verbose: bool = False,
    save: bool = True,
    interactive: bool | None = None,
) -> None:
    ui = TerminalUI(
        console, provider=provider.name, session_id=session_id, saved=save,
        ner_status=engine.ner_status, version=__version__, interactive=interactive,
    )
    ui.header()
    if provider.name == "codex":
        ui.note(
            "Codex can read files on this computer (its sandbox is read-only, not read-nothing). "
            "Use -p claude-code for the strictest setup.",
            style=ORANGE,
        )

    def persist() -> None:
        if save:
            save_session(session_id, session_map, messages, provider_state=provider.get_state())

    while True:
        raw = await ui.read()
        if raw is None:
            break
        if not raw:
            continue

        command = raw.lower()
        if command in ("/quit", "/exit"):
            break
        if command == "/help":
            ui.help()
            continue
        if command == "/map":
            ui.token_map(session_map.get_map())
            continue
        if command == "/raw":
            ui.raw_mode = not ui.raw_mode
            if ui.raw_mode:
                ui.note("Raw mode ON: messages are sent exactly as typed, with no scrubbing.", style=f"bold {ORANGE}")
            else:
                ui.note("Raw mode OFF: scrubbing enabled.")
            continue
        if command == "/clear":
            messages.clear()
            provider.reset()
            persist()
            ui.note("Conversation cleared.")
            continue

        # Scrub personal data (unless raw mode)
        if ui.raw_mode:
            scrubbed = raw
        else:
            scrubbed = engine.scrub(raw)
            if scrubbed != raw:
                known = session_map.get_map()
                tokens = list(dict.fromkeys(t for t in _TOKEN.findall(scrubbed) if t in known))
                ui.protected_line(tokens)

        if verbose:
            ui.sent(scrubbed)

        messages.append({"role": "user", "content": scrubbed})

        try:
            async with ui.thinking():
                response = await provider.send(messages, model=model)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Ctrl+C while waiting: drop this turn and keep the session going.
            task = asyncio.current_task()
            if task is not None and hasattr(task, "uncancel"):
                task.uncancel()
            ui.note("Cancelled.")
            messages.pop()
            continue
        except Exception as e:
            ui.error(str(e))
            messages.pop()
            continue

        if verbose:
            ui.raw_response(response)

        messages.append({"role": "assistant", "content": response})

        # Restore personal data in the response
        ui.assistant(session_map.rehydrate(response))

        persist()


@click.group(invoke_without_command=True)
@click.option("--provider", "-p", default="claude-code", type=_ProviderChoice(PROVIDERS),
              help="AI backend. claude-code, codex and grok use your existing login (no API key).")
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
    if save and not vault.available():
        # Never fall back to plaintext: the token map holds the real values.
        console.print(Text(f"Sessions won't be saved: {vault.unavailable_reason()}.", style="yellow"))
        save = False
    if vault.available():
        migrate_plaintext()

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

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


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
    if not vault.available():
        console.print(Text(f"Saved sessions can't be opened: {vault.unavailable_reason()}.", style="yellow"))
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
