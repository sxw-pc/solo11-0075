from __future__ import annotations
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def _python_files():
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and ".venv" not in path.parts
    )
def test_each_python_file_at_most_300_lines():
    offenders = {}
    for path in _python_files():
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > 300:
            offenders[str(path.relative_to(ROOT))] = count
    assert offenders == {}
def test_total_python_line_budget():
    total = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in _python_files())
    assert 2500 <= total <= 4500, total
