"""User configuration — allowlist, denylist, custom patterns, regions."""

from __future__ import annotations

import locale
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(os.environ.get("BALDANDERS_HOME", Path.home() / ".baldanders"))
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"


@dataclass
class Config:
    # Terms that should NEVER be scrubbed (S3 buckets, table names, etc.)
    allowlist: list[str] = field(default_factory=list)

    # Terms that should ALWAYS be scrubbed (custom sensitive values)
    denylist: dict[str, str] = field(default_factory=dict)  # value -> entity_type

    # Extra regex patterns: entity name -> pattern string
    custom_patterns: dict[str, str] = field(default_factory=dict)

    # Countries (ISO 3166 alpha-2) whose local phone formats to recognise.
    # Numbers written with a +country prefix are always recognised.
    regions: list[str] = field(default_factory=list)

    # Also scrub checksum-valid ID numbers that have no keyword ("DNI", "BSN", ...) nearby.
    paranoid: bool = False

    # NER confidence threshold (0-1) for all entity types. None = tuned per-type defaults.
    ner_threshold: float | None = None

    # Write conversations and the token mapping to ~/.baldanders/sessions.
    save_sessions: bool = True


def system_regions() -> list[str]:
    """Best guess at the user's country from the OS locale (e.g. es_AR -> AR)."""
    candidates = [os.environ.get(v, "") for v in ("LC_ALL", "LC_TELEPHONE", "LANG")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except Exception:
        pass
    for value in candidates:
        m = re.match(r"^[a-z]{2,3}[_-]([A-Z]{2})\b", value)
        if m:
            return [m.group(1)]
    return []


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load config from YAML file. Returns defaults if file doesn't exist."""
    if not path.exists():
        return Config(regions=system_regions())

    try:
        import yaml
    except ImportError:
        log.warning("PyYAML not installed — ignoring config file. pip install pyyaml")
        return Config(regions=system_regions())

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        log.warning("Failed to parse config at %s", path, exc_info=True)
        return Config(regions=system_regions())

    regions = [str(r).upper() for r in (data.get("regions") or [])] or system_regions()
    return Config(
        allowlist=[str(t) for t in (data.get("allowlist") or [])],
        denylist={str(k): str(v).upper() for k, v in (data.get("denylist") or {}).items()},
        custom_patterns={str(k): str(v) for k, v in (data.get("custom_patterns") or {}).items()},
        regions=regions,
        paranoid=bool(data.get("paranoid", False)),
        ner_threshold=float(data["ner_threshold"]) if data.get("ner_threshold") is not None else None,
        save_sessions=bool(data.get("save_sessions", True)),
    )


def create_default_config(path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Create a starter config file with examples."""
    if path.exists():
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("""\
# Baldanders configuration
# ========================

# Countries whose local phone formats to recognise (ISO codes like AR, US, DE, IN).
# Numbers with a +country prefix are always caught. Empty = guess from your OS locale.
regions: []
  # - AR
  # - US

# Also scrub checksum-valid ID numbers (BSN, PESEL, Steuer-ID, ...) even when
# no keyword like "DNI" or "BSN" is nearby. Catches more, but plain numbers
# in code or maths may get tokenised too.
paranoid: false

# Name/address model confidence (0-1). Lower catches more, with more false
# positives. Leave unset for tuned defaults (names 0.7, addresses 0.4).
# ner_threshold: 0.6

# Save conversations (including the real values behind each token) to
# ~/.baldanders/sessions so you can --resume them. Files are readable only by you.
save_sessions: true

# Terms that should NEVER be scrubbed.
# Use this for internal infrastructure names, bucket names, table names, etc.
allowlist:
  # - verify-internal-data-prod
  # - my-s3-bucket-name

# Terms that should ALWAYS be scrubbed (case-insensitive), mapped to an entity type.
# Use this for values the detectors might miss.
denylist: {}
  # "Acme Corp Internal": ORGANIZATION
  # "Project Falcon": PROJECT

# Custom regex patterns. The whole match is scrubbed; if the pattern has a
# capturing group, only the first group is.
custom_patterns: {}
  # customer_id: "CID-\\\\d{8}"
  # portfolio_ref: "PF/\\\\d{4}/\\\\d{6}"
""", encoding="utf-8")
    log.info("Created default config at %s", path)
