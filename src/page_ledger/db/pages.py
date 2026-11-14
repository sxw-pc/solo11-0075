"""B-tree page headers and cell parsing for the audit subset."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..audit.model import Problem
from ..io.reader import be_u16, be_u32

BTREE_TYPES = {
    0x02: "index_interior",
    0x05: "table_interior",
    0x0A: "index_leaf",
    0x0D: "table_leaf",
}
TYPE_RAW = {value: key for key, value in BTREE_TYPES.items()}
INTERIOR_TYPES = {"index_interior", "table_interior"}
INDEX_TYPES = {"index_interior", "index_leaf"}
TABLE_TYPES = {"table_interior", "table_leaf"}


@dataclass
class PageParse:
    page_no: int
    raw_type: int
    page_type: str
    is_btree: bool
    header_offset: int
    page_header_size: int
    first_freeblock: int
    cell_count: int
    content_start: int
    fragmented_bytes: int
    right_child: int
    cell_pointers: list[int]
    header_end: int
    pointer_end: int
    usable_end: int
    problems: list[Problem] = field(default_factory=list)
    @property
    def free_gap(self) -> int:
        return max(0, self.content_start - self.pointer_end)
    @property
    def content_bytes(self) -> int:
        return max(0, self.usable_end - self.content_start)
@dataclass
class CellParse:
    index: int
    pointer: int
    cell_size: int = 0
    payload_size: int = 0
    local_size: int = 0
    payload_offset: int = 0
    rowid: int | None = None
    child_page: int | None = None
    overflow_page: int | None = None
    overflow_bytes: int = 0
    problems: list[Problem] = field(default_factory=list)
def decode_varint(data: bytes, offset: int, end: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        position = offset + index
        if position >= end:
            raise ValueError("varint 越过可用区")
        byte = data[position]
        if index == 8:
            value = (value << 8) | byte
            return value, 9
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index + 1
    raise ValueError("varint 过长")
def local_payload_size(payload: int, usable: int, index_btree: bool) -> int:
    if payload < 0 or usable < 480:
        raise ValueError("payload 或 usable size 非法")
    max_local = usable - 35 if not index_btree else ((usable - 12) * 64) // 255 - 23
    min_local = ((usable - 12) * 32) // 255 - 23
    if payload <= max_local:
        return payload
    k = min_local + ((payload - min_local) % (usable - 4))
    return k if k <= max_local else min_local
def _problem(
    code: str,
    page: int,
    field: str,
    expected: object,
    actual: object,
    evidence: str,
) -> Problem:
    return Problem(code, page=page, field=field, expected=expected, actual=actual, evidence=evidence)
def parse_page(
    data: bytes,
    page_no: int,
    page_size: int,
    usable_size: int,
    actual_page_count: int,
) -> PageParse:
    problems: list[Problem] = []
    offset = (page_no - 1) * page_size
    header_offset = 100 if page_no == 1 else 0
    usable_end = offset + usable_size
    if page_no < 1 or page_no > actual_page_count or offset + page_size > len(data):
        problems.append(
            _problem("PAGE_OUT_OF_RANGE", page_no, "page", f"1..{actual_page_count}", page_no, "页切片越过文件")
        )
        return PageParse(page_no, 0, "out_of_range", False, header_offset, 0, 0, 0, usable_end, 0, 0, [], usable_end, usable_end, usable_end, problems)
    raw_type = data[offset + header_offset]
    page_type = BTREE_TYPES.get(raw_type, "unknown")
    if raw_type not in BTREE_TYPES:
        problems.append(
            _problem("PAGE_TYPE_UNKNOWN", page_no, "raw_type", list(BTREE_TYPES), raw_type, "未知 B 树页类型")
        )
        return PageParse(page_no, raw_type, page_type, False, header_offset, 0, 0, 0, usable_end, 0, 0, [], usable_end, usable_end, usable_end, problems)
    interior = page_type in INTERIOR_TYPES
    page_header_size = 12 if interior else 8
    header_start = offset + header_offset
    header_end = header_start + page_header_size
    if header_end > usable_end:
        problems.append(
            _problem("PAGE_HEADER_INVALID", page_no, "header_size", f"<= {usable_size}", page_header_size, "页头越过可用区")
        )
        return PageParse(page_no, raw_type, page_type, True, header_offset, page_header_size, 0, 0, usable_end, 0, 0, [], header_end, header_end, usable_end, problems)
    first_freeblock = be_u16(data, header_start + 1)
    cell_count = be_u16(data, header_start + 3)
    raw_content_start = be_u16(data, header_start + 5)
    content_start = offset + (65536 if raw_content_start == 0 else raw_content_start)
    fragmented = data[header_start + 7]
    right_child = be_u32(data, header_start + 8) if interior else 0
    pointer_end = header_end + cell_count * 2
    pointers: list[int] = []
    if pointer_end > usable_end:
        problems.append(
            _problem("PAGE_CELL_COUNT_ANOMALY", page_no, "cell_count", f"<= {(usable_size - page_header_size - header_offset) // 2}", cell_count, "cell pointer array 越过可用区")
        )
    else:
        for index in range(cell_count):
            pointers.append(offset + be_u16(data, header_end + index * 2))
    if content_start < header_end or content_start < pointer_end:
        problems.append(
            _problem("PAGE_CONTENT_OUT_OF_RANGE", page_no, "content_start", f">= {pointer_end}", content_start, "内容区起点与页头或指针数组冲突")
        )
        problems.append(
            _problem("PAGE_FREE_SPACE_NEGATIVE", page_no, "free_space", ">= 0", pointer_end - content_start, "内容区起点早于指针数组末端")
        )
    if content_start > usable_end:
        problems.append(
            _problem("PAGE_CONTENT_OUT_OF_RANGE", page_no, "content_start", f"<= {usable_end}", content_start, "内容区起点越过可用区")
        )
    for index, absolute in enumerate(pointers):
        if absolute < content_start or absolute >= usable_end:
            problems.append(
                _problem("PAGE_CELL_PTR_OUT_OF_RANGE", page_no, f"cell[{index}].pointer", f"{content_start}..{usable_end - 1}", absolute, "cell 指针不在内容区内")
            )
    if right_child and not (1 <= right_child <= actual_page_count):
        problems.append(
            _problem("PAGE_HEADER_INVALID", page_no, "right_child", f"1..{actual_page_count}", right_child, "内部页右子页越界")
        )
    return PageParse(page_no, raw_type, page_type, True, header_offset, page_header_size, first_freeblock, cell_count, content_start, fragmented, right_child, pointers, header_end, pointer_end, usable_end, problems)
def _payload_cell(
    data: bytes,
    page: PageParse,
    cell: CellParse,
    usable: int,
    index_btree: bool,
    start: int,
) -> int:
    payload, used = decode_varint(data, start, page.usable_end)
    payload_offset = start + used
    local = local_payload_size(payload, usable, index_btree)
    overflow = 4 if payload > local else 0
    cell_end = payload_offset + local + overflow
    if cell_end > page.usable_end:
        raise ValueError("cell payload 越过可用区")
    cell.payload_size = payload
    cell.local_size = local
    cell.payload_offset = payload_offset
    cell.overflow_bytes = payload - local
    if overflow:
        cell.overflow_page = be_u32(data, payload_offset + local)
    cell.cell_size = cell_end - cell.pointer
    return cell_end
def parse_cell(data: bytes, page: PageParse, index: int, usable: int) -> CellParse:
    pointer = page.cell_pointers[index]
    cell = CellParse(index=index, pointer=pointer)
    try:
        if pointer >= page.usable_end or pointer < page.content_start:
            raise ValueError("cell 指针不在内容区")
        kind = page.page_type
        if kind == "table_interior":
            if pointer + 5 > page.usable_end:
                raise ValueError("table interior cell 过短")
            cell.child_page = be_u32(data, pointer)
            _, used = decode_varint(data, pointer + 4, page.usable_end)
            cell.cell_size = 4 + used
        elif kind == "table_leaf":
            payload, used = decode_varint(data, pointer, page.usable_end)
            rowid, row_used = decode_varint(data, pointer + used, page.usable_end)
            payload_offset = pointer + used + row_used
            local = local_payload_size(payload, usable, False)
            overflow = 4 if payload > local else 0
            cell_end = payload_offset + local + overflow
            if cell_end > page.usable_end:
                raise ValueError("cell payload 越过可用区")
            cell.payload_size = payload
            cell.local_size = local
            cell.payload_offset = payload_offset
            cell.overflow_bytes = payload - local
            cell.rowid = rowid
            if overflow:
                cell.overflow_page = be_u32(data, payload_offset + local)
            cell.cell_size = cell_end - pointer
        elif kind == "index_leaf":
            _payload_cell(data, page, cell, usable, True, pointer)
        elif kind == "index_interior":
            if pointer + 5 > page.usable_end:
                raise ValueError("index interior cell 过短")
            cell.child_page = be_u32(data, pointer)
            _payload_cell(data, page, cell, usable, True, pointer + 4)
        else:
            raise ValueError("非 B 树页")
    except (ValueError, IndexError) as exc:
        cell.problems.append(
            _problem("PAGE_CELL_PARSE_FAILED", page.page_no, f"cell[{index}]", "可解析 cell", str(exc), f"pointer={pointer}")
        )
    return cell
def check_cell_overlaps(page: PageParse, cells: list[CellParse]) -> None:
    valid = sorted(
        (cell for cell in cells if not cell.problems and cell.cell_size > 0),
        key=lambda item: item.pointer,
    )
    previous: CellParse | None = None
    for cell in valid:
        if previous is not None and cell.pointer < previous.pointer + previous.cell_size:
            page.problems.append(
                _problem(
                    "PAGE_CELL_PTR_OVERLAP",
                    page.page_no,
                    f"cell[{cell.index}].pointer",
                    f">= {previous.pointer + previous.cell_size}",
                    cell.pointer,
                    f"与 cell[{previous.index}] 的区间重叠",
                )
            )
        if previous is None or cell.pointer + cell.cell_size > previous.pointer + previous.cell_size:
            previous = cell
