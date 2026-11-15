"""Walk the SQLite freelist trunk/leaf structure."""

from __future__ import annotations

from ..audit.model import FreelistResult, Problem
from ..io.reader import PageReader, be_u32


def _issue(
    result: FreelistResult,
    code: str,
    page: int | None,
    field: str,
    expected: object,
    actual: object,
    evidence: str,
) -> None:
    result.problems.append(Problem(code, page=page, field=field, expected=expected, actual=actual, evidence=evidence))
def audit_freelist(reader: PageReader, header) -> FreelistResult:
    result = FreelistResult(
        first_trunk=header.first_freelist_trunk,
        declared_count=header.freelist_pages,
    )
    first = header.first_freelist_trunk
    declared = header.freelist_pages
    if declared and not first:
        _issue(result, "FREE_FIRST_TRUNK_INVALID", None, "first_freelist_trunk", "> 0", first, "声明 freelist 页数非零但首 trunk 为 0")
    if first and not declared:
        _issue(result, "FREE_COUNT_MISMATCH", None, "freelist_pages", 0, declared, "首 trunk 非零但头部 freelist 页数为 0")
    if not first and not declared:
        return result
    actual_pages = reader.actual_page_count
    trunks: set[int] = set()
    leaves: set[int] = set()
    current = first
    while current:
        if not 2 <= current <= actual_pages:
            _issue(result, "FREE_TRUNK_OUT_OF_RANGE", current, "trunk_page", f"2..{actual_pages}", current, "trunk 页号越界")
            result.completed = False
            break
        if current in leaves:
            _issue(result, "FREE_TRUNK_LEAF_CONFLICT", current, "page", "trunk 与 leaf 不重叠", current, "链指针指向已登记叶子页")
            result.completed = False
            break
        if current in trunks:
            _issue(result, "FREE_TRUNK_CYCLE", current, "trunk_page", "不重复", current, "trunk 链回到已访问 trunk")
            _issue(result, "FREE_TRUNK_DUPLICATE", current, "trunk_page", "每个 trunk 只出现一次", current, "重复 trunk")
            result.completed = False
            break
        page = reader.page(current)
        if page is None:
            _issue(result, "FREE_TRUNK_OUT_OF_RANGE", current, "trunk_page", f"2..{actual_pages}", current, "无法读取 trunk 页")
            result.completed = False
            break
        if len(page) < 8:
            _issue(result, "FREE_TRUNK_HEADER_INVALID", current, "trunk_header", ">= 8 字节", len(page), "trunk 页头过短")
            result.completed = False
            break
        next_trunk = be_u32(page, 0)
        leaf_count = be_u32(page, 4)
        if 8 + leaf_count * 4 > header.usable_size:
            _issue(
                result,
                "FREE_TRUNK_HEADER_INVALID",
                current,
                "leaf_count",
                f"<= {(header.usable_size - 8) // 4}",
                leaf_count,
                "trunk 叶子计数无法放入页内",
            )
            result.completed = False
            break
        trunks.add(current)
        leaf_values: list[int] = []
        for index in range(leaf_count):
            leaf = be_u32(page, 8 + index * 4)
            leaf_values.append(leaf)
            if not 2 <= leaf <= actual_pages:
                _issue(result, "FREE_LEAF_OUT_OF_RANGE", current, f"leaf[{index}]", f"2..{actual_pages}", leaf, "freelist 叶子页越界")
                continue
            if leaf in trunks or leaf == current:
                _issue(result, "FREE_TRUNK_LEAF_CONFLICT", current, f"leaf[{index}]", "leaf 不等于 trunk", leaf, "叶子页与 trunk 冲突")
                continue
            if leaf in leaves:
                _issue(result, "FREE_LEAF_DUPLICATE", current, f"leaf[{index}]", "唯一", leaf, "同一叶子页被多个 trunk 登记")
                continue
            leaves.add(leaf)
        result.trunk_links.append(
            {"trunk_page": current, "next_trunk": next_trunk, "leaf_count": leaf_count, "leaves": leaf_values}
        )
        current = next_trunk
    result.trunk_pages = sorted(trunks)
    result.leaf_pages = sorted(leaves)
    result.free_pages = sorted(trunks | leaves)
    if len(result.free_pages) != declared:
        _issue(
            result,
            "FREE_COUNT_MISMATCH",
            None,
            "freelist_pages",
            declared,
            len(result.free_pages),
            "头部声明 freelist 页数与实际唯一页数不同",
        )
    return result
