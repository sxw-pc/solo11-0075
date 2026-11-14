"""Reconcile sqlite_schema objects with traversed B-tree roots."""

from __future__ import annotations

from typing import Any

from ..db.btree import BtreeWalker
from ..db.pages import INDEX_TYPES, TABLE_TYPES
from ..db.schema import SidecarProbe, probe_sidecar
from .model import Problem, SchemaObject


def _issue(code: str, page: int | None, field: str, expected: object, actual: object, evidence: str) -> Problem:
    return Problem(code, page=page, field=field, expected=expected, actual=actual, evidence=evidence)
def _object_key(item: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(item.get("object_type", item.get("type", ""))),
        str(item.get("name", "")),
        str(item.get("table_name", item.get("tbl_name", ""))),
        int(item.get("root_page", item.get("rootpage", 0)) or 0),
    )
def audit_schema(
    header,
    walker: BtreeWalker,
    objects: list[SchemaObject],
    snapshot_path: str,
) -> tuple[list[Problem], dict[str, Any]]:
    problems: list[Problem] = []
    parsed_roots: set[int] = set()
    walked_roots: set[int] = set()
    root_walks: dict[int, list[int]] = {}
    for obj in objects:
        if obj.object_type not in {"table", "index"} or obj.root_page <= 0:
            continue
        expected_kind = "table" if obj.object_type == "table" else "index"
        allowed = TABLE_TYPES if expected_kind == "table" else INDEX_TYPES
        parsed_roots.add(obj.root_page)
        walk = walker.walk_root(obj.root_page, expected_kind, expected_kind)
        root_walks[obj.root_page] = sorted(walk.pages)
        obj.page_count = len(walk.pages)
        obj.cell_count = walk.cell_count
        obj.depth = walk.max_depth
        obj.reason = walk.reason
        root_page = walker.pages.get(obj.root_page)
        if root_page is not None:
            obj.page_type = root_page.page_type
        if obj.root_page == 1:
            problems.append(
                _issue("SCHEMA_ROOT_MISSING", 1, "root_page", ">= 2", obj.root_page, f"对象 {obj.name} 不能使用 schema 页作为根")
            )
            continue
        if not walk.pages:
            problems.append(
                _issue(
                    "SCHEMA_ROOT_MISSING",
                    obj.root_page,
                    "root_page",
                    f"2..{walker.reader.actual_page_count} 且可达",
                    obj.root_page,
                    f"对象 {obj.name} 的根页不可达",
                )
            )
            continue
        walked_roots.add(obj.root_page)
        obj.traversed = True
        if obj.page_type not in allowed:
            problems.append(
                _issue(
                    "SCHEMA_ROOT_TYPE_MISMATCH",
                    obj.root_page,
                    "page_type",
                    sorted(allowed),
                    obj.page_type,
                    f"{obj.object_type} 对象 {obj.name} 的根页类型不符",
                )
            )
        if walk.reason == "already-visited":
            problems.append(
                _issue("SCHEMA_TRAVERSAL_FAILED", obj.root_page, "root_page", "唯一根页", obj.root_page, f"对象 {obj.name} 复用了已遍历根页")
            )
        elif not walk.complete:
            problems.append(
                _issue(
                    "SCHEMA_TRAVERSAL_FAILED",
                    obj.root_page,
                    "traversal",
                    "完整遍历",
                    walk.reason or "incomplete",
                    f"对象 {obj.name} 的树遍历未完成",
                )
            )
    if parsed_roots != walked_roots:
        problems.append(
            _issue(
                "SCHEMA_ROOT_DIFF",
                None,
                "root_pages",
                sorted(parsed_roots),
                sorted(walked_roots),
                "schema 声明根页与实际成功遍历根页不同",
            )
        )
    sidecar = probe_sidecar(snapshot_path)
    if not sidecar.available:
        problems.append(
            _issue(
                "SCHEMA_SIDECAR_UNAVAILABLE",
                None,
                "sqlite3",
                "只读旁证可用",
                sidecar.error or "unknown",
                "旁证失败不影响主审计结果",
            )
        )
    else:
        problems.extend(_compare_sidecar(header, objects, parsed_roots, sidecar))
    schema_payload = _schema_payload(objects, parsed_roots, walked_roots, root_walks, sidecar)
    return problems, schema_payload
def _compare_sidecar(
    header,
    objects: list[SchemaObject],
    parsed_roots: set[int],
    sidecar: SidecarProbe,
) -> list[Problem]:
    problems: list[Problem] = []
    if sidecar.page_count is not None and sidecar.page_count != header.declared_pages:
        problems.append(
            _issue(
                "SCHEMA_SIDECAR_MISMATCH",
                None,
                "page_count",
                header.declared_pages,
                sidecar.page_count,
                "PRAGMA page_count 与文件头声明不同",
            )
        )
    sidecar_roots = sidecar.roots()
    if sidecar_roots != parsed_roots:
        problems.append(
            _issue(
                "SCHEMA_ROOT_DIFF",
                None,
                "root_pages",
                sorted(parsed_roots),
                sorted(sidecar_roots),
                "解析 schema 记录与只读旁证的根页集合不同",
            )
        )
    parsed_keys = {_object_key(item.to_dict()) for item in objects}
    sidecar_keys = {_object_key(item) for item in sidecar.objects}
    if parsed_keys != sidecar_keys:
        problems.append(
            _issue(
                "SCHEMA_OBJECT_MISMATCH",
                None,
                "objects",
                sorted(str(item) for item in parsed_keys),
                sorted(str(item) for item in sidecar_keys),
                "解析 schema 对象集合与只读旁证不同",
            )
        )
    return problems
def _schema_payload(
    objects: list[SchemaObject],
    parsed_roots: set[int],
    walked_roots: set[int],
    root_walks: dict[int, list[int]],
    sidecar: SidecarProbe,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for obj in objects:
        counts[obj.object_type] = counts.get(obj.object_type, 0) + 1
    return {
        "objects": [item.to_dict() for item in objects],
        "object_counts": {key: counts[key] for key in sorted(counts)},
        "parsed_root_pages": sorted(parsed_roots),
        "walked_root_pages": sorted(walked_roots),
        "root_walk_pages": {str(key): value for key, value in sorted(root_walks.items())},
        "sidecar": {
            "available": sidecar.available,
            "error": sidecar.error,
            "page_count": sidecar.page_count,
            "object_count": len(sidecar.objects),
            "root_pages": sorted(sidecar.roots()),
        },
    }
