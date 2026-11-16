"""Synthetic snapshot builders shared by the cross-snapshot diff tests."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from page_ledger.audit.engine import AuditEngine
from page_ledger.report import write_json_report

Pair = tuple[Path, Path, Path, Path]


def audit(db: Path, out: Path) -> Path:
    write_json_report(AuditEngine().run(str(db)), out)
    return out / "report.json"
def pair(db_a: Path, report_a: Path, db_b: Path, report_b: Path) -> Pair:
    return db_a, audit(db_a, report_a), db_b, audit(db_b, report_b)
def make_vacuum_pair(base: Path) -> Pair:
    """A dropped table leaves a freelist; VACUUM compacts and moves roots."""
    before = base / "before.db"
    connection = sqlite3.connect(str(before))
    try:
        connection.execute("PRAGMA page_size=1024")
        connection.execute("PRAGMA auto_vacuum=NONE")
        connection.executescript(
            """
            CREATE TABLE junk (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE items (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE INDEX idx_items_payload ON items(payload);
            """
        )
        connection.executemany("INSERT INTO junk VALUES (?, ?)", [(i, "j" * 390) for i in range(1, 301)])
        connection.executemany("INSERT INTO items VALUES (?, ?)", [(i, "i" * 150) for i in range(1, 201)])
        connection.commit()
        connection.execute("DROP TABLE junk")
        connection.commit()
    finally:
        connection.close()
    after = base / "after.db"
    shutil.copyfile(before, after)
    connection = sqlite3.connect(str(after))
    try:
        connection.execute("VACUUM")
        connection.commit()
    finally:
        connection.close()
    return pair(before, base / "vac-before", after, base / "vac-after")
def make_swap_pair(base: Path) -> Pair:
    """Two tables exchange root pages: global type totals stay identical."""
    before = base / "swap-before.db"
    connection = sqlite3.connect(str(before))
    try:
        connection.execute("PRAGMA page_size=1024")
        connection.executescript(
            "CREATE TABLE x (id INTEGER PRIMARY KEY, v TEXT);"
            "CREATE TABLE y (id INTEGER PRIMARY KEY, v TEXT);"
        )
        connection.executemany("INSERT INTO x VALUES (?, ?)", [(i, "a" * 100) for i in range(1, 301)])
        connection.executemany("INSERT INTO y VALUES (?, ?)", [(i, "b" * 100) for i in range(1, 301)])
        connection.commit()
    finally:
        connection.close()
    after = base / "swap-after.db"
    shutil.copyfile(before, after)
    connection = sqlite3.connect(str(after))
    try:
        roots = {name: root for name, root in connection.execute("SELECT name, rootpage FROM sqlite_schema")}
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute("UPDATE sqlite_schema SET rootpage=? WHERE name='x'", (roots["y"],))
        connection.execute("UPDATE sqlite_schema SET rootpage=? WHERE name='y'", (roots["x"],))
        connection.commit()
    finally:
        connection.close()
    return pair(before, base / "swap-before", after, base / "swap-after")
def make_added_table_pair(base: Path) -> Pair:
    before = base / "base.db"
    connection = sqlite3.connect(str(before))
    try:
        connection.execute("PRAGMA page_size=1024")
        connection.execute("CREATE TABLE keep (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
        connection.executemany("INSERT INTO keep VALUES (?, ?)", [(i, "k" * 120) for i in range(1, 200)])
        connection.commit()
    finally:
        connection.close()
    after = base / "added.db"
    shutil.copyfile(before, after)
    connection = sqlite3.connect(str(after))
    try:
        connection.execute("CREATE TABLE extra (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
        connection.commit()
    finally:
        connection.close()
    return pair(before, base / "base-report", after, base / "added-report")
def make_page_size_pair(base: Path) -> Pair:
    small, large = base / "small.db", base / "large.db"
    for path, page_size in ((small, 1024), (large, 4096)):
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(f"PRAGMA page_size={page_size}")
            connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
            connection.executemany("INSERT INTO t VALUES (?, ?)", [(i, "v" * 40) for i in range(1, 120)])
            connection.commit()
        finally:
            connection.close()
    return pair(small, base / "small-report", large, base / "large-report")
def make_duplicate_key_db(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA page_size=1024")
        connection.execute("CREATE TABLE a (id INTEGER PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO a VALUES (1, 'x')")
        connection.commit()
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "INSERT INTO sqlite_schema(type, name, tbl_name, rootpage, sql) "
            "VALUES ('table', 'a', 'a', 2, 'CREATE TABLE a (id INTEGER PRIMARY KEY, v TEXT)')"
        )
        connection.commit()
    finally:
        connection.close()
def run_cli(*args: str) -> subprocess.CompletedProcess:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    return subprocess.run(
        [sys.executable, "-m", "page_ledger", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
