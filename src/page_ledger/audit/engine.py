"""Audit orchestration for one read-only SQLite snapshot."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..config import AuditConfig
from ..db.btree import BtreeWalker
from ..db.freelist import audit_freelist
from ..db.header import parse_header
from ..db.overflow import OverflowAuditor
from ..db.pages import TABLE_TYPES
from ..db.schema import extract_schema, probe_sidecar
from ..io.reader import PageReader
from ..io.snapshot import load_snapshot
from .ledger import (
    freelist_records,
    orphan_record,
    overflow_record,
    space_findings,
    summarize_pages,
    tree_record,
)
from .model import Problem
from .report_builder import build_report, empty_freelist, empty_overflow, overflow_payload
from .schema_check import audit_schema

LIMITATIONS = [
    "仅审计单个主数据库文件快照；不读取、不合并、不修改 -wal 文件。",
    "不处理加密 SQLite、自定义 VFS 或并发写入中的数据库；请在稳定副本上运行。",
    "不解析业务记录字段值，仅使用 schema 元数据、页头、cell 布局和溢出链结构。",
    "损坏严重时只输出部分结果；未完成项以 AUDIT_PARTIAL_RESULT、*_INCOMPLETE 或 INVALID 错误码标注。",
    "本工具不是修复、恢复或 vacuum 工具，不执行 VACUUM、REINDEX 或任何写操作。",
]
OUT_OF_RANGE_CODES = {
    "PAGE_OUT_OF_RANGE",
    "BTREE_CHILD_OUT_OF_RANGE",
    "FREE_TRUNK_OUT_OF_RANGE",
    "FREE_LEAF_OUT_OF_RANGE",
    "OVFL_PAGE_OUT_OF_RANGE",
    "OVFL_FIRST_PAGE_INVALID",
}


class AuditEngine:
    def __init__(self, config: AuditConfig | None = None):
        self.config = config or AuditConfig()
    def run(self, path: str) -> dict[str, Any]:
        snapshot = load_snapshot(path)
        header = parse_header(
            snapshot.data,
            file_size=snapshot.size,
            wal_present=snapshot.wal_present,
            wal_name=snapshot.wal_name,
        )
        problems: list[Problem] = list(header.problems)
        limitations = list(LIMITATIONS)
        if header.wal_present:
            limitations.insert(1, "发现同名 -wal 文件：报告只反映主文件已落盘页，不包含 WAL 未合并帧。")
        if not header.can_read_pages:
            problems.append(
                Problem(
                    "AUDIT_PARTIAL_RESULT",
                    field="page_size",
                    expected="可安全切片",
                    actual=header.page_size,
                    evidence="文件头或文件长度不允许页级遍历",
                    blocking=True,
                )
            )
            schema, sidecar_problems = self._schema_without_walk(snapshot.path)
            problems.extend(sidecar_problems)
            return build_report(
                self.config,
                snapshot,
                header,
                problems,
                [],
                {"root_walks": [], "visited_pages": [], "tree_page_count": 0, "schema_record_count": 0, "max_depth": 0, "page_type_counts": {}},
                empty_freelist(header),
                empty_overflow(),
                schema,
                limitations,
                partial=True,
            )
        reader = PageReader(snapshot, header.page_size)
        overflow = OverflowAuditor(reader, header, self.config)
        walker = BtreeWalker(reader, header, self.config, overflow)
        root_walk = walker.walk_root(1, "schema", "table", collect_schema=True)
        schema_objects, schema_problems = extract_schema(walker.schema_records, header.encoding_name)
        problems.extend(schema_problems)
        schema_problems, schema = audit_schema(header, walker, schema_objects, snapshot.path)
        problems.extend(schema_problems)
        freelist = audit_freelist(reader, header)
        problems.extend(freelist.problems)
        problems.extend(walker.problems)
        problems.extend(overflow.result.problems)
        problems.extend(self._conflicts(walker, overflow, freelist))
        records, orphans = self._records(reader, header, walker, overflow, freelist)
        record_map = {item.page_no: item for item in records}
        for page_no in orphans:
            problems.append(Problem("BTREE_UNREACHABLE_PAGE", page=page_no, field="page", expected="可达或位于 freelist", actual=record_map[page_no].page_type, evidence="页既不在 B 树/溢出可达图，也不在已发现 freelist 中"))
        space_problems, top_pages = space_findings(records, self.config)
        problems.extend(space_problems)
        page_summary = summarize_pages(records)
        reachable = walker.visited | overflow.result.reachable_pages
        out_of_range = sum(1 for problem in problems if problem.code in OUT_OF_RANGE_CODES)
        bad_pages = {problem.actual for problem in problems if problem.code in OUT_OF_RANGE_CODES and isinstance(problem.actual, int)}
        root_page = walker.pages.get(1)
        key_order_incomplete = any(
            item.key_order is not None and item.key_order.applicable and item.key_order.undetermined_count > 0
            for item in walker.roots
        )
        partial = bool(
            any(problem.blocking for problem in header.problems)
            or root_page is None
            or root_page.page_type not in TABLE_TYPES
            or not root_walk.complete
            or root_page.parse.problems
            or any(problem.code == "BTREE_TRAVERSAL_LIMIT" for problem in problems)
            or any(not item.complete for item in walker.roots)
            or not freelist.completed
            or any(not chain.complete for chain in overflow.result.chains)
            or key_order_incomplete
        )
        if partial:
            problems.append(Problem("AUDIT_PARTIAL_RESULT", field="coverage", expected="完整页级遍历", actual="partial", evidence="头部、根页、freelist、溢出链或遍历限制使审计只覆盖可安全解释的部分", blocking=True))
        traversal = {
            "root_walks": [item.to_dict() for item in walker.roots],
            "visited_pages": sorted(walker.visited),
            "tree_page_count": len(walker.pages),
            "schema_record_count": len(walker.schema_records),
            "max_depth": max((item.max_depth for item in walker.roots), default=0),
            "page_type_counts": self._count_types(walker),
            "key_order": self._key_order_summary(walker),
        }
        freelist_payload = freelist.to_dict()
        freelist_payload.update({"trunk_count": len(freelist.trunk_pages), "leaf_count": len(freelist.leaf_pages), "completed": freelist.completed})
        file_summary = {
            "file_size": snapshot.size,
            "page_size": header.page_size,
            "usable_size": header.usable_size,
            "declared_pages": header.declared_pages,
            "actual_pages": reader.actual_page_count,
            "reachable_pages": len(reachable),
            "tree_pages": len(walker.pages),
            "overflow_pages": len(overflow.result.reachable_pages - walker.visited),
            "freelist_pages": len(freelist.free_pages),
            "orphan_pages": len(orphans),
            "unknown_page_type_pages": sum(1 for item in records if item.page_type == "unknown"),
            "out_of_range_references": out_of_range,
            "out_of_range_pages": len(bad_pages),
            "schema_objects": len(schema_objects),
            "page_count_matches_file": header.declared_pages == reader.actual_page_count and header.trailing_bytes == 0,
            "wal_present": header.wal_present,
        }
        schema["root_walk_count"] = len(walker.roots)
        return build_report(self.config, snapshot, header, problems, [item.to_dict() for item in records], traversal, freelist_payload, overflow_payload(overflow), schema, limitations, partial=partial, page_summary=page_summary, top_pages=top_pages, file_summary=file_summary)
    def _schema_without_walk(self, path: str) -> tuple[dict[str, Any], list[Problem]]:
        sidecar = probe_sidecar(path)
        problems: list[Problem] = []
        if not sidecar.available:
            problems.append(
                Problem(
                    "SCHEMA_SIDECAR_UNAVAILABLE",
                    field="sqlite3",
                    expected="只读旁证可用",
                    actual=sidecar.error or "unknown",
                    evidence="页级审计因头部不可切片而停止，仅尝试登记旁证状态",
                )
            )
        schema = {
            "objects": [],
            "object_counts": {},
            "parsed_root_pages": [],
            "walked_root_pages": [],
            "root_walk_pages": {},
            "sidecar": {
                "available": sidecar.available,
                "error": sidecar.error,
                "page_count": sidecar.page_count,
                "object_count": len(sidecar.objects),
                "root_pages": sorted(sidecar.roots()),
            },
        }
        return schema, problems
    def _count_types(self, walker: BtreeWalker) -> dict[str, int]:
        counts = Counter(page.page_type for page in walker.pages.values())
        return {key: counts[key] for key in sorted(counts)}
    def _key_order_summary(self, walker: BtreeWalker) -> dict[str, Any]:
        """Aggregate per-root key-order verdicts so reports never imply unproven order."""
        statuses: dict[str, list[int]] = {}
        for walk in walker.roots:
            if walk.key_order is not None:
                statuses.setdefault(walk.key_order.payload["status"], []).append(walk.root_page)
        return {"status_counts": {key: len(value) for key, value in sorted(statuses.items())}, "verified_roots": sorted(statuses.get("verified", [])), "violated_roots": sorted(statuses.get("violated", [])), "undetermined_roots": sorted(statuses.get("undetermined", [])), "unverified_index_roots": sorted(statuses.get("not-applicable", [])), "signed_rowids": True, "index_order_checked": False}
    def _conflicts(self, walker, overflow, freelist) -> list[Problem]:
        problems: list[Problem] = []
        reachable = walker.visited | overflow.result.reachable_pages
        for page_no in freelist.free_pages:
            if page_no in reachable:
                problems.append(
                    Problem(
                        "FREE_TRUNK_LEAF_CONFLICT",
                        page=page_no,
                        field="free_page",
                        expected="freelist 页不可达",
                        actual="reachable-and-free",
                        evidence="同一页同时出现在 freelist 与可达页图",
                    )
                )
        for page_no in sorted(overflow.result.reachable_pages):
            if page_no in walker.visited:
                problems.append(
                    Problem(
                        "OVFL_DUPLICATE_PAGE",
                        page=page_no,
                        field="overflow_page",
                        expected="溢出页不与 B 树页重叠",
                        actual=page_no,
                        evidence="溢出链引用了 B 树页",
                    )
                )
        return problems
    def _records(self, reader, header, walker, overflow, freelist):
        records: dict[int, Any] = {}
        for tree in walker.pages.values():
            records[tree.page_no] = tree_record(tree, header.page_size, header.usable_size)
        for page_no in sorted(overflow.result.reachable_pages):
            if page_no not in records:
                records[page_no] = overflow_record(page_no, header.page_size, header.usable_size)
        for record in freelist_records(reader, header, freelist):
            if record.page_no not in records:
                records[record.page_no] = record
        reachable = walker.visited | overflow.result.reachable_pages
        orphans = [
            page_no
            for page_no in range(2, reader.actual_page_count + 1)
            if page_no not in reachable and page_no not in freelist.free_pages
        ]
        for page_no in orphans:
            if page_no not in records:
                records[page_no] = orphan_record(reader, page_no, header.page_size, header.usable_size)
        return [records[key] for key in sorted(records)], orphans
