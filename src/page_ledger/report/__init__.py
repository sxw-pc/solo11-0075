"""Report writers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .html_report import render_html, write_html_report
from .json_report import load_report, write_json_report
from .markdown_report import render_markdown, write_markdown_report


def write_reports(report: dict[str, Any], out_dir: str | Path) -> dict[str, Path]:
    target = Path(out_dir)
    return {
        "json": write_json_report(report, target),
        "markdown": write_markdown_report(report, target),
        "html": write_html_report(report, target),
    }
__all__ = [
    "load_report",
    "render_html",
    "render_markdown",
    "write_html_report",
    "write_json_report",
    "write_markdown_report",
    "write_reports",
]
