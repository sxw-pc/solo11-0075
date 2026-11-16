"""Deterministic Markdown rendering for a cross-snapshot diff."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..report.markdown_report import _table, _text
from ..util.ordering import write_text
from .diff_pages import ownership

SLOT_LIMIT = 50
CONCLUSION_LABEL = {
    "no_change": "无变化",
    "page_rearrangement": "页面重排/迁移",
    "object_membership_changed": "schema 对象增减",
    "mixed": "对象增减且页面/异常变化",
    "layout_and_findings": "页面布局与 finding 均变化",
    "finding_changes": "仅 finding 变化",
    "limited_scope": "范围受限，未发现可确认变化",
    "refused": "拒绝比较",
}
CHANGE_LABEL = {
    "root_page": "根页",
    "page_set": "页集合",
    "reported_page_count": "页数",
    "reported_cell_count": "cell 数",
    "reported_depth": "深度",
    "traversed": "遍历完成",
    "page_type": "页类型",
    "reason": "遍历原因",
    "ownership": "归属",
    "findings": "finding",
}


def _delta_text(delta: dict[str, Any]) -> str:
    return ", ".join(f"{name}={value:+d}" for name, value in sorted(delta.items()) if value)
def _slot_row(slot: dict[str, Any]) -> list[Any]:
    state = slot.get("state")
    old = slot.get("old") or {}
    new = slot.get("new") or {}
    owner = slot.get("ownership") or {}
    if state == "changed":
        change_names = ",".join(sorted(slot.get("changes", {})))
        detail = "; ".join(
            f"{name}: {slot['changes'][name].get('old')}→{slot['changes'][name].get('new')}"
            for name in sorted(slot.get("changes", {}))
            if "old" in slot["changes"][name]
        )
        return [slot.get("page_no"), state, f"{owner.get('old')}→{owner.get('new')}", change_names, detail]
    if state == "released":
        return [slot.get("page_no"), state, f"{ownership(old)}→-", "-", f"page_type={old.get('page_type')}"]
    if state == "allocated":
        return [slot.get("page_no"), state, f"-→{ownership(new)}", "-", f"page_type={new.get('page_type')}"]
    if state == "unknown":
        return [slot.get("page_no"), state, "-", "-", f"presence {slot.get('old_presence')}/{slot.get('new_presence')}"]
    return [slot.get("page_no"), state, f"{owner.get('old')}/{owner.get('new')}", "-", "-"]
def _object_row(row: dict[str, Any]) -> list[Any]:
    key = row["key"]
    changes = row.get("changes", {})
    names = sorted(name for name in changes if not name.startswith("space_"))
    old = row.get("old", {}).get("reported", {})
    new = row.get("new", {}).get("reported", {})
    return [
        key.get("object_type"),
        key.get("name"),
        key.get("table_name"),
        row.get("state"),
        f"{row['root'].get('old')}→{row['root'].get('new')}",
        f"{old.get('page_count', '-')}→{new.get('page_count', '-')}",
        f"{old.get('depth', '-')}→{new.get('depth', '-')}",
        ",".join(CHANGE_LABEL.get(name, name) for name in names) or "-",
    ]
def render_diff_markdown(result: dict[str, Any]) -> str:
    scope = result.get("scope", {})
    conclusion = str(result.get("conclusion"))
    physical = result.get("page_view", {})
    logical = result.get("object_view", {})
    physical_summary = physical.get("summary", {})
    logical_summary = logical.get("summary", {})
    lines = [
        "# 跨快照迁移差异报告",
        "",
        f"- 结论：**{CONCLUSION_LABEL.get(conclusion, _text(conclusion))}** (`{_text(conclusion)}`)",
        f"- 比较状态：{_text(result.get('status'))}",
        f"- 完整范围：{scope.get('full')}；受限：{scope.get('limited')}；拒绝：{scope.get('refused')}",
        f"- 差异版本：{_text(result.get('diff_version'))}",
        "",
        "## 输入与复核",
        "",
    ]
    rows = []
    for label in ("a", "b"):
        item = result.get("inputs", {}).get(label, {})
        snapshot = item.get("snapshot", {})
        rows.append(
            [
                label.upper(),
                item.get("report_path"),
                item.get("db_path"),
                snapshot.get("size"),
                snapshot.get("sha256"),
                item.get("valid"),
                item.get("report_status"),
                ",".join(item.get("failed_checks", [])) or "-",
            ]
        )
    lines.append(_table(["侧", "报告", "数据库", "字节", "sha256", "复核", "报告状态", "失败检查"], rows))
    if scope.get("limitations"):
        lines.extend(["", "## 拒绝原因" if scope.get("refused") else "## 受限范围", ""])
        for reason, text in zip(scope.get("reasons", []), scope.get("limitations", [])):
            lines.append(f"- `{_text(reason)}`：{_text(text, 400)}")
    if scope.get("refused"):
        lines.extend(["", "复核未通过或拒绝完整比较，未生成页槽与对象差异。", ""])
        return "\n".join(lines)
    lines.extend(
        [
            "",
            "## 物理页槽视角（page_no）",
            "",
            _table(
                ["指标", "值"],
                [
                    ["保留页", physical_summary.get("retained")],
                    ["变化页", physical_summary.get("changed")],
                    ["释放页", physical_summary.get("released")],
                    ["分配页", physical_summary.get("allocated")],
                    ["未知页", physical_summary.get("unknown")],
                    ["归属转换", physical_summary.get("ownership_transitions")],
                    ["按归属释放", physical_summary.get("released_by_ownership")],
                    ["按归属分配", physical_summary.get("allocated_by_ownership")],
                ],
            ),
            "",
        ]
    )
    type_rows = []
    for row in physical.get("page_type_totals", []):
        delta = row.get("delta", {})
        if any(delta.values()):
            type_rows.append([row.get("page_type"), row.get("old"), row.get("new"), _delta_text(delta)])
    lines.append("### 页类型总量变化")
    lines.append("")
    if type_rows:
        lines.append(
            _table(
                ["页类型", "页数（旧→新）", "差值"],
                [
                    [row[0], f"{row[1].get('page_count', 0)}→{row[2].get('page_count', 0)}", row[3]]
                    for row in type_rows
                ],
            )
        )
    else:
        lines.append("按页类型汇总无变化；这不代表对象归属或页槽无变化。")
    slots = [slot for slot in physical.get("slots", []) if slot.get("state") in {"changed", "released", "allocated", "unknown"}]
    lines.extend(["", f"### 关键页槽（前 {SLOT_LIMIT} 条）", ""])
    if slots:
        lines.append(
            _table(
                ["页", "状态", "归属", "变化字段", "明细"],
                [_slot_row(slot) for slot in slots[:SLOT_LIMIT]],
            )
        )
        if len(slots) > SLOT_LIMIT:
            lines.append("")
            lines.append(f"仅列前 {SLOT_LIMIT} 条，共 {len(slots)} 条；完整明细见 diff.json。")
    else:
        lines.append("没有页槽级变化。")
    lines.extend(
        [
            "",
            "## 逻辑对象视角（object_type,name,table_name）",
            "",
            _table(
                ["指标", "值"],
                [
                    ["新增对象", logical_summary.get("added")],
                    ["删除对象", logical_summary.get("removed")],
                    ["变化对象", logical_summary.get("changed")],
                    ["未变对象", logical_summary.get("unchanged")],
                    ["根页迁移对象", logical_summary.get("root_moved")],
                    ["未归属 finding 新增", logical_summary.get("unattributed_findings_added")],
                    ["未归属 finding 消失", logical_summary.get("unattributed_findings_removed")],
                ],
            ),
            "",
            _table(
                ["类型", "名称", "所属表", "状态", "根页", "页数", "深度", "变化"],
                [_object_row(row) for row in logical.get("objects", [])],
            ),
        ]
    )
    unattributed = logical.get("unattributed_findings", {})
    lines.extend(["", "## 未归属 finding 变化", ""])
    if unattributed.get("added") or unattributed.get("removed"):
        lines.append(
            _table(
                ["变化", "code", "级别", "页", "字段"],
                [
                    [kind, item.get("code"), item.get("level"), item.get("page"), item.get("field")]
                    for kind in ("added", "removed")
                    for item in unattributed.get(kind, [])
                ],
            )
        )
    else:
        lines.append("无。")
    lines.extend(
        [
            "",
            "## 解释提示",
            "",
            "- 物理视角只说明页槽与归属变化；页槽释放不代表对象删除。",
            "- 对象视角只说明稳定对象键的归属变化；对象未变不代表页槽布局未变。",
            "- 受限范围中的未知页不能解读为无变化或已释放。",
            "",
            "---",
            "",
            "本报告由 page-ledger diff 生成，只读比较，不修复数据库。",
            "",
        ]
    )
    return "\n".join(lines)
def write_diff_markdown(result: dict[str, Any], out_dir: str | Path) -> Path:
    target = Path(out_dir) / "diff.md"
    write_text(target, render_diff_markdown(result))
    return target
