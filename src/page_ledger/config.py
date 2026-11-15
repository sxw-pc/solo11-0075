"""Configuration for the page ledger audit.

All thresholds live here so that reports and rule evaluation are explicit and
repeatable.  A JSON file supplied with ``--config`` may override any field.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping


class ConfigError(ValueError):
    """Raised when an override file is not a valid audit configuration."""
@dataclass(frozen=True)
class AuditConfig:
    """Deterministic thresholds and safety limits."""
    top_pages: int = 10
    fragmentation_warn_ratio: float = 0.05
    utilization_low_ratio: float = 0.20
    max_pages: int = 100_000
    max_btree_depth: int = 64
    max_overflow_chain: int = 4_096
    max_findings_per_code: int = 250
    low_util_min_cells: int = 2
    include_info_findings: bool = True
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AuditConfig":
        if not isinstance(values, Mapping):
            raise ConfigError("配置必须是 JSON 对象")
        known = {item.name: item for item in fields(cls)}
        cleaned: dict[str, Any] = {}
        for key, value in values.items():
            if key not in known:
                raise ConfigError(f"未知配置项: {key}")
            current = getattr(cls(), key)
            if isinstance(current, bool):
                if not isinstance(value, bool):
                    raise ConfigError(f"{key} 必须是布尔值")
            elif isinstance(current, int):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ConfigError(f"{key} 必须是非负整数")
            elif isinstance(current, float):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConfigError(f"{key} 必须是数字")
                if not 0.0 <= float(value) <= 1.0:
                    raise ConfigError(f"{key} 必须在 0 到 1 之间")
                value = float(value)
            cleaned[key] = value
        merged = cls().to_dict()
        merged.update(cleaned)
        return cls(**merged)
def load_config(path: str | Path | None) -> AuditConfig:
    if path is None:
        return AuditConfig()
    try:
        import json
        raw = Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"配置文件不是合法 JSON: {exc}") from exc
    return AuditConfig.from_mapping(payload)
