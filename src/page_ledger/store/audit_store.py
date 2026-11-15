"""Local SQLite ledger for audit runs, findings and summaries."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..util.hashing import path_digest

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    path_sha256 TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    page_size INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    status TEXT NOT NULL,
    report_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    run_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    code TEXT NOT NULL,
    level TEXT NOT NULL,
    page_no INTEGER,
    field_name TEXT NOT NULL,
    expected_value TEXT NOT NULL,
    actual_value TEXT NOT NULL,
    evidence TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS page_summary (
    run_id INTEGER NOT NULL,
    page_type TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    cell_count INTEGER NOT NULL,
    used_bytes INTEGER NOT NULL,
    free_bytes INTEGER NOT NULL,
    fragment_bytes INTEGER NOT NULL,
    reserved_bytes INTEGER NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS rule_summary (
    run_id INTEGER NOT NULL,
    rule_code TEXT NOT NULL,
    hits INTEGER NOT NULL,
    max_level TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_pages_run ON page_summary(run_id, page_type);
CREATE INDEX IF NOT EXISTS idx_rules_run ON rule_summary(run_id, rule_code);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
def write_audit_store(
    report: dict[str, Any],
    path: str | Path,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot = report["snapshot"]
    header = report["header"]
    started = started_at or utc_now()
    finished = finished_at or utc_now()
    connection = sqlite3.connect(str(target))
    try:
        connection.executescript(SCHEMA)
        with connection:
            cursor = connection.execute(
                "INSERT INTO runs(file_name, path_sha256, sha256, file_size, page_size, "
                "started_at, finished_at, conclusion, status, report_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot["display_name"],
                    path_digest(str(snapshot["path"])),
                    snapshot["sha256"],
                    int(snapshot["size"]),
                    int(header.get("page_size") or 0),
                    started,
                    finished,
                    str(report.get("conclusion", "")),
                    str(report.get("status", "")),
                    str(report.get("report_version", "")),
                ),
            )
            run_id = int(cursor.lastrowid)
            for ordinal, item in enumerate(report.get("findings", [])):
                connection.execute(
                    "INSERT INTO findings(run_id, ordinal, code, level, page_no, field_name, "
                    "expected_value, actual_value, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        ordinal,
                        str(item.get("code", "")),
                        str(item.get("level", "")),
                        item.get("page"),
                        str(item.get("field", "")),
                        str(item.get("expected", "")),
                        str(item.get("actual", "")),
                        str(item.get("evidence", "")),
                    ),
                )
            for item in report.get("summary", {}).get("pages", {}).get("by_page_type", []):
                connection.execute(
                    "INSERT INTO page_summary(run_id, page_type, page_count, cell_count, "
                    "used_bytes, free_bytes, fragment_bytes, reserved_bytes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        str(item.get("page_type", "")),
                        int(item.get("page_count", 0)),
                        int(item.get("cell_count", 0)),
                        int(item.get("used_bytes", 0)),
                        int(item.get("free_bytes", 0)),
                        int(item.get("fragmented_bytes", 0)),
                        int(item.get("reserved_bytes", 0)),
                    ),
                )
            for item in report.get("rule_stats", []):
                connection.execute(
                    "INSERT INTO rule_summary(run_id, rule_code, hits, max_level) VALUES (?, ?, ?, ?)",
                    (
                        run_id,
                        str(item.get("code", "")),
                        int(item.get("hits", 0)),
                        str(item.get("max_level", "")),
                    ),
                )
        return run_id
    finally:
        connection.close()
