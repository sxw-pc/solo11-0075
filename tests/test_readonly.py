from __future__ import annotations
import shutil
from pathlib import Path
from page_ledger.audit.engine import AuditEngine
from page_ledger.util.hashing import sha256_file
def _state(path: Path):
    stat = path.stat()
    return sha256_file(str(path)), stat.st_size, stat.st_mtime_ns
def test_audit_does_not_change_database(tmp_path, fixture_paths):
    db = tmp_path / "snapshot.db"
    shutil.copyfile(fixture_paths["clean"], db)
    before = _state(db)
    directory_before = sorted(item.name for item in tmp_path.iterdir())
    report = AuditEngine().run(str(db))
    after = _state(db)
    assert before == after
    assert sorted(item.name for item in tmp_path.iterdir()) == directory_before
    assert not (tmp_path / "snapshot.db-wal").exists()
    assert not (tmp_path / "snapshot.db-shm").exists()
    assert report["snapshot"]["read_only"] is True
def test_wal_hint_is_read_only(tmp_path, fixture_paths):
    db = tmp_path / "wal.db"
    shutil.copyfile(fixture_paths["clean"], db)
    wal = tmp_path / "wal.db-wal"
    wal.write_bytes(b"synthetic-unmerged-wal-frame-not-a-real-wal")
    before_db, before_wal = _state(db), _state(wal)
    report = AuditEngine().run(str(db))
    assert report["snapshot"]["wal_present"] is True
    assert report["snapshot"]["wal_name"] == "wal.db-wal"
    assert "HDR_WAL_PRESENT" in {item["code"] for item in report["findings"]}
    assert any("WAL" in item for item in report["limitations"])
    assert _state(db) == before_db
    assert _state(wal) == before_wal
def test_source_has_no_network_clients():
    root = Path(__file__).resolve().parents[1] / "src"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    for token in ("requests", "urllib.request", "http.client", "socket.socket", "urllib3"):
        assert token not in text
