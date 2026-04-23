from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_SELECTORS: dict[str, Any] = {
    "common": {
        "account_name": [
            "[data-testid='account-name']",
            ".account-name",
            "text=/계좌.*/",
        ],
        "account_masked_id": [
            "[data-testid='account-number']",
            ".account-number",
        ],
    },
    "domestic": {
        "table": [
            "table:has(th:has-text('종목명'))",
            "table:has-text('국내')",
            "table",
        ],
        "column_map": {
            "name": 0,
            "quantity": 1,
            "amount_in_krw": 4,
        },
    },
    "foreign": {
        "table": [
            "table:has(th:has-text('국가'))",
            "table:has(th:has-text('종목명'))",
            "table:has-text('해외')",
            "table",
        ],
        "column_map": {
            "market": 0,
            "name": 1,
            "quantity": 2,
            "amount_in_unit_currency": 4,
        },
    },
    "cash": {
        "table": [
            "table:has-text('예수금')",
            "table:has-text('RP')",
            "table",
        ],
        "column_map": {
            "name": 0,
            "unit_currency": 1,
            "amount_in_unit_currency": 2,
            "fx_rate_to_krw": 3,
            "amount_in_krw": 4,
        },
    },
}


@dataclass(slots=True)
class SelectorConfig:
    raw: dict[str, Any] = field(default_factory=lambda: DEFAULT_SELECTORS.copy())

    @classmethod
    def load(cls, path: str | None) -> "SelectorConfig":
        if not path:
            return cls(raw=DEFAULT_SELECTORS)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        merged = _deep_merge(DEFAULT_SELECTORS, data)
        return cls(raw=merged)

    def section(self, name: str) -> dict[str, Any]:
        return self.raw[name]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in base.items():
        result[key] = value
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
