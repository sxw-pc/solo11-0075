"""Page-level space ledger, summaries and threshold findings."""

from __future__ import annotations

from collections import defaultdict

from ..config import AuditConfig
from ..db.btree import TreePage
from ..db.freelist import FreelistResult
from ..db.pages import BTREE_TYPES
from ..io.reader import PageReader, be_u32
from ..util.ordering import top_by
from .model import PageRecord, Problem


def _ratio(numerator: float, denominator: int) -> float:
    return round(float(numerator) / denominator, 6) if denominator else 0.0
def tree_record(tree: TreePage, page_size: int, usable_size: int) -> PageRecord:
    parse = tree.parse
    header_bytes = parse.header_offset + parse.page_header_size
    pointers = len(parse.cell_pointers) * 2
    cells = tree.cell_bytes
    free = max(0, usable_size - header_bytes - pointers - cells)
    used = header_bytes + pointers + cells
    utilization = _ratio(min(used, usable_size), usable_size)
    fragmentation = _ratio(min(parse.fragmented_bytes, usable_size), usable_size)
    return PageRecord(
        page_no=tree.page_no,
        page_type=tree.page_type,
        role=tree.role,
        parent_page=tree.parent_page,
        depth=tree.depth,
        cell_count=len(tree.cells),
        cell_bytes=cells,
        header_bytes=header_bytes,
        pointer_bytes=pointers,
        content_bytes=cells,
        free_bytes=free,
        fragmented_bytes=parse.fragmented_bytes,
        reserved_bytes=page_size - usable_size,
        utilization=utilization,
        fragmentation_ratio=fragmentation,
        page_size=page_size,
        usable_size=usable_size,
    )
def _special(
    page_no: int,
    page_type: str,
    role: str,
    page_size: int,
    usable_size: int,
    header_bytes: int,
    pointer_bytes: int,
    content_bytes: int,
) -> PageRecord:
    used = header_bytes + pointer_bytes + content_bytes
    free = max(0, usable_size - used)
    return PageRecord(
        page_no=page_no,
        page_type=page_type,
        role=role,
        parent_page=None,
        depth=0,
        cell_count=0,
        cell_bytes=content_bytes,
        header_bytes=header_bytes,
        pointer_bytes=pointer_bytes,
        content_bytes=content_bytes,
        free_bytes=free,
        fragmented_bytes=0,
        reserved_bytes=page_size - usable_size,
        utilization=_ratio(min(used, usable_size), usable_size),
        fragmentation_ratio=0.0,
        page_size=page_size,
        usable_size=usable_size,
    )
def overflow_record(page_no: int, page_size: int, usable_size: int) -> PageRecord:
    return _special(page_no, "overflow", "overflow", page_size, usable_size, 4, 0, max(0, usable_size - 4))
def freelist_records(
    reader: PageReader,
    header,
    freelist: FreelistResult,
) -> list[PageRecord]:
    records: list[PageRecord] = []
    for page_no in freelist.trunk_pages:
        page = reader.page(page_no)
        count = be_u32(page, 4) if page is not None and len(page) >= 8 else 0
        pointer_bytes = min(max(0, header.usable_size - 8), count * 4)
        records.append(
            _special(
                page_no,
                "freelist_trunk",
                "freelist_trunk",
                header.page_size,
                header.usable_size,
                8,
                pointer_bytes,
                0,
            )
        )
    for page_no in freelist.leaf_pages:
        records.append(_special(page_no, "freelist_leaf", "freelist_leaf", header.page_size, header.usable_size, 0, 0, 0))
    return records
def orphan_record(reader: PageReader, page_no: int, page_size: int, usable_size: int) -> PageRecord:
    page = reader.page(page_no)
    raw_type = page[0] if page else -1
    page_type = BTREE_TYPES.get(raw_type, "unknown")
    return _special(page_no, page_type, "orphan", page_size, usable_size, 0, 0, 0)
def summarize_pages(records: list[PageRecord]) -> dict[str, object]:
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"page_count": 0, "cell_count": 0, "used_bytes": 0, "free_bytes": 0, "fragmented_bytes": 0, "reserved_bytes": 0}
    )
    roles: dict[str, int] = defaultdict(int)
    for record in records:
        bucket = buckets[record.page_type]
        bucket["page_count"] += 1
        bucket["cell_count"] += record.cell_count
        bucket["used_bytes"] += record.header_bytes + record.pointer_bytes + record.cell_bytes
        bucket["free_bytes"] += record.free_bytes
        bucket["fragmented_bytes"] += record.fragmented_bytes
        bucket["reserved_bytes"] += record.reserved_bytes
        roles[record.role] += 1
    by_type = [{"page_type": key, **buckets[key]} for key in sorted(buckets)]
    totals = {
        "page_count": sum(item["page_count"] for item in buckets.values()),
        "cell_count": sum(item["cell_count"] for item in buckets.values()),
        "used_bytes": sum(item["used_bytes"] for item in buckets.values()),
        "free_bytes": sum(item["free_bytes"] for item in buckets.values()),
        "fragmented_bytes": sum(item["fragmented_bytes"] for item in buckets.values()),
        "reserved_bytes": sum(item["reserved_bytes"] for item in buckets.values()),
    }
    return {"by_page_type": by_type, "by_role": {key: roles[key] for key in sorted(roles)}, "totals": totals}
def space_findings(records: list[PageRecord], config: AuditConfig) -> tuple[list[Problem], dict[str, object]]:
    problems: list[Problem] = []
    for record in records:
        used = record.header_bytes + record.pointer_bytes + record.cell_bytes
        if used > record.usable_size or record.fragmented_bytes > record.usable_size:
            problems.append(Problem("SPACE_LEDGER_INCONSISTENT", page=record.page_no, field="used_bytes", expected=f"<= {record.usable_size}", actual=used, evidence=f"fragmented={record.fragmented_bytes}, type={record.page_type}"))
        if record.role not in {"schema", "table", "index", "btree"} or record.page_no == 1:
            continue
        if record.fragmented_bytes and record.fragmentation_ratio > config.fragmentation_warn_ratio:
            problems.append(Problem("SPACE_FRAGMENTATION_HIGH", page=record.page_no, field="fragmented_bytes", expected=f"ratio <= {config.fragmentation_warn_ratio}", actual=f"{record.fragmented_bytes}/{record.usable_size} ({record.fragmentation_ratio})", evidence=f"页 {record.page_no} 碎片率超过阈值"))
        if record.cell_count >= config.low_util_min_cells and record.utilization < config.utilization_low_ratio:
            problems.append(Problem("SPACE_UTILIZATION_LOW", page=record.page_no, field="utilization", expected=f">= {config.utilization_low_ratio}", actual=record.utilization, evidence=f"页 {record.page_no} 已用空间占比过低"))
    btree = [record for record in records if record.role in {"schema", "table", "index", "btree"} and record.page_no != 1]
    tops = {"fragmentation": [item.to_dict() for item in top_by(btree, "fragmentation_ratio", config.top_pages, True)], "low_utilization": [item.to_dict() for item in top_by(btree, "utilization", config.top_pages, False)]}
    return problems, tops
