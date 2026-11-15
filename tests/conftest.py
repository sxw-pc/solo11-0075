from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from page_ledger.audit.engine import AuditEngine  # noqa: E402
from page_ledger.db.header import parse_header  # noqa: E402
@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    target = ROOT / "fixtures" / "generated"
    subprocess.run(
        [sys.executable, str(ROOT / "fixtures" / "build_fixtures.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return target
@pytest.fixture(scope="session")
def fixture_paths(fixtures_dir: Path) -> dict[str, Path]:
    names = (
        "clean",
        "freelist",
        "overflow",
        "indexed",
        "broken_header",
        "bad_cell_ptr",
        "freelist_cycle",
        "overflow_break",
    )
    return {name: fixtures_dir / f"{name}.db" for name in names}
@pytest.fixture
def copy_db(tmp_path: Path, fixture_paths: dict[str, Path]):
    def _copy(name: str, new_name: str | None = None) -> Path:
        source = fixture_paths[name]
        target = tmp_path / (new_name or source.name)
        shutil.copyfile(source, target)
        return target
    return _copy
@pytest.fixture(scope="session")
def clean_report(fixture_paths: dict[str, Path]) -> dict:
    return AuditEngine().run(str(fixture_paths["clean"]))
@pytest.fixture(scope="session")
def clean_header(clean_report: dict) -> dict:
    return clean_report["header"]
def load_db_bytes(path: Path) -> tuple[bytearray, dict]:
    data = bytearray(path.read_bytes())
    header = parse_header(bytes(data), len(data))
    return data, header.to_dict()
