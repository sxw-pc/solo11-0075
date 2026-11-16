"""Command line interface for page-ledger."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .audit.diff import compare_snapshots, diff_exit_code, write_diff
from .audit.engine import AuditEngine
from .audit.model import LEVEL_ORDER
from .audit.verify import VerificationError, verify_report
from .config import ConfigError, load_config
from .io.snapshot import SnapshotError
from .report import write_reports
from .store.audit_store import utc_now, write_audit_store


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="page-ledger",
        description="只读 SQLite 页账核验台：页类型、B 树、freelist、溢出链与空间账本审计。",
    )
    parser.add_argument("--version", action="version", version=f"page-ledger {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="审计一个 SQLite 数据库文件快照")
    audit.add_argument("--db", required=True, help="本地 SQLite 文件路径（只读）")
    audit.add_argument("--out", required=True, help="报告输出目录")
    audit.add_argument("--config", help="JSON 阈值配置文件")
    audit.add_argument("--ledger", help="审计台账 SQLite 路径，默认 <out>/audit-ledger.sqlite")
    audit.add_argument(
        "--fail-on",
        choices=("warning", "error", "critical"),
        help="达到该级别时以退出码 1 结束（默认仅生成报告）",
    )
    verify = commands.add_parser("verify", help="只读复核报告与原始数据库")
    verify.add_argument("--report", required=True, help="report.json 路径")
    verify.add_argument("--db", required=True, help="原始 SQLite 文件路径")
    verify.add_argument("--json", action="store_true", help="以 JSON 输出复核结果")
    diff = commands.add_parser("diff", help="比较两个已验证快照的物理页槽与 schema 对象迁移")
    diff.add_argument("--report-a", required=True, help="A 侧 report.json 路径（旧快照）")
    diff.add_argument("--db-a", required=True, help="A 侧原始 SQLite 文件路径（旧快照）")
    diff.add_argument("--report-b", required=True, help="B 侧 report.json 路径（新快照）")
    diff.add_argument("--db-b", required=True, help="B 侧原始 SQLite 文件路径（新快照）")
    diff.add_argument("--out", required=True, help="差异输出目录，写入 diff.json 和 diff.md")
    diff.add_argument("--json", action="store_true", help="以 JSON 输出差异摘要")
    return parser
def _normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))
def _check_output_paths(db_path: str, out_dir: Path, ledger: Path) -> None:
    db_key = _normalized(Path(db_path))
    reports = {name: out_dir / name for name in ("report.json", "report.md", "report.html")}
    outputs = {**reports, "audit-ledger.sqlite": ledger}
    for name, path in outputs.items():
        if _normalized(path) == db_key:
            raise ValueError(f"输出 {name} 不得覆盖被审计数据库")
    report_keys = {_normalized(path) for path in reports.values()}
    if _normalized(ledger) in report_keys:
        raise ValueError("审计台账路径不得与报告文件相同")
def _run_audit(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    started = utc_now()
    report = AuditEngine(config).run(args.db)
    out_dir = Path(args.out)
    ledger = Path(args.ledger) if args.ledger else out_dir / "audit-ledger.sqlite"
    _check_output_paths(args.db, out_dir, ledger)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = write_reports(report, out_dir)
    run_id = write_audit_store(report, ledger, started_at=started, finished_at=utc_now())
    counts = report["summary"]["finding_counts"]
    print(f"数据库: {report['snapshot']['display_name']}")
    print(f"状态: {report['status']}  结论: {report['conclusion']}")
    print(
        "异常: critical={critical} error={error} warning={warning} info={info}".format(**counts)
    )
    print(f"JSON: {paths['json']}")
    print(f"Markdown: {paths['markdown']}")
    print(f"HTML: {paths['html']}")
    print(f"台账: {ledger} (run_id={run_id})")
    if args.fail_on:
        level = report["conclusion"] if report["conclusion"] in LEVEL_ORDER else "info"
        if LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER[args.fail_on]:
            return 1
    return 0
def _run_verify(args: argparse.Namespace) -> int:
    result = verify_report(args.report, args.db)
    if args.json:
        import json
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"复核结果: {'通过' if result.valid else '失败'}")
        print(f"报告结论: {result.report.get('conclusion')} / 状态: {result.report.get('status')}")
        for item in result.checks:
            mark = "OK" if item["ok"] else "FAIL"
            print(f"[{mark}] {item['check']}: {item['detail']}")
            if not item["ok"]:
                print(f"       expected={item['expected']!r}")
                print(f"       actual={item['actual']!r}")
    return 0 if result.valid else 1
def _check_diff_output_paths(out_dir: Path, inputs: Sequence[str]) -> None:
    protected = {_normalized(Path(path)) for path in inputs}
    for name in ("diff.json", "diff.md"):
        if _normalized(out_dir / name) in protected:
            raise ValueError(f"差异输出 {name} 不得覆盖输入报告或数据库")
def _run_diff(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    inputs = (args.report_a, args.db_a, args.report_b, args.db_b)
    _check_diff_output_paths(out_dir, inputs)
    result = compare_snapshots(args.report_a, args.db_a, args.report_b, args.db_b)
    paths = write_diff(result, out_dir)
    code = diff_exit_code(result)
    if args.json:
        import json
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"比较状态: {result['status']}  结论: {result['conclusion']}")
        print(f"完整范围: {result['scope']['full']}  受限原因: {len(result['scope']['reasons'])}")
        if result["scope"]["reasons"]:
            for reason in result["scope"]["reasons"]:
                print(f"  - {reason}")
        physical = result["page_view"]["summary"]
        if physical:
            print(
                "页槽: changed={changed} released={released} allocated={allocated} unknown={unknown}".format(**physical)
            )
        logical = result["object_view"]["summary"]
        if logical:
            print(
                "对象: added={added} removed={removed} changed={changed} root_moved={root_moved}".format(**logical)
            )
        print(f"JSON: {paths['json']}")
        print(f"Markdown: {paths['markdown']}")
    return code
def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            return _run_audit(args)
        if args.command == "verify":
            return _run_verify(args)
        if args.command == "diff":
            return _run_diff(args)
    except (ConfigError, SnapshotError, VerificationError, OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    parser.error("未知命令")
    return 2
