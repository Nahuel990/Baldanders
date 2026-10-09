"""User configuration — allowlist, denylist, custom patterns."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".baldanders" / "config.yaml"


@dataclass
class Config:
    # Terms that should NEVER be scrubbed (S3 buckets, table names, etc.)
    allowlist: list[str] = field(default_factory=list)

    # Terms that should ALWAYS be scrubbed (custom sensitive values)
    denylist: dict[str, str] = field(default_factory=dict)  # value -> entity_type

    # Extra regex patterns: name -> pattern string
    custom_patterns: dict[str, str] = field(default_factory=dict)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load config from YAML file. Returns defaults if file doesn't exist."""
    if not path.exists():
        return Config()

    try:
        import yaml
    except ImportError:
        log.warning("PyYAML not installed — ignoring config file. pip install pyyaml")
        return Config()

    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        log.warning("Failed to parse config at %s", path, exc_info=True)
        return Config()

    return Config(
        allowlist=data.get("allowlist") or [],
        denylist=data.get("denylist") or {},
        custom_patterns=data.get("custom_patterns") or {},
    )


def create_default_config(path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Create a starter config file with examples."""
    if path.exists():
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("""\
# Baldanders configuration
# ========================

# Terms that should NEVER be scrubbed.
# Use this for internal infrastructure names, bucket names, table names, etc.
allowlist:
  # - verify-internal-data-prod
  # - data_warehouse_product
  # - my-s3-bucket-name

# Terms that should ALWAYS be scrubbed, mapped to an entity type.
# Use this for values the NER model might miss.
denylist: {}
  # "Acme Corp Internal": ORGANIZATION
  # "Project Falcon": PROJECT

# Custom regex patterns for bank-specific formats.
# Each pattern should have one capturing group for the value to scrub.
custom_patterns: {}
  # customer_id: "CID-\\\\d{8}"
  # portfolio_ref: "PF/\\\\d{4}/\\\\d{6}"
""")
    log.info("Created default config at %s", path)
