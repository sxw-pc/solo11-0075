"""Stable ordering and JSON output helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def sorted_dict(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {key: sorted_dict(payload[key]) for key in sorted(payload)}
    if isinstance(payload, (list, tuple)):
        return [sorted_dict(item) for item in payload]
    return payload
def stable_json(payload: Any, indent: int = 2) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
        allow_nan=False,
    ) + "\n"
def write_text(path: str | Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
def top_by(
    records: list[Any],
    key: str,
    limit: int,
    descending: bool,
    tie_key: str = "page_no",
) -> list[Any]:
    ordered = sorted(
        records,
        key=lambda item: (
            -getattr(item, key) if descending else getattr(item, key),
            getattr(item, tie_key),
        ),
    )
    return ordered[:limit]
