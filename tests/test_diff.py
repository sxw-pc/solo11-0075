"""Core cross-snapshot migration diff behaviour."""

from __future__ import annotations

import json
import shutil

from diff_helpers import make_added_table_pair, make_swap_pair, make_vacuum_pair, pair
from page_ledger.audit.diff import compare_snapshots, diff_exit_code, write_diff
from page_ledger.util.hashing import canonical_digest


def _diff(pair_value):
    db_a, report_a, db_b, report_b = pair_value
    return compare_snapshots(str(report_a), str(db_a), str(report_b), str(db_b))


def test_vacuum_reports_slot_release_and_object_root_move(tmp_path):
    result = _diff(make_vacuum_pair(tmp_path))
    assert result["status"] == "complete"
    assert result["scope"]["full"] is True
    assert result["conclusion"] == "page_rearrangement"
    assert diff_exit_code(result) == 1
    physical = result["page_view"]
    assert physical["summary"]["released"] > 0
    assert physical["summary"]["changed"] > 0
    changed_slots = [slot for slot in physical["slots"] if slot["state"] == "changed"]
    assert any("parent_page" in slot["changes"] for slot in changed_slots)
    released_slots = [slot for slot in physical["slots"] if slot["state"] == "released"]
    assert all(slot["new_presence"] == "absent" for slot in released_slots)
    assert physical["summary"]["released_by_ownership"]
    logical = result["object_view"]
    assert logical["summary"]["added"] == 0 and logical["summary"]["removed"] == 0
    items = [row for row in logical["objects"] if row["key"]["name"] == "items"][0]
    assert items["state"] == "changed"
    assert items["root"]["old"] != items["root"]["new"]
    assert "page_set" in items["changes"] and "root_page" in items["changes"]
    assert items["key"]["table_name"] == "items"


def test_swapped_segments_are_invisible_to_global_totals_but_not_objects(tmp_path):
    result = _diff(make_swap_pair(tmp_path))
    assert result["status"] == "complete"
    assert result["conclusion"] == "page_rearrangement"
    assert all(not any(row["delta"].values()) for row in result["page_view"]["page_type_totals"])
    assert result["page_view"]["summary"]["released"] == 0
    assert result["page_view"]["summary"]["allocated"] == 0
    logical = result["object_view"]
    assert logical["summary"]["root_moved"] == 2
    roots = {row["key"]["name"]: (row["root"]["old"], row["root"]["new"]) for row in logical["objects"]}
    assert roots["x"][0] == roots["y"][1] and roots["x"][1] == roots["y"][0]


def test_same_snapshot_is_no_change(tmp_path):
    db_a, report_a, _, _ = make_swap_pair(tmp_path)
    result = compare_snapshots(str(report_a), str(db_a), str(report_a), str(db_a))
    assert result["status"] == "complete"
    assert result["conclusion"] == "no_change"
    assert diff_exit_code(result) == 0
    assert result["page_view"]["summary"]["changed"] == 0
    assert result["object_view"]["summary"]["changed"] == 0


def test_added_and_removed_objects_are_reported_by_key(tmp_path):
    pair_value = make_added_table_pair(tmp_path)
    result = _diff(pair_value)
    assert result["object_view"]["summary"]["added"] == 1
    assert result["object_view"]["summary"]["removed"] == 0
    added_row = [row for row in result["object_view"]["objects"] if row["state"] == "added"][0]
    assert added_row["key"] == {"object_type": "table", "name": "extra", "table_name": "extra"}
    assert result["conclusion"] in {"object_membership_changed", "mixed"}
    db_a, report_a, db_b, report_b = pair_value
    reverse = compare_snapshots(str(report_b), str(db_b), str(report_a), str(db_a))
    assert reverse["object_view"]["summary"]["removed"] == 1
    removed_row = [row for row in reverse["object_view"]["objects"] if row["state"] == "removed"][0]
    assert removed_row["root"]["new"] is None


def test_partial_side_marks_limited_scope_instead_of_no_change(tmp_path, fixture_paths):
    db_a = tmp_path / "clean.db"
    shutil.copyfile(fixture_paths["clean"], db_a)
    db_b = tmp_path / "broken.db"
    shutil.copyfile(fixture_paths["overflow_break"], db_b)
    result = _diff(pair(db_a, tmp_path / "clean-report", db_b, tmp_path / "broken-report"))
    assert result["status"] == "limited"
    assert result["scope"]["limited"] is True
    assert "b_report_partial" in result["scope"]["reasons"]
    assert result["scope"]["limitations"]
    assert diff_exit_code(result) == 3
    assert result["page_view"]["summary"]["unknown"] > 0
    assert result["page_view"]["summary"]["released"] == 0
    assert result["conclusion"] != "no_change"


def test_diff_files_are_deterministic_and_escaped(tmp_path):
    result = _diff(make_vacuum_pair(tmp_path))
    first = write_diff(result, tmp_path / "out-a")
    second = write_diff(result, tmp_path / "out-b")
    assert first["json"].read_bytes() == second["json"].read_bytes()
    assert first["markdown"].read_bytes() == second["markdown"].read_bytes()
    loaded = json.loads(first["json"].read_text(encoding="utf-8"))
    payload = {key: value for key, value in loaded.items() if key != "integrity"}
    assert loaded["integrity"]["digest"] == canonical_digest(payload)
    markdown = first["markdown"].read_text(encoding="utf-8")
    for heading in (
        "# 跨快照迁移差异报告",
        "## 物理页槽视角（page_no）",
        "## 逻辑对象视角（object_type,name,table_name）",
        "## 未归属 finding 变化",
    ):
        assert heading in markdown
    assert "page_rearrangement" in markdown
