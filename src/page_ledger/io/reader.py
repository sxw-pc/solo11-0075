"""Page slicing over an immutable in-memory snapshot."""

from __future__ import annotations

from dataclasses import dataclass

from .snapshot import FileSnapshot


def be_u16(data: bytes | memoryview, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")
def be_u32(data: bytes | memoryview, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")
def signed_u32(data: bytes | memoryview, offset: int) -> int:
    value = be_u32(data, offset)
    return value - 0x100000000 if value & 0x80000000 else value
@dataclass
class PageReader:
    snapshot: FileSnapshot
    page_size: int
    @property
    def actual_page_count(self) -> int:
        if self.page_size <= 0:
            return 0
        return self.snapshot.size // self.page_size
    def page(self, page_no: int) -> bytes | None:
        if not self.valid_page(page_no):
            return None
        start = (page_no - 1) * self.page_size
        return self.snapshot.data[start : start + self.page_size]
    def page_prefix(self, page_no: int, length: int) -> bytes | None:
        page = self.page(page_no)
        if page is None:
            return None
        return page[:length]
    def valid_page(self, page_no: int) -> bool:
        return 1 <= page_no <= self.actual_page_count
    def valid_usable_page(self, page_no: int, usable_size: int) -> bool:
        if not self.valid_page(page_no):
            return False
        page = self.page(page_no)
        return page is not None and len(page) >= usable_size
    def read_at(self, offset: int, length: int) -> bytes:
        return self.snapshot.data[offset : offset + length]
