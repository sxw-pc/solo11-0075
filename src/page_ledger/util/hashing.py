"""Hashing helpers used for snapshots, path summaries and report integrity."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
def canonical_bytes(payload: Any) -> bytes:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text.encode("utf-8")
def canonical_digest(payload: Any) -> str:
    return sha256_bytes(canonical_bytes(payload))
def path_digest(path: str) -> str:
    return sha256_bytes(path.encode("utf-8"))
