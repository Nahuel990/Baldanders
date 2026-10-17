<p align="center">
  <img src="https://raw.githubusercontent.com/Nahuel990/Baldanders/main/baldanders.svg" alt="Baldanders" width="320">
</p>

# Baldanders

[![PyPI](https://img.shields.io/pypi/v/baldanders?cacheSeconds=3600)](https://pypi.org/project/baldanders/)
[![Python](https://img.shields.io/pypi/pyversions/baldanders?cacheSeconds=3600)](https://pypi.org/project/baldanders/)
[![CI](https://github.com/Nahuel990/Baldanders/actions/workflows/ci.yml/badge.svg)](https://github.com/Nahuel990/Baldanders/actions/workflows/ci.yml)

Local personal-data firewall for cloud LLMs. Chat with Claude, ChatGPT, Grok and others while your names, emails, ID numbers, bank details and secrets stay on your machine.

<p align="center">
  <img src="https://raw.githubusercontent.com/Nahuel990/Baldanders/main/docs/demo.gif" alt="Baldanders scrubbing an ID, IBAN, phone, email, API key and password" width="720">
</p>

```
You:        Hi, I'm Nahuel Nucera, my DNI is 30.123.456 and my email is nahuel@gmail.com
Sent to AI: Hi, I'm [PERSON_1], my DNI is [NATIONAL_ID_1] and my email is [EMAIL_1]
AI replied: Hi [PERSON_1], ...
You see:    Hi Nahuel Nucera, ...
```

## How it works

You type normally. Baldanders finds personal details, swaps each one for a token like `[PERSON_1]`, and sends only the cleaned text to the AI. When the reply comes back, the tokens are swapped back to the real values. The AI never sees your data, and the token map never leaves your computer.

Detection is local and runs in layers:

1. **Your rules**: terms and patterns from your config that must always be hidden.
2. **Deterministic detectors**: emails, phones (any country), cards, IBANs, 55+ national ID formats, secrets. Each one is checksum-validated where the format has a checksum.
3. **A multilingual AI model** (optional) for names and street addresses in 20+ languages.

## Install

```bash
pip install baldanders                 # core: regex/checksum detectors, all providers except the API ones
pip install "baldanders[ner]"          # + name & address model (pulls in PyTorch, ~1 GB)
pip install "baldanders[anthropic]"    # + direct Claude API provider
pip install "baldanders[all]"          # everything
```

[pipx](https://pipx.pypa.io) keeps it isolated: `pipx install "baldanders[all]"`.

For name and address detection, download the model once (about 320 MB, checksum-verified). After that it runs fully offline:

```bash
baldanders download
```

## Usage

```bash
baldanders                      # Claude, via your Claude Code login (no API key)
baldanders -p chatgpt           # ChatGPT, via your Codex CLI login (no API key)
baldanders -p grok              # Grok, via grok-cli (SuperGrok / X Premium+, no API key)

baldanders -p anthropic-api     # Claude API (ANTHROPIC_API_KEY or `ant auth login`)
baldanders -p openai-api        # OPENAI_API_KEY
baldanders -p deepseek          # DEEPSEEK_API_KEY
baldanders -p groq              # GROQ_API_KEY
baldanders -p openai-api --base-url http://localhost:11434/v1 -m llama3   # any OpenAI-compatible API

baldanders -v                   # show exactly what leaves your machine
baldanders --no-save            # don't write this conversation to disk
baldanders sessions             # list saved sessions
baldanders --resume 20261002-143022
baldanders sessions --delete 20261002-143022
```

Scrub text without talking to any AI (useful in scripts and pipelines):

```bash
echo "Call me on +54 9 11 4567-8901" | baldanders scrub
# Call me on [PHONE_1]
baldanders scrub --json "IBAN DE89 3704 0044 0532 0130 00"
```

### Commands inside the chat

| Command | What it does |
|---------|-------------|
| `/paste` | Multi-line input (end with `.` on its own line) |
| `/map` | Show which token stands for which real value |
| `/raw` | Toggle scrubbing off/on for messages with nothing sensitive |
| `/clear` | Start a fresh conversation (keeps the token map) |
| `/help` | Show commands |
| `/quit` | Exit |

## What gets scrubbed

**Everywhere:** emails (including non-Latin addresses), phone numbers from any country (Google's libphonenumber), credit cards (Luhn-checked), IBANs for 89 countries (length and mod-97 checked), IPv4/IPv6 and MAC addresses, crypto wallets, dates of birth, passport numbers next to a keyword, and names and street addresses with the model installed.

**Secrets:** API keys (AWS, GitHub, GitLab, Slack, Stripe, Google, OpenAI, Anthropic, Hugging Face, npm), JWTs, private keys, passwords in `password: …` / `contraseña = …` / database URLs.

**National IDs and tax numbers**, validated with [python-stdnum](https://arthurdejong.org/python-stdnum/) where a checksum exists:

| Region | Identifiers |
|--------|-------------|
| Americas | US SSN/ITIN · Canada SIN · Brazil CPF/CNPJ · Argentina DNI/CUIT/CUIL · Chile RUT · Mexico CURP/RFC · Colombia NIT · Ecuador cédula · Uruguay RUT |
| Europe | UK NINO/NHS · Spain DNI/NIE · Portugal NIF · France NIR · Italy codice fiscale · Germany Steuer-ID · Netherlands BSN · Belgium national number · Poland PESEL · Sweden/Norway/Denmark/Finland personal numbers · Switzerland AHV · Austria SVNR · Ireland PPS · Czech/Slovak birth number · Romania CNP · Bulgaria EGN · Croatia OIB · Greece AMKA · Estonia · Lithuania · Turkey TC Kimlik · Russia INN · Ukraine RNTRC |
| Asia & Pacific | India Aadhaar/PAN · Pakistan CNIC · China resident ID · Hong Kong HKID · Taiwan ID · Japan My Number · South Korea RRN · Malaysia MyKad · Singapore NRIC · Indonesia NIK · Thailand ID · Israel ID · Australia TFN · New Zealand IRD |
| Africa | South Africa ID |
| Anywhere | Any ID-shaped number right after a keyword like *DNI, cédula, ID number, Ausweis, 身份证, …* |

Some IDs are just a run of digits with no distinctive shape, like a BSN, PESEL or Steuer-ID. Those are scrubbed when a keyword is nearby ("BSN 111222333"). If you set `paranoid: true`, they're scrubbed on checksum alone.

Ordinary text is left alone: code, SQL, version numbers, order numbers, amounts, ordinary dates and city names. If something is over-scrubbed anyway, the AI only sees a token and you still get the real value back in the reply.

## Configuration

`~/.baldanders/config.yaml` is created on first run:

```yaml
regions: [AR, US]        # local phone formats to recognise; empty = guess from OS locale
paranoid: false          # scrub checksum-valid IDs even without a keyword nearby
# ner_threshold: 0.6     # name/address model confidence (default: names 0.7, addresses 0.4)
save_sessions: true

allowlist:               # never scrub these
  - my-s3-bucket-name

denylist:                # always scrub these (case-insensitive) as the given type
  "Project Falcon": PROJECT

custom_patterns:         # extra regexes; first capturing group is scrubbed if present
  customer_id: "CID-\\d{8}"
```

Set `BALDANDERS_HOME` to keep config, sessions and the model somewhere other than `~/.baldanders`.

## Privacy notes

- **What is sent:** only the scrubbed text, plus a short instruction telling the AI to keep the tokens intact. Run with `-v` to see every byte.
- **What stays local:** the token map, the model, and your config. Saved sessions (`~/.baldanders/sessions/`) contain the real values so `--resume` works. They are readable only by your user. Turn them off with `--no-save` or `save_sessions: false`.
- **Subscription CLIs:** Baldanders runs `claude` and `codex` in an empty private directory. For Claude Code, all tools and MCP servers are disabled, so the AI can't read your files. Codex runs in its read-only sandbox, so it can still read files outside that directory if it chooses to. Prefer `-p claude` or an API provider for sensitive work. These CLIs add their own context to each request (OS, working directory path, and for Claude Code your personal `~/.claude/CLAUDE.md` if you have one). Baldanders doesn't control that context.
- **No detector is perfect.** Unusual names, free-form addresses, or IDs written in unexpected ways can slip through. Check `-v` output when it matters, and add anything specific to your work to the `denylist`.

## Requirements

- Python 3.10+
- For `-p claude`: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), logged in
- For `-p chatgpt`: [Codex CLI](https://github.com/openai/codex) (`npm install -g @openai/codex`, then `codex login`)
- For `-p grok`: [grok-cli](https://github.com/Moore-developers/grok-cli) (`grok-cli login`)

## Development

```bash
pip install -e ".[dev,ner,anthropic]"
baldanders download   # optional: enables the NER integration tests
pytest
```

### Releasing

Releases go to PyPI from GitHub Actions via [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored anywhere.

1. One-time setup: on PyPI, add a pending publisher for project `baldanders`, owner `Nahuel990`, repo `Baldanders`, workflow `release.yml`, environment `pypi`. On GitHub, create an environment named `pypi`.
2. Bump `__version__` in `src/baldanders/__init__.py` and commit.
3. Tag and push: `git tag v0.1.0 && git push origin v0.1.0`.

## License

MIT
