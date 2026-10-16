"""Model download and cache management."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable

import httpx

from baldanders.config import BASE_DIR

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = BASE_DIR / "models"

# Multilingual GLiNER (20+ languages, Apache-2.0), int8-quantized ONNX export.
# The base size is markedly more precise on names than gliner-x-small, which
# tags capitalised verbs ("Ayudame", "Decile") and product names as people.
# Pinned to a commit so a changed upstream file can't slip in silently.
MODEL_REPO = "knowledgator/gliner-x-base"
MODEL_REVISION = "a6c7a8fbfe2066c68d12c0480418504fde6d91d1"
MODEL_NAME = "gliner-x-base"

# (path in repo, sha256 or None for small text files)
MODEL_FILES: list[tuple[str, str | None]] = [
    ("gliner_config.json", None),
    ("tokenizer_config.json", None),
    ("special_tokens_map.json", None),
    ("added_tokens.json", None),
    ("spiece.model", "ef78f86560d809067d12bac6c09f19a462cb3af3f54d2b8acbba26e1433125d6"),
    ("tokenizer.json", "619d3790cf59a0e50afdf04239db19093568fbd8d806963e35d54a8fb71d099c"),
    ("onnx/model_quantized.onnx", "9850feb5ecd7ccfa3e4f1802b7c7d404f1b357cb7b6198cc014fb441e91ace43"),
]

ProgressCallback = Callable[[str, int, int | None], None]


def model_dir(cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / MODEL_NAME


def is_downloaded(cache_dir: Path = DEFAULT_CACHE_DIR) -> bool:
    d = model_dir(cache_dir)
    return all((d / name).exists() for name, _ in MODEL_FILES)


def _url(name: str) -> str:
    return f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{name}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


async def download_model(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    progress: ProgressCallback | None = None,
) -> Path:
    """Download the model files, verifying checksums. Safe to re-run."""
    d = model_dir(cache_dir)
    d.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=300), follow_redirects=True) as client:
        for name, sha in MODEL_FILES:
            target = d / name
            if target.exists() and (sha is None or _sha256(target) == sha):
                log.info("Already cached: %s", name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            log.info("Downloading %s ...", name)
            async with client.stream("GET", _url(name)) as resp:
                resp.raise_for_status()
                total = int(resp.headers["content-length"]) if "content-length" in resp.headers else None
                done = 0
                with partial.open("wb") as f:
                    async for block in resp.aiter_bytes(1 << 20):
                        f.write(block)
                        done += len(block)
                        if progress:
                            progress(name, done, total)
            if sha is not None and _sha256(partial) != sha:
                partial.unlink(missing_ok=True)
                raise RuntimeError(f"Checksum mismatch for {name}. The download is corrupted, try again.")
            partial.replace(target)

    return d
