"""Read-only verification of an existing JSON report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import AuditConfig, ConfigError
from ..io.snapshot import SnapshotError, load_snapshot
from ..report.json_report import ReportError, load_report
from ..util.hashing import canonical_digest, sha256_file
from .engine import AuditEngine


class VerificationError(ValueError):
    """Raised when verification cannot be attempted."""
@dataclass
class VerifyResult:
    valid: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    replay: dict[str, Any] | None = None
    def add(self, name: str, ok: bool, expected: Any = None, actual: Any = None, detail: str = "") -> None:
        self.checks.append(
            {
                "check": name,
                "ok": bool(ok),
                "expected": expected,
                "actual": actual,
                "detail": detail,
            }
        )
    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "checks": self.checks,
            "report_conclusion": self.report.get("conclusion"),
            "report_status": self.report.get("status"),
            "replay_conclusion": self.replay.get("conclusion") if self.replay else None,
        }
def _core(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report.get(key)
        for key in (
            "status",
            "conclusion",
            "header",
            "summary",
            "pages",
            "findings",
            "freelist",
            "overflow",
            "schema",
            "traversal",
        )
    }
def verify_report(report_path: str, db_path: str) -> VerifyResult:
    result = VerifyResult()
    try:
        report = load_report(report_path)
    except ReportError as exc:
        raise VerificationError(str(exc)) from exc
    result.report = report
    required = {"report_version", "snapshot", "header", "summary", "findings", "integrity"}
    missing = sorted(required - set(report))
    result.add("report_shape", not missing, "required keys", sorted(report.keys()), f"missing={missing}")
    if missing:
        result.valid = False
        return result
    recorded_digest = report.get("integrity", {}).get("digest")
    payload = {key: value for key, value in report.items() if key != "integrity"}
    computed_digest = canonical_digest(payload)
    result.add("report_integrity_digest", recorded_digest == computed_digest, recorded_digest, computed_digest, "报告自摘要")
    try:
        snapshot = load_snapshot(db_path)
        current_sha = sha256_file(db_path)
    except (SnapshotError, OSError) as exc:
        raise VerificationError(f"无法读取原始数据库: {exc}") from exc
    expected_snapshot = report.get("snapshot", {})
    result.add("file_sha256", expected_snapshot.get("sha256") == current_sha, expected_snapshot.get("sha256"), current_sha, "输入文件未变化")
    result.add("file_size", expected_snapshot.get("size") == snapshot.size, expected_snapshot.get("size"), snapshot.size, "文件大小一致")
    report_path_value = Path(str(expected_snapshot.get("path", ""))).resolve()
    supplied_path = Path(db_path).resolve()
    same_path = str(report_path_value).casefold() == str(supplied_path).casefold()
    result.add("snapshot_path", same_path, str(report_path_value), str(supplied_path), "报告与原始数据库路径一致")
    config_values = report.get("config", {})
    try:
        config = AuditConfig.from_mapping(config_values)
        result.add("config_valid", True, "合法配置", "valid", "报告中的阈值可重建")
    except ConfigError as exc:
        config = AuditConfig()
        result.add("config_valid", False, "合法配置", str(exc), "无法按报告配置重放")
    try:
        replay = AuditEngine(config).run(db_path)
    except Exception as exc:  # keep verification deterministic and non-crashing
        result.add("audit_replay", False, "可重放", f"{type(exc).__name__}: {exc}", "重放审计失败")
        result.valid = False
        return result
    result.replay = replay
    result.add(
        "page_size",
        report.get("header", {}).get("page_size") == replay.get("header", {}).get("page_size"),
        report.get("header", {}).get("page_size"),
        replay.get("header", {}).get("page_size"),
        "页大小与重放一致",
    )
    result.add(
        "core_replay",
        _core(report) == _core(replay),
        canonical_digest(_core(report)),
        canonical_digest(_core(replay)),
        "报告核心内容与只读重放一致",
    )
    result.add(
        "header_replay",
        _core(report).get("header") == replay.get("header"),
        canonical_digest(report.get("header")),
        canonical_digest(replay.get("header")),
        "头部解析可复现",
    )
    result.add(
        "page_ledger_replay",
        _core(report).get("pages") == replay.get("pages"),
        canonical_digest(report.get("pages")),
        canonical_digest(replay.get("pages")),
        "页账可复现",
    )
    result.add(
        "findings_replay",
        _core(report).get("findings") == replay.get("findings"),
        canonical_digest(report.get("findings")),
        canonical_digest(replay.get("findings")),
        "异常列表可复现",
    )
    result.add(
        "summary_replay",
        _core(report).get("summary") == replay.get("summary"),
        canonical_digest(report.get("summary")),
        canonical_digest(replay.get("summary")),
        "汇总与 top 页可复现",
    )
    replay_sha = replay.get("snapshot", {}).get("sha256")
    result.add("replay_sha256", replay_sha == current_sha, current_sha, replay_sha, "重放快照哈希一致")
    result.valid = all(item["ok"] for item in result.checks)
    return result
