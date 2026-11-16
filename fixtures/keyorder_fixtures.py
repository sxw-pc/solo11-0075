"""Synthetic multi-level table b-trees for key-order fixtures (byte-level edits only).

Page links are never changed by the corrupt variants, so reachability, page
types and cycles stay valid while rowid keys cross an ancestor separator.
"""
from __future__ import annotations
import shutil
import sqlite3
from pathlib import Path
PAGE_SIZE = 1024
FILLER_UNIT = "keyorder-{:05d};"
def _filler(seed: int, length: int) -> str:
    unit = FILLER_UNIT.format(seed)
    return (unit * ((length // len(unit)) + 1))[:length]
def _offset(page_no: int) -> int:
    return (page_no - 1) * PAGE_SIZE
def decode_varint(data, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        byte = data[offset + index]
        if index == 8:
            return (value << 8) | byte, 9
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index + 1
    raise ValueError("bad varint")
def encode_varint9(value: int) -> bytes:
    """SQLite's 9-byte varint: eight 7-bit groups followed by the low 8 bits."""
    value &= 0xFFFFFFFFFFFFFFFF
    high = value >> 8
    out = bytearray(0x80 | ((high >> shift) & 0x7F) for shift in (49, 42, 35, 28, 21, 14, 7, 0))
    out.append(value & 0xFF)
    return bytes(out)
def _write(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    connection.execute(f"PRAGMA page_size={PAGE_SIZE}")
    connection.execute("PRAGMA auto_vacuum=NONE")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=OFF")
    connection.executescript(
        """
        CREATE TABLE keyed (id INTEGER PRIMARY KEY, tag TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX idx_keyed_tag ON keyed(tag COLLATE NOCASE);
        CREATE TABLE ranged (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        """
    )
    rows = [(-9223372036854775808, "Alpha", _filler(1, 420))]
    for index in range(-201, 202):
        rows.append((index, "Alpha" if index % 2 else "alpha", _filler(index + 10000, 420)))
    rows.append((9223372036854775807, "ALPHA", _filler(2, 420)))
    connection.executemany("INSERT INTO keyed VALUES (?, ?, ?)", rows)
    wide = 1 << 56
    ranged = [(-wide - index, _filler(index + 30000, 440)) for index in range(200)]
    ranged += [(wide + index, _filler(index + 40000, 440)) for index in range(200)]
    connection.executemany("INSERT INTO ranged VALUES (?, ?)", ranged)
    connection.commit()
    connection.close()
def build_keyorder(path: Path) -> None:
    """Write the legitimate tree plus the swap and separator corrupt variants."""
    clean = path / "keyorder.db"
    _write(clean)
    _swap_cross_separator(clean, path / "keyorder_swap.db")
    _scramble_separator(clean, path / "keyorder_separator.db")
def _root_page(source: Path, name: str) -> int:
    connection = sqlite3.connect(str(source))
    try:
        row = connection.execute("SELECT rootpage FROM sqlite_schema WHERE name = ?", (name,)).fetchone()
    finally:
        connection.close()
    if not row:
        raise RuntimeError(f"fixture object missing: {name}")
    return int(row[0])
def _entries(data, page_no: int) -> list[tuple[int, int, int, int, int]]:
    offset = _offset(page_no)
    count = int.from_bytes(data[offset + 3 : offset + 5], "big")
    entries = []
    for index in range(count):
        pointer = int.from_bytes(data[offset + 12 + index * 2 : offset + 14 + index * 2], "big")
        absolute = offset + pointer
        child = int.from_bytes(data[absolute : absolute + 4], "big")
        separator, used = decode_varint(data, absolute + 4)
        entries.append((index, absolute + 4, used, child, separator))
    return entries
def _cells(data, page_no: int) -> list[tuple[int, int, int]]:
    offset = _offset(page_no)
    count = int.from_bytes(data[offset + 3 : offset + 5], "big")
    cells = []
    for index in range(count):
        pointer = int.from_bytes(data[offset + 8 + index * 2 : offset + 10 + index * 2], "big")
        absolute = offset + pointer
        _, used = decode_varint(data, absolute)
        rowid_offset = absolute + used
        rowid, width = decode_varint(data, rowid_offset)
        cells.append((rowid_offset, width, rowid))
    return cells
def _subtree(data, page_no: int, depth: int = 0) -> tuple[list[int], list[int]]:
    offset = _offset(page_no)
    kind = data[offset]
    if kind == 0x0D:
        return [], [page_no]
    if kind != 0x05 or depth > 8:
        return [], []
    interiors, leaves = [page_no], []
    children = [child for _, _, _, child, _ in _entries(data, page_no)]
    children.append(int.from_bytes(data[offset + 8 : offset + 12], "big"))
    for child in children:
        sub_interiors, sub_leaves = _subtree(data, child, depth + 1)
        interiors += sub_interiors
        leaves += sub_leaves
    return interiors, leaves
def _copy(source: Path, target: Path) -> bytearray:
    shutil.copyfile(source, target)
    return bytearray(target.read_bytes())
def _swap_cross_separator(source: Path, target: Path) -> None:
    """Move the rowid sets of the left-most and right-most leaves across each other.

    Every key in the left-most leaf lands beyond its ancestor separators and vice
    versa.  Cell pointers, payload lengths and child links are untouched, so only
    the key-partition contract is broken.
    """
    data = _copy(source, target)
    _, leaves = _subtree(data, _root_page(source, "ranged"))
    leaves.sort()
    left_cells, right_cells = _cells(data, leaves[0]), _cells(data, leaves[-1])
    if not all(width == 9 for _, width, _ in left_cells + right_cells):
        raise RuntimeError("ranged fixture must use 9-byte rowid varints")
    left_anchor = min(rowid for _, _, rowid in left_cells)
    right_anchor = max(rowid for _, _, rowid in right_cells)
    for position, (offset, _, _) in enumerate(left_cells):
        data[offset : offset + 9] = encode_varint9(right_anchor - len(left_cells) + 1 + position)
    for position, (offset, _, _) in enumerate(right_cells):
        data[offset : offset + 9] = encode_varint9(left_anchor - len(right_cells) + 1 + position)
    target.write_bytes(data)
def _scramble_separator(source: Path, target: Path) -> None:
    """Duplicate the second separator into the first cell of one interior page."""
    data = _copy(source, target)
    interiors, _ = _subtree(data, _root_page(source, "keyed"))
    target_page = next(
        (page_no for page_no in interiors if data[_offset(page_no)] == 0x05 and len(_entries(data, page_no)) >= 2),
        None,
    )
    if target_page is None:
        raise RuntimeError("no interior page with two separators")
    first, second = _entries(data, target_page)[:2]
    if first[2] != 9:
        raise RuntimeError("expected a 9-byte leading separator varint")
    data[first[1] : first[1] + 9] = encode_varint9(second[4])
    target.write_bytes(data)
