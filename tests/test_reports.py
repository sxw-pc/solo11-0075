from __future__ import annotations
import copy
import json
from page_ledger.report import (
    render_html,
    render_markdown,
    write_html_report,
    write_json_report,
    write_markdown_report,
)
from page_ledger.util.hashing import canonical_digest
def test_json_report_stable_and_roundtrips(tmp_path, clean_report):
    target = write_json_report(clean_report, tmp_path)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == clean_report
    assert loaded["integrity"]["algorithm"] == "sha256"
    payload = {key: value for key, value in loaded.items() if key != "integrity"}
    assert loaded["integrity"]["digest"] == canonical_digest(payload)
    assert target.read_text(encoding="utf-8") == target.read_text(encoding="utf-8")
def test_markdown_sections_and_escaping(tmp_path, clean_report):
    report = copy.deepcopy(clean_report)
    report["findings"] = [
        {
            "level": "error",
            "code": "PAGE_TEST",
            "page": 4,
            "field": "evidence|pipe",
            "expected": "a\nb",
            "actual": "<bad>",
            "evidence": "line|break\nnext",
            "blocking": False,
            "title": "synthetic",
        }
    ]
    text = render_markdown(report)
    for heading in ("# 页账核验报告", "## 文件快照", "## 关键统计", "## 页类型账本", "## 异常表", "## Schema 对账", "## 限制"):
        assert heading in text
    assert "evidence\\|pipe" in text
    assert "line\\|break next" in text
    target = write_markdown_report(report, tmp_path)
    assert target.name == "report.md" and target.exists()
def test_html_self_contained_and_escaped(tmp_path, clean_report):
    report = copy.deepcopy(clean_report)
    script = '<script>alert("x")</script>&'
    report["findings"] = [
        {
            "level": "critical",
            "code": "XSS_TEST",
            "page": 4,
            "field": script,
            "expected": script,
            "actual": script,
            "evidence": script,
            "blocking": True,
            "title": script,
        }
    ]
    text = render_html(report)
    assert "<!doctype html>" in text
    assert "<style>" in text and "</style>" in text
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&amp;" in text
    assert "<script>" not in text
    for marker in ("http://", "https://", "<link", "src=", "@import", "url("):
        assert marker not in text
    target = write_html_report(report, tmp_path)
    assert target.name == "report.html" and target.exists()
    assert "<script>" not in target.read_text(encoding="utf-8")
def test_report_output_file_names(tmp_path, clean_report):
    assert write_json_report(clean_report, tmp_path).name == "report.json"
    assert write_html_report(clean_report, tmp_path).name == "report.html"
