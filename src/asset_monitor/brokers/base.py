from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from asset_monitor.models import AssetRecord


@dataclass(slots=True)
class BrokerPartialCollectionError(Exception):
    results: dict[str, list[AssetRecord]]
    errors: dict[str, str]

    def __str__(self) -> str:
        return "; ".join(f"{key}={value}" for key, value in sorted(self.errors.items()))


class BrokerCollector(Protocol):
    def collect(self, captured_at: str) -> dict[str, list[AssetRecord]]:
        ...
