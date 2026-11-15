"""Parse and validate the 100-byte SQLite database header."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..audit.model import Problem
from ..io.reader import be_u16, be_u32

MAGIC = b"SQLite format 3\x00"

@dataclass
class HeaderInfo:
    magic: str
    magic_hex: str
    magic_valid: bool
    raw_page_size: int
    page_size: int
    reserved_bytes: int
    usable_size: int
    write_version: int
    read_version: int
    declared_pages: int
    first_freelist_trunk: int
    freelist_pages: int
    schema_cookie: int
    schema_format: int
    default_cache_size: int
    largest_root_page: int
    text_encoding: int
    encoding_name: str
    user_version: int
    incremental_vacuum: int
    application_id: int
    sqlite_version_number: int
    change_counter: int
    file_size: int
    actual_page_count: int
    declared_bytes: int
    trailing_bytes: int
    wal_present: bool = False
    wal_name: str | None = None
    auto_vacuum: int = 0
    problems: list[Problem] = field(default_factory=list)
    @property
    def can_read_pages(self) -> bool:
        return (
            self.page_size >= 512
            and self.usable_size >= 480
            and self.actual_page_count >= 1
        )
    @property
    def effective_page_count(self) -> int:
        return min(self.declared_pages, self.actual_page_count) if self.declared_pages else self.actual_page_count
    def to_dict(self) -> dict[str, Any]:
        return {
            "magic": self.magic,
            "magic_hex": self.magic_hex,
            "magic_valid": self.magic_valid,
            "raw_page_size": self.raw_page_size,
            "page_size": self.page_size,
            "usable_size": self.usable_size,
            "reserved_bytes": self.reserved_bytes,
            "write_version": self.write_version,
            "read_version": self.read_version,
            "declared_pages": self.declared_pages,
            "first_freelist_trunk": self.first_freelist_trunk,
            "freelist_pages": self.freelist_pages,
            "schema_cookie": self.schema_cookie,
            "schema_format": self.schema_format,
            "default_cache_size": self.default_cache_size,
            "largest_root_page": self.largest_root_page,
            "text_encoding": self.text_encoding,
            "encoding_name": self.encoding_name,
            "user_version": self.user_version,
            "incremental_vacuum": self.incremental_vacuum,
            "auto_vacuum": self.auto_vacuum,
            "application_id": self.application_id,
            "sqlite_version_number": self.sqlite_version_number,
            "change_counter": self.change_counter,
            "file_size": self.file_size,
            "actual_page_count": self.actual_page_count,
            "declared_bytes": self.declared_bytes,
            "trailing_bytes": self.trailing_bytes,
            "wal_present": self.wal_present,
            "wal_name": self.wal_name,
            "can_read_pages": self.can_read_pages,
        }
def _encoding_name(value: int) -> str:
    return {1: "utf-8", 2: "utf-16le", 3: "utf-16be"}.get(value, "unknown")
def parse_header(
    data: bytes,
    file_size: int | None = None,
    wal_present: bool = False,
    wal_name: str | None = None,
) -> HeaderInfo:
    size = len(data) if file_size is None else file_size
    raw = data[:100]
    problems: list[Problem] = []
    if len(raw) < 100:
        problems.append(
            Problem(
                "HDR_FILE_TOO_SMALL",
                field="file_size",
                expected=">= 100",
                actual=size,
                evidence="文件不足一个 SQLite 文件头",
                blocking=True,
            )
        )
    padded = raw + b"\x00" * (100 - len(raw))
    magic = padded[:16]
    magic_valid = magic == MAGIC
    if not magic_valid:
        problems.append(
            Problem(
                "HDR_MAGIC_INVALID",
                field="magic_hex",
                expected=MAGIC.hex(),
                actual=magic.hex(),
                evidence="前 16 字节不是 SQLite format 3 魔数",
                blocking=True,
            )
        )
    raw_page_size = be_u16(padded, 16)
    encoded_page_size = 65536 if raw_page_size == 1 else raw_page_size
    page_size_valid = (
        encoded_page_size >= 512
        and encoded_page_size <= 65536
        and encoded_page_size & (encoded_page_size - 1) == 0
    )
    if not page_size_valid:
        page_size = 0
        problems.append(
            Problem(
                "HDR_PAGE_SIZE_INVALID",
                field="raw_page_size",
                expected="512..65536 的 2 的幂（65536 编码为 1）",
                actual=raw_page_size,
                evidence="页大小非法，无法安全按页切片",
                blocking=True,
            )
        )
    else:
        page_size = encoded_page_size
    reserved = padded[20]
    usable = page_size - reserved if page_size else 0
    if page_size and (reserved >= page_size or usable < 480):
        problems.append(
            Problem(
                "HDR_RESERVED_INVALID",
                field="reserved_bytes",
                expected=f"usable_size >= 480 且 < {page_size}",
                actual=reserved,
                evidence=f"page_size={page_size}, reserved={reserved}, usable={usable}",
                blocking=True,
            )
        )
    fractions = tuple(padded[21:24])
    if fractions != (64, 32, 32):
        problems.append(
            Problem(
                "HDR_PAYLOAD_FRACTIONS_INVALID",
                field="payload_fractions",
                expected=[64, 32, 32],
                actual=list(fractions),
                evidence="B 树 payload 分数与 SQLite 格式不一致",
            )
        )
    declared_pages = be_u32(padded, 28)
    actual_pages = size // page_size if page_size else 0
    trailing = size % page_size if page_size else size
    if declared_pages == 0:
        problems.append(
            Problem(
                "HDR_PAGE_COUNT_INVALID",
                field="declared_pages",
                expected="> 0",
                actual=0,
                evidence="头部声明页数为 0",
            )
        )
    if page_size and declared_pages > actual_pages:
        problems.append(
            Problem(
                "HDR_FILE_TRUNCATED",
                field="declared_pages",
                expected=declared_pages,
                actual=actual_pages,
                evidence="头部声明页数大于文件可读完整页数",
                blocking=True,
            )
        )
    elif page_size and actual_pages != declared_pages:
        problems.append(
            Problem(
                "HDR_PAGE_COUNT_MISMATCH",
                field="declared_pages",
                expected=declared_pages,
                actual=actual_pages,
                evidence="头部声明页数与实际完整页数不同",
            )
        )
    if page_size and trailing:
        problems.append(
            Problem(
                "HDR_TRAILING_BYTES",
                field="trailing_bytes",
                expected=0,
                actual=trailing,
                evidence="文件长度不是页大小整数倍",
            )
        )
    text_encoding = be_u32(padded, 56)
    if text_encoding not in (1, 2, 3):
        problems.append(
            Problem(
                "HDR_ENCODING_INVALID",
                field="text_encoding",
                expected=[1, 2, 3],
                actual=text_encoding,
                evidence="未知文本编码",
            )
        )
    largest_root_page = be_u32(padded, 52)
    incremental = be_u32(padded, 64)
    if incremental not in (0, 1):
        problems.append(
            Problem(
                "HDR_INCREMENTAL_VACUUM_INVALID",
                field="incremental_vacuum",
                expected=[0, 1],
                actual=incremental,
                evidence="incremental vacuum 标志不是 0 或 1",
            )
        )
    if largest_root_page and declared_pages and largest_root_page > declared_pages:
        problems.append(
            Problem(
                "HDR_AUTOVACUUM_INVALID",
                field="largest_root_page",
                expected=f"<= {declared_pages}",
                actual=largest_root_page,
                evidence="auto-vacuum 最大根页号超过声明页数",
            )
        )
    # SQLite does not store the mode directly.  A non-zero largest-root field
    # marks auto-vacuum, and the incremental flag distinguishes the two modes.
    auto_vacuum = 0 if largest_root_page == 0 else (2 if incremental == 1 else 1)
    if wal_present:
        problems.append(
            Problem(
                "HDR_WAL_PRESENT",
                field="wal_present",
                expected=False,
                actual=True,
                evidence="发现同名 -wal 文件；本次结果不包含未合并 WAL 帧",
            )
        )
    return HeaderInfo(
        magic=magic.decode("latin-1"),
        magic_hex=magic.hex(),
        magic_valid=magic_valid,
        raw_page_size=raw_page_size,
        page_size=page_size,
        reserved_bytes=reserved,
        usable_size=usable,
        write_version=padded[18],
        read_version=padded[19],
        declared_pages=declared_pages,
        first_freelist_trunk=be_u32(padded, 32),
        freelist_pages=be_u32(padded, 36),
        schema_cookie=be_u32(padded, 40),
        schema_format=be_u32(padded, 44),
        default_cache_size=be_u32(padded, 48),
        largest_root_page=be_u32(padded, 52),
        text_encoding=text_encoding,
        encoding_name=_encoding_name(text_encoding),
        user_version=be_u32(padded, 60),
        incremental_vacuum=incremental,
        application_id=be_u32(padded, 68),
        sqlite_version_number=be_u32(padded, 96),
        change_counter=be_u32(padded, 24),
        file_size=size,
        actual_page_count=actual_pages,
        declared_bytes=declared_pages * page_size,
        trailing_bytes=trailing,
        wal_present=wal_present,
        wal_name=wal_name,
        auto_vacuum=auto_vacuum,
        problems=problems,
    )
