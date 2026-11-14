"""Human-review Markdown report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..util.ordering import write_text


def _text(value: Any, limit: int = 220) -> str:
    rendered = "" if value is None else str(value)
    rendered = rendered.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    if len(rendered) > limit:
        rendered = rendered[: limit - 1] + "…"
    return rendered
def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_text(item) for item in row) + " |")
    return "\n".join(lines)
def render_markdown(report: dict[str, Any]) -> str:
    file_summary = report.get("summary", {}).get("file", {})
    header = report.get("header", {})
    snapshot = report.get("snapshot", {})
    findings = report.get("findings", [])
    lines = [
        "# 页账核验报告",
        "",
        f"- 结论：**{_text(report.get('conclusion'))}**",
        f"- 审计状态：{_text(report.get('status'))}",
        f"- 报告版本：{_text(report.get('report_version'))}",
        f"- 工具：{_text(report.get('tool', {}).get('name'))} {_text(report.get('tool', {}).get('version'))}",
        "",
        "## 文件快照",
        "",
        _table(
            ["字段", "值"],
            [
                ["文件名", snapshot.get("display_name")],
                ["大小", snapshot.get("size")],
                ["sha256", snapshot.get("sha256")],
                ["mtime (UTC)", snapshot.get("mtime_utc")],
                ["只读标记", snapshot.get("read_only")],
                ["WAL 存在", snapshot.get("wal_present")],
            ],
        ),
        "",
        "## 关键统计",
        "",
        _table(
            ["指标", "值"],
            [
                ["页大小 / usable", f"{header.get('page_size')} / {header.get('usable_size')}"],
                ["声明页数 / 实际页数", f"{file_summary.get('declared_pages')} / {file_summary.get('actual_pages')}"],
                ["可达页 / B 树页", f"{file_summary.get('reachable_pages')} / {file_summary.get('tree_pages')}"],
                ["溢出页 / freelist 页", f"{file_summary.get('overflow_pages')} / {file_summary.get('freelist_pages')}"],
                ["孤儿页 / 未知类型页", f"{file_summary.get('orphan_pages')} / {file_summary.get('unknown_page_type_pages')}"],
                ["越界引用事件", file_summary.get("out_of_range_references")],
                ["schema 对象", file_summary.get("schema_objects")],
                ["页数一致", file_summary.get("page_count_matches_file")],
            ],
        ),
        "",
        "## 页类型账本",
        "",
    ]
    page_rows = []
    for row in report.get("summary", {}).get("pages", {}).get("by_page_type", []):
        page_rows.append(
            [
                row.get("page_type"),
                row.get("page_count"),
                row.get("cell_count"),
                row.get("used_bytes"),
                row.get("free_bytes"),
                row.get("fragmented_bytes"),
                row.get("reserved_bytes"),
            ]
        )
    lines.append(_table(["页类型", "页数", "cell 数", "已用字节", "空闲字节", "碎片字节", "保留字节"], page_rows))
    lines.extend(["", "## 异常表", ""])
    if findings:
        rows = [
            [
                item.get("level"),
                item.get("code"),
                item.get("page"),
                item.get("field"),
                item.get("expected"),
                item.get("actual"),
                item.get("evidence"),
            ]
            for item in findings
        ]
        lines.append(_table(["级别", "错误码", "页", "字段", "期望", "实际", "证据"], rows))
    else:
        lines.append("未发现异常。")
    lines.extend(["", "## Top 碎片页", ""])
    frag = report.get("summary", {}).get("top_pages", {}).get("fragmentation", [])
    if frag:
        lines.append(
            _table(
                ["页", "类型", "碎片字节", "碎片率", "cell 数"],
                [[item.get("page_no"), item.get("page_type"), item.get("fragmented_bytes"), item.get("fragmentation_ratio"), item.get("cell_count")] for item in frag],
            )
        )
    else:
        lines.append("无。")
    lines.extend(["", "## Top 低利用率页", ""])
    low = report.get("summary", {}).get("top_pages", {}).get("low_utilization", [])
    if low:
        lines.append(
            _table(
                ["页", "类型", "利用率", "已用 cell 字节", "空闲字节"],
                [[item.get("page_no"), item.get("page_type"), item.get("utilization"), item.get("cell_bytes"), item.get("free_bytes")] for item in low],
            )
        )
    else:
        lines.append("无。")
    lines.extend(["", "## Schema 对账", ""])
    objects = report.get("schema", {}).get("objects", [])
    if objects:
        lines.append(
            _table(
                ["类型", "名称", "根页", "已遍历", "页类型", "页数", "cell 数", "深度"],
                [
                    [
                        item.get("object_type"),
                        item.get("name"),
                        item.get("root_page"),
                        item.get("traversed"),
                        item.get("page_type"),
                        item.get("page_count"),
                        item.get("cell_count"),
                        item.get("depth"),
                    ]
                    for item in objects
                ],
            )
        )
    else:
        lines.append("没有可核对的表或索引对象。")
    sidecar = report.get("schema", {}).get("sidecar", {})
    lines.extend(
        [
            "",
            f"只读旁证：{'可用' if sidecar.get('available') else '不可用'}；"
            f"旁证 page_count={sidecar.get('page_count')}；旁证对象={sidecar.get('object_count')}。",
            "",
            "## 限制",
            "",
        ]
    )
    for item in report.get("limitations", []):
        lines.append(f"- {_text(item, 400)}")
    lines.extend(
        [
            "",
            "---",
            "",
            "本报告由 page-ledger 生成，只读审计，不修复数据库。",
            "",
        ]
    )
    return "\n".join(lines)
def write_markdown_report(report: dict[str, Any], out_dir: str | Path) -> Path:
    target = Path(out_dir) / "report.md"
    write_text(target, render_markdown(report))
    return target
