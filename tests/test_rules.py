from __future__ import annotations
import pytest
from page_ledger.audit.engine import AuditEngine
from page_ledger.audit.ledger import space_findings
from page_ledger.audit.model import Finding, PageRecord, Problem
from page_ledger.audit.rules import RULES, RULE_CATEGORIES, make_finding, rule_summary, rules_document
from page_ledger.config import AuditConfig, ConfigError
def _record(page_no=2, fragmented=0, used=900):
    return PageRecord(
        page_no=page_no,
        page_type="table_leaf",
        role="table",
        parent_page=1,
        depth=1,
        cell_count=3,
        cell_bytes=used - 20,
        header_bytes=12,
        pointer_bytes=8,
        content_bytes=used - 20,
        free_bytes=1024 - used,
        fragmented_bytes=fragmented,
        reserved_bytes=0,
        utilization=used / 1024,
        fragmentation_ratio=fragmented / 1024,
        page_size=1024,
        usable_size=1024,
    )
def test_rule_catalogue_complete_and_typed():
    assert len(RULES) > 40
    assert all(rule.level in {"info", "warning", "error", "critical"} for rule in RULES.values())
    for category in RULE_CATEGORIES:
        assert any(code.startswith(category + "_") for code in RULES)
    documented = rules_document()
    assert {item["code"] for item in documented} == set(RULES)
    assert all(item["evidence_fields"] for item in documented)
@pytest.mark.parametrize(
    "code,level",
    [
        ("HDR_MAGIC_INVALID", "critical"),
        ("PAGE_TYPE_UNKNOWN", "error"),
        ("BTREE_CYCLE", "error"),
        ("FREE_COUNT_MISMATCH", "warning"),
        ("OVFL_CHAIN_BROKEN", "error"),
        ("SCHEMA_ROOT_MISSING", "error"),
        ("SPACE_UTILIZATION_LOW", "info"),
    ],
)
def test_representative_findings(code, level):
    finding = make_finding(Problem(code, page=7, field="probe", expected="a", actual="b", evidence="synthetic"))
    assert finding.code == code and finding.level == level and finding.page == 7
    assert finding.evidence == "synthetic"
    assert finding.to_dict()["field"] == "probe"
def test_rule_summary_and_levels():
    findings = [
        Finding("FREE_COUNT_MISMATCH", "warning", None, "x", 1, 2, "e", False, "t"),
        Finding("FREE_COUNT_MISMATCH", "warning", 2, "x", 1, 2, "e", False, "t"),
        Finding("HDR_MAGIC_INVALID", "critical", None, "x", 1, 2, "e", True, "t"),
    ]
    summary = rule_summary(findings)
    assert summary == [
        {"code": "FREE_COUNT_MISMATCH", "hits": 2, "max_level": "warning"},
        {"code": "HDR_MAGIC_INVALID", "hits": 1, "max_level": "critical"},
    ]
def test_space_rule_positive_negative():
    config = AuditConfig(fragmentation_warn_ratio=0.05, utilization_low_ratio=0.5, low_util_min_cells=2)
    positive, _ = space_findings([_record(fragmented=100, used=200)], config)
    codes = {item.code for item in positive}
    assert {"SPACE_FRAGMENTATION_HIGH", "SPACE_UTILIZATION_LOW"} <= codes
    negative, _ = space_findings([_record(fragmented=0, used=1000)], config)
    assert not negative
def test_config_validation_and_engine_controls(fixture_paths):
    assert AuditConfig.from_mapping({"top_pages": 2}).top_pages == 2
    with pytest.raises(ConfigError):
        AuditConfig.from_mapping({"unknown_key": 1})
    with pytest.raises(ConfigError):
        AuditConfig.from_mapping({"fragmentation_warn_ratio": 2})
    report = AuditEngine(AuditConfig(include_info_findings=False)).run(str(fixture_paths["clean"]))
    assert all(item["level"] != "info" for item in report["findings"])
    capped = AuditEngine(AuditConfig(max_findings_per_code=1)).run(str(fixture_paths["freelist_cycle"]))
    counts = {}
    for item in capped["findings"]:
        counts[item["code"]] = counts.get(item["code"], 0) + 1
    assert max(counts.values()) == 1
