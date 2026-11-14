from __future__ import annotations
from page_ledger.audit.engine import AuditEngine
from page_ledger.db.header import parse_header
from page_ledger.db.pages import parse_page
def _context(path):
    data = path.read_bytes()
    header = parse_header(data, len(data))
    return data, header
def _find_page(header, data, raw_type, min_cells=1):
    for page_no in range(1, header.actual_page_count + 1):
        page = parse_page(data, page_no, header.page_size, header.usable_size, header.actual_page_count)
        if page.raw_type == raw_type and page.cell_count >= min_cells:
            return page
    raise AssertionError("page not found")
def _finding_codes(path):
    return {item["code"] for item in AuditEngine().run(str(path))["findings"]}
def test_page1_and_cell_pointers(clean_header, fixture_paths):
    data = fixture_paths["clean"].read_bytes()
    header = parse_header(data, len(data))
    page = parse_page(data, 1, header.page_size, header.usable_size, header.actual_page_count)
    assert page.page_type == "table_leaf"
    assert page.header_offset == 100
    assert page.cell_count >= 1
    assert all(page.content_start <= ptr < page.usable_end for ptr in page.cell_pointers)
    assert page.content_bytes > 0
def test_clean_page_types_cover_table_and_index(clean_report):
    counts = clean_report["traversal"]["page_type_counts"]
    assert counts["table_interior"] > 0
    assert counts["table_leaf"] > 0
    assert counts["index_interior"] > 0
    assert counts["index_leaf"] > 0
def test_bad_cell_pointer_fixture(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["bad_cell_ptr"]))
    codes = {item["code"] for item in report["findings"]}
    assert "PAGE_CELL_PTR_OUT_OF_RANGE" in codes
    finding = next(item for item in report["findings"] if item["code"] == "PAGE_CELL_PTR_OUT_OF_RANGE")
    assert finding["level"] == "error"
    assert finding["page"] is not None
    assert finding["actual"] > 0
def test_page_out_of_range_object():
    data = b"\x00" * 1024
    page = parse_page(data, 9, 1024, 1024, 1)
    assert "PAGE_OUT_OF_RANGE" in {item.code for item in page.problems}
def test_cell_pointer_overlap(copy_db):
    path = copy_db("clean")
    data, header = _context(path)
    target = _find_page(header, data, 0x0D, min_cells=2)
    if target.page_no == 1:
        target = _find_page(header, data, 0x0D, min_cells=2)
    buffer = bytearray(data)
    offset = (target.page_no - 1) * header.page_size
    first = buffer[target.header_end : target.header_end + 2]
    buffer[target.header_end + 2 : target.header_end + 4] = first
    path.write_bytes(buffer)
    assert "PAGE_CELL_PTR_OVERLAP" in _finding_codes(path)
def test_unknown_page_type(copy_db):
    path = copy_db("clean")
    data, header = _context(path)
    target = None
    for page_no in range(2, header.actual_page_count + 1):
        page = parse_page(data, page_no, header.page_size, header.usable_size, header.actual_page_count)
        if page.raw_type == 0x0D:
            target = page
            break
    assert target is not None
    buffer = bytearray(data)
    buffer[(target.page_no - 1) * header.page_size] = 0x42
    path.write_bytes(buffer)
    assert "PAGE_TYPE_UNKNOWN" in _finding_codes(path)
