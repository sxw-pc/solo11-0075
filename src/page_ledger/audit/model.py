"""Structured audit objects shared by parsers, rules and reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LEVEL_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


@dataclass(frozen=True)
class Problem:
    """A low-level observation; the rule engine assigns its severity."""
    code: str
    page: int | None = None
    field: str = ""
    expected: Any = None
    actual: Any = None
    evidence: str = ""
    blocking: bool = False
    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "page": self.page,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "evidence": self.evidence,
        }
@dataclass(frozen=True)
class Finding:
    code: str
    level: str
    page: int | None
    field: str
    expected: Any
    actual: Any
    evidence: str
    blocking: bool
    title: str
    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "level": self.level,
            "page": self.page,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "evidence": self.evidence,
            "blocking": self.blocking,
            "title": self.title,
        }
@dataclass
class PageRecord:
    page_no: int
    page_type: str
    role: str
    parent_page: int | None
    depth: int
    cell_count: int
    cell_bytes: int
    header_bytes: int
    pointer_bytes: int
    content_bytes: int
    free_bytes: int
    fragmented_bytes: int
    reserved_bytes: int
    utilization: float
    fragmentation_ratio: float
    page_size: int
    usable_size: int
    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "page_type": self.page_type,
            "role": self.role,
            "parent_page": self.parent_page,
            "depth": self.depth,
            "cell_count": self.cell_count,
            "cell_bytes": self.cell_bytes,
            "header_bytes": self.header_bytes,
            "pointer_bytes": self.pointer_bytes,
            "content_bytes": self.content_bytes,
            "free_bytes": self.free_bytes,
            "fragmented_bytes": self.fragmented_bytes,
            "reserved_bytes": self.reserved_bytes,
            "utilization": self.utilization,
            "fragmentation_ratio": self.fragmentation_ratio,
            "page_size": self.page_size,
            "usable_size": self.usable_size,
        }
@dataclass
class FreelistResult:
    first_trunk: int
    declared_count: int
    trunk_pages: list[int] = field(default_factory=list)
    leaf_pages: list[int] = field(default_factory=list)
    free_pages: list[int] = field(default_factory=list)
    completed: bool = True
    trunk_links: list[dict[str, Any]] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]:
        return {
            "first_trunk": self.first_trunk,
            "declared_count": self.declared_count,
            "trunk_pages": sorted(self.trunk_pages),
            "leaf_pages": sorted(self.leaf_pages),
            "free_pages": sorted(self.free_pages),
            "actual_count": len(self.free_pages),
            "completed": self.completed,
            "trunk_links": self.trunk_links,
        }
@dataclass
class OverflowChain:
    first_page: int
    origin_page: int
    cell_index: int
    payload_bytes: int
    local_bytes: int
    expected_pages: int
    pages: list[int] = field(default_factory=list)
    complete: bool = False
    audited_bytes: int = 0
    status: str = "incomplete"
@dataclass
class SchemaObject:
    object_type: str
    name: str
    table_name: str
    root_page: int
    traversed: bool = False
    page_type: str = ""
    page_count: int = 0
    cell_count: int = 0
    depth: int = 0
    reason: str = ""
    def to_dict(self) -> dict[str, Any]:
        return {
            "object_type": self.object_type,
            "name": self.name,
            "table_name": self.table_name,
            "root_page": self.root_page,
            "traversed": self.traversed,
            "page_type": self.page_type,
            "page_count": self.page_count,
            "cell_count": self.cell_count,
            "depth": self.depth,
            "reason": self.reason,
        }
def worst_level(findings: list[Finding]) -> str:
    if not findings:
        return "info"
    return max((item.level for item in findings), key=lambda level: LEVEL_ORDER[level])
def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: (
            -(LEVEL_ORDER.get(item.level, 0)),
            item.code,
            item.page if item.page is not None else -1,
            item.field,
            str(item.expected),
            str(item.actual),
            item.evidence,
        ),
    )
