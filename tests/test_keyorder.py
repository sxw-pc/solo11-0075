from __future__ import annotations
from page_ledger.audit.engine import AuditEngine
from page_ledger.config import AuditConfig
from page_ledger.db.header import parse_header
from page_ledger.db.pages import varint_to_signed
from page_ledger.report import write_json_report
from page_ledger.audit.verify import verify_report
def _codes(report):
    return {item["code"] for item in report["findings"]}
def _findings(report, code):
    return [item for item in report["findings"] if item["code"] == code]
def _table_walk(report, root_page):
    return next(item for item in report["traversal"]["root_walks"] if item["root_page"] == root_page)
def _root(report, name):
    return next(item["root_page"] for item in report["schema"]["objects"] if item["name"] == name)
def test_signed_varint_semantics():
    assert varint_to_signed(0) == 0
    assert varint_to_signed(9223372036854775807) == 9223372036854775807
    assert varint_to_signed(0x8000_0000_0000_0000) == -9223372036854775808
    assert varint_to_signed(0xFFFF_FFFF_FFFF_FFFF) == -1
    # Only the low 64 bits are meaningful; wider accumulator values wrap.
    assert varint_to_signed((1 << 64) + 5) == 5
def test_legit_multilevel_tree_is_verified(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["keyorder"]))
    assert report["status"] == "complete"
    assert report["conclusion"] == "ok"
    ranged = _table_walk(report, _root(report, "ranged"))
    keyed = _table_walk(report, _root(report, "keyed"))
    assert ranged["max_depth"] >= 2 and ranged["complete"]
    assert ranged["key_order"]["status"] == "verified"
    assert keyed["key_order"]["status"] == "verified"
    assert ranged["key_order"]["violations"] == 0
    assert "BTREE_ROWID_OUT_OF_PARTITION" not in _codes(report)
    assert "BTREE_SEPARATOR_MAX_MISMATCH" not in _codes(report)
    assert "BTREE_KEY_ORDER_INCOMPLETE" not in _codes(report)
def test_cross_separator_swap_is_detected_at_parent_and_child(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["keyorder_swap"]))
    ranged_root = _root(report, "ranged")
    walk = _table_walk(report, ranged_root)
    assert walk["complete"] is True
    assert walk["key_order"]["status"] == "violated"
    hits = _findings(report, "BTREE_ROWID_OUT_OF_PARTITION")
    assert hits, "swapped keys must violate the ancestor separator partition"
    # Evidence must locate the parent page, the separator and the child min/max.
    parent_pages = {item["page"] for item in hits}
    assert ranged_root in parent_pages or any(item["page"] != ranged_root for item in hits)
    evidence = " ".join(item["evidence"] for item in hits)
    assert "分隔键" in evidence and "范围" in evidence
    assert report["status"] == "complete"
    assert report["conclusion"] == "error"
    # Everything structural still holds: all pages reachable, no cycles.
    assert "BTREE_CYCLE" not in _codes(report)
    assert "BTREE_CHILD_OUT_OF_RANGE" not in _codes(report)
    assert report["summary"]["file"]["orphan_pages"] == 0
    # The legitimate `keyed` tree in the same file stays verified.
    assert _table_walk(report, _root(report, "keyed"))["key_order"]["status"] == "verified"
def test_separator_scramble_is_detected(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["keyorder_separator"]))
    codes = _codes(report)
    assert "BTREE_SEPARATOR_ORDER_INVALID" in codes
    assert "BTREE_SEPARATOR_MAX_MISMATCH" in codes
    root = _root(report, "keyed")
    mismatch = _findings(report, "BTREE_SEPARATOR_MAX_MISMATCH")[0]
    assert mismatch["page"] == root
    assert int(mismatch["expected"]) < int(mismatch["actual"]) or int(mismatch["actual"]) > int(mismatch["expected"])
    assert _table_walk(report, root)["key_order"]["status"] == "violated"
def test_index_tree_is_marked_unverified_not_passed(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["keyorder"]))
    index_root = _root(report, "idx_keyed_tag")
    walk = _table_walk(report, index_root)
    assert walk["expected_kind"] == "index"
    assert walk["key_order"]["status"] == "not-applicable"
    assert walk["key_order"]["applicable"] is False
    assert "collation" in walk["key_order"]["note"]
    unverified = _findings(report, "BTREE_INDEX_KEY_ORDER_UNVERIFIED")
    assert any(item["page"] == index_root for item in unverified)
    assert all(item["level"] == "info" for item in unverified)
def test_partial_traversal_never_claims_order(fixture_paths):
    report = AuditEngine(AuditConfig(max_pages=20)).run(str(fixture_paths["keyorder"]))
    assert report["status"] == "partial"
    assert "AUDIT_PARTIAL_RESULT" in _codes(report)
    assert "BTREE_KEY_ORDER_INCOMPLETE" in _codes(report)
    for item in report["traversal"]["root_walks"]:
        if not item["complete"] and item["expected_kind"] == "table":
            assert item["key_order"]["status"] == "undetermined"
            assert item["key_order"]["undetermined"]
            assert all("reason" in entry for entry in item["key_order"]["undetermined"])
def test_out_of_range_child_leaves_range_undetermined(copy_db):
    path = copy_db("keyorder")
    data = bytearray(path.read_bytes())
    header = parse_header(bytes(data), len(data))
    root = next(page_no for page_no in range(2, header.actual_page_count + 1) if data[(page_no - 1) * header.page_size] == 0x05)
    offset = (root - 1) * header.page_size
    data[offset + 8 : offset + 12] = (header.actual_page_count + 9).to_bytes(4, "big")
    path.write_bytes(data)
    report = AuditEngine().run(str(path))
    assert "BTREE_CHILD_OUT_OF_RANGE" in _codes(report)
    assert "BTREE_KEY_ORDER_INCOMPLETE" in _codes(report)
    assert report["status"] == "partial"
    walk = _table_walk(report, root)
    assert walk["key_order"]["status"] == "undetermined"
    assert walk["key_order"]["violations"] == 0
def test_key_order_findings_are_stable_and_replayed(fixture_paths, tmp_path):
    from page_ledger.audit.model import LEVEL_ORDER
    report = AuditEngine().run(str(fixture_paths["keyorder_swap"]))
    keys = [(-LEVEL_ORDER[item["level"]], item["code"], item["page"] if item["page"] is not None else -1, item["field"]) for item in report["findings"]]
    assert keys == sorted(keys)
    assert report == AuditEngine().run(str(fixture_paths["keyorder_swap"]))
    target = write_json_report(report, tmp_path)
    result = verify_report(str(target), str(fixture_paths["keyorder_swap"]))
    assert result.valid is True
    assert result.replay["findings"] == report["findings"]
def test_report_summary_includes_key_order_stats(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["keyorder_swap"]))
    walks = {item["root_page"]: item["key_order"] for item in report["traversal"]["root_walks"]}
    violated = [item for item in walks.values() if item["status"] == "violated"]
    assert violated and all(item["violations"] > 0 for item in violated)
    stats = {item["code"]: item for item in report["rule_stats"]}
    assert stats["BTREE_ROWID_OUT_OF_PARTITION"]["hits"] >= 1
    assert stats["BTREE_ROWID_OUT_OF_PARTITION"]["max_level"] == "error"
    assert report["summary"]["finding_counts"]["error"] >= 1
