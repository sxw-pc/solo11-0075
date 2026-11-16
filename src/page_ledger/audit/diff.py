"""Cross-snapshot migration diff built on two verified audit reports.

Both sides are first bound to their input bytes by :func:`verify_report`, so a
stale or tampered report can never become a baseline for a new database.  Then
two independent identities are compared: physical ``page_no`` slots and stable
schema-object keys ``(object_type, name, table_name)``.  Incomplete coverage is
reported as a limited scope, never as "no change".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..util.hashing import canonical_digest
from ..util.ordering import stable_json, write_text
from .diff_markdown import render_diff_markdown
from .diff_objects import key_conflicts, object_set_trusted, object_view
from .diff_pages import coverage, page_view
from .verify import VerificationError, verify_report

SCOPE_TEXT = {
    "a_report_partial": "A 侧报告 status=partial，未覆盖页不能解释为释放或删除。",
    "b_report_partial": "B 侧报告 status=partial，未覆盖页不能解释为分配或保留。",
    "a_page_records_gap": "A 侧 complete 报告存在页槽缺记录，按未知处理。",
    "b_page_records_gap": "B 侧 complete 报告存在页槽缺记录，按未知处理。",
    "object_root_unmapped": "对象声明的根页没有 root_walk_pages，对象页集合未知。",
    "object_root_shared": "多个 schema 对象共享根页或遍历页，对象页归属不唯一。",
    "object_walk_page_record_missing": "对象遍历页在 pages 中缺记录，对象空间账本不完整。",
    "object_set_unverified": "一侧 partial 报告没有解析出任何 schema 对象，无法据此认定对象增减。",
    "page_size_mismatch": "两侧 page_size 不同，页号不能作为同一物理槽位比较。",
    "schema_object_key_conflict": "同一侧出现重复 (object_type,name,table_name) 键，对象身份不唯一。",
    "a_verification_error": "A 侧复核无法执行。",
    "a_verification_failed": "A 侧报告与输入文件或重放结果不一致。",
    "b_verification_error": "B 侧复核无法执行。",
    "b_verification_failed": "B 侧报告与输入文件或重放结果不一致。",
}


def _side(label: str, report_path: str, db_path: str) -> dict[str, Any]:
    side: dict[str, Any] = {
        "label": label,
        "report_path": str(Path(report_path).resolve()),
        "db_path": str(Path(db_path).resolve()),
        "valid": False,
        "error": "",
        "checks": [],
        "failed_checks": [],
        "report": None,
        "replay": None,
    }
    try:
        result = verify_report(report_path, db_path)
    except VerificationError as exc:
        side["error"] = str(exc)
        return side
    side.update(
        valid=result.valid,
        checks=result.checks,
        failed_checks=[item["check"] for item in result.checks if not item["ok"]],
        report=result.report,
        replay=result.replay,
    )
    return side
def _source(side: dict[str, Any]) -> dict[str, Any]:
    return side.get("replay") or side.get("report") or {}
def _input_summary(side: dict[str, Any]) -> dict[str, Any]:
    report = side.get("report") or {}
    snapshot = report.get("snapshot", {})
    return {
        "report_path": side["report_path"],
        "db_path": side["db_path"],
        "valid": bool(side["valid"]),
        "error": side["error"],
        "failed_checks": list(side["failed_checks"]),
        "report_status": report.get("status"),
        "report_conclusion": report.get("conclusion"),
        "snapshot": {
            "display_name": snapshot.get("display_name"),
            "size": snapshot.get("size"),
            "sha256": snapshot.get("sha256"),
        },
        "checks": list(side["checks"]),
    }
def _scope(coverage_map: dict[str, dict], reasons: list[str], refused: bool) -> dict[str, Any]:
    limited = bool(reasons) and not refused
    return {
        "full": not reasons,
        "limited": limited,
        "refused": refused,
        "reasons": reasons,
        "limitations": [SCOPE_TEXT.get(item, item) for item in reasons],
        "coverage": {
            label: {
                "report_status": cov["status"],
                "actual_pages": cov["actual_pages"],
                "recorded_pages": cov["recorded_pages"],
                "missing_records": len(cov["missing_records"]),
                "complete": cov["complete"],
            }
            for label, cov in coverage_map.items()
        },
    }
def _refused(sides: dict[str, dict], reasons: list[str], coverage_map: dict) -> dict[str, Any]:
    return {
        "status": "refused",
        "conclusion": "refused",
        "scope": _scope(coverage_map, reasons, True),
        "inputs": {label: _input_summary(side) for label, side in sides.items()},
        "page_view": {"summary": {}, "page_type_totals": [], "slots": [], "not_compared": True},
        "object_view": {
            "summary": {},
            "objects": [],
            "unattributed_findings": {"old": [], "new": [], "added": [], "removed": []},
            "not_compared": True,
        },
    }
def _empty_coverage() -> dict[str, dict]:
    return {
        label: {"status": None, "page_size": None, "actual_pages": 0, "recorded_pages": 0, "missing_records": [], "complete": False}
        for label in ("a", "b")
    }
def compare_snapshots(report_a: str, db_a: str, report_b: str, db_b: str) -> dict[str, Any]:
    """Verify both sides, then compare physical page slots and schema objects."""
    sides = {"a": _side("a", report_a, db_a), "b": _side("b", report_b, db_b)}
    reasons = [f"{label}_verification_{'error' if side['error'] else 'failed'}" for label, side in sides.items() if side["error"] or not side["valid"]]
    if reasons:
        return _finalize(_refused(sides, reasons, _empty_coverage()))
    source_a, source_b = _source(sides["a"]), _source(sides["b"])
    coverage_map = {"a": coverage(source_a), "b": coverage(source_b)}
    conflicts = key_conflicts(source_a) + key_conflicts(source_b)
    if coverage_map["a"]["page_size"] != coverage_map["b"]["page_size"]:
        reasons.append("page_size_mismatch")
    if conflicts:
        reasons.append("schema_object_key_conflict")
    if reasons:
        refused = _refused(sides, reasons, coverage_map)
        refused["schema_key_conflicts"] = conflicts
        return _finalize(refused)
    limited_reasons: list[str] = []
    for label, cov in coverage_map.items():
        if cov["status"] != "complete":
            limited_reasons.append(f"{label}_report_partial")
        elif cov["missing_records"]:
            limited_reasons.append(f"{label}_page_records_gap")
    physical = page_view(source_a, source_b, coverage_map["a"], coverage_map["b"])
    logical, object_limits = object_view(source_a, source_b)
    limited_reasons.extend(object_limits)
    sets_unverified = not (object_set_trusted(source_a) and object_set_trusted(source_b))
    if sets_unverified:
        limited_reasons.append("object_set_unverified")
    logical["summary"]["membership_trusted"] = not sets_unverified
    limited = bool(limited_reasons)
    membership = bool(logical["summary"]["added"] or logical["summary"]["removed"]) and not sets_unverified
    findings_changed = bool(
        logical["summary"]["unattributed_findings_added"]
        or logical["summary"]["unattributed_findings_removed"]
        or any("findings" in row.get("changes", {}) for row in logical["objects"])
    )
    layout_changed = bool(
        physical["summary"]["changed"]
        or physical["summary"]["released"]
        or physical["summary"]["allocated"]
        or logical["summary"]["changed"]
    )
    if membership and (layout_changed or findings_changed):
        conclusion = "mixed"
    elif membership:
        conclusion = "object_membership_changed"
    elif layout_changed and findings_changed:
        conclusion = "layout_and_findings"
    elif layout_changed:
        conclusion = "page_rearrangement"
    elif findings_changed:
        conclusion = "finding_changes"
    elif limited:
        conclusion = "limited_scope"
    else:
        conclusion = "no_change"
    result = {
        "status": "limited" if limited else "complete",
        "conclusion": conclusion,
        "scope": _scope(coverage_map, limited_reasons, False),
        "inputs": {label: _input_summary(side) for label, side in sides.items()},
        "page_size": {"a": coverage_map["a"]["page_size"], "b": coverage_map["b"]["page_size"], "compatible": True},
        "page_view": physical,
        "object_view": logical,
    }
    return _finalize(result)
def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    payload["diff_version"] = "1.0"
    payload["integrity"] = {"algorithm": "sha256", "digest": canonical_digest(payload)}
    return payload
def diff_exit_code(result: dict[str, Any]) -> int:
    """0 no change, 1 change, 2 refused, 3 limited coverage."""
    if result.get("status") == "refused":
        return 2
    if result.get("status") == "limited":
        return 3
    return 0 if result.get("conclusion") == "no_change" else 1
def write_diff(result: dict[str, Any], out_dir: str | Path) -> dict[str, Path]:
    """Write deterministic ``diff.json`` and ``diff.md`` regardless of outcome."""
    target = Path(out_dir)
    json_path = target / "diff.json"
    markdown_path = target / "diff.md"
    write_text(json_path, stable_json(result))
    write_text(markdown_path, render_diff_markdown(result))
    return {"json": json_path, "markdown": markdown_path}
