from __future__ import annotations
import hashlib
import json
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "clean.db",
    "freelist.db",
    "overflow.db",
    "indexed.db",
    "broken_header.db",
    "bad_cell_ptr.db",
    "freelist_cycle.db",
    "overflow_break.db",
    "keyorder.db",
    "keyorder_swap.db",
    "keyorder_separator.db",
}
def _hashes(directory: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.glob("*.db")}
def test_generated_fixtures_and_manifest(fixtures_dir):
    assert {path.name for path in fixtures_dir.glob("*.db")} == EXPECTED
    manifest = json.loads((fixtures_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["page_size"] == 1024
    entries = {item["name"]: item for item in manifest["fixtures"]}
    assert set(entries) == EXPECTED
    for name, hash_value in _hashes(fixtures_dir).items():
        assert entries[name]["sha256"] == hash_value
        assert entries[name]["synthetic"] is True
    assert (fixtures_dir / "clean.db").read_bytes()[:16] == b"SQLite format 3\x00"
    assert (fixtures_dir / "broken_header.db").read_bytes()[:16] != b"SQLite format 3\x00"
def test_fixture_generation_is_repeatable(tmp_path, fixtures_dir):
    result = subprocess.run(
        [sys.executable, str(ROOT / "fixtures" / "build_fixtures.py"), "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "clean.db" in result.stdout
    assert _hashes(tmp_path) == _hashes(fixtures_dir)
