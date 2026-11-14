from __future__ import annotations
import json
from page_ledger.audit.engine import AuditEngine
from page_ledger.audit.model import LEVEL_ORDER
from page_ledger.report import write_reports
from page_ledger.util.ordering import stable_json
def _keys(report):
    return [
        (-LEVEL_ORDER[item["level"]], item["code"], item["page"] if item["page"] is not None else -1, item["field"])
        for item in report["findings"]
    ]
def test_same_input_same_report(fixture_paths):
    first = AuditEngine().run(str(fixture_paths["clean"]))
    second = AuditEngine().run(str(fixture_paths["clean"]))
    assert first == second
    assert stable_json(first) == stable_json(second)
    assert "generated_at" not in stable_json(first)
    assert _keys(first) == sorted(_keys(first))
    assert [item["page_no"] for item in first["pages"]] == sorted(item["page_no"] for item in first["pages"])
def test_report_files_are_deterministic(tmp_path, fixture_paths):
    report = AuditEngine().run(str(fixture_paths["overflow_break"]))
    first = write_reports(report, tmp_path / "a")
    second = write_reports(report, tmp_path / "b")
    for kind in ("json", "markdown", "html"):
        assert first[kind].read_bytes() == second[kind].read_bytes()
    loaded = json.loads(first["json"].read_text(encoding="utf-8"))
    assert loaded["summary"]["top_pages"]["fragmentation"] == sorted(
        loaded["summary"]["top_pages"]["fragmentation"],
        key=lambda item: (-item["fragmentation_ratio"], item["page_no"]),
    )
    assert loaded["summary"]["top_pages"]["low_utilization"] == sorted(
        loaded["summary"]["top_pages"]["low_utilization"],
        key=lambda item: (item["utilization"], item["page_no"]),
    )
