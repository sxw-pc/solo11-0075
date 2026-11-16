"""Self-contained HTML report with inline CSS and escaped text."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from ..util.ordering import write_text

STYLE = """
:root{color-scheme:light dark;--bg:#f6f7f9;--panel:#fff;--ink:#17202a;--muted:#5d6d7e;--line:#d5dbdb;--accent:#17506b}
@media (prefers-color-scheme:dark){:root{--bg:#14181c;--panel:#1d242b;--ink:#edf2f7;--muted:#aab7c4;--line:#34414d;--accent:#8ac7df}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 20px 60px}h1,h2{line-height:1.25}h1{font-size:26px;margin:0 0 8px}h2{font-size:19px;margin:32px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.meta{color:var(--muted)}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0;overflow:auto}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;border:1px solid var(--line);font-weight:600}
.critical{color:#b03a2e}.error{color:#c0392b}.warning{color:#9a6700}.info{color:var(--accent)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;white-space:nowrap}code{font-family:ui-monospace,Consolas,monospace;font-size:12px;word-break:break-word}
ul{padding-left:22px}.small{font-size:12px;color:var(--muted)}.empty{color:var(--muted);font-style:italic}
"""


def _e(value: Any, limit: int = 500) -> str:
    rendered = "" if value is None else str(value)
    if len(rendered) > limit:
        rendered = rendered[: limit - 1] + "…"
    return escape(rendered, quote=True)
def _table(headers: list[str], rows: list[list[Any]], classes: list[str] | None = None) -> str:
    out = ["<table><thead><tr>"]
    out.extend(f"<th>{_e(item)}</th>" for item in headers)
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for index, item in enumerate(row):
            cls = f' class="{classes[index]}"' if classes and index < len(classes) and classes[index] else ""
            out.append(f"<td{cls}>{_e(item)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)
def render_html(report: dict[str, Any]) -> str:
    snapshot = report.get("snapshot", {})
    header = report.get("header", {})
    file_summary = report.get("summary", {}).get("file", {})
    level = str(report.get("conclusion", "ok"))
    safe_level = level if level in {"critical", "error", "warning", "info", "ok"} else "info"
    parts = [
        "<!doctype html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>页账核验报告</title>",
        f"<style>{STYLE}</style></head><body><main>",
        "<h1>页账核验报告</h1>",
        f'<p class="meta">结论 <span class="badge {safe_level}">{_e(report.get("conclusion"))}</span> · '
        f'状态 {_e(report.get("status"))} · 版本 {_e(report.get("report_version"))}</p>',
        '<h2>文件快照</h2><div class="panel">',
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
        "</div>",
        '<h2>关键统计</h2><div class="panel">',
        _table(
            ["指标", "值"],
            [
                ["页大小 / usable", f"{header.get('page_size')} / {header.get('usable_size')}"],
                ["声明页 / 实际页", f"{file_summary.get('declared_pages')} / {file_summary.get('actual_pages')}"],
                ["可达页 / B 树页", f"{file_summary.get('reachable_pages')} / {file_summary.get('tree_pages')}"],
                ["溢出页 / freelist 页", f"{file_summary.get('overflow_pages')} / {file_summary.get('freelist_pages')}"],
                ["孤儿页 / 未知类型页", f"{file_summary.get('orphan_pages')} / {file_summary.get('unknown_page_type_pages')}"],
                ["越界引用事件", file_summary.get("out_of_range_references")],
                ["schema 对象", file_summary.get("schema_objects")],
                ["页数一致", file_summary.get("page_count_matches_file")],
            ],
        ),
        "</div>",
        '<h2>页类型账本</h2><div class="panel">',
    ]
    page_rows = [
        [
            row.get("page_type"),
            row.get("page_count"),
            row.get("cell_count"),
            row.get("used_bytes"),
            row.get("free_bytes"),
            row.get("fragmented_bytes"),
            row.get("reserved_bytes"),
        ]
        for row in report.get("summary", {}).get("pages", {}).get("by_page_type", [])
    ]
    parts.append(_table(["页类型", "页数", "cell 数", "已用字节", "空闲字节", "碎片字节", "保留字节"], page_rows))
    parts.append("</div>")
    parts.append('<h2>异常表</h2><div class="panel">')
    findings = report.get("findings", [])
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
        parts.append(_table(["级别", "错误码", "页", "字段", "期望", "实际", "证据"], rows))
    else:
        parts.append('<p class="empty">未发现异常。</p>')
    parts.append("</div>")
    parts.append('<h2>Top 碎片页</h2><div class="panel">')
    frag = report.get("summary", {}).get("top_pages", {}).get("fragmentation", [])
    if frag:
        parts.append(_table(["页", "类型", "碎片字节", "碎片率", "cell 数"], [[item.get("page_no"), item.get("page_type"), item.get("fragmented_bytes"), item.get("fragmentation_ratio"), item.get("cell_count")] for item in frag]))
    else:
        parts.append('<p class="empty">无。</p>')
    parts.append("</div>")
    parts.append('<h2>Top 低利用率页</h2><div class="panel">')
    low = report.get("summary", {}).get("top_pages", {}).get("low_utilization", [])
    if low:
        parts.append(_table(["页", "类型", "利用率", "cell 字节", "空闲字节"], [[item.get("page_no"), item.get("page_type"), item.get("utilization"), item.get("cell_bytes"), item.get("free_bytes")] for item in low]))
    else:
        parts.append('<p class="empty">无。</p>')
    parts.append("</div>")
    parts.append('<h2>键序核验</h2><div class="panel">')
    order = report.get("traversal", {}).get("key_order") or {}
    parts.append(
        '<p class="small">普通 rowid table b-tree 按有符号 64-bit rowid 核验；'
        f'已核验根页 {_e(order.get("verified_roots"))}；违约根页 {_e(order.get("violated_roots"))}；'
        f'未判定根页 {_e(order.get("undetermined_roots"))}。index b-tree 未核验 SQL 排序'
        '（collation/表达式/record 编码），字节序不代表 SQL 顺序。</p>'
    )
    order_rows = [
        [item.get("root_page"), item.get("expected_kind"), item.get("complete"), (item.get("key_order") or {}).get("status"), (item.get("key_order") or {}).get("checked_pages"), (item.get("key_order") or {}).get("violations"), (item.get("key_order") or {}).get("undetermined_count")]
        for item in report.get("traversal", {}).get("root_walks", [])
    ]
    if order_rows:
        parts.append(_table(["根页", "类型", "遍历完整", "键序状态", "已核验页", "违约", "未判定"], order_rows))
    parts.append("</div>")
    parts.append('<h2>Schema 对账</h2><div class="panel">')
    objects = report.get("schema", {}).get("objects", [])
    if objects:
        rows = [[item.get("object_type"), item.get("name"), item.get("root_page"), item.get("traversed"), item.get("page_type"), item.get("page_count"), item.get("cell_count"), item.get("depth")] for item in objects]
        parts.append(_table(["类型", "名称", "根页", "已遍历", "页类型", "页数", "cell 数", "深度"], rows))
    else:
        parts.append('<p class="empty">没有可核对的表或索引对象。</p>')
    sidecar = report.get("schema", {}).get("sidecar", {})
    parts.append(
        f'<p class="small">只读旁证：{"可用" if sidecar.get("available") else "不可用"}；'
        f'旁证 page_count={_e(sidecar.get("page_count"))}；旁证对象={_e(sidecar.get("object_count"))}</p>'
    )
    parts.append("</div>")
    parts.append('<h2>限制</h2><div class="panel"><ul>')
    parts.extend(f"<li>{_e(item, 800)}</li>" for item in report.get("limitations", []))
    parts.append("</ul></div>")
    parts.append('<p class="small">本报告由 page-ledger 生成，只读审计，不修复数据库。报告为自包含文件，无外部资源。</p>')
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"
def write_html_report(report: dict[str, Any], out_dir: str | Path) -> Path:
    target = Path(out_dir) / "report.html"
    write_text(target, render_html(report))
    return target
