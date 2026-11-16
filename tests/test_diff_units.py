"""Unit-level scope semantics for the cross-snapshot diff views."""

from __future__ import annotations

import json

from page_ledger.audit.diff import compare_snapshots
from page_ledger.audit.diff_objects import object_view
from page_ledger.audit.diff_pages import coverage, page_view
from diff_helpers import make_swap_pair


def _report(pages: list[dict], actual_pages: int = 4, schema: dict | None = None) -> dict:
    return {
        "status": "complete",
        "header": {"page_size": 1024},
        "summary": {"file": {"actual_pages": actual_pages}},
        "pages": pages,
        "findings": [],
        "schema": schema or {"objects": [], "root_walk_pages": {}},
        "traversal": {"root_walks": [{"root_page": 1, "complete": True}]},
    }


def _page(page_no: int) -> dict:
    return {
        "page_no": page_no,
        "page_type": "table_leaf",
        "role": "table",
        "parent_page": None,
        "depth": 0,
        "cell_count": 1,
        "cell_bytes": 10,
        "header_bytes": 8,
        "pointer_bytes": 2,
        "free_bytes": 100,
        "fragmented_bytes": 0,
        "reserved_bytes": 0,
    }


def test_page_record_gap_is_unknown_not_released():
    side_a = _report([_page(2), _page(3), _page(4)])
    side_b = _report([_page(2), _page(3)])
    cover_a, cover_b = coverage(side_a), coverage(side_b)
    assert cover_b["missing_records"] == [4]
    assert cover_b["complete"] is False
    view = page_view(side_a, side_b, cover_a, cover_b)
    assert view["summary"]["released"] == 0
    assert view["summary"]["unknown"] == 1
    unknown = [slot for slot in view["slots"] if slot["state"] == "unknown"][0]
    assert unknown["page_no"] == 4
    assert unknown["new_presence"] == "unknown"


def test_shorter_complete_file_counts_as_release():
    side_a = _report([_page(2), _page(3), _page(4)])
    side_b = _report([_page(2), _page(3)], actual_pages=3)
    view = page_view(side_a, side_b, coverage(side_a), coverage(side_b))
    assert view["summary"]["released"] == 1
    assert view["summary"]["unknown"] == 0


def test_object_root_without_walk_is_a_limitation():
    schema = {
        "objects": [
            {"object_type": "table", "name": "t", "table_name": "t", "root_page": 2, "traversed": False, "page_count": 0, "cell_count": 0, "depth": 0}
        ],
        "root_walk_pages": {},
    }
    report = _report([_page(2)], schema=schema)
    payload, limitations = object_view(report, report)
    assert limitations == ["object_root_unmapped"]
    assert payload["summary"]["added"] == 0
    assert payload["summary"]["removed"] == 0


def test_recomputed_digest_does_not_hide_core_tamper(tmp_path):
    from page_ledger.audit.diff import diff_exit_code
    from page_ledger.util.hashing import canonical_digest

    db_a, report_a, db_b, report_b = make_swap_pair(tmp_path)
    payload = json.loads(report_a.read_text(encoding="utf-8"))
    payload["summary"]["pages"]["totals"]["page_count"] = 0
    payload.pop("integrity", None)
    payload["integrity"] = {"algorithm": "sha256", "digest": canonical_digest(payload), "covers": "tampered"}
    report_a.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = compare_snapshots(str(report_a), str(db_a), str(report_b), str(db_b))
    assert result["status"] == "refused"
    assert "core_replay" in result["inputs"]["a"]["failed_checks"]
    assert diff_exit_code(result) == 2
