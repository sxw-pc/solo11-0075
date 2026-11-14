# 页账核验台（page-ledger）

`page-ledger` 是一个纯本地、完全离线、只读、确定性的 SQLite 数据库文件页级健康检查与空间账本审计工具。它面向单文件数据库快照，在归档、迁移、备份验收和磁盘空间排查前，把文件头声明、B 树可达页、freelist、溢出页链、页空间占用和异常证据展开成可复核的页账。

它**不是**修复、恢复、vacuum、性能分析或数据导出工具；不会写回数据库，也不会读取业务字段值。

## 合规与只读声明

- 仅用于审计本人拥有或已获得明确授权的本地数据库文件。
- 对被审计文件只用 `rb` / SQLite `mode=ro&immutable=1` 方式读取；不修改、不重命名、不删除原文件。
- 不执行 `VACUUM`、`REINDEX`、`PRAGMA writable_schema` 或任何用户 SQL。
- 运行期不发起网络请求，不访问外部 API、CDN、云服务或消息队列。
- 不读取、不合并、不修改同名 `-wal` 文件；发现时只提示结果不包含未合并 WAL 帧。
- 不解析或展示业务记录内容；只解析页结构、`sqlite_schema` 元数据和合成 fixture。
- 仓库内所有数据库、表名、列名和文本均为合成 fixture，由 `fixtures/build_fixtures.py` 确定性生成。

完整边界见 [docs/COMPLIANCE.md](docs/COMPLIANCE.md) 与 [docs/LIMITATIONS.md](docs/LIMITATIONS.md)。

## 快速开始

要求 Python 3.10+。运行期只使用标准库，测试使用 pytest。

```bash
# 一条命令：生成 fixture 并运行全部测试
make test

# 一条命令：对内置 clean.db 执行审计并生成报告
make audit-sample
```

没有 `make` 时，可执行等价命令：

```bash
python fixtures/build_fixtures.py
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m page_ledger audit \
  --db fixtures/generated/clean.db \
  --out out/sample
```

项目声明了 `page-ledger` console script，但日常使用推荐 `PYTHONPATH=src python -m page_ledger`。如需在已具备 setuptools 的离线环境中安装为本地命令：

```bash
python -m pip install -e . --no-build-isolation --no-deps
page-ledger audit --db fixtures/generated/clean.db --out out/sample
```

项目不声明运行期第三方依赖，也不会在审计命令中下载依赖。

## 命令

### audit

```bash
python -m page_ledger audit \
  --db fixtures/generated/overflow.db \
  --out out/overflow-audit \
  --config thresholds.json \
  --ledger out/audit-ledger.sqlite
```

参数：

| 参数 | 说明 |
| --- | --- |
| `--db` | 本地 SQLite 文件路径，只读打开 |
| `--out` | 报告输出目录，自动创建 |
| `--config` | 可选 JSON 阈值配置 |
| `--ledger` | 审计台账路径，默认 `<out>/audit-ledger.sqlite` |
| `--fail-on` | 可选 `warning/error/critical`；结论达到级别时退出码 1 |

默认审计成功即退出码 0，异常写入报告；只有输入、配置或输出故障才退出码 2。使用 `--fail-on` 可把严重结论用于归档流水线门禁。

### verify

```bash
python -m page_ledger verify \
  --report out/sample/report.json \
  --db fixtures/generated/clean.db
```

`verify` 只读复核：

- 报告自身 `integrity.digest` 是否被篡改；
- 原始数据库 sha256、大小、路径是否与报告登记一致；
- 用报告中的阈值重新执行审计，比对头部、页账、汇总、异常、freelist、溢出链和 schema 对账；
- 报告与原始数据库关键统计是否可复现。

通过退出码 0，失败退出码 1。加 `--json` 可输出机器可读的复核结果。

## 输出

每次 `audit` 在输出目录写入：

```text
report.json                 完整结构化结果，键和数组顺序稳定
report.md                   面向人工复核的摘要
report.html                 自包含 HTML，内联 CSS，无外部资源
audit-ledger.sqlite         本地审计台账
```

`report.json` 主要字段：

- `snapshot`：文件路径、大小、sha256、mtime、只读标记、WAL 存在提示。
- `header`：magic、页大小、usable size、保留区、声明页数、freelist 头、schema cookie、编码、auto-vacuum 推导值等。
- `pages`：每个已解释页的类型、父页、层级、cell 数、页头/指针/cell 空间、空闲、碎片、保留区、利用率和碎片率。
- `findings`：稳定错误码、级别、页号、字段、期望值、实际值、证据和阻断标记。
- `summary`：文件级页数账本、按页类型汇总、top 碎片页、top 低利用率页、级别计数。
- `traversal`、`freelist`、`overflow`、`schema`：四类结构的可复核明细。
- `integrity`：规范化 JSON 的 sha256，用于检测报告改动。

HTML 中所有动态文本都经过 HTML 转义，可直接离线打开。

## 审计内容

- **F1 文件头**：100 字节头、页大小、usable size、保留区、声明页数、freelist 指针、编码、auto-vacuum 标志、文件长度一致性、WAL 提示。
- **F2 B 树遍历**：表/索引内部页与叶子页、cell pointer array、cell 数量、右子页、层级、父页、越界、重叠、环和重复引用；坏页不会导致整个审计崩溃。
- **F3 空间账本**：页头、cell pointer、cell 内容、空闲区、碎片、保留区；按页类型汇总；确定性 top N。
- **F4 引用图**：可达页、孤儿页、freelist 冲突、越界引用、重复引用、环引用；每个异常带位置和证据。
- **F5 溢出页**：payload 本地大小、溢出首页、链长、断链、环、重复页、长度不匹配、前缀证据和“未完成”标记。
- **F6 schema 对账**：从 `sqlite_schema` 解出表/索引根页，核对可达性和页类型；只读 `sqlite3` 旁证查询仅作对照，失败只记录“旁证不可用”。
- **F7 规则引擎**：`HDR_*`、`PAGE_*`、`BTREE_*`、`FREE_*`、`OVFL_*`、`SCHEMA_*`、`SPACE_*`、`AUDIT_*`；阈值可配置。
- **F8 报告与台账**：JSON / Markdown / HTML、SQLite 审计台账、`verify` 只读复核。

规则清单见 [docs/RULES.md](docs/RULES.md)，SQLite 格式子集见 [docs/FORMAT.md](docs/FORMAT.md)。

## 合成 fixture

运行 `python fixtures/build_fixtures.py` 后生成到 `fixtures/generated/`：

| 文件 | 用途 |
| --- | --- |
| `clean.db` | 普通表、主键、索引、内部页和叶子页 |
| `freelist.db` | 删除大量数据后的 trunk/leaf freelist |
| `overflow.db` | 多页 payload 的溢出链 |
| `indexed.db` | 唯一、复合、多列索引 |
| `broken_header.db` | 篡改 magic 的头部损坏库 |
| `bad_cell_ptr.db` | 越界 cell pointer |
| `freelist_cycle.db` | freelist trunk 自环 |
| `overflow_break.db` | 篡改溢出链 next 指针 |

生成过程不联网、不依赖外部文件；同一环境重复生成内容一致。生成目录不提交大二进制文件。

## 阈值配置

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

未知键、类型错误或比例越界会以退出码 2 拒绝运行。

## 测试

```bash
make test
```

测试覆盖头部错误、页分类、内部/叶子页、cell pointer 越界与重叠、B 树环/重复/越界、freelist trunk/leaf、溢出链断/环/重复/长度不匹配、schema 根页与旁证不可用、规则级别与证据、JSON/Markdown/HTML、CLI `audit`/`verify`、只读性、确定性、fixture 可重复生成和单文件行数限制。

## 目录结构

```text
page-ledger/
  README.md
  Makefile
  pyproject.toml
  docs/
    FORMAT.md  RULES.md  COMPLIANCE.md  LIMITATIONS.md
  src/page_ledger/
    cli.py  config.py  __main__.py
    io/       reader.py snapshot.py
    db/       header.py pages.py btree.py freelist.py overflow.py schema.py
    audit/    model.py ledger.py rules.py engine.py schema_check.py
              report_builder.py verify.py
    report/   json_report.py markdown_report.py html_report.py
    store/    audit_store.py
    util/     hashing.py ordering.py
  fixtures/
    build_fixtures.py  generated/  .gitkeep
  tests/
  out/
```

## 明确不做

不修复数据库，不执行 VACUUM/REINDEX，不恢复删除数据，不导出业务字段，不处理加密 SQLite，不合并 WAL，不做并发写库在线审计，不做性能/慢查询/索引建议，不上传文件，不提供常驻 Web 服务。
