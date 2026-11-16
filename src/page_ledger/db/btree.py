"""B-tree traversal, child graph checks and sqlite_schema collection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..audit.model import Problem
from ..config import AuditConfig
from ..io.reader import PageReader
from .keyorder import KeyOrderState
from .overflow import OverflowAuditor
from .pages import (
    CellParse,
    INDEX_TYPES,
    TABLE_TYPES,
    PageParse,
    check_cell_overlaps,
    parse_cell,
    parse_page,
)


@dataclass
class TreePage:
    parse: PageParse
    parent_page: int | None
    depth: int
    role: str
    cells: list[CellParse] = field(default_factory=list)
    @property
    def page_no(self) -> int:
        return self.parse.page_no
    @property
    def page_type(self) -> str:
        return self.parse.page_type
    @property
    def cell_bytes(self) -> int:
        return sum(cell.cell_size for cell in self.cells if not cell.problems)
@dataclass
class RootWalk:
    root_page: int
    role: str
    expected_kind: str
    pages: list[int] = field(default_factory=list)
    cell_count: int = 0
    max_depth: int = 0
    complete: bool = False
    reason: str = ""
    key_order: KeyOrderState | None = None
    def to_dict(self) -> dict[str, Any]:
        return {
            "root_page": self.root_page,
            "role": self.role,
            "expected_kind": self.expected_kind,
            "pages": sorted(self.pages),
            "page_count": len(self.pages),
            "cell_count": self.cell_count,
            "max_depth": self.max_depth,
            "complete": self.complete,
            "reason": self.reason,
            "key_order": self.key_order.payload if self.key_order else None,
        }
class BtreeWalker:
    def __init__(self, reader: PageReader, header, config: AuditConfig, overflow: OverflowAuditor):
        self.reader = reader
        self.header = header
        self.config = config
        self.overflow = overflow
        self.visited: set[int] = set()
        self.queued: set[int] = set()
        self.parents: dict[int, int | None] = {}
        self.pages: dict[int, TreePage] = {}
        self.roots: list[RootWalk] = []
        self.problems: list[Problem] = []
        self.schema_records: list[tuple[int, int, bytes]] = []
        self._limit_reported = False
    def _issue(self, code: str, page: int | None, field: str, expected: object, actual: object, evidence: str) -> None:
        self.problems.append(Problem(code, page=page, field=field, expected=expected, actual=actual, evidence=evidence))
    def _is_ancestor(self, candidate: int, page: int) -> bool:
        current: int | None = page
        seen: set[int] = set()
        while current is not None and current not in seen:
            if current == candidate:
                return True
            seen.add(current)
            current = self.parents.get(current)
        return False
    def _queue_child(self, current: int, child: int, depth: int, expected: str, queue: deque) -> bool:
        if child < 1 or child > self.reader.actual_page_count:
            self._issue(
                "BTREE_CHILD_OUT_OF_RANGE",
                current,
                "child_page",
                f"1..{self.reader.actual_page_count}",
                child,
                f"页 {current} 引用越界子页",
            )
            return False
        if child == current or self._is_ancestor(child, current):
            self._issue("BTREE_CYCLE", current, "child_page", "无环", child, f"页 {current} 引用祖先页")
            return False
        if child in self.visited or child in self.queued:
            previous = self.parents.get(child)
            self._issue(
                "BTREE_DUPLICATE_REFERENCE",
                current,
                "child_page",
                "每页只有一个父页",
                child,
                f"页 {child} 已被页 {previous} 引用",
            )
            return False
        self.queued.add(child)
        self.parents[child] = current
        queue.append((child, current, depth, expected))
        return True
    def _schema_payload(self, page_no: int, cell: CellParse) -> bytes:
        page = self.reader.page(page_no)
        if page is None:
            return b""
        local = self.reader.snapshot.data[cell.payload_offset : cell.payload_offset + cell.local_size]
        if cell.overflow_bytes <= 0 or not cell.overflow_page:
            return bytes(local)
        chain = self.overflow.walk(page_no, cell.index, cell.overflow_page, cell.payload_size, cell.local_size)
        payload = bytearray(local)
        remaining = cell.overflow_bytes
        for overflow_page in chain.pages:
            block = self.reader.page(overflow_page)
            if block is None:
                break
            take = min(remaining, max(0, self.header.usable_size - 4))
            payload.extend(block[4 : 4 + take])
            remaining -= take
            if remaining <= 0:
                break
        return bytes(payload)
    def _handle_overflow(self, page_no: int, cell: CellParse) -> None:
        if cell.problems:
            return
        if cell.overflow_bytes > 0:
            self.overflow.walk(page_no, cell.index, cell.overflow_page or 0, cell.payload_size, cell.local_size)
    def walk_root(
        self,
        root_page: int,
        role: str,
        expected_kind: str,
        collect_schema: bool = False,
    ) -> RootWalk:
        result = RootWalk(root_page=root_page, role=role, expected_kind=expected_kind)
        key_order = KeyOrderState(root_page=root_page, expected=expected_kind)
        result.key_order = key_order
        if root_page < 1 or root_page > self.reader.actual_page_count:
            result.reason = "root-out-of-range"
            self._finish_key_order(key_order, result)
            return result
        if root_page in self.visited:
            result.pages = sorted(page for page, value in self.pages.items() if self.parents.get(page) is None)
            result.reason = "already-visited"
            key_order.undecided(root_page, None, None, "already-visited")
            self._finish_key_order(key_order, result)
            return result
        queue: deque[tuple[int, int | None, int, str]] = deque([(root_page, None, 0, expected_kind)])
        self.queued.add(root_page)
        self.parents[root_page] = None
        while queue:
            page_no, parent, depth, expected = queue.popleft()
            self.queued.discard(page_no)
            if page_no in self.visited:
                key_order.undecided(page_no, None, None, "already-visited")
                continue
            if len(self.visited) >= self.config.max_pages:
                if not self._limit_reported:
                    self._issue(
                        "BTREE_TRAVERSAL_LIMIT",
                        page_no,
                        "max_pages",
                        f"<= {self.config.max_pages}",
                        len(self.visited),
                        "达到遍历安全上限，结果只覆盖前缀",
                    )
                    self._limit_reported = True
                result.reason = "page-limit"
                key_order.undecided(page_no, None, None, "page-limit")
                for stale, _, _, _ in queue:
                    key_order.undecided(stale, None, None, "page-limit")
                break
            self.visited.add(page_no)
            self.parents[page_no] = parent
            page = parse_page(
                self.reader.snapshot.data,
                page_no,
                self.header.page_size,
                self.header.usable_size,
                self.reader.actual_page_count,
            )
            tree_page = TreePage(page, parent, depth, role)
            self.pages[page_no] = tree_page
            result.pages.append(page_no)
            result.max_depth = max(result.max_depth, depth)
            if depth > self.config.max_btree_depth:
                self._issue(
                    "BTREE_DEPTH_ANOMALY",
                    page_no,
                    "depth",
                    f"<= {self.config.max_btree_depth}",
                    depth,
                    "B 树深度超过配置上限",
                )
            existing_problems = len(page.problems)
            self.problems.extend(page.problems)
            if not page.is_btree:
                result.reason = "non-btree-root"
                key_order.undecided(page_no, None, None, f"page-not-btree:{page.page_type}")
                continue
            allowed = TABLE_TYPES if expected == "table" else INDEX_TYPES
            if page.page_type not in allowed:
                self._issue(
                    "PAGE_TYPE_UNEXPECTED",
                    page_no,
                    "page_type",
                    sorted(allowed),
                    page.page_type,
                    f"页 {page_no} 与 {expected} 树预期不符",
                )
            for index in range(len(page.cell_pointers)):
                cell = parse_cell(self.reader.snapshot.data, page, index, self.header.usable_size)
                tree_page.cells.append(cell)
                self.problems.extend(cell.problems)
                if cell.problems:
                    continue
                result.cell_count += 1
                if collect_schema and page.page_type == "table_leaf":
                    payload = self._schema_payload(page_no, cell)
                    self.schema_records.append((page_no, index, payload))
                else:
                    self._handle_overflow(page_no, cell)
            check_cell_overlaps(page, tree_page.cells)
            self.problems.extend(page.problems[existing_problems:])
            page_broken = bool(page.problems)
            children = key_order.inspect(page, tree_page.cells, depth, page_broken)
            for child in children:
                if not self._queue_child(page_no, child, depth + 1, expected, queue):
                    key_order.reject(page_no, child)
        result.complete = not queue and result.reason not in {"page-limit", "non-btree-root"}
        if not result.complete and not result.reason:
            result.reason = "interrupted"
        self._finish_key_order(key_order, result)
        self.roots.append(result)
        return result
    def _finish_key_order(self, key_order: KeyOrderState, result: RootWalk) -> None:
        key_order.finish(result.complete, result.reason)
        self.problems.extend(key_order.problems)
