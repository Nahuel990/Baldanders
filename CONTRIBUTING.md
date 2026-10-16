# Contributing

Thanks for helping make Baldanders better. Contributions of any size are welcome, especially new national ID formats, address styles and languages.

## Setup

```bash
git clone https://github.com/Nahuel990/Baldanders.git
cd Baldanders
pip install -e ".[dev,ner,anthropic]"
baldanders download   # optional: enables the name/address model tests
pytest
```

## Never use real personal data

Not in tests, issues, commits or screenshots. Use made-up names and the documented example numbers from [python-stdnum](https://arthurdejong.org/python-stdnum/), which pass checksums but belong to no one.

## Adding a detector

Rules live in `src/baldanders/scrubber/regex.py`. Each rule is a regex plus, when the format has one, a checksum validator.

- **Distinctive formats** (dashes, letters, fixed structure, e.g. `20-05536168-2`) can fire on their own.
- **Plain runs of digits** must require a keyword nearby (`context=...`, `always=False`), otherwise ordinary numbers in code and maths get scrubbed.
- Add a positive example to `WORLDWIDE` in `tests/test_regex.py`, a bad-checksum example to `test_bad_checksums_rejected`, and make sure `test_no_false_positives` still passes.

## Pull requests

- Keep each PR focused on one change, with tests.
- `pytest` must pass. CI runs it on Linux, macOS and Windows with Python 3.10–3.13.
- Describe what the change catches or fixes, with a made-up example.
