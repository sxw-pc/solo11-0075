"""Cross-snapshot diff refusal paths and CLI exit-code contract."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from diff_helpers import (
    audit,
    make_duplicate_key_db,
    make_page_size_pair,
    make_swap_pair,
    make_vacuum_pair,
    run_cli,
)
from page_ledger.audit.diff import compare_snapshots, diff_exit_code

ROOT = Path(__file__).resolve().parents[1]


def test_page_size_mismatch_is_refused(tmp_path):
    db_a, report_a, db_b, report_b = make_page_size_pair(tmp_path)
    result = compare_snapshots(str(report_a), str(db_a), str(report_b), str(db_b))
    assert result["status"] == "refused"
    assert result["conclusion"] == "refused"
    assert "page_size_mismatch" in result["scope"]["reasons"]
    assert diff_exit_code(result) == 2
    assert result["page_view"].get("not_compared") is True
    assert result["object_view"].get("not_compared") is True


def test_tampered_report_digest_is_refused(tmp_path):
    db_a, report_a, db_b, report_b = make_swap_pair(tmp_path)
    payload = json.loads(report_a.read_text(encoding="utf-8"))
    payload["summary"]["pages"]["totals"]["page_count"] = 0
    report_a.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = compare_snapshots(str(report_a), str(db_a), str(report_b), str(db_b))
    assert result["status"] == "refused"
    assert "a_verification_failed" in result["scope"]["reasons"]
    assert "report_integrity_digest" in result["inputs"]["a"]["failed_checks"]
    assert diff_exit_code(result) == 2


def test_report_bound_to_other_input_bytes_is_refused(tmp_path):
    db_a, report_a, db_b, report_b = make_swap_pair(tmp_path)
    result = compare_snapshots(str(report_a), str(db_b), str(report_b), str(db_a))
    assert result["status"] == "refused"
    assert "a_verification_failed" in result["scope"]["reasons"]
    assert "b_verification_failed" in result["scope"]["reasons"]
    assert "file_sha256" in result["inputs"]["a"]["failed_checks"]
    assert result["inputs"]["a"]["snapshot"]["sha256"] != result["inputs"]["b"]["snapshot"]["sha256"]


def test_schema_object_key_conflict_is_refused(tmp_path):
    db = tmp_path / "dup.db"
    make_duplicate_key_db(db)
    report = audit(db, tmp_path / "dup-report")
    result = compare_snapshots(str(report), str(db), str(report), str(db))
    assert result["status"] == "refused"
    assert "schema_object_key_conflict" in result["scope"]["reasons"]
    assert result["schema_key_conflicts"]
    assert diff_exit_code(result) == 2


def test_cli_diff_refuses_to_overwrite_inputs(tmp_path):
    db_a, report_a, _, _ = make_vacuum_pair(tmp_path)
    target = tmp_path / "diff.json"
    target.write_bytes(report_a.read_bytes())
    before = target.read_bytes()
    result = run_cli(
        "diff",
        "--report-a", str(target), "--db-a", str(db_a),
        "--report-b", str(target), "--db-b", str(db_a),
        "--out", str(tmp_path),
    )
    assert result.returncode == 2
    assert target.read_bytes() == before


def test_cli_diff_exit_codes_and_outputs(tmp_path, fixture_paths):
    db_a, report_a, db_b, report_b = make_vacuum_pair(tmp_path)
    out = tmp_path / "diff-out"
    changed = run_cli(
        "diff",
        "--report-a", str(report_a), "--db-a", str(db_a),
        "--report-b", str(report_b), "--db-b", str(db_b),
        "--out", str(out),
    )
    assert changed.returncode == 1, changed.stderr
    assert "page_rearrangement" in changed.stdout
    assert (out / "diff.json").exists() and (out / "diff.md").exists()
    same = run_cli(
        "diff",
        "--report-a", str(report_a), "--db-a", str(db_a),
        "--report-b", str(report_a), "--db-b", str(db_a),
        "--out", str(tmp_path / "same"),
        "--json",
    )
    assert same.returncode == 0, same.stderr
    assert json.loads(same.stdout)["conclusion"] == "no_change"
    refused = run_cli(
        "diff",
        "--report-a", str(report_a), "--db-a", str(db_b),
        "--report-b", str(report_b), "--db-b", str(db_a),
        "--out", str(tmp_path / "refused"),
    )
    assert refused.returncode == 2
    assert (tmp_path / "refused" / "diff.json").exists()
    limited_db = tmp_path / "broken.db"
    shutil.copyfile(fixture_paths["overflow_break"], limited_db)
    limited_report = audit(limited_db, tmp_path / "broken-report")
    limited = run_cli(
        "diff",
        "--report-a", str(report_a), "--db-a", str(db_a),
        "--report-b", str(limited_report), "--db-b", str(limited_db),
        "--out", str(tmp_path / "limited"),
    )
    assert limited.returncode == 3, limited.stderr
    assert "limited" in limited.stdout
