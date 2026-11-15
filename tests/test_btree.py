from __future__ import annotations
from page_ledger.audit.engine import AuditEngine
from page_ledger.config import AuditConfig
from page_ledger.db.header import parse_header
from page_ledger.db.pages import parse_page
def _load(path):
    data = bytearray(path.read_bytes())
    header = parse_header(bytes(data), len(data))
    return data, header
def _interiors(header, data):
    found = []
    for page_no in range(1, header.actual_page_count + 1):
        page = parse_page(data, page_no, header.page_size, header.usable_size, header.actual_page_count)
        if page.raw_type in (0x02, 0x05):
            found.append(page)
    return found
def _codes(report):
    return {item["code"] for item in report["findings"]}
def test_clean_traversal_is_complete(clean_report):
    assert clean_report["status"] == "complete"
    assert clean_report["traversal"]["max_depth"] >= 1
    assert clean_report["schema"]["parsed_root_pages"] == clean_report["schema"]["walked_root_pages"]
    assert clean_report["summary"]["file"]["orphan_pages"] == 0
    assert "BTREE_CYCLE" not in _codes(clean_report)
    assert "BTREE_DUPLICATE_REFERENCE" not in _codes(clean_report)
def test_child_out_of_range(copy_db):
    path = copy_db("clean")
    data, header = _load(path)
    target = _interiors(header, data)[0]
    offset = (target.page_no - 1) * header.page_size
    bad_child = header.actual_page_count + 10
    data[offset + 8 : offset + 12] = bad_child.to_bytes(4, "big")
    path.write_bytes(data)
    report = AuditEngine().run(str(path))
    assert "BTREE_CHILD_OUT_OF_RANGE" in _codes(report)
    assert "PAGE_HEADER_INVALID" in _codes(report)
def test_cycle_detection(copy_db):
    path = copy_db("clean")
    data, header = _load(path)
    target = _interiors(header, data)[0]
    offset = (target.page_no - 1) * header.page_size
    data[offset + 8 : offset + 12] = target.page_no.to_bytes(4, "big")
    path.write_bytes(data)
    assert "BTREE_CYCLE" in _codes(AuditEngine().run(str(path)))
def test_duplicate_child_reference(copy_db):
    path = copy_db("clean")
    data, header = _load(path)
    target = next(page for page in _interiors(header, data) if page.cell_count and page.right_child)
    offset = (target.page_no - 1) * header.page_size
    cell_pointer = int.from_bytes(data[target.header_end : target.header_end + 2], "big")
    data[offset + cell_pointer : offset + cell_pointer + 4] = target.right_child.to_bytes(4, "big")
    path.write_bytes(data)
    assert "BTREE_DUPLICATE_REFERENCE" in _codes(AuditEngine().run(str(path)))
def test_depth_threshold_and_orphans(fixture_paths):
    report = AuditEngine(AuditConfig(max_btree_depth=0)).run(str(fixture_paths["clean"]))
    assert "BTREE_DEPTH_ANOMALY" in _codes(report)
    broken = AuditEngine().run(str(fixture_paths["overflow_break"]))
    assert "BTREE_UNREACHABLE_PAGE" in _codes(broken)
    assert broken["summary"]["file"]["orphan_pages"] > 0
