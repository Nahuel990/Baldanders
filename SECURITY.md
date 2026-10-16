# Security policy

Baldanders exists to keep personal data from leaving your machine, so a bug that lets data slip through counts as a security issue.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through [GitHub's private vulnerability reporting](https://github.com/Nahuel990/Baldanders/security/advisories/new).

Useful things to include:

- what leaked or could leak (e.g. "a German IBAN written with dots isn't scrubbed", "session files are world-readable on Windows")
- a minimal example that reproduces it, using **made-up data**, never real personal details
- your Baldanders version (`baldanders --version`), OS and provider (`-p ...`)

You'll get a reply within a week. Fixes for confirmed issues are released as soon as possible and credited in the release notes unless you'd rather stay anonymous.

## What's in scope

- Personal data or secrets reaching the AI provider despite scrubbing being on
- The token map or saved sessions being readable by other users or processes
- Anything that makes Baldanders send data somewhere other than the chosen provider

A detector simply missing an unusual format is usually a normal bug. Open a [detection issue](https://github.com/Nahuel990/Baldanders/issues/new?template=detection.yml) for those, again with made-up data.

## Supported versions

Only the latest release on [PyPI](https://pypi.org/project/baldanders/) gets security fixes.
