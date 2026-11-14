"""Deterministic rule catalogue and conversion of observations to findings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import Finding, LEVEL_ORDER, Problem, sort_findings


@dataclass(frozen=True)
class Rule:
    code: str
    level: str
    title: str
    condition: str
    evidence_fields: str
    blocking: bool = False
    recommendation: str = "保留证据并在归档验收单中标注。"
def _r(
    code: str,
    level: str,
    title: str,
    condition: str,
    evidence: str,
    blocking: bool = False,
    recommendation: str = "保留证据并在归档验收单中标注。",
) -> Rule:
    return Rule(code, level, title, condition, evidence, blocking, recommendation)
_RULE_LIST = [
    _r("HDR_FILE_TOO_SMALL", "critical", "文件小于 100 字节", "无法读取完整文件头", "file_size"),
    _r("HDR_MAGIC_INVALID", "critical", "SQLite 魔数不匹配", "前 16 字节不等于 SQLite format 3", "magic_hex", blocking=True),
    _r("HDR_PAGE_SIZE_INVALID", "critical", "页大小非法", "页大小不是 512..65536 的 2 的幂", "raw_page_size", blocking=True),
    _r("HDR_RESERVED_INVALID", "error", "保留区非法", "保留区越过页尾或 usable size 小于 480", "reserved_bytes,usable_size"),
    _r("HDR_PAYLOAD_FRACTIONS_INVALID", "warning", "payload 分数异常", "头部的 64/32/32 分数不匹配格式", "payload_fractions"),
    _r("HDR_FILE_TRUNCATED", "error", "文件被截断", "实际完整页数小于头部声明页数", "declared_pages,actual_pages", blocking=True),
    _r("HDR_PAGE_COUNT_MISMATCH", "warning", "页数账本不一致", "实际完整页数与声明页数不同", "declared_pages,actual_pages"),
    _r("HDR_PAGE_COUNT_INVALID", "warning", "声明页数为零", "数据库头部 page count 为 0", "declared_pages"),
    _r("HDR_TRAILING_BYTES", "warning", "文件尾部有残字节", "文件大小不是页大小整数倍", "file_size,page_size,trailing_bytes"),
    _r("HDR_ENCODING_INVALID", "error", "文本编码非法", "头部文本编码不在 1..3", "text_encoding"),
    _r("HDR_AUTOVACUUM_INVALID", "warning", "auto_vacuum 模式非法", "auto_vacuum 不在 0..2", "auto_vacuum"),
    _r("HDR_INCREMENTAL_VACUUM_INVALID", "warning", "增量 vacuum 标志非法", "incremental vacuum 不是 0 或 1", "incremental_vacuum"),
    _r("HDR_WAL_PRESENT", "info", "存在同名 WAL 文件", "仅提示未合并 WAL 帧", "wal_path", recommendation="需要包含最新提交时，先在数据库副本上完成 WAL 合并。"),
    _r("PAGE_OUT_OF_RANGE", "error", "页号超出文件范围", "访问页号不在 1..actual_page_count", "page,actual_page_count"),
    _r("PAGE_TYPE_UNKNOWN", "error", "未知 B 树页类型", "页首类型字节不是 0x02/0x05/0x0a/0x0d", "raw_type"),
    _r("PAGE_HEADER_INVALID", "error", "B 树页头非法", "cell 数或内容区起点使头部溢界", "cell_count,content_start,usable_end"),
    _r("PAGE_CELL_COUNT_ANOMALY", "error", "cell 数量异常", "cell pointer array 放不进 usable area", "cell_count,pointer_bytes"),
    _r("PAGE_CELL_PTR_OUT_OF_RANGE", "error", "cell 指针越界", "cell 指针不在内容区内", "cell_index,cell_pointer,content_start,usable_end"),
    _r("PAGE_CELL_PTR_OVERLAP", "error", "cell 指针重叠", "两个 cell 的字节区间重叠", "cell_index,cell_pointer,cell_size"),
    _r("PAGE_CONTENT_OUT_OF_RANGE", "error", "内容区边界非法", "内容区起点早于 pointer array 末端或晚于 usable 末端", "content_start,pointer_end,usable_end"),
    _r("PAGE_CELL_PARSE_FAILED", "error", "cell 无法解析", "varint、payload 或 cell 长度越界", "cell_index,cell_pointer"),
    _r("PAGE_FREE_SPACE_NEGATIVE", "error", "空闲空间为负", "页头记录的空间边界相互冲突", "content_start,pointer_end"),
    _r("PAGE_TYPE_UNEXPECTED", "warning", "页类型与对象预期不符", "同一棵树混入 table/index 页", "expected,actual"),
    _r("BTREE_CHILD_OUT_OF_RANGE", "error", "子页号越界", "子页指针不指向有效页", "parent_page,child_page"),
    _r("BTREE_CYCLE", "error", "B 树出现环", "子页是当前页或其祖先", "parent_page,child_page"),
    _r("BTREE_DUPLICATE_REFERENCE", "error", "B 树页被重复引用", "同一页从多个父引用到达", "parent_page,child_page,previous_parent"),
    _r("BTREE_DEPTH_ANOMALY", "warning", "B 树深度异常", "深度超过配置上限或同层异常", "page,depth"),
    _r("BTREE_UNREACHABLE_PAGE", "warning", "不可达孤儿页", "页既不可达也不在 freelist", "page,page_type"),
    _r("BTREE_TRAVERSAL_LIMIT", "warning", "遍历达到安全上限", "达到 max_pages，结果只覆盖前缀", "max_pages", blocking=True),
    _r("BTREE_OBJECT_TRAVERSAL_FAILED", "error", "schema 对象遍历失败", "对象根页无法完成遍历", "object_root,page_count"),
    _r("FREE_TRUNK_OUT_OF_RANGE", "error", "freelist trunk 越界", "trunk 页号不在有效范围", "trunk_page,actual_page_count"),
    _r("FREE_TRUNK_CYCLE", "error", "freelist trunk 环", "trunk 链回到已访问 trunk", "trunk_page,previous_trunk"),
    _r("FREE_TRUNK_DUPLICATE", "warning", "freelist trunk 重复", "同一 trunk 被链引用多次", "trunk_page"),
    _r("FREE_TRUNK_LEAF_CONFLICT", "error", "trunk 与叶子页冲突", "一个页同时被登记为 trunk 和 leaf", "page"),
    _r("FREE_TRUNK_HEADER_INVALID", "error", "freelist trunk 头非法", "叶子计数器放不进页可用空间", "trunk_page,leaf_count,usable_size"),
    _r("FREE_LEAF_OUT_OF_RANGE", "error", "freelist 叶子页越界", "叶子页号不在 2..actual_page_count", "trunk_page,leaf_page"),
    _r("FREE_LEAF_DUPLICATE", "warning", "freelist 叶子页重复", "同一叶子页被登记多次", "leaf_page,trunk_page"),
    _r("FREE_COUNT_MISMATCH", "warning", "freelist 总数不匹配", "头部 freelist 页数与实际发现页数不同", "declared_count,actual_count"),
    _r("FREE_FIRST_TRUNK_INVALID", "error", "freelist 首 trunk 非法", "首 trunk 为 0 但声明数非 0，或 trunk 越界", "first_trunk,declared_count"),
    _r("OVFL_FIRST_PAGE_INVALID", "error", "溢出链首页非法", "首页不在有效页范围", "first_page,remaining_bytes"),
    _r("OVFL_PAGE_OUT_OF_RANGE", "error", "溢出页越界", "溢出链指针超过实际页数", "origin_page,overflow_page,actual_page_count"),
    _r("OVFL_CHAIN_CYCLE", "error", "溢出链成环", "溢出链回到链内已见页", "origin_page,first_page,repeated_page"),
    _r("OVFL_CHAIN_BROKEN", "error", "溢出链提前结束", "payload 剩余字节但 next 为 0", "origin_page,first_page,expected_pages,actual_pages"),
    _r("OVFL_LENGTH_MISMATCH", "warning", "溢出链长度不匹配", "链页数与 payload 所需页数不同", "expected_pages,actual_pages"),
    _r("OVFL_DUPLICATE_PAGE", "error", "溢出页重复引用", "同一溢出页属于多个 cell 或链", "overflow_page,origin_page,previous_origin"),
    _r("OVFL_INCOMPLETE", "warning", "溢出链审计未完成", "链在坏指针处中断，仅保留前缀", "origin_page,audited_pages"),
    _r("OVFL_LIMIT_REACHED", "warning", "溢出链达到安全上限", "超过 max_overflow_chain", "first_page,max_overflow_chain", blocking=True),
    _r("SCHEMA_RECORD_INVALID", "warning", "schema 记录无法解析", "sqlite_schema payload 不是合法 record", "page,cell_index"),
    _r("SCHEMA_ROOT_MISSING", "error", "schema 根页缺失", "对象根页为 0、越界或不可达", "object_name,root_page"),
    _r("SCHEMA_ROOT_TYPE_MISMATCH", "error", "schema 根页类型不符", "表根必须是 table btree，索引根必须是 index btree", "object_name,root_page,expected_type,actual_type"),
    _r("SCHEMA_TRAVERSAL_FAILED", "error", "schema 根页遍历失败", "根页存在但没有任何树页记录", "object_name,root_page"),
    _r("SCHEMA_SIDECAR_UNAVAILABLE", "info", "只读旁证不可用", "sqlite3 只读连接无法读取 schema 或 page_count", "error"),
    _r("SCHEMA_SIDECAR_MISMATCH", "warning", "只读旁证不一致", "旁证 page_count 或 schema 根页与页解析不同", "field,parsed,sidecar"),
    _r("SCHEMA_ROOT_DIFF", "warning", "schema 根页集合差异", "解析记录和旁证记录的根页集合不同", "parsed_roots,sidecar_roots"),
    _r("SCHEMA_OBJECT_MISMATCH", "warning", "schema 对象差异", "schema 记录对象集合与旁证对象集合不同", "parsed_objects,sidecar_objects"),
    _r("SPACE_FRAGMENTATION_HIGH", "warning", "碎片率超过阈值", "fragmented_bytes / usable_size 超过阈值", "fragmented_bytes,usable_size,ratio,threshold"),
    _r("SPACE_UTILIZATION_LOW", "info", "页利用率过低", "内容区占比低于阈值且 cell 数达到最小值", "content_bytes,usable_size,ratio,threshold"),
    _r("SPACE_LEDGER_INCONSISTENT", "warning", "页空间账本不一致", "页头空间分项相加超过可用区", "header_bytes,pointer_bytes,content_bytes,free_bytes,usable_size"),
    _r("AUDIT_PARTIAL_RESULT", "warning", "审计仅覆盖部分页", "页级覆盖或任一子结构遍历未完成", "reason", blocking=True),
]

RULES: dict[str, Rule] = {rule.code: rule for rule in _RULE_LIST}
RULE_CATEGORIES = ("HDR", "PAGE", "BTREE", "FREE", "OVFL", "SCHEMA", "SPACE", "AUDIT")


def rule_for(code: str) -> Rule:
    try:
        return RULES[code]
    except KeyError as exc:  # pragma: no cover - programming error guard
        raise KeyError(f"规则错误码未登记: {code}") from exc
def make_finding(problem: Problem) -> Finding:
    rule = rule_for(problem.code)
    return Finding(
        code=problem.code,
        level=rule.level,
        page=problem.page,
        field=problem.field,
        expected=problem.expected,
        actual=problem.actual,
        evidence=problem.evidence,
        blocking=rule.blocking or problem.blocking,
        title=rule.title,
    )
def build_findings(problems: Iterable[Problem], max_per_code: int) -> list[Finding]:
    findings: list[Finding] = []
    counts: dict[str, int] = {}
    for problem in problems:
        count = counts.get(problem.code, 0)
        if count >= max_per_code:
            continue
        counts[problem.code] = count + 1
        findings.append(make_finding(problem))
    return sort_findings(findings)
def rule_summary(findings: Iterable[Finding]) -> list[dict[str, object]]:
    buckets: dict[str, list[str]] = {}
    for finding in findings:
        buckets.setdefault(finding.code, []).append(finding.level)
    result = []
    for code in sorted(buckets):
        levels = buckets[code]
        maximum = max(levels, key=lambda level: LEVEL_ORDER[level])
        result.append({"code": code, "hits": len(levels), "max_level": maximum})
    return result
def rules_document() -> list[dict[str, object]]:
    return [
        {
            "code": rule.code,
            "level": rule.level,
            "title": rule.title,
            "condition": rule.condition,
            "evidence_fields": rule.evidence_fields,
            "blocking": rule.blocking,
            "recommendation": rule.recommendation,
        }
        for rule in sorted(RULES.values(), key=lambda item: item.code)
    ]
