# SQLite 页格式与审计模型

本文说明 `page-ledger` 实际读取的 SQLite 文件格式子集，以及报告中的字段如何从字节推导。范围是单文件、未加密、传统页式 SQLite 3 主数据库的**结构审计**，不是 SQLite 格式的完整规范，也不替代 SQLite 官方文档。

## 1. 基本约定

- 所有多字节整数均为大端序（big-endian）。
- 页号从 1 开始；页 `N` 的文件偏移为 `(N - 1) * page_size`。
- 页大小来自文件头，编码值 `1` 表示 65536；合法值为 512 到 65536 之间的 2 的幂。
- `usable_size = page_size - reserved_bytes`。保留区位于每个页的尾部，工具不会把它算作可用内容区。
- 页 1 的前 100 字节是数据库文件头，因此页 1 的 B 树页头从文件偏移 100 开始；其他页从页偏移 0 开始。
- 本次审计不会读取业务字段值。`sqlite_schema` 的 record 只解出对象类型、名称、表名和根页号；普通表的 record payload 只用于确定本地 payload 长度和溢出首页指针。

## 2. 100 字节数据库头

工具读取并校验以下字段：

| 偏移 | 长度 | 字段 | 工具用途 |
| ---: | ---: | --- | --- |
| 0 | 16 | magic | 必须为 `SQLite format 3\0` |
| 16 | 2 | page size | 页大小；`1` 表示 65536 |
| 18 | 1 | write format | 记录到头部快照，不做写入版本兼容性判断 |
| 19 | 1 | read format | 同上 |
| 20 | 1 | reserved bytes per page | 计算 usable size，并检查保留区合法性 |
| 21 | 3 | payload fractions | 必须为 `64, 32, 32` |
| 24 | 4 | file change counter | 记录到头部快照 |
| 28 | 4 | database size in pages | 与实际文件完整页数对账 |
| 32 | 4 | first freelist trunk page | freelist 遍历起点 |
| 36 | 4 | freelist page count | 与发现的实际 freelist 页数对账 |
| 40 | 4 | schema cookie | 记录到头部快照 |
| 44 | 4 | schema format | 记录到头部快照 |
| 48 | 4 | default page cache size | 记录到头部快照 |
| 52 | 4 | largest root b-tree page | 非零表示 auto-vacuum；与声明页数对账 |
| 56 | 4 | text encoding | `1=utf-8`、`2=utf-16le`、`3=utf-16be`；只有 schema 文本解码使用 |
| 60 | 4 | user version | 记录到头部快照 |
| 64 | 4 | incremental vacuum | 必须为 0 或 1；与 offset 52 联合推导 auto-vacuum 模式 |
| 68 | 4 | application id | 记录到头部快照 |
| 72 | 20 | reserved | 不解释 |
| 92 | 4 | version-valid-for | 不参与判断 |
| 96 | 4 | sqlite version number | 记录到头部快照 |

推导规则：

```text
usable_size       = page_size - reserved_bytes
actual_pages      = floor(file_size / page_size)
declared_bytes    = declared_pages * page_size
trailing_bytes    = file_size % page_size
auto_vacuum       = 0   if largest_root_page == 0
                    1   if largest_root_page != 0 and incremental_vacuum == 0
                    2   if largest_root_page != 0 and incremental_vacuum == 1
```

SQLite 文件头并不直接保存 auto-vacuum 模式编号；这里采用“最大根页非零 + 增量标志”的格式约定推导，报告会同时保留原始字段。`declared_pages > actual_pages` 视为截断；二者不等或文件尾部有余数会记录头部异常。当页大小、usable size 或实际页数无法安全切片时，工具停止页级遍历，输出 `status=partial` 的部分结果，并仍尝试以只读旁证登记 schema 是否可读；若 magic 非法但页大小仍可安全切片，则继续页级解析并把结果标记为 `partial`。

## 3. B 树页

### 3.1 页类型

| 首字节 | 类型 | 角色 |
| ---: | --- | --- |
| `0x02` | index interior | 索引内部页 |
| `0x05` | table interior | 表内部页 |
| `0x0A` | index leaf | 索引叶子页 |
| `0x0D` | table leaf | 表叶子页 |

只有这四种页类型按 B 树页解释。其他首字节记录为 `unknown`，并产生 `PAGE_TYPE_UNKNOWN`。freelist 页和溢出页不依赖 B 树页头。

### 3.2 页头布局

叶子页页头 8 字节，内部页页头 12 字节，从页的 `header_offset`（页 1 为 100，其他为 0）开始：

| 相对偏移 | 长度 | 字段 |
| ---: | ---: | --- |
| 0 | 1 | 页类型 |
| 1 | 2 | first freeblock |
| 3 | 2 | cell 数量 |
| 5 | 2 | cell content area 起点；`0` 解释为 65536 |
| 7 | 1 | fragmented free bytes |
| 8 | 4 | 最右子页号（仅内部页） |

cell pointer array 紧接页头，每个 cell 占 2 字节，值是**相对于页起始**的偏移。内容区从页尾向前增长。工具检查：

- `cell_count * 2` 是否放得进 usable area；
- 页头、pointer array、内容起点是否相交；
- cell 指针是否落在内容区内；
- cell 的实际字节区间是否相互重叠；
- 内部页的 right-child 是否在 `1..actual_pages` 内。

页 1 的 usable area 从偏移 100 开始；页头、pointer array 和 cell 都不应进入前 100 字节的文件头区域。

### 3.3 cell 布局

varint 最多 9 字节。前 8 字节每字节贡献 7 位，第 9 字节贡献完整 8 位；这是工具 `decode_varint` 的实现边界。

| 页类型 | cell 布局（按顺序） |
| --- | --- |
| table interior | 4 字节左子页号、varint rowid |
| table leaf | varint payload 长度、varint rowid、本地 payload、可选 4 字节溢出首页 |
| index leaf | varint payload 长度、本地 payload、可选 4 字节溢出首页 |
| index interior | 4 字节左子页号、varint payload 长度、本地 payload、可选 4 字节溢出首页 |

table leaf 的 payload 前有两个 varint；不能把第一个 varint 后面的位置直接当成 payload 起点。普通记录的 payload 内容不解码，只计算长度、本地边界和溢出指针。

### 3.4 本地 payload 与溢出

设 `U = usable_size`，`P = payload_size`：

```text
table btree:
    max_local = U - 35
index btree:
    max_local = floor((U - 12) * 64 / 255) - 23

min_local = floor((U - 12) * 32 / 255) - 23

若 P <= max_local:
    local = P
否则:
    k = min_local + ((P - min_local) mod (U - 4))
    local = k       若 k <= max_local
    local = min_local 否则
```

- 当 `P > local` 时，cell 末尾有 4 字节溢出首页号。
- 溢出字节数为 `P - local`。
- 每个溢出页可承载 `U - 4` 字节：前 4 字节是下一页号，其余为 payload 片段。
- 期望溢出页数为 `ceil((P - local) / (U - 4))`。
- 一条链以 next=0 结束；页号为 0 不是有效页，页 1 也不是有效溢出页。

工具只读取溢出页的前 4 字节和 payload 片段长度，不还原记录文本，也不输出 payload 内容。报告保留溢出首页、链中页号、期望页数、实际页数、状态和已审计字节数。环、重复、越界、提前结束、超长链和审计上限分别有独立错误码；链中断后保留已解析前缀并标记 `status=incomplete`。

## 4. B 树遍历

1. 从页 1 开始，按 `schema` 角色遍历 `sqlite_schema` 表 B 树。
2. 解码 `sqlite_schema` 的 table leaf record；前四列用于对象登记：`type`、`name`、`tbl_name`、`rootpage`。
3. 对每个 rootpage > 0 的表/索引对象，分别按 `table` 或 `index` 预期从根页开始遍历。
4. 子页号来自内部页的每个 cell 左子页和 right-child。遍历使用确定性队列，页访问顺序不依赖字典迭代顺序。
5. 记录 visited、queued、parent 和 depth；检查越界、环、重复父引用、类型不符和深度阈值。
6. 遍历中发现的溢出链归入可达页图；已经访问的 B 树页再次被引用会产生重复引用异常。
7. 页 1 或 schema 根树不是 table btree、根页无法解析、或达到 `max_pages` 时，结果标记为部分覆盖。

`max_btree_depth` 是告警阈值，不是强制截断；真正的强制安全上限是 `max_pages`。`ROOT_WALK.complete` 为 false 时，报告会保留已完成前缀，并提示部分覆盖。

## 5. sqlite_schema record

`sqlite_schema` 是一个普通 table btree，根页通常是页 1。工具对 table leaf 的 payload 按 SQLite record 格式解码：

- record 开头 varint 为 header 长度；
- header 中每个 varint 为 serial type；
- 按 serial type 计算字段长度并跳过字段内容。

serial type 支持：`0` NULL、`1..6` 有符号整数、`7` IEEE-754 双精度、`8` 常量 0、`9` 常量 1、`>=12` BLOB（偶数）或文本（奇数）。`10` 和 `11` 为保留类型，遇到时记录 `SCHEMA_RECORD_INVALID`。文本编码按头部 1/2/3 选择 UTF-8、UTF-16LE、UTF-16BE。

工具只取前四列作为元数据，不读取 `sql` 列用于执行。非 schema 表的 record 不做 serial type 解码，不导出字段值。

### 只读旁证

为了把“自己解析的页结构”和“SQLite 自己理解的 schema”做对照，工具会尝试启一个**只读且 immutable** 的连接：

```text
file:///.../input.db?mode=ro&immutable=1
PRAGMA query_only = ON
PRAGMA page_count
SELECT type, name, tbl_name, rootpage FROM sqlite_schema
```

这条路径只用于登记旁证 page_count、对象集合和根页集合。旁证失败（损坏、sqlite3 不可用、文件锁等）只产生 `SCHEMA_SIDECAR_UNAVAILABLE`，不阻断页级审计。`immutable=1` 明确告诉 SQLite 不读取或合并旁路 WAL/SHM 状态；它不能消除“审计期间有其他写者”的竞态，因此仍应在稳定副本上审计。

## 6. Freelist

SQLite freelist 由 trunk 页和叶子页组成：

- 头部 offset 32 是首个 trunk 页号，offset 36 是声明的 freelist 页总数。
- 一个 trunk 页的前 4 字节是下一个 trunk 页号（0 表示结束），随后 4 字节是本 trunk 登记的叶子页数量，再之后是该数量的 4 字节叶子页号。
- 叶子页在 freelist 中没有需要解释的 B 树结构。
- 有效 freelist 页号为 `2..actual_pages`；页 1 永远不是 freelist 页。

工具沿 trunk 链遍历，记录 trunk 页、叶子页、trunk 链接和每页登记的叶子数；检查：

- 首 trunk 与声明计数是否一致；
- trunk/leaf 越界；
- trunk 环和重复 trunk；
- trunk 与 leaf 冲突；
- 叶子计数是否能放进 usable area；
- 叶子越界、重复；
- 实际唯一 freelist 页数与头部声明总数是否一致；
- freelist 页是否又被 B 树或溢出链可达（跨结构冲突）。

发现环或结构错误时停止该链的后续遍历，保留已解析前缀，不把未读部分猜成正常页。

## 7. 页空间账本

报告中的 `pages[]` 是**审计模型**，不是 SQLite 的官方页计数器。每个页记录：

| 字段 | 含义 |
| --- | --- |
| `header_bytes` | 页 1 额外 100 字节文件头 + 该页 B 树页头（8 或 12）；特殊页按其结构记录 |
| `pointer_bytes` | B 树 cell pointer array 的 `2 * cell_count`；freelist trunk 为登记的 leaf 指针字节数 |
| `cell_bytes` / `content_bytes` | 成功解析的 cell 字节数；溢出页为 `usable_size - 4` 的 payload 区；freelist 叶子为 0 |
| `free_bytes` | `max(0, usable_size - header_bytes - pointer_bytes - cell_bytes)` |
| `fragmented_bytes` | 页头声明的 fragmented free bytes；非 B 树页为 0 |
| `reserved_bytes` | `page_size - usable_size` |
| `utilization` | `min(used, usable_size) / usable_size`，保留 6 位小数 |
| `fragmentation_ratio` | `min(fragmented_bytes, usable_size) / usable_size`，保留 6 位小数 |

其中 `used = header_bytes + pointer_bytes + cell_bytes`。特殊页模型：

- **overflow**：4 字节 next 指针 + `usable_size - 4` 的 payload 容量；
- **freelist trunk**：8 字节 trunk 头 + 实际登记 leaf 指针的字节数；
- **freelist leaf**：不解释内容，记为 0 已用内容；
- **orphan**：按页首字节猜测页类型，记为 0 已用内容，不把不可达页当作正常 B 树页。

`summary.pages.by_page_type` 按页类型汇总页数、cell 数、已用、空闲、碎片和保留区。top 碎片页和 top 低利用率页按确定性排序，最多 `top_pages` 条；同值以页号稳定排序。页 1 不参与空间利用率告警，避免把文件头开销误报为低利用率。

空间告警阈值：

- `SPACE_FRAGMENTATION_HIGH`：`fragmentation_ratio > fragmentation_warn_ratio`，且只对可达 schema/table/index 页；
- `SPACE_UTILIZATION_LOW`：`utilization < utilization_low_ratio` 且 `cell_count >= low_util_min_cells`；
- `SPACE_LEDGER_INCONSISTENT`：模型账本分项相加超过 usable size，或碎片字节超过 usable size。

这些阈值是审计策略，不是 SQLite 损坏判定。低利用率可能是合法的稀疏页；碎片率高也可能是正常的删除/更新结果。

## 8. 报告页图与文件汇总

- **reachable_pages**：B 树 visited 页与溢出链页的并集，不包含 freelist。
- **tree_pages**：所有被解释为 B 树的页，包括 schema 树。
- **overflow_pages**：属于溢出链且未被 B 树 visited 的页数。
- **freelist_pages**：已发现的 freelist trunk 与 leaf 唯一页数。
- **orphan_pages**：既不可达也不在 freelist 的 `2..actual_pages` 页。
- **out_of_range_references**：越界类异常条数；`out_of_range_pages` 是涉及的唯一页数。
- **page_count_matches_file**：声明页数等于实际完整页数且没有尾部残字节。

孤儿页只表示“从当前可达图和 freelist 都没有解释到”，不等于数据损坏。可能来源包括损坏导致的失联页、页类型无法识别、以及遍历因安全上限提前停止。报告会把状态标为 `partial` 或给出相应异常，便于人工复核。

`status=partial` 的条件是页级覆盖或任一子结构未完成：头部不可安全切片、schema 根树或对象树遍历未完成、遍历达到安全上限、freelist 遍历中断、任一溢出链未完成。`status=complete` 只表示这些结构都走完了，**不表示**没有异常；例如某个 cell 解析失败仍可能得到 `complete` 状态和 `error` 结论。各子结构自己的 `completed` / `complete` / `incomplete_chains` 字段提供更细的完成度。

## 9. 不在此格式子集内

不读取 WAL 帧、不解析 rollback journal、不处理加密页、不处理自定义 VFS、不解析虚拟表内部结构、不做索引键排序/唯一性校验、不校验 SQL 文本语义、不导出普通表字段值。完整边界见 [LIMITATIONS.md](LIMITATIONS.md)。
