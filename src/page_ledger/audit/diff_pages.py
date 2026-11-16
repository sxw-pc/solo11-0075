"""Physical page-slot view for the cross-snapshot diff."""

from __future__ import annotations

from collections import Counter
from typing import Any

SPACE_FIELDS = (
    "cell_bytes",
    "free_bytes",
    "fragmented_bytes",
    "header_bytes",
    "pointer_bytes",
    "reserved_bytes",
)
PAGE_FIELDS = ("page_type", "role", "parent_page", "depth", "cell_count") + SPACE_FIELDS
BTREE_ROLES = {"schema", "table", "index", "btree"}


def ownership(record: dict[str, Any]) -> str:
    """Collapse a page role into the structure that owns the physical slot."""
    role = str(record.get("role", ""))
    if role in BTREE_ROLES:
        return "btree"
    if role.startswith("freelist"):
        return "freelist"
    if role in {"overflow", "orphan"}:
        return role
    return "unknown"
def records(report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(item["page_no"]): item for item in report.get("pages", [])}
def space(items: list[dict[str, Any]]) -> dict[str, int]:
    totals = {field: sum(int(item.get(field) or 0) for item in items) for field in SPACE_FIELDS}
    totals["used_bytes"] = totals["header_bytes"] + totals["pointer_bytes"] + totals["cell_bytes"]
    totals["page_count"] = len(items)
    return totals
def coverage(report: dict[str, Any]) -> dict[str, Any]:
    """Describe which physical slot numbers the report can actually speak for."""
    known = records(report)
    actual = int(report.get("summary", {}).get("file", {}).get("actual_pages") or 0)
    missing = [page for page in range(2, actual + 1) if page not in known]
    return {
        "status": report.get("status"),
        "page_size": report.get("header", {}).get("page_size"),
        "actual_pages": actual,
        "recorded_pages": len(known),
        "missing_records": missing,
        "complete": report.get("status") == "complete" and not missing,
    }
def _presence(page_no: int, known: dict[int, Any], cover: dict[str, Any]) -> str:
    if page_no in known:
        return "known"
    # A partial or gap-ridden side cannot prove a slot is gone; keep it unknown
    # instead of turning incomplete coverage into a "released" claim.
    if not cover["complete"]:
        return "unknown"
    return "absent" if cover["actual_pages"] > 0 and page_no > cover["actual_pages"] else "unknown"
def _type_totals(report: dict[str, Any]) -> dict[str, dict[str, int]]:
    rows = report.get("summary", {}).get("pages", {}).get("by_page_type", [])
    keys = ("page_count", "cell_count", "used_bytes", "free_bytes", "fragmented_bytes", "reserved_bytes")
    return {str(row.get("page_type")): {key: int(row.get(key) or 0) for key in keys} for row in rows}
def page_view(a: dict[str, Any], b: dict[str, Any], cover_a: dict, cover_b: dict) -> dict[str, Any]:
    """Compare each ``page_no`` as a physical slot.

    A slot is only called released/allocated when the other side's file is
    demonstrably shorter.  Otherwise it is ``unknown`` so that incomplete
    coverage never turns into a false "no change" or false "freed" claim.
    """
    known_a, known_b = records(a), records(b)
    slots: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    released: Counter[str] = Counter()
    allocated: Counter[str] = Counter()
    for page_no in sorted(set(known_a) | set(known_b)):
        old, new = known_a.get(page_no), known_b.get(page_no)
        state_a = "known" if old is not None else _presence(page_no, known_a, cover_a)
        state_b = "known" if new is not None else _presence(page_no, known_b, cover_b)
        if old is not None and new is not None:
            changes = {
                field: {"old": old.get(field), "new": new.get(field)}
                for field in PAGE_FIELDS
                if old.get(field) != new.get(field)
            }
            old_owner, new_owner = ownership(old), ownership(new)
            if old_owner != new_owner:
                transitions[f"{old_owner}->{new_owner}"] += 1
            if changes:
                counts["changed"] += 1
                slots.append(
                    {
                        "page_no": page_no,
                        "state": "changed",
                        "ownership": {"old": old_owner, "new": new_owner},
                        "changes": changes,
                        "old": old,
                        "new": new,
                    }
                )
            else:
                counts["retained"] += 1
                slots.append({"page_no": page_no, "state": "retained", "ownership": {"old": old_owner, "new": new_owner}})
            continue
        if old is not None and state_b == "absent":
            counts["released"] += 1
            released[ownership(old)] += 1
            slots.append({"page_no": page_no, "state": "released", "old": old, "old_presence": state_a, "new_presence": state_b})
        elif new is not None and state_a == "absent":
            counts["allocated"] += 1
            allocated[ownership(new)] += 1
            slots.append({"page_no": page_no, "state": "allocated", "new": new, "old_presence": state_a, "new_presence": state_b})
        else:
            counts["unknown"] += 1
            slots.append({"page_no": page_no, "state": "unknown", "old_presence": state_a, "new_presence": state_b})
    totals_a, totals_b = _type_totals(a), _type_totals(b)
    page_types = sorted(set(totals_a) | set(totals_b))
    type_rows = []
    for page_type in page_types:
        old = totals_a.get(page_type, {})
        new = totals_b.get(page_type, {})
        delta = {key: int(new.get(key, 0)) - int(old.get(key, 0)) for key in sorted(set(old) | set(new))}
        type_rows.append({"page_type": page_type, "old": old, "new": new, "delta": delta})
    return {
        "summary": {
            "retained": counts["retained"],
            "changed": counts["changed"],
            "released": counts["released"],
            "allocated": counts["allocated"],
            "unknown": counts["unknown"],
            "ownership_transitions": dict(sorted(transitions.items())),
            "released_by_ownership": dict(sorted(released.items())),
            "allocated_by_ownership": dict(sorted(allocated.items())),
        },
        "page_type_totals": type_rows,
        "slots": slots,
    }
