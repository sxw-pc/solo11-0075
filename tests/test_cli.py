from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def _run(args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "page_ledger", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
def test_audit_and_verify_cli(tmp_path, fixture_paths):
    out_dir = tmp_path / "reports"
    ledger = out_dir / "audit-ledger.sqlite"
    result = _run(["audit", "--db", str(fixture_paths["clean"]), "--out", str(out_dir)])
    assert result.returncode == 0, result.stderr
    assert "结论: ok" in result.stdout
    assert (out_dir / "report.json").exists()
    assert (out_dir / "report.md").exists()
    assert (out_dir / "report.html").exists()
    assert ledger.exists()
    connection = sqlite3.connect(ledger)
    try:
        for table in ("runs", "findings", "page_summary", "rule_summary"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] >= 1 if table != "findings" else True
        run = connection.execute("SELECT status, conclusion, page_size FROM runs").fetchone()
        assert run == ("complete", "ok", 1024)
    finally:
        connection.close()
    verify = _run(
        ["verify", "--report", str(out_dir / "report.json"), "--db", str(fixture_paths["clean"]), "--json"]
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert json.loads(verify.stdout)["valid"] is True
def test_verify_detects_tampering(tmp_path, fixture_paths):
    out_dir = tmp_path / "reports"
    assert _run(["audit", "--db", str(fixture_paths["clean"]), "--out", str(out_dir)]).returncode == 0
    report_path = out_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["conclusion"] = "critical"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    result = _run(["verify", "--report", str(report_path), "--db", str(fixture_paths["clean"])])
    assert result.returncode == 1
    assert "失败" in result.stdout
def test_verify_detects_changed_input(tmp_path, fixture_paths):
    db = tmp_path / "input.db"
    db.write_bytes(fixture_paths["clean"].read_bytes())
    out_dir = tmp_path / "reports"
    assert _run(["audit", "--db", str(db), "--out", str(out_dir)]).returncode == 0
    with db.open("ab") as handle:
        handle.write(b"tamper")
    result = _run(["verify", "--report", str(out_dir / "report.json"), "--db", str(db)])
    assert result.returncode == 1
    assert "file_sha256" in result.stdout
def test_cli_refuses_to_overwrite_input(tmp_path, fixture_paths):
    db = tmp_path / "report.json"
    db.write_bytes(fixture_paths["clean"].read_bytes())
    before = db.read_bytes()
    result = _run(["audit", "--db", str(db), "--out", str(tmp_path)])
    assert result.returncode == 2
    assert db.read_bytes() == before
    other = tmp_path / "input.db"
    other.write_bytes(fixture_paths["clean"].read_bytes())
    before = other.read_bytes()
    result = _run(["audit", "--db", str(other), "--out", str(tmp_path / "out"), "--ledger", str(other)])
    assert result.returncode == 2
    assert other.read_bytes() == before
def test_cli_errors_and_fail_on(tmp_path, fixture_paths):
    bad_config = tmp_path / "bad.json"
    bad_config.write_text('{"unknown": 1}', encoding="utf-8")
    failed = _run(
        ["audit", "--db", str(fixture_paths["clean"]), "--out", str(tmp_path / "o"), "--config", str(bad_config)]
    )
    assert failed.returncode == 2
    gate = _run(
        [
            "audit",
            "--db",
            str(fixture_paths["broken_header"]),
            "--out",
            str(tmp_path / "broken"),
            "--fail-on",
            "critical",
        ]
    )
    assert gate.returncode == 1
    assert (tmp_path / "broken" / "report.json").exists()
