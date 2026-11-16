"""Logical schema-object view for the cross-snapshot diff."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .diff_pages import SPACE_FIELDS, ownership, records, space

KEY_FIELDS = ("object_type", "name", "table_name")
REPORTED_FIELDS = ("page_count", "cell_count", "depth")
SIGNATURE_FIELDS = ("code", "level", "page", "field")


def object_key(obj: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(str(obj.get(field, "")) for field in KEY_FIELDS)  # type: ignore[return-value]
def key_conflicts(report: dict[str, Any]) -> list[str]:
    counts = Counter(object_key(obj) for obj in report.get("schema", {}).get("objects", []))
    return [str(key) for key, count in sorted(counts.items()) if count > 1]
def object_set_trusted(report: dict[str, Any]) -> bool:
    """Whether the report's schema object *set* can be treated as exhaustive.

    A ``partial`` report may still have a fully parsed schema page; only an
    unfinished schema walk, an unparsable schema record or a traversal limit
    makes a missing object key unsafe to interpret as "removed".
    """
    if not report.get("schema", {}).get("objects"):
        return False
    codes = {str(item.get("code")) for item in report.get("findings", [])}
    if codes & {"SCHEMA_RECORD_INVALID", "BTREE_TRAVERSAL_LIMIT"}:
        return False
    for walk in report.get("traversal", {}).get("root_walks", []):
        if walk.get("root_page") == 1:
            return bool(walk.get("complete"))
    return False
def _signature(item: dict[str, Any]) -> tuple[str, str, int, str]:
    page = item.get("page")
    return (str(item.get("code")), str(item.get("level")), page if isinstance(page, int) else -1, str(item.get("field")))
def _compact(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in SIGNATURE_FIELDS}
def _entry(
    obj: dict[str, Any],
    root_walks: dict[str, Any],
    pages: dict[int, dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    root = int(obj.get("root_page") or 0)
    walk = sorted({int(page) for page in root_walks.get(str(root), [])})
    walked = [pages[page] for page in walk if page in pages]
    page_set = set(walk)
    related = sorted(
        {_signature(item): _compact(item) for item in findings if isinstance(item.get("page"), int) and int(item["page"]) in page_set}.values(),
        key=_signature,
    )
    return {
        "key": dict(zip(KEY_FIELDS, object_key(obj))),
        "root_page": root,
        "traversed": bool(obj.get("traversed")),
        "page_type": obj.get("page_type"),
        "reason": str(obj.get("reason") or ""),
        "reported": {field: int(obj.get(field) or 0) for field in REPORTED_FIELDS},
        "walk_pages": walk,
        "walk_available": bool(walk),
        "expected_walk": root > 0,
        "missing_records": [page for page in walk if page not in pages],
        "space": space(walked),
        "ownership": dict(sorted(Counter(ownership(item) for item in walked).items())),
        "findings": related,
    }
def object_entries(report: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    schema = report.get("schema", {})
    root_walks = schema.get("root_walk_pages", {}) or {}
    pages = records(report)
    findings = list(report.get("findings", []))
    entries = [_entry(obj, root_walks, pages, findings) for obj in schema.get("objects", [])]
    roots = Counter(entry["root_page"] for entry in entries if entry["expected_walk"])
    owners: Counter[int] = Counter()
    for entry in entries:
        owners.update(entry["walk_pages"])
    stats = {
        "shared_roots": sum(1 for count in roots.values() if count > 1),
        "shared_pages": sum(1 for count in owners.values() if count > 1),
    }
    return entries, stats, findings
def _finding_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    """Object-level findings are compared without ``page``.

    A pure page renumbering moves a finding's page number but does not change
    what the object suffers from; page movement is already captured by the
    page-set comparison.
    """
    return (str(item.get("code")), str(item.get("level")), str(item.get("field")))
def _finding_delta(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    old_counts = Counter(_finding_identity(item) for item in old)
    new_counts = Counter(_finding_identity(item) for item in new)
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for identity in sorted(set(old_counts) | set(new_counts)):
        delta = new_counts[identity] - old_counts[identity]
        source = new if delta > 0 else old
        sample = next(item for item in source if _finding_identity(item) == identity)
        bucket = added if delta > 0 else removed
        for _ in range(abs(delta)):
            bucket.append(_compact(sample))
    return {"added": added, "removed": removed}
def _changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if old["root_page"] != new["root_page"]:
        changes["root_page"] = {"old": old["root_page"], "new": new["root_page"]}
    for field in ("traversed", "page_type", "reason"):
        if old[field] != new[field]:
            changes[field] = {"old": old[field], "new": new[field]}
    for field in REPORTED_FIELDS:
        if old["reported"][field] != new["reported"][field]:
            changes[f"reported_{field}"] = {"old": old["reported"][field], "new": new["reported"][field]}
    for field in list(SPACE_FIELDS) + ["used_bytes", "page_count"]:
        if old["space"].get(field, 0) != new["space"].get(field, 0):
            changes[f"space_{field}"] = {"old": old["space"].get(field, 0), "new": new["space"].get(field, 0)}
    added = sorted(set(new["walk_pages"]) - set(old["walk_pages"]))
    removed = sorted(set(old["walk_pages"]) - set(new["walk_pages"]))
    if added or removed:
        changes["page_set"] = {"added": added, "removed": removed}
    if old["ownership"] != new["ownership"]:
        changes["ownership"] = {"old": old["ownership"], "new": new["ownership"]}
    findings = _finding_delta(old["findings"], new["findings"])
    if findings["added"] or findings["removed"]:
        changes["findings"] = findings
    return changes
def _unattributed(findings: list[dict[str, Any]], attributed: set[int]) -> list[dict[str, Any]]:
    unique = {
        _signature(item): _compact(item)
        for item in findings
        if not (isinstance(item.get("page"), int) and int(item["page"]) in attributed)
    }
    return [unique[key] for key in sorted(unique)]
def object_view(a: dict[str, Any], b: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Compare objects by stable key, independent of physical page numbering."""
    entries_a, stats_a, findings_a = object_entries(a)
    entries_b, stats_b, findings_b = object_entries(b)
    index_a = {object_key(entry["key"]): entry for entry in entries_a}
    index_b = {object_key(entry["key"]): entry for entry in entries_b}
    limitations: list[str] = []
    if stats_a["shared_roots"] or stats_b["shared_roots"] or stats_a["shared_pages"] or stats_b["shared_pages"]:
        limitations.append("object_root_shared")
    for entry in entries_a + entries_b:
        if entry["expected_walk"] and not entry["walk_available"]:
            limitations.append("object_root_unmapped")
        if entry["missing_records"]:
            limitations.append("object_walk_page_record_missing")
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for key in sorted(set(index_a) | set(index_b)):
        old, new = index_a.get(key), index_b.get(key)
        key_dict = dict(zip(KEY_FIELDS, key))
        if old is None:
            counts["added"] += 1
            rows.append({"key": key_dict, "state": "added", "root": {"old": None, "new": new["root_page"]}, "new": new})
        elif new is None:
            counts["removed"] += 1
            rows.append({"key": key_dict, "state": "removed", "root": {"old": old["root_page"], "new": None}, "old": old})
        else:
            changes = _changes(old, new)
            row = {"key": key_dict, "state": "changed" if changes else "unchanged", "root": {"old": old["root_page"], "new": new["root_page"]}}
            if changes:
                counts["changed"] += 1
                counts["root_moved"] += int(old["root_page"] != new["root_page"])
                row["changes"] = changes
                row["old"], row["new"] = old, new
            else:
                counts["unchanged"] += 1
            rows.append(row)
    attributed: set[int] = set()
    for entry in entries_a + entries_b:
        attributed.update(entry["walk_pages"])
    old_global = _unattributed(findings_a, attributed)
    new_global = _unattributed(findings_b, attributed)
    old_set, new_set = {_signature(item) for item in old_global}, {_signature(item) for item in new_global}
    unattributed = {
        "old": old_global,
        "new": new_global,
        "added": [item for item in new_global if _signature(item) in new_set - old_set],
        "removed": [item for item in old_global if _signature(item) in old_set - new_set],
    }
    payload = {
        "summary": {
            "added": counts["added"],
            "removed": counts["removed"],
            "changed": counts["changed"],
            "unchanged": counts["unchanged"],
            "root_moved": counts["root_moved"],
            "unattributed_findings_added": len(unattributed["added"]),
            "unattributed_findings_removed": len(unattributed["removed"]),
        },
        "objects": rows,
        "unattributed_findings": unattributed,
    }
    return payload, sorted(set(limitations))
