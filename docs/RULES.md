# 规则表

本表由 `src/page_ledger/audit/rules.py` 中的规则目录生成，错误码、默认级别和证据字段保持稳定。阈值可在审计时通过 `--config` 覆盖；级别不可由配置修改。
严重级别：`info` < `warning` < `error` < `critical`。`blocking` 表示该规则可能使页级结果只能覆盖前缀。

| 错误码 | 级别 | 标题 | 触发条件 | 证据字段 | 阻断后续 | 处置建议 |
| --- | --- | --- | --- | --- | --- | --- |
| `AUDIT_PARTIAL_RESULT` | warning | 审计仅覆盖部分页 | 页级覆盖或任一子结构遍历未完成 | reason | 是 | 保留证据并在归档验收单中标注。 |
| `BTREE_CHILD_OUT_OF_RANGE` | error | 子页号越界 | 子页指针不指向有效页 | parent_page,child_page | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_ROWID_ORDER_INVALID` | error | 叶页 rowid 未递增 | 同一 table leaf 的 rowid 未按有符号 64-bit 严格递增 | page,cell_index,rowid | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_SEPARATOR_ORDER_INVALID` | error | 内部页分隔键未递增 | 同一 table interior 的分隔键未按有符号 64-bit 严格递增 | page,cell_index,separator | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_SEPARATOR_MAX_MISMATCH` | error | 分隔键不等于左子树最大 rowid | table interior 分隔键应等于其左子树的最大 rowid | separator_page,separator,child_page,child_min,child_max | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_ROWID_OUT_OF_PARTITION` | error | rowid 落入错误子树 | 子页 rowid 越过祖先分隔键划分的键域 | child_page,separator_page,separator,child_min,child_max | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_KEY_ORDER_INCOMPLETE` | warning | 键序核验未判定 | 部分子树、坏页、越界 child 或遍历中断使键序无法完整判定 | root_page,undetermined_count,reason | 是 | 保留证据并在归档验收单中标注。 |
| `BTREE_INDEX_KEY_ORDER_UNVERIFIED` | info | 索引键序未核验 | index b-tree 排序依赖 collation/表达式，字节序不代表 SQL 顺序 | root_page,note | 否 | 需要索引键序结论时使用 SQLite 自身的完整性检查。 |
| `BTREE_CYCLE` | error | B 树出现环 | 子页是当前页或其祖先 | parent_page,child_page | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_DEPTH_ANOMALY` | warning | B 树深度异常 | 深度超过配置上限或同层异常 | page,depth | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_DUPLICATE_REFERENCE` | error | B 树页被重复引用 | 同一页从多个父引用到达 | parent_page,child_page,previous_parent | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_OBJECT_TRAVERSAL_FAILED` | error | schema 对象遍历失败 | 对象根页无法完成遍历 | object_root,page_count | 否 | 保留证据并在归档验收单中标注。 |
| `BTREE_TRAVERSAL_LIMIT` | warning | 遍历达到安全上限 | 达到 max_pages，结果只覆盖前缀 | max_pages | 是 | 保留证据并在归档验收单中标注。 |
| `BTREE_UNREACHABLE_PAGE` | warning | 不可达孤儿页 | 页既不可达也不在 freelist | page,page_type | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_COUNT_MISMATCH` | warning | freelist 总数不匹配 | 头部 freelist 页数与实际发现页数不同 | declared_count,actual_count | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_FIRST_TRUNK_INVALID` | error | freelist 首 trunk 非法 | 首 trunk 为 0 但声明数非 0，或 trunk 越界 | first_trunk,declared_count | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_LEAF_DUPLICATE` | warning | freelist 叶子页重复 | 同一叶子页被登记多次 | leaf_page,trunk_page | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_LEAF_OUT_OF_RANGE` | error | freelist 叶子页越界 | 叶子页号不在 2..actual_page_count | trunk_page,leaf_page | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_TRUNK_CYCLE` | error | freelist trunk 环 | trunk 链回到已访问 trunk | trunk_page,previous_trunk | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_TRUNK_DUPLICATE` | warning | freelist trunk 重复 | 同一 trunk 被链引用多次 | trunk_page | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_TRUNK_HEADER_INVALID` | error | freelist trunk 头非法 | 叶子计数器放不进页可用空间 | trunk_page,leaf_count,usable_size | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_TRUNK_LEAF_CONFLICT` | error | trunk 与叶子页冲突 | 一个页同时被登记为 trunk 和 leaf | page | 否 | 保留证据并在归档验收单中标注。 |
| `FREE_TRUNK_OUT_OF_RANGE` | error | freelist trunk 越界 | trunk 页号不在有效范围 | trunk_page,actual_page_count | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_AUTOVACUUM_INVALID` | warning | auto_vacuum 模式非法 | auto_vacuum 不在 0..2 | auto_vacuum | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_ENCODING_INVALID` | error | 文本编码非法 | 头部文本编码不在 1..3 | text_encoding | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_FILE_TOO_SMALL` | critical | 文件小于 100 字节 | 无法读取完整文件头 | file_size | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_FILE_TRUNCATED` | error | 文件被截断 | 实际完整页数小于头部声明页数 | declared_pages,actual_pages | 是 | 保留证据并在归档验收单中标注。 |
| `HDR_INCREMENTAL_VACUUM_INVALID` | warning | 增量 vacuum 标志非法 | incremental vacuum 不是 0 或 1 | incremental_vacuum | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_MAGIC_INVALID` | critical | SQLite 魔数不匹配 | 前 16 字节不等于 SQLite format 3 | magic_hex | 是 | 保留证据并在归档验收单中标注。 |
| `HDR_PAGE_COUNT_INVALID` | warning | 声明页数为零 | 数据库头部 page count 为 0 | declared_pages | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_PAGE_COUNT_MISMATCH` | warning | 页数账本不一致 | 实际完整页数与声明页数不同 | declared_pages,actual_pages | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_PAGE_SIZE_INVALID` | critical | 页大小非法 | 页大小不是 512..65536 的 2 的幂 | raw_page_size | 是 | 保留证据并在归档验收单中标注。 |
| `HDR_PAYLOAD_FRACTIONS_INVALID` | warning | payload 分数异常 | 头部的 64/32/32 分数不匹配格式 | payload_fractions | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_RESERVED_INVALID` | error | 保留区非法 | 保留区越过页尾或 usable size 小于 480 | reserved_bytes,usable_size | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_TRAILING_BYTES` | warning | 文件尾部有残字节 | 文件大小不是页大小整数倍 | file_size,page_size,trailing_bytes | 否 | 保留证据并在归档验收单中标注。 |
| `HDR_WAL_PRESENT` | info | 存在同名 WAL 文件 | 仅提示未合并 WAL 帧 | wal_path | 否 | 需要包含最新提交时，先在数据库副本上完成 WAL 合并。 |
| `OVFL_CHAIN_BROKEN` | error | 溢出链提前结束 | payload 剩余字节但 next 为 0 | origin_page,first_page,expected_pages,actual_pages | 否 | 保留证据并在归档验收单中标注。 |
| `OVFL_CHAIN_CYCLE` | error | 溢出链成环 | 溢出链回到链内已见页 | origin_page,first_page,repeated_page | 否 | 保留证据并在归档验收单中标注。 |
| `OVFL_DUPLICATE_PAGE` | error | 溢出页重复引用 | 同一溢出页属于多个 cell 或链 | overflow_page,origin_page,previous_origin | 否 | 保留证据并在归档验收单中标注。 |
| `OVFL_FIRST_PAGE_INVALID` | error | 溢出链首页非法 | 首页不在有效页范围 | first_page,remaining_bytes | 否 | 保留证据并在归档验收单中标注。 |
| `OVFL_INCOMPLETE` | warning | 溢出链审计未完成 | 链在坏指针处中断，仅保留前缀 | origin_page,audited_pages | 否 | 保留证据并在归档验收单中标注。 |
| `OVFL_LENGTH_MISMATCH` | warning | 溢出链长度不匹配 | 链页数与 payload 所需页数不同 | expected_pages,actual_pages | 否 | 保留证据并在归档验收单中标注。 |
| `OVFL_LIMIT_REACHED` | warning | 溢出链达到安全上限 | 超过 max_overflow_chain | first_page,max_overflow_chain | 是 | 保留证据并在归档验收单中标注。 |
| `OVFL_PAGE_OUT_OF_RANGE` | error | 溢出页越界 | 溢出链指针超过实际页数 | origin_page,overflow_page,actual_page_count | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_CELL_COUNT_ANOMALY` | error | cell 数量异常 | cell pointer array 放不进 usable area | cell_count,pointer_bytes | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_CELL_PARSE_FAILED` | error | cell 无法解析 | varint、payload 或 cell 长度越界 | cell_index,cell_pointer | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_CELL_PTR_OUT_OF_RANGE` | error | cell 指针越界 | cell 指针不在内容区内 | cell_index,cell_pointer,content_start,usable_end | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_CELL_PTR_OVERLAP` | error | cell 指针重叠 | 两个 cell 的字节区间重叠 | cell_index,cell_pointer,cell_size | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_CONTENT_OUT_OF_RANGE` | error | 内容区边界非法 | 内容区起点早于 pointer array 末端或晚于 usable 末端 | content_start,pointer_end,usable_end | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_FREE_SPACE_NEGATIVE` | error | 空闲空间为负 | 页头记录的空间边界相互冲突 | content_start,pointer_end | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_HEADER_INVALID` | error | B 树页头非法 | cell 数或内容区起点使头部溢界 | cell_count,content_start,usable_end | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_OUT_OF_RANGE` | error | 页号超出文件范围 | 访问页号不在 1..actual_page_count | page,actual_page_count | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_TYPE_UNEXPECTED` | warning | 页类型与对象预期不符 | 同一棵树混入 table/index 页 | expected,actual | 否 | 保留证据并在归档验收单中标注。 |
| `PAGE_TYPE_UNKNOWN` | error | 未知 B 树页类型 | 页首类型字节不是 0x02/0x05/0x0a/0x0d | raw_type | 否 | 保留证据并在归档验收单中标注。 |
| `SCHEMA_OBJECT_MISMATCH` | warning | schema 对象差异 | schema 记录对象集合与旁证对象集合不同 | parsed_objects,sidecar_objects | 否 | 保留证据并在归档验收单中标注。 |
| `SCHEMA_RECORD_INVALID` | warning | schema 记录无法解析 | sqlite_schema payload 不是合法 record | page,cell_index | 否 | 保留证据并在归档验收单中标注。 |
| `SCHEMA_ROOT_DIFF` | warning | schema 根页集合差异 | 解析记录和旁证记录的根页集合不同 | parsed_roots,sidecar_roots | 否 | 保留证据并在归档验收单中标注。 |
| `SCHEMA_ROOT_MISSING` | error | schema 根页缺失 | 对象根页为 0、越界或不可达 | object_name,root_page | 否 | 保留证据并在归档验收单中标注。 |
| `SCHEMA_ROOT_TYPE_MISMATCH` | error | schema 根页类型不符 | 表根必须是 table btree，索引根必须是 index btree | object_name,root_page,expected_type,actual_type | 否 | 保留证据并在归档验收单中标注。 |
| `SCHEMA_SIDECAR_MISMATCH` | warning | 只读旁证不一致 | 旁证 page_count 或 schema 根页与页解析不同 | field,parsed,sidecar | 否 | 保留证据并在归档验收单中标注。 |
| `SCHEMA_SIDECAR_UNAVAILABLE` | info | 只读旁证不可用 | sqlite3 只读连接无法读取 schema 或 page_count | error | 否 | 保留证据并在归档验收单中标注。 |
| `SCHEMA_TRAVERSAL_FAILED` | error | schema 根页遍历失败 | 根页存在但没有任何树页记录 | object_name,root_page | 否 | 保留证据并在归档验收单中标注。 |
| `SPACE_FRAGMENTATION_HIGH` | warning | 碎片率超过阈值 | fragmented_bytes / usable_size 超过阈值 | fragmented_bytes,usable_size,ratio,threshold | 否 | 保留证据并在归档验收单中标注。 |
| `SPACE_LEDGER_INCONSISTENT` | warning | 页空间账本不一致 | 页头空间分项相加超过可用区 | header_bytes,pointer_bytes,content_bytes,free_bytes,usable_size | 否 | 保留证据并在归档验收单中标注。 |
| `SPACE_UTILIZATION_LOW` | info | 页利用率过低 | 内容区占比低于阈值且 cell 数达到最小值 | content_bytes,usable_size,ratio,threshold | 否 | 保留证据并在归档验收单中标注。 |

## 阈值配置

默认值定义在 `src/page_ledger/config.py`，可用 JSON 文件覆盖：

```json
{
  "top_pages": 10,
  "fragmentation_warn_ratio": 0.05,
  "utilization_low_ratio": 0.2,
  "low_util_min_cells": 2,
  "max_pages": 100000,
  "max_btree_depth": 64,
  "max_overflow_chain": 4096,
  "max_findings_per_code": 250,
  "include_info_findings": true
}
```

`SPACE_FRAGMENTATION_HIGH` 使用 `fragmented_bytes / usable_size` 与 `fragmentation_warn_ratio` 比较；`SPACE_UTILIZATION_LOW` 使用 `(header_bytes + pointer_bytes + cell_bytes) / usable_size`，并要求 cell 数不少于 `low_util_min_cells`。
