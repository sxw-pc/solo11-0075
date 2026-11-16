"""Audit SQLite overflow-page chains without reading row values."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from ..audit.model import OverflowChain, Problem
from ..config import AuditConfig
from ..io.reader import PageReader, be_u32


@dataclass
class OverflowAudit:
    chains: list[OverflowChain] = field(default_factory=list)
    reachable_pages: set[int] = field(default_factory=set)
    problems: list[Problem] = field(default_factory=list)
    page_owners: dict[int, tuple[int, int]] = field(default_factory=dict)
    def owner_of(self, page_no: int) -> tuple[int, int] | None:
        return self.page_owners.get(page_no)
class OverflowAuditor:
    def __init__(self, reader: PageReader, header, config: AuditConfig):
        self.reader = reader
        self.header = header
        self.config = config
        self.result = OverflowAudit()
        self._bytes_per_page = max(1, header.usable_size - 4)
        self._actual_pages = reader.actual_page_count
    def _problem(
        self,
        code: str,
        origin: int,
        field: str,
        expected: object,
        actual: object,
        evidence: str,
    ) -> None:
        self.result.problems.append(Problem(code, page=origin, field=field, expected=expected, actual=actual, evidence=evidence))
    def walk(self, origin_page: int, cell_index: int, first_page: int, payload_size: int, local_size: int) -> OverflowChain:
        overflow_bytes = max(0, payload_size - local_size)
        expected = ceil(overflow_bytes / self._bytes_per_page) if overflow_bytes else 0
        chain = OverflowChain(
            first_page=first_page,
            origin_page=origin_page,
            cell_index=cell_index,
            payload_bytes=payload_size,
            local_bytes=local_size,
            expected_pages=expected,
            audited_bytes=0,
        )
        if expected == 0:
            chain.complete = True
            chain.status = "complete"
            self.result.chains.append(chain)
            return chain
        if not 2 <= first_page <= self._actual_pages:
            self._problem("OVFL_FIRST_PAGE_INVALID", origin_page, "overflow_page", f"2..{self._actual_pages}", first_page, f"cell[{cell_index}] 的溢出首页非法")
            chain.status = "invalid-first-page"
            self.result.chains.append(chain)
            return chain
        current = first_page
        seen: set[int] = set()
        max_pages = self.config.max_overflow_chain
        while current:
            if not 2 <= current <= self._actual_pages:
                self._problem("OVFL_PAGE_OUT_OF_RANGE", origin_page, "overflow_page", f"2..{self._actual_pages}", current, f"链 {first_page} 的 next 指针越界")
                chain.status = "out-of-range"
                break
            if current in seen:
                self._problem("OVFL_CHAIN_CYCLE", origin_page, "overflow_page", "不与链内已见页重复", current, f"链 {first_page} 回到页 {current}")
                chain.status = "cycle"
                break
            owner = self.result.page_owners.get(current)
            if owner is not None and owner != (origin_page, cell_index):
                self._problem("OVFL_DUPLICATE_PAGE", origin_page, "overflow_page", "每页只属于一个溢出链", f"{origin_page}:cell[{cell_index}] -> {current}", f"此前由页 {owner[0]} cell[{owner[1]}] 引用")
                chain.status = "duplicate"
                break
            seen.add(current)
            self.result.page_owners[current] = (origin_page, cell_index)
            self.result.reachable_pages.add(current)
            chain.pages.append(current)
            page = self.reader.page(current)
            if page is None:
                chain.status = "unreadable"
                break
            next_page = be_u32(page, 0)
            if next_page == 0:
                if len(chain.pages) < expected:
                    self._problem("OVFL_CHAIN_BROKEN", origin_page, "overflow_page", expected, len(chain.pages), f"payload 需要 {expected} 页，链在页 {current} 提前结束")
                    chain.status = "broken"
                elif len(chain.pages) > expected:
                    self._problem("OVFL_LENGTH_MISMATCH", origin_page, "overflow_pages", expected, len(chain.pages), "链页数超过 payload 所需页数")
                    chain.status = "length-mismatch"
                    chain.complete = True
                else:
                    chain.complete = True
                    chain.status = "complete"
                break
            if next_page in seen:
                self._problem("OVFL_CHAIN_CYCLE", origin_page, "overflow_page", "不与链内已见页重复", next_page, f"链 {first_page} 在页 {current} 指向链内页 {next_page}")
                chain.status = "cycle"
                break
            if next_page < 2 or next_page > self._actual_pages:
                self._problem("OVFL_PAGE_OUT_OF_RANGE", origin_page, "overflow_page", f"2..{self._actual_pages}", next_page, f"页 {current} 的 next 指针越界")
                chain.status = "out-of-range"
                break
            if len(chain.pages) >= max_pages:
                self._problem("OVFL_LIMIT_REACHED", origin_page, "overflow_pages", f"<= {max_pages}", len(chain.pages) + 1, "达到溢出链审计安全上限")
                chain.status = "limit"
                break
            current = next_page
        chain.audited_bytes = min(overflow_bytes, len(chain.pages) * self._bytes_per_page)
        if chain.status in {"out-of-range", "cycle", "duplicate", "limit", "unreadable", "broken"}:
            self._problem("OVFL_INCOMPLETE", origin_page, "overflow_audit", "完成整条链", f"已审计 {len(chain.pages)} 页 / 需要 {expected} 页", f"链状态={chain.status}，保留已解析前缀")
        self.result.chains.append(chain)
        return chain
