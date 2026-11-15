"""Stable JSON report writer and loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..util.ordering import stable_json, write_text


class ReportError(ValueError):
    """Raised when a report file cannot be loaded as a JSON object."""
def write_json_report(report: dict[str, Any], out_dir: str | Path) -> Path:
    target = Path(out_dir) / "report.json"
    write_text(target, stable_json(report))
    return target
def load_report(path: str | Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except OSError as exc:
        raise ReportError(f"无法读取报告: {exc}") from exc
    except ValueError as exc:
        raise ReportError(f"报告不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportError("报告顶层必须是 JSON 对象")
    return payload
