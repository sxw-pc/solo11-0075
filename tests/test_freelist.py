from __future__ import annotations
from page_ledger.audit.engine import AuditEngine
from page_ledger.db.freelist import audit_freelist
from page_ledger.db.header import parse_header
from page_ledger.io.reader import PageReader
from page_ledger.io.snapshot import load_snapshot
def _codes(report):
    return {item["code"] for item in report["findings"]}
def _load(path):
    data = bytearray(path.read_bytes())
    header = parse_header(bytes(data), len(data))
    return data, header
def test_clean_freelist_fixture(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["freelist"]))
    section = report["freelist"]
    assert section["actual_count"] > 0
    assert section["actual_count"] == section["declared_count"]
    assert section["trunk_count"] > 0 and section["leaf_count"] > 0
    assert section["completed"] is True
    assert not {code for code in _codes(report) if code.startswith("FREE_")}
def test_freelist_count_mismatch(copy_db):
    path = copy_db("freelist")
    data, _ = _load(path)
    declared = int.from_bytes(data[36:40], "big")
    data[36:40] = (declared + 1).to_bytes(4, "big")
    path.write_bytes(data)
    assert "FREE_COUNT_MISMATCH" in _codes(AuditEngine().run(str(path)))
def test_freelist_cycle_fixture(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["freelist_cycle"]))
    codes = _codes(report)
    assert "FREE_TRUNK_CYCLE" in codes
    assert "FREE_COUNT_MISMATCH" in codes
    assert report["freelist"]["completed"] is False
    assert report["status"] == "partial"
def test_duplicate_and_out_of_range_leaf(copy_db):
    path = copy_db("freelist")
    data, header = _load(path)
    first_trunk = int.from_bytes(data[32:36], "big")
    offset = (first_trunk - 1) * header.page_size
    leaf_count = int.from_bytes(data[offset + 4 : offset + 8], "big")
    assert leaf_count >= 2
    first_leaf = data[offset + 8 : offset + 12]
    data[offset + 12 : offset + 16] = first_leaf
    path.write_bytes(data)
    assert "FREE_LEAF_DUPLICATE" in _codes(AuditEngine().run(str(path)))
    data[offset + 8 : offset + 12] = (header.actual_page_count + 20).to_bytes(4, "big")
    path.write_bytes(data)
    assert "FREE_LEAF_OUT_OF_RANGE" in _codes(AuditEngine().run(str(path)))
def test_first_trunk_variants(copy_db):
    path = copy_db("freelist")
    data, header = _load(path)
    data[32:36] = (0).to_bytes(4, "big")
    path.write_bytes(data)
    assert "FREE_FIRST_TRUNK_INVALID" in _codes(AuditEngine().run(str(path)))
    data[32:36] = (header.actual_page_count + 30).to_bytes(4, "big")
    path.write_bytes(data)
    assert "FREE_TRUNK_OUT_OF_RANGE" in _codes(AuditEngine().run(str(path)))
def test_freelist_parser_direct(fixture_paths):
    snapshot = load_snapshot(fixture_paths["freelist"])
    header = parse_header(snapshot.data, snapshot.size)
    result = audit_freelist(PageReader(snapshot, header.page_size), header)
    assert result.free_pages == sorted(result.free_pages)
    assert len(result.free_pages) == result.declared_count
