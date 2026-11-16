"""Assemble the final deterministic report dictionary."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .. import __version__
from ..config import AuditConfig
from ..db.overflow import OverflowAuditor
from ..io.snapshot import FileSnapshot
from ..util.hashing import canonical_digest
from .ledger import summarize_pages
from .model import Problem, worst_level
from .rules import build_findings, rule_summary


def empty_freelist(header) -> dict[str, Any]:
    return {
        "first_trunk": header.first_freelist_trunk,
        "declared_count": header.freelist_pages,
        "trunk_pages": [],
        "leaf_pages": [],
        "free_pages": [],
        "actual_count": 0,
        "completed": False,
        "trunk_links": [],
        "trunk_count": 0,
        "leaf_count": 0,
    }
def empty_overflow() -> dict[str, Any]:
    return {"chains": [], "page_count": 0, "complete": False, "incomplete_chains": 0, "first_pages": []}
def overflow_payload(overflow: OverflowAuditor) -> dict[str, Any]:
    chains = []
    for chain in overflow.result.chains:
        item = asdict(chain)
        item["actual_pages"] = len(chain.pages)
        item["pages"] = list(chain.pages)
        chains.append(item)
    chains.sort(key=lambda item: (item["origin_page"], item["cell_index"], item["first_page"]))
    return {
        "chains": chains,
        "page_count": len(overflow.result.reachable_pages),
        "complete": all(item["complete"] for item in chains),
        "incomplete_chains": sum(1 for item in chains if not item["complete"]),
        "first_pages": sorted({item["first_page"] for item in chains}),
    }
def build_report(config: AuditConfig, snapshot: FileSnapshot, header, problems: list[Problem], pages: list[dict[str, Any]], traversal: dict[str, Any], freelist: dict[str, Any], overflow: dict[str, Any], schema: dict[str, Any], limitations: list[str], partial: bool, page_summary: dict[str, Any] | None = None, top_pages: dict[str, Any] | None = None, file_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    findings = build_findings(problems, config.max_findings_per_code)
    if not config.include_info_findings:
        findings = [item for item in findings if item.level != "info"]
    counts = {level: 0 for level in ("critical", "error", "warning", "info")}
    for finding in findings:
        counts[finding.level] += 1
    level = worst_level(findings)
    conclusion = "ok" if level == "info" else level
    if page_summary is None:
        page_summary = summarize_pages([])
    if top_pages is None:
        top_pages = {"fragmentation": [], "low_utilization": []}
    if file_summary is None:
        file_summary = {
            "file_size": snapshot.size,
            "page_size": header.page_size,
            "usable_size": header.usable_size,
            "declared_pages": header.declared_pages,
            "actual_pages": header.actual_page_count,
            "reachable_pages": 0,
            "tree_pages": 0,
            "overflow_pages": 0,
            "freelist_pages": 0,
            "orphan_pages": 0,
            "unknown_page_type_pages": 0,
            "out_of_range_references": 0,
            "out_of_range_pages": 0,
            "schema_objects": len(schema.get("objects", [])),
            "page_count_matches_file": header.declared_pages == header.actual_page_count,
            "wal_present": header.wal_present,
        }
    report: dict[str, Any] = {
        "report_version": "1.0",
        "tool": {"name": "page-ledger", "version": __version__, "runtime": "python-stdlib"},
        "status": "partial" if partial else "complete",
        "conclusion": conclusion,
        "snapshot": snapshot.to_dict(),
        "header": header.to_dict(),
        "config": config.to_dict(),
        "summary": {
            "file": file_summary,
            "pages": page_summary,
            "top_pages": top_pages,
            "finding_counts": counts,
            "rule_count": len({finding.code for finding in findings}),
        },
        "pages": sorted(pages, key=lambda item: item["page_no"]),
        "findings": [finding.to_dict() for finding in findings],
        "rule_stats": rule_summary(findings),
        "traversal": traversal,
        "freelist": freelist,
        "overflow": overflow,
        "schema": schema,
        "limitations": list(limitations),
    }
    report["integrity"] = {
        "algorithm": "sha256",
        "digest": canonical_digest(report),
        "covers": "canonical JSON of report excluding integrity",
    }
    return report
