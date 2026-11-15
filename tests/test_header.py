from __future__ import annotations
import json
from page_ledger.db.header import MAGIC, parse_header
def codes(header) -> set[str]:
    return {problem.code for problem in header.problems}
def test_clean_header_fields(fixture_paths):
    data = fixture_paths["clean"].read_bytes()
    header = parse_header(data, len(data))
    assert header.magic_valid and header.magic.encode("latin-1") == MAGIC
    assert header.page_size == 1024
    assert header.usable_size == 1024
    assert header.reserved_bytes == 0
    assert header.text_encoding == 1 and header.encoding_name == "utf-8"
    assert header.auto_vacuum == 0
    assert header.declared_pages == header.actual_page_count == 159
    assert header.first_freelist_trunk == 0 and header.freelist_pages == 0
    assert header.trailing_bytes == 0
    assert not header.problems
    assert json.loads(json.dumps(header.to_dict()))["page_size"] == 1024
def test_bad_magic(fixture_paths):
    data = fixture_paths["broken_header"].read_bytes()
    header = parse_header(data, len(data))
    assert not header.magic_valid
    assert "HDR_MAGIC_INVALID" in codes(header)
    assert header.page_size == 1024
def test_invalid_page_size(fixture_paths):
    data = bytearray(fixture_paths["clean"].read_bytes())
    data[16:18] = (1234).to_bytes(2, "big")
    header = parse_header(bytes(data), len(data))
    assert header.page_size == 0
    assert "HDR_PAGE_SIZE_INVALID" in codes(header)
    assert not header.can_read_pages
def test_truncated_file(fixture_paths):
    data = fixture_paths["clean"].read_bytes()
    truncated = data[:-1024]
    header = parse_header(truncated, len(truncated))
    assert "HDR_FILE_TRUNCATED" in codes(header)
    assert header.actual_page_count == header.declared_pages - 1
def test_page_count_mismatch(fixture_paths):
    data = bytearray(fixture_paths["clean"].read_bytes())
    data[28:32] = (158).to_bytes(4, "big")
    header = parse_header(bytes(data), len(data))
    assert "HDR_PAGE_COUNT_MISMATCH" in codes(header)
    assert header.declared_pages == 158 and header.actual_page_count == 159
def test_reserved_and_encoding_invalid(fixture_paths):
    data = bytearray(fixture_paths["clean"].read_bytes())
    data[16:18] = (512).to_bytes(2, "big")
    data[20] = 255
    data[56:60] = (9).to_bytes(4, "big")
    header = parse_header(bytes(data), len(data))
    assert "HDR_RESERVED_INVALID" in codes(header)
    assert "HDR_ENCODING_INVALID" in codes(header)
    assert not header.can_read_pages
def test_file_too_small_and_wal_hint(tmp_path):
    header = parse_header(b"short", 5, wal_present=True, wal_name="short.db-wal")
    assert "HDR_FILE_TOO_SMALL" in codes(header)
    assert "HDR_WAL_PRESENT" in codes(header)
    assert header.wal_present and header.wal_name == "short.db-wal"
