#!/usr/bin/env python3
"""Build deterministic synthetic SQLite fixtures for page-ledger tests.

The script uses only the Python standard library and never touches the network.
Every database name, schema name and payload is synthetic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

PAGE_SIZE = 1024
FIXTURES = (
    "clean.db",
    "freelist.db",
    "overflow.db",
    "indexed.db",
    "broken_header.db",
    "bad_cell_ptr.db",
    "freelist_cycle.db",
    "overflow_break.db",
)

def filler(seed: int, length: int) -> str:
    unit = f"record-{seed:05d}-synthetic;"
    return (unit * ((length // len(unit)) + 1))[:length]

def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.execute(f"PRAGMA page_size={PAGE_SIZE}")
    connection.execute("PRAGMA auto_vacuum=NONE")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection

def create_clean(path: Path) -> None:
    connection = connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                sku TEXT NOT NULL,
                label TEXT NOT NULL,
                qty INTEGER NOT NULL
            );
            CREATE INDEX idx_products_sku ON products(sku);
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                note TEXT NOT NULL
            );
            CREATE INDEX idx_events_kind ON events(kind);
            CREATE TABLE meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        products = [
            (index, f"sku-{index:05d}", filler(index, 110), index % 97)
            for index in range(1, 601)
        ]
        events = [
            (index, f"kind-{index % 7}", filler(index + 1000, 140))
            for index in range(1, 301)
        ]
        connection.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", products)
        connection.executemany("INSERT INTO events VALUES (?, ?, ?)", events)
        connection.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [("format", "synthetic-page-ledger"), ("version", "1")],
        )
        connection.commit()
    finally:
        connection.close()

def create_freelist(path: Path) -> None:
    connection = connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE keep (id INTEGER PRIMARY KEY, note TEXT NOT NULL);
            CREATE TABLE scratch (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE INDEX idx_scratch_payload ON scratch(payload);
            """
        )
        connection.execute("INSERT INTO keep VALUES (1, 'retained-synthetic-row')")
        rows = [(index, filler(index, 420)) for index in range(1, 501)]
        connection.executemany("INSERT INTO scratch VALUES (?, ?)", rows)
        connection.commit()
        connection.execute("DELETE FROM scratch")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
    finally:
        connection.close()

def create_overflow(path: Path) -> None:
    connection = connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE INDEX idx_notes_title ON notes(title);
            """
        )
        rows = [(index, f"note-{index:03d}", filler(index * 31, 20000)) for index in range(1, 5)]
        connection.executemany("INSERT INTO notes VALUES (?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()

def create_indexed(path: Path) -> None:
    connection = connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                customer TEXT NOT NULL,
                created_at TEXT NOT NULL,
                amount INTEGER NOT NULL,
                note TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_orders_code ON orders(code);
            CREATE INDEX idx_orders_customer_created ON orders(customer, created_at);
            CREATE INDEX idx_orders_amount ON orders(amount);
            """
        )
        rows = [
            (
                index,
                f"ord-{index:06d}",
                f"customer-{index % 43:03d}",
                f"2025-01-{(index % 28) + 1:02d}",
                (index * 17) % 10000,
                filler(index, 80),
            )
            for index in range(1, 501)
        ]
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()

def copy_fixture(source: Path, target: Path) -> bytearray:
    shutil.copyfile(source, target)
    return bytearray(target.read_bytes())

def corrupt_header(source: Path, target: Path) -> None:
    data = copy_fixture(source, target)
    data[0:16] = b"NotSQLiteLedger!"[:16].ljust(16, b"\x00")
    target.write_bytes(data)

def _page_offset(page_no: int) -> int:
    return (page_no - 1) * PAGE_SIZE

def corrupt_cell_pointer(source: Path, target: Path) -> None:
    data = copy_fixture(source, target)
    for page_no in range(2, len(data) // PAGE_SIZE + 1):
        offset = _page_offset(page_no)
        if data[offset] != 0x0D:
            continue
        cells = int.from_bytes(data[offset + 3 : offset + 5], "big")
        if cells < 1:
            continue
        data[offset + 8 : offset + 10] = (0xFFF0).to_bytes(2, "big")
        target.write_bytes(data)
        return
    raise RuntimeError("no table leaf page found for bad_cell_ptr")

def corrupt_freelist_cycle(source: Path, target: Path) -> None:
    data = copy_fixture(source, target)
    first_trunk = int.from_bytes(data[32:36], "big")
    if first_trunk == 0:
        raise RuntimeError("freelist fixture has no trunk")
    offset = _page_offset(first_trunk)
    data[offset : offset + 4] = first_trunk.to_bytes(4, "big")
    target.write_bytes(data)

def decode_varint(data: bytearray, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        byte = data[offset + index]
        if index == 8:
            return (value << 8) | byte, 9
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index + 1
    raise ValueError("bad varint")

def local_payload(payload: int, usable: int) -> int:
    max_local = usable - 35
    min_local = ((usable - 12) * 32) // 255 - 23
    if payload <= max_local:
        return payload
    k = min_local + ((payload - min_local) % (usable - 4))
    return k if k <= max_local else min_local

def first_overflow_page(data: bytearray, root_page: int) -> int:
    usable = int.from_bytes(data[16:18], "big")
    page_no = root_page
    for _ in range(20):
        offset = _page_offset(page_no)
        kind = data[offset]
        if kind == 0x05:
            cells = int.from_bytes(data[offset + 3 : offset + 5], "big")
            if cells:
                pointer = int.from_bytes(data[offset + 12 : offset + 14], "big")
                page_no = int.from_bytes(data[offset + pointer : offset + pointer + 4], "big")
                continue
            page_no = int.from_bytes(data[offset + 8 : offset + 12], "big")
            continue
        if kind != 0x0D:
            raise RuntimeError(f"unexpected root page type 0x{kind:02x}")
        pointer = int.from_bytes(data[offset + 8 : offset + 10], "big")
        cell = offset + pointer
        payload, used = decode_varint(data, cell)
        _, rowid_used = decode_varint(data, cell + used)
        local = local_payload(payload, usable)
        if payload <= local:
            raise RuntimeError("fixture cell does not overflow")
        return int.from_bytes(data[cell + used + rowid_used + local : cell + used + rowid_used + local + 4], "big")
    raise RuntimeError("could not descend to a leaf page")

def corrupt_overflow(source: Path, target: Path) -> None:
    data = copy_fixture(source, target)
    connection = sqlite3.connect(str(source))
    try:
        row = connection.execute("SELECT rootpage FROM sqlite_schema WHERE name = 'notes'").fetchone()
    finally:
        connection.close()
    if not row:
        raise RuntimeError("overflow notes table missing")
    overflow = first_overflow_page(data, int(row[0]))
    pages = len(data) // PAGE_SIZE
    bad_next = pages + 50
    offset = _page_offset(overflow)
    data[offset : offset + 4] = bad_next.to_bytes(4, "big")
    target.write_bytes(data)

def build(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in FIXTURES:
        path = out_dir / name
        if path.exists():
            path.unlink()
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    created = {name: out_dir / name for name in FIXTURES}
    create_clean(created["clean.db"])
    create_freelist(created["freelist.db"])
    create_overflow(created["overflow.db"])
    create_indexed(created["indexed.db"])
    corrupt_header(created["clean.db"], created["broken_header.db"])
    corrupt_cell_pointer(created["clean.db"], created["bad_cell_ptr.db"])
    corrupt_freelist_cycle(created["freelist.db"], created["freelist_cycle.db"])
    corrupt_overflow(created["overflow.db"], created["overflow_break.db"])
    manifest = []
    for name in FIXTURES:
        data = created[name].read_bytes()
        manifest.append(
            {
                "name": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "synthetic": True,
            }
        )
    manifest_path.write_text(
        json.dumps({"fixtures": manifest, "page_size": PAGE_SIZE}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return [created[name] for name in FIXTURES]

def main() -> int:
    parser = argparse.ArgumentParser(description="生成 page-ledger 合成 SQLite fixture")
    parser.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "generated"))
    args = parser.parse_args()
    paths = build(Path(args.out_dir))
    for path in paths:
        print(f"{path.name}: {path.stat().st_size} bytes")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
