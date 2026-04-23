from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import AssetRecord


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
