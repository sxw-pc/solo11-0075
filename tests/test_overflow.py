from __future__ import annotations
from pathlib import Path
from page_ledger.audit.engine import AuditEngine
from page_ledger.db.header import parse_header
def _codes(report):
    return {item["code"] for item in report["findings"]}
def _load(path: Path):
    data = bytearray(path.read_bytes())
    header = parse_header(bytes(data), len(data))
    return data, header
def _first_chain(report):
    return next(chain for chain in report["overflow"]["chains"] if chain["actual_pages"] >= 2)
def test_normal_overflow_chains(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["overflow"]))
    section = report["overflow"]
    assert section["page_count"] > 0
    assert len(section["chains"]) == 4
    assert section["complete"] is True
    assert section["incomplete_chains"] == 0
    assert not {code for code in _codes(report) if code.startswith("OVFL_")}
    for chain in section["chains"]:
        assert chain["expected_pages"] == chain["actual_pages"]
        assert len(chain["pages"]) == chain["actual_pages"]
def test_broken_overflow_fixture(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["overflow_break"]))
    codes = _codes(report)
    assert "OVFL_PAGE_OUT_OF_RANGE" in codes
    assert "OVFL_INCOMPLETE" in codes
    assert report["overflow"]["incomplete_chains"] == 1
    assert report["summary"]["file"]["orphan_pages"] > 0
    assert report["status"] == "partial"
def test_overflow_cycle(copy_db, fixture_paths):
    path = copy_db("overflow")
    report = AuditEngine().run(str(fixture_paths["overflow"]))
    chain = _first_chain(report)
    data, header = _load(path)
    last = chain["pages"][-1]
    offset = (last - 1) * header.page_size
    data[offset : offset + 4] = chain["first_page"].to_bytes(4, "big")
    path.write_bytes(data)
    broken = AuditEngine().run(str(path))
    assert "OVFL_CHAIN_CYCLE" in _codes(broken)
    assert broken["overflow"]["incomplete_chains"] >= 1
def test_overflow_length_mismatch(copy_db, fixture_paths):
    path = copy_db("overflow")
    report = AuditEngine().run(str(fixture_paths["overflow"]))
    chain = _first_chain(report)
    data, header = _load(path)
    new_page = header.actual_page_count + 1
    last = chain["pages"][-1]
    offset = (last - 1) * header.page_size
    data[offset : offset + 4] = new_page.to_bytes(4, "big")
    data.extend(b"\x00" * header.page_size)
    data[28:32] = new_page.to_bytes(4, "big")
    path.write_bytes(data)
    broken = AuditEngine().run(str(path))
    assert "OVFL_LENGTH_MISMATCH" in _codes(broken)
    chain_after = next(item for item in broken["overflow"]["chains"] if item["first_page"] == chain["first_page"])
    assert chain_after["actual_pages"] == chain_after["expected_pages"] + 1
def test_overflow_duplicate_page(copy_db, fixture_paths):
    path = copy_db("overflow")
    report = AuditEngine().run(str(fixture_paths["overflow"]))
    chains = [chain for chain in report["overflow"]["chains"] if chain["actual_pages"] >= 2]
    first, second = chains[0], chains[1]
    data, header = _load(path)
    last = first["pages"][-1]
    offset = (last - 1) * header.page_size
    data[offset : offset + 4] = second["first_page"].to_bytes(4, "big")
    path.write_bytes(data)
    assert "OVFL_DUPLICATE_PAGE" in _codes(AuditEngine().run(str(path)))
