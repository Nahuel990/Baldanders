# Baldanders

Local personal-data firewall for cloud LLMs. Scrubs names, emails, and other private details before they leave your machine.

## How it works

You type normally. Baldanders detects personal details (names, emails, IBANs, credit cards, etc.), replaces them with tokens like `[PERSON_1]`, sends the clean text to the AI, then swaps the real data back into the response. The AI never sees your personal data.

## Install

```bash
pip install -e .
```

## Download the NER model (optional, improves name detection)

```bash
baldanders download
```

## Usage

```bash
# Claude (uses Claude Code, no API key needed)
baldanders

# ChatGPT (uses Codex CLI, no API key needed)
baldanders -p chatgpt

# Grok (uses grok-cli, no API key needed)
baldanders -p grok

# API-key providers
baldanders -p deepseek --api-key sk-...

# Verbose mode — see exactly what leaves your machine
baldanders -v

# Resume a previous session
baldanders --resume 20261002-143022

# List saved sessions
baldanders sessions
```

## Commands (inside the REPL)

| Command | What it does |
|---------|-------------|
| `/paste` | Multi-line input mode (end with `.` on a new line) |
| `/map` | Show current personal-data token mapping |
| `/raw` | Toggle scrubbing off/on for non-sensitive messages |
| `/clear` | Clear conversation history |
| `/quit` | Exit |

## What gets scrubbed

**Regex layer (deterministic):** emails, credit cards (Luhn-validated), IBANs, UK sort codes, UK NINOs, phone numbers, IP addresses, SSNs

**NER layer (GLiNER model):** person names, street addresses, passport numbers

## Architecture

```
You type → Baldanders scrubs personal data locally → Clean text sent to AI → Response restored → You see real data
```

The token mapping lives only on your machine. Nothing is persisted to the cloud. Sessions are saved to `~/.baldanders/sessions/`.

## Requirements

- Python 3.10+
- For Claude: [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and logged in
- For ChatGPT: [Codex CLI](https://github.com/openai/codex) installed and logged in
- For Grok: [grok-cli](https://github.com/Moore-developers/grok-cli) installed and logged in
