from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .models import AssetRecord


_DECIMAL_FIELDS = {"quantity", "amount_in_unit_currency", "fx_rate_to_krw", "amount_in_krw"}


class LocalSnapshotLogger:
    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = logs_dir

    def append(self, captured_at: str, records: Iterable[AssetRecord]) -> Path:
        date_part = captured_at.split("T", 1)[0]
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.logs_dir / f"raw_snapshots-{date_part}.jsonl"

        with log_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

        return log_path

    def latest_records_for_accounts(
        self,
        account_keys: set[tuple[str, str]],
        *,
        before_captured_at: str | None = None,
    ) -> list[AssetRecord]:
        if not account_keys or not self.logs_dir.exists():
            return []

        latest_captured_at: dict[tuple[str, str], str] = {}
        latest_records: dict[tuple[str, str], dict[tuple[str, str, str, str, str, str], AssetRecord]] = {}
        for log_path in sorted(self.logs_dir.glob("raw_snapshots-*.jsonl")):
            with log_path.open(encoding="utf-8") as handle:
                for line in handle:
                    record = _record_from_json_line(line)
                    if record is None:
                        continue
                    if before_captured_at is not None and record.captured_at >= before_captured_at:
                        continue
                    account_key = (record.broker_name, record.owner_name)
                    if account_key not in account_keys:
                        continue
                    previous_captured_at = latest_captured_at.get(account_key)
                    if previous_captured_at is None or record.captured_at > previous_captured_at:
                        latest_captured_at[account_key] = record.captured_at
                        latest_records[account_key] = {}
                    if record.captured_at == latest_captured_at[account_key]:
                        latest_records[account_key][record.identity_key()] = record

        records: list[AssetRecord] = []
        for account_records in latest_records.values():
            records.extend(account_records.values())
        return records


def _record_from_json_line(line: str) -> AssetRecord | None:
    try:
        payload = json.loads(line)
        for field in _DECIMAL_FIELDS:
            payload[field] = Decimal(payload[field]) if payload.get(field) else None
        return AssetRecord(**payload)
    except (json.JSONDecodeError, TypeError, KeyError, InvalidOperation):
        return None
