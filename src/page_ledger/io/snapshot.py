"""Read-only snapshot registration for a single SQLite file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..util.hashing import sha256_bytes


class SnapshotError(OSError):
    """Raised when an input file cannot be snapshotted read-only."""
@dataclass
class FileSnapshot:
    path: str
    display_name: str
    data: bytes
    size: int
    sha256: str
    mtime_utc: str
    wal_present: bool
    wal_name: str | None
    read_only: bool = True
    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "display_name": self.display_name,
            "size": self.size,
            "sha256": self.sha256,
            "mtime_utc": self.mtime_utc,
            "wal_present": self.wal_present,
            "wal_name": self.wal_name,
            "read_only": self.read_only,
        }
def load_snapshot(path: str | Path) -> FileSnapshot:
    target = Path(path)
    if not target.exists():
        raise SnapshotError(f"数据库文件不存在: {target}")
    if not target.is_file():
        raise SnapshotError(f"输入不是普通文件: {target}")
    try:
        stat = target.stat()
        # "rb" is deliberate: the audit never opens the database for writing.
        with open(target, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise SnapshotError(f"无法只读打开数据库文件: {exc}") from exc
    if not data:
        raise SnapshotError(f"数据库文件为空: {target}")
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    wal = Path(str(target) + "-wal")
    try:
        wal_present = wal.is_file()
    except OSError:
        wal_present = False
    return FileSnapshot(
        path=str(target.resolve()),
        display_name=target.name,
        data=data,
        size=len(data),
        sha256=sha256_bytes(data),
        mtime_utc=mtime.isoformat(timespec="seconds"),
        wal_present=wal_present,
        wal_name=wal.name if wal_present else None,
    )
