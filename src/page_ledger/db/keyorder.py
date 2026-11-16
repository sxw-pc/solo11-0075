"""Table b-tree key order: signed 64-bit rowids and parent/child partitions.

Only rowids and interior separators are read; business columns are never decoded.
Index b-trees need collation/expression awareness and are reported as unverified,
never judged by raw byte order.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from ..audit.model import Problem
from .pages import CellParse, PageParse, TABLE_TYPES
MAX_RANGES = 256
INDEX_NOTE = "index b-tree 键序受 collation、表达式与 record 编码影响，未按 SQL 顺序核验；字节序不代表 SQL 顺序。"
TABLE_NOTE = "table b-tree 按有符号 64-bit rowid 核验；仅对完整解析的子树给出结论。"
@dataclass(frozen=True)
class Subtree:
    complete: bool
    low: int | None = None
    high: int | None = None
    @property
    def empty(self) -> bool:
        return self.low is None and self.high is None
@dataclass
class PageInsight:
    page_no: int
    depth: int
    kind: str
    broken: bool
    low: int | None = None
    high: int | None = None
    edges: list[tuple[int, int | None]] = field(default_factory=list)
    rejected: set[int] = field(default_factory=set)
    def children(self) -> list[int]:
        return [child for child, _ in self.edges if child not in self.rejected]
def _links(page: PageParse, cells: list[CellParse], with_separators: bool) -> list[tuple[int, int | None]]:
    links: list[tuple[int, int | None]] = []
    for cell in cells:
        if cell.problems or not cell.child_page:
            continue
        links.append((cell.child_page, cell.separator if with_separators else None))
    if page.right_child:
        links.append((page.right_child, None))
    return links
@dataclass
class KeyOrderState:
    root_page: int
    expected: str
    problems: list[Problem] = field(default_factory=list)
    violations: int = 0
    checked_pages: int = 0
    undetermined_count: int = 0
    undetermined: list[dict] = field(default_factory=list)
    records: dict[int, PageInsight] = field(default_factory=dict)
    payload: dict | None = None
    @property
    def applicable(self) -> bool:
        return self.expected == "table"
    def _emit(self, code: str, page: int | None, field_name: str, expected: object, actual: object, evidence: str) -> None:
        self.violations += 1
        self.problems.append(Problem(code, page=page, field=field_name, expected=expected, actual=actual, evidence=evidence))
    def _undecided(self, page: int, child: int | None, separator: int | None, reason: str) -> None:
        self.undetermined_count += 1
        if len(self.undetermined) < MAX_RANGES:
            self.undetermined.append({"page": page, "child": child, "separator": separator, "reason": reason})
    def undecided(self, page: int, child: int | None, separator: int | None, reason: str) -> None:
        self._undecided(page, child, separator, reason)
    def inspect(self, page: PageParse, cells: list[CellParse], depth: int, page_broken: bool) -> list[int]:
        """Record one visited page and return its child page numbers in key order."""
        if not self.applicable:
            return [child for child, _ in _links(page, cells, False)]
        if page.page_type not in TABLE_TYPES or not page.is_btree:
            self._undecided(page.page_no, None, None, f"unexpected-page-type:{page.page_type}")
            insight = PageInsight(page.page_no, depth, "other", True, edges=_links(page, cells, False))
            self.records[page.page_no] = insight
            return insight.children()
        self.checked_pages += 1
        broken = page_broken or any(cell.problems for cell in cells)
        if page.page_type == "table_leaf":
            rowids = [cell.rowid for cell in cells if not cell.problems and cell.rowid is not None]
            previous: int | None = None
            for cell in cells:
                if cell.problems or cell.rowid is None:
                    continue
                if previous is not None and cell.rowid <= previous:
                    self._emit("BTREE_ROWID_ORDER_INVALID", page.page_no, f"cell[{cell.index}].rowid", f"> {previous}", cell.rowid, "同一 table leaf 的 rowid 未严格递增")
                previous = cell.rowid
            insight = PageInsight(page.page_no, depth, "leaf", broken, min(rowids) if rowids else None, max(rowids) if rowids else None)
            self.records[page.page_no] = insight
            return []
        separators: list[tuple[int, int]] = []
        for cell in cells:
            if not cell.problems and cell.separator is not None:
                separators.append((cell.index, cell.separator))
        previous = None
        for index, value in separators:
            if previous is not None and value <= previous:
                self._emit("BTREE_SEPARATOR_ORDER_INVALID", page.page_no, f"cell[{index}].separator", f"> {previous}", value, "table interior 分隔键未严格递增")
            previous = value
        edges = _links(page, cells, True)
        self.records[page.page_no] = PageInsight(page.page_no, depth, "interior", broken, edges=edges)
        return [child for child, _ in edges]
    def reject(self, parent: int, child: int) -> None:
        insight = self.records.get(parent)
        if insight is not None:
            insight.rejected.add(child)
    def _summaries(self) -> dict[int, Subtree]:
        summaries: dict[int, Subtree] = {}
        for page_no in sorted(self.records, key=lambda value: -self.records[value].depth):
            insight = self.records[page_no]
            if insight.broken or insight.kind == "other":
                summaries[page_no] = Subtree(False)
                continue
            if insight.kind == "leaf":
                summaries[page_no] = Subtree(True, insight.low, insight.high)
                continue
            children = [summaries.get(child, Subtree(False)) for child, _ in insight.edges]
            complete = all(item.complete for item in children) and not insight.rejected
            lows = [item.low for item in children if item.low is not None]
            highs = [item.high for item in children if item.high is not None]
            summaries[page_no] = Subtree(complete, min(lows) if lows else None, max(highs) if highs else None)
        return summaries
    def _check_interior(self, insight: PageInsight, summaries: dict[int, Subtree]) -> None:
        for position, (child, separator) in enumerate(insight.edges):
            if separator is None:
                continue
            left = summaries.get(child, Subtree(False))
            right_edge = insight.edges[position + 1] if position + 1 < len(insight.edges) else None
            right = summaries.get(right_edge[0], Subtree(False)) if right_edge else Subtree(False)
            span = f"子页 {child} rowid 范围 {left.low}..{left.high}"
            if not left.complete:
                self._undecided(insight.page_no, child, separator, "left-subtree-incomplete")
            elif left.empty:
                self._undecided(insight.page_no, child, separator, "left-subtree-empty")
            elif left.high > separator:
                self._emit("BTREE_ROWID_OUT_OF_PARTITION", child, "rowid", f"<= {separator}", left.high, f"子页 {child} 有 rowid 落入分隔键右侧；父页 {insight.page_no} 分隔键 {separator}；{span}")
            elif left.high < separator:
                self._emit("BTREE_SEPARATOR_MAX_MISMATCH", insight.page_no, f"separator[{separator}]", left.high, separator, f"分隔键应等于左子树 {child} 的最大 rowid；{span}")
            if right_edge is None:
                continue
            if not right.complete:
                self._undecided(insight.page_no, right_edge[0], separator, "right-subtree-incomplete")
            elif right.empty:
                self._undecided(insight.page_no, right_edge[0], separator, "right-subtree-empty")
            elif right.low is not None and right.low <= separator:
                self._emit("BTREE_ROWID_OUT_OF_PARTITION", right_edge[0], "rowid", f"> {separator}", right.low, f"右侧子页 {right_edge[0]} 的 rowid 未严格大于父页 {insight.page_no} 分隔键 {separator}；子页范围 {right.low}..{right.high}")
    def finish(self, traversal_complete: bool, reason: str) -> dict:
        if self.payload is not None:
            return self.payload
        if not self.applicable:
            self.problems.append(Problem("BTREE_INDEX_KEY_ORDER_UNVERIFIED", page=self.root_page, field="key_order", expected="SQL 顺序可证", actual="not-applicable", evidence=INDEX_NOTE))
            self.payload = self._payload("not-applicable")
            return self.payload
        summaries = self._summaries()
        for page_no in sorted(self.records):
            insight = self.records[page_no]
            if insight.kind == "interior" and not insight.broken:
                self._check_interior(insight, summaries)
            if insight.rejected:
                for child in sorted(insight.rejected):
                    self._undecided(page_no, child, None, "child-not-traversed")
        root_complete = summaries.get(self.root_page, Subtree(False)).complete
        if not traversal_complete or not root_complete:
            self._undecided(self.root_page, None, None, f"traversal:{reason or 'incomplete'}")
        status = "violated" if self.violations else "undetermined" if self.undetermined_count else "verified"
        if status == "undetermined":
            self.problems.append(Problem("BTREE_KEY_ORDER_INCOMPLETE", page=self.root_page, field="key_order", expected="完整可判定遍历", actual=reason or "undetermined", evidence=f"{self.undetermined_count} 个范围未判定，不推断排序通过", blocking=True))
        self.payload = self._payload(status)
        return self.payload
    def _payload(self, status: str) -> dict:
        undetermined = sorted(self.undetermined, key=lambda item: (item["page"], item["child"] if item["child"] is not None else -1, item["separator"] if item["separator"] is not None else 0, item["reason"]))
        return {"status": status, "applicable": self.applicable, "checked_pages": self.checked_pages, "violations": self.violations, "undetermined_count": self.undetermined_count, "undetermined": undetermined, "note": TABLE_NOTE if self.applicable else INDEX_NOTE}
