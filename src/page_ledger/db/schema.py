"""sqlite_schema record decoding and read-only sidecar comparison."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..audit.model import Problem, SchemaObject
from .pages import decode_varint


class RecordError(ValueError):
    """Raised when a SQLite record payload cannot be decoded."""
def decode_record(payload: bytes, encoding: str = "utf-8") -> list[Any]:
    if not payload:
        raise RecordError("空 record")
    header_size, used = decode_varint(payload, 0, len(payload))
    if header_size < used or header_size > len(payload):
        raise RecordError("record header size 非法")
    serials: list[int] = []
    position = used
    while position < header_size:
        serial, consumed = decode_varint(payload, position, header_size)
        serials.append(serial)
        position += consumed
    if position != header_size:
        raise RecordError("record header 未对齐")
    data_pos = header_size
    values: list[Any] = []
    sizes = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8, 7: 8, 8: 0, 9: 0}
    for serial in serials:
        if serial in (10, 11):
            raise RecordError("保留 serial type")
        if serial in sizes:
            length = sizes[serial]
            if data_pos + length > len(payload):
                raise RecordError("record 字段越过 payload")
            chunk = payload[data_pos : data_pos + length]
            data_pos += length
            if serial == 0:
                values.append(None)
            elif serial == 7:
                import struct
                values.append(struct.unpack(">d", chunk)[0])
            elif serial == 8:
                values.append(0)
            elif serial == 9:
                values.append(1)
            else:
                values.append(int.from_bytes(chunk, "big", signed=True))
        else:
            length = (serial - 12) // 2 if serial % 2 == 0 else (serial - 13) // 2
            if data_pos + length > len(payload):
                raise RecordError("record 可变字段越过 payload")
            chunk = payload[data_pos : data_pos + length]
            data_pos += length
            if serial % 2 == 0:
                values.append(bytes(chunk))
            else:
                values.append(chunk.decode(encoding, errors="replace"))
    return values
def schema_object_from_payload(payload: bytes, encoding: str = "utf-8") -> SchemaObject:
    values = decode_record(payload, encoding)
    if len(values) < 4:
        raise RecordError("sqlite_schema record 少于 4 列")
    object_type = str(values[0]) if values[0] is not None else ""
    name = str(values[1]) if values[1] is not None else ""
    table_name = str(values[2]) if values[2] is not None else ""
    raw_root = values[3]
    if raw_root is None:
        root_page = 0
    elif isinstance(raw_root, int):
        root_page = raw_root
    else:
        raise RecordError("rootpage 不是整数")
    return SchemaObject(object_type=object_type, name=name, table_name=table_name, root_page=root_page)
def extract_schema(
    records: list[tuple[int, int, bytes]],
    encoding: str = "utf-8",
) -> tuple[list[SchemaObject], list[Problem]]:
    objects: list[SchemaObject] = []
    problems: list[Problem] = []
    for page_no, cell_index, payload in records:
        try:
            objects.append(schema_object_from_payload(payload, encoding))
        except (RecordError, ValueError) as exc:
            problems.append(
                Problem(
                    "SCHEMA_RECORD_INVALID",
                    page=page_no,
                    field=f"cell[{cell_index}]",
                    expected="合法 SQLite record",
                    actual=str(exc),
                    evidence="sqlite_schema payload 无法解码",
                )
            )
    objects.sort(key=lambda item: (item.object_type, item.name, item.root_page))
    return objects, problems
@dataclass
class SidecarProbe:
    available: bool = False
    error: str = ""
    page_count: int | None = None
    objects: list[dict[str, Any]] = field(default_factory=list)
    def roots(self) -> set[int]:
        return {int(item["rootpage"]) for item in self.objects if int(item.get("rootpage") or 0) > 0}
def probe_sidecar(path: str) -> SidecarProbe:
    """Query metadata through a read-only sqlite3 connection.
    This is only corroboration.  It never executes caller supplied SQL and it
    does not contribute records to the page traversal.
    """
    uri_path = Path(path).resolve().as_uri()
    # immutable=1 prevents SQLite from consulting -wal/-shm while probing.
    uri = f"{uri_path}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error as exc:
        return SidecarProbe(available=False, error=f"{type(exc).__name__}: {exc}")
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute("PRAGMA page_count").fetchone()
        page_count = int(row[0]) if row else None
        cursor = connection.execute(
            "SELECT type, name, tbl_name, rootpage FROM sqlite_schema "
            "ORDER BY type, name, rootpage"
        )
        objects = [
            {
                "type": str(row[0]),
                "name": str(row[1]),
                "tbl_name": str(row[2]),
                "rootpage": int(row[3] or 0),
            }
            for row in cursor.fetchall()
        ]
        return SidecarProbe(available=True, page_count=page_count, objects=objects)
    except sqlite3.Error as exc:
        return SidecarProbe(available=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        connection.close()
