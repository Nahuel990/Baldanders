"""Model download and cache management."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".baldanders" / "models"

# GLiNER edge ONNX — update these when a new version ships
MODEL_URL = (
    "https://huggingface.co/knowledgator/gliner-pii-edge-v1.0/resolve/main/onnx/model.onnx"
)
TOKENIZER_URL = (
    "https://huggingface.co/knowledgator/gliner-pii-edge-v1.0/resolve/main/tokenizer.json"
)
CONFIG_URL = (
    "https://huggingface.co/knowledgator/gliner-pii-edge-v1.0/resolve/main/gliner_config.json"
)
TOKENIZER_CONFIG_URL = (
    "https://huggingface.co/knowledgator/gliner-pii-edge-v1.0/resolve/main/tokenizer_config.json"
)


def model_dir(cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / "gliner-pii-edge-v1.0"


def is_downloaded(cache_dir: Path = DEFAULT_CACHE_DIR) -> bool:
    d = model_dir(cache_dir)
    return (d / "model.onnx").exists() and (d / "tokenizer.json").exists()


async def download_model(cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    """Download the GLiNER ONNX model to the cache directory."""
    d = model_dir(cache_dir)
    d.mkdir(parents=True, exist_ok=True)

    files = [
        ("model.onnx", MODEL_URL),
        ("tokenizer.json", TOKENIZER_URL),
        ("gliner_config.json", CONFIG_URL),
        ("tokenizer_config.json", TOKENIZER_CONFIG_URL),
    ]

    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        for filename, url in files:
            target = d / filename
            if target.exists():
                log.info("Already cached: %s", filename)
                continue
            log.info("Downloading %s ...", filename)
            resp = await client.get(url)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            log.info("Saved %s (%d bytes)", filename, len(resp.content))

    return d
