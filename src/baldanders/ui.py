"""Interactive terminal interface, in the style of coding-agent CLIs.

Prompt box with a `/` command menu and a status line, a live "thinking"
indicator, and replies rendered as markdown. Falls back to plain line input
when stdin or stdout isn't a terminal (pipes, tests).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

# Logo colours
ACCENT = "#E11D48"
ORANGE = "#F97316"

BULLET = "●"
RESULT = "⎿"

COMMANDS: dict[str, str] = {
    "/map": "Show which token stands for which real value",
    "/raw": "Toggle scrubbing off/on (only for messages with nothing sensitive)",
    "/clear": "Start a fresh conversation (keeps the token map)",
    "/login": "Sign in to the current tool again",
    "/help": "Show commands and shortcuts",
    "/quit": "Exit",
}

SHORTCUTS = (
    ("/", "command menu"),
    ("\\ + Enter", "new line"),
    ("Esc then Enter", "new line"),
    ("Ctrl+C", "clear input, twice to exit"),
    ("Ctrl+D", "exit"),
)


class TerminalUI:
    def __init__(
        self,
        console: Console,
        provider: str,
        session_id: str,
        saved: bool,
        ner_status: Callable[[], str] | str,
        version: str,
        interactive: bool | None = None,
    ) -> None:
        self.console = console
        self.provider = provider
        self.session_id = session_id
        self.saved = saved
        self._ner_status = ner_status if callable(ner_status) else (lambda: ner_status)
        self.version = version
        self.protected = 0
        self.raw_mode = False
        self._last_interrupt = 0.0
        self.interactive = (
            sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
        )
        self._session = self._build_prompt() if self.interactive else None

    # --- Header -----------------------------------------------------------

    @property
    def ner_status(self) -> str:
        return self._ner_status()

    def header(self) -> None:
        status = self.ner_status
        model = {"loaded": "loaded", "loading": "loading in the background"}.get(status, f"regex only ({status})")
        body = Text()
        body.append("◆ ", style=f"bold {ACCENT}")
        body.append("Baldanders", style="bold")
        body.append(f" {self.version}\n", style="dim")
        body.append("Your personal data never leaves this machine.\n\n", style="dim")
        body.append("/help for commands · / for the menu\n\n", style="dim")
        for label, value in (
            ("provider", self.provider),
            ("names model", model),
            ("session", self.session_id + ("" if self.saved else " (not saved)")),
            ("cwd", _short_cwd()),
        ):
            body.append(f"{label}: ", style="dim")
            body.append(f"{value}\n")
        body.rstrip()
        self.console.print(Panel(body, border_style=ACCENT, expand=False, padding=(0, 2)))
        self.console.print()

    # --- Input ------------------------------------------------------------

    def _build_prompt(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style

        class SlashCompleter(Completer):
            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                if not text.startswith("/") or " " in text or "\n" in text:
                    return
                for cmd, desc in COMMANDS.items():
                    if cmd.startswith(text):
                        yield Completion(cmd, start_position=-len(text), display_meta=desc)

        bindings = KeyBindings()

        @bindings.add("enter")
        def _(event):
            buf = event.current_buffer
            if buf.complete_state and buf.complete_state.current_completion:
                buf.apply_completion(buf.complete_state.current_completion)
                return
            if buf.text.endswith("\\"):
                buf.delete_before_cursor(1)
                buf.insert_text("\n")
                return
            buf.validate_and_handle()

        @bindings.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        @bindings.add("c-c")
        def _(event):
            buf = event.current_buffer
            if buf.text:
                buf.reset()
                return
            now = time.monotonic()
            if now - self._last_interrupt < 2:
                event.app.exit(exception=EOFError())
            else:
                self._last_interrupt = now
                event.app.invalidate()

        @bindings.add("c-d")
        def _(event):
            if not event.current_buffer.text:
                event.app.exit(exception=EOFError())

        style = Style.from_dict({
            "prompt": f"bold {ACCENT}",
            "raw": f"bold {ORANGE}",
            "rule": "#3f3f46",
            "toolbar": "noreverse #a1a1aa",
            "bottom-toolbar": "noreverse bg:default #71717a",
            "placeholder": "#52525b",
            "completion-menu": "bg:#18181b #e4e4e7",
            "completion-menu.completion.current": f"bg:{ACCENT} #ffffff",
            "completion-menu.meta.completion": "bg:#18181b #71717a",
            "completion-menu.meta.completion.current": f"bg:{ACCENT} #ffffff",
        })

        def message():
            width = self.console.width
            rule = "─" * max(10, width)
            mark = ("raw", "! ") if self.raw_mode else ("prompt", "> ")
            return [("class:rule", rule + "\n"), (f"class:{mark[0]}", mark[1])]

        def toolbar():
            if time.monotonic() - self._last_interrupt < 2:
                return HTML("<toolbar>  Press Ctrl+C again to exit</toolbar>")
            parts = [self.provider]
            if self.ner_status == "loading":
                parts.append("loading names model…")
            if self.raw_mode:
                parts.append("<b><style fg='%s'>RAW: scrubbing off</style></b>" % ORANGE)
            parts.append(f"{self.protected} item{'s' if self.protected != 1 else ''} protected")
            parts.append("/ for commands · \\⏎ for newline")
            return HTML("<toolbar>  " + " · ".join(parts) + "</toolbar>")

        return PromptSession(
            message=message,
            completer=SlashCompleter(),
            complete_while_typing=True,
            key_bindings=bindings,
            bottom_toolbar=toolbar,
            placeholder=HTML("<placeholder>Type a message, or / for commands</placeholder>"),
            style=style,
            multiline=True,
            prompt_continuation=lambda width, line_number, wrap_count: "  ",
            enable_history_search=False,
        )

    async def read(self, default: str = "") -> str | None:
        """Read one message. Returns None when the user exits.

        `default` pre-fills the prompt, e.g. with a message that failed to send.
        """
        if self._session is None:
            return self._read_plain()
        try:
            text = await self._session.prompt_async(default=default)
        except (EOFError, KeyboardInterrupt):
            return None
        text = text.strip()
        if text.lower() == "/paste":
            # Pasting works directly in the prompt; keep /paste for habit's sake.
            self.console.print(Text(f"  {RESULT}  Paste straight into the prompt; it keeps every line.", style="dim"))
            return ""
        return text

    def _read_plain(self) -> str | None:
        """Line input for pipes and dumb terminals."""
        prompt = "! " if self.raw_mode else "> "
        try:
            first_line = self.console.input(prompt)
        except (EOFError, KeyboardInterrupt):
            return None
        if first_line.strip().lower() == "/paste":
            lines: list[str] = []
            while True:
                try:
                    line = self.console.input("")
                except (EOFError, KeyboardInterrupt):
                    break
                if line.strip() == ".":
                    break
                lines.append(line)
            return "\n".join(lines).strip()
        return first_line.strip()

    # --- Output -----------------------------------------------------------

    def note(self, text: str, style: str = "dim") -> None:
        self.console.print(Text(f"  {RESULT}  {text}", style=style))
        self.console.print()

    def protected_line(self, tokens: list[str]) -> None:
        self.protected += len(tokens)
        line = Text(f"  {RESULT}  ", style="dim")
        line.append(f"Protected {len(tokens)}: ", style=ORANGE)
        line.append(", ".join(tokens), style="dim")
        self.console.print(line)

    def sent(self, text: str) -> None:
        """Verbose mode: show exactly what leaves the machine."""
        self.console.print(Panel(
            Text(text), title="sent to AI", title_align="left",
            border_style="#52525b", padding=(0, 1),
        ))

    def raw_response(self, text: str) -> None:
        self.console.print(Panel(
            Text(text), title="raw reply from AI", title_align="left",
            border_style="#52525b", padding=(0, 1),
        ))

    def assistant(self, text: str) -> None:
        self.console.print()
        self.console.print(self._reply(text))
        self.console.print()

    def error(self, message: str) -> None:
        line = Text(f"  {RESULT}  ", style="dim")
        line.append("Error: ", style="bold red")
        line.append(message, style="red")
        self.console.print(line)
        self.console.print()

    def token_map(self, mapping: dict[str, str]) -> None:
        if not mapping:
            self.note("No personal data mapped yet.")
            return
        width = max(len(t) for t in mapping)
        for token, real in mapping.items():
            line = Text(f"  {token:<{width}}  ", style="dim")
            line.append(real, style="bold")
            self.console.print(line)
        self.console.print()

    def help(self) -> None:
        width = max(len(c) for c in COMMANDS)
        for cmd, desc in COMMANDS.items():
            line = Text(f"  {cmd:<{width}}  ", style=f"bold {ACCENT}")
            line.append(desc, style="dim")
            self.console.print(line)
        self.console.print()
        width = max(len(k) for k, _ in SHORTCUTS)
        for key, desc in SHORTCUTS:
            line = Text(f"  {key:<{width}}  ")
            line.append(desc, style="dim")
            self.console.print(line)
        self.console.print()

    async def confirm(self, question: str, default: bool = True) -> bool:
        """Yes/no question. Non-interactive sessions get the default."""
        if not self.interactive:
            return default
        from prompt_toolkit import PromptSession

        hint = "Y/n" if default else "y/N"
        try:
            answer = await PromptSession().prompt_async(f"{question} [{hint}] ")
        except (EOFError, KeyboardInterrupt):
            return False
        answer = answer.strip().lower()
        return default if not answer else answer in ("y", "yes", "s", "si", "sí")

    async def stream_reply(self, pieces: AsyncIterator[str]) -> str:
        """Show a reply as it arrives, then print it in full. Returns the text shown."""
        text = ""
        live: Live | None = None
        async with self.thinking():
            iterator = pieces.__aiter__()
            # Wait for the first piece under the spinner.
            while True:
                try:
                    piece = await iterator.__anext__()
                except StopAsyncIteration:
                    iterator = None
                    break
                if piece:
                    text += piece
                    break
        try:
            if iterator is not None:
                live = Live(console=self.console, refresh_per_second=15, transient=True)
                live.start()
                live.update(_Tail(self._reply(text), self.console.height - 4))
                async for piece in iterator:
                    if piece:
                        text += piece
                        live.update(_Tail(self._reply(text), self.console.height - 4))
        finally:
            if live is not None:
                live.stop()
            if text:
                self.assistant(text)
        return text

    def _reply(self, text: str) -> Table:
        grid = Table.grid(padding=(0, 1))
        grid.add_column(width=1, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text(BULLET, style=f"bold {ACCENT}"), Markdown(text))
        return grid

    @asynccontextmanager
    async def thinking(self, label: str = "Thinking…") -> AsyncIterator[None]:
        """Animated status with elapsed time while waiting."""
        start = time.monotonic()
        status = self.console.status(_thinking_text(0, label), spinner="dots", spinner_style=ACCENT)
        status.start()

        async def tick() -> None:
            while True:
                await asyncio.sleep(1)
                status.update(_thinking_text(int(time.monotonic() - start), label))

        task = asyncio.create_task(tick())
        try:
            yield
        finally:
            task.cancel()
            status.stop()


class _Tail:
    """Renders only the last `height` lines, so a long streaming reply never
    overflows the screen while it's still being written."""

    def __init__(self, renderable, height: int) -> None:
        self.renderable = renderable
        self.height = max(3, height)

    def __rich_console__(self, console, options):
        lines = console.render_lines(self.renderable, options, pad=False)
        for line in lines[-self.height:]:
            yield from line
            yield Segment.line()


def _thinking_text(seconds: int, label: str = "Thinking…") -> Text:
    text = Text(f"{label} ", style=f"bold {ACCENT}")
    text.append(f"({seconds}s · ctrl+c to cancel)", style="dim")
    return text


def _short_cwd() -> str:
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd
