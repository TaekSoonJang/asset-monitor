from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .brokers import BrokerPartialCollectionError, create_broker_collector
from .config import AppConfig
from .debug import prepare_debug_dir
from .lockfile import FileLock
from .models import RunLogEntry
from .raw_log import LocalSnapshotLogger
from .sheets import GoogleSheetsWriter, build_run_log_message


def run_pipeline(config: AppConfig) -> None:
    captured_at = datetime.now(ZoneInfo(config.timezone)).isoformat(timespec="seconds")
    raw_logger = LocalSnapshotLogger(config.logs_dir)
    sheets = GoogleSheetsWriter(config.google_service_account_info, config.spreadsheet_id)

    with FileLock(config.lock_file):
        all_records: list = []
        failures: dict[str, str] = {}

        for account in config.accounts:
            debug_dir = prepare_debug_dir(
                config.debug_output_dir / account.broker / _safe_name(account.name),
                captured_at,
            )
            collector = create_broker_collector(config, account, debug_dir)
            results: dict[str, list] = {"domestic": [], "foreign": [], "cash": []}

            try:
                results = collector.collect(captured_at)
                records = results["domestic"] + results["foreign"] + results["cash"]
                if not records:
                    raise RuntimeError("Collector returned 0 records.")

                all_records.extend(records)
                sheets.ensure_tabs()
                sheets.append_run_log(
                    RunLogEntry(
                        captured_at=captured_at,
                        broker_name=account.broker,
                        owner_name=account.name,
                        status="success",
                        total_records=len(records),
                        domestic_records=len(results["domestic"]),
                        foreign_records=len(results["foreign"]),
                        cash_records=len(results["cash"]),
                        message="성공",
                        debug_dir=str(debug_dir),
                    )
                )
            except BrokerPartialCollectionError as exc:
                failures[f"{account.broker}:{account.name}"] = build_run_log_message(exc.errors)
                sheets.ensure_tabs()
                sheets.append_run_log(
                    RunLogEntry(
                        captured_at=captured_at,
                        broker_name=account.broker,
                        owner_name=account.name,
                        status="failed",
                        total_records=sum(len(items) for items in exc.results.values()),
                        domestic_records=len(exc.results["domestic"]),
                        foreign_records=len(exc.results["foreign"]),
                        cash_records=len(exc.results["cash"]),
                        message=build_run_log_message(exc.errors),
                        debug_dir=str(debug_dir),
                    )
                )
            except Exception as exc:
                failures[f"{account.broker}:{account.name}"] = str(exc)
                sheets.ensure_tabs()
                sheets.append_run_log(
                    RunLogEntry(
                        captured_at=captured_at,
                        broker_name=account.broker,
                        owner_name=account.name,
                        status="failed",
                        total_records=sum(len(items) for items in results.values()),
                        domestic_records=len(results["domestic"]),
                        foreign_records=len(results["foreign"]),
                        cash_records=len(results["cash"]),
                        message=str(exc),
                        debug_dir=str(debug_dir),
                    )
                )

        if not all_records:
            raise RuntimeError("모든 계정 수집이 실패했습니다.")

        raw_logger.append(captured_at, all_records)

        if failures:
            raise RuntimeError(
                "일부 계정 수집이 실패했습니다: "
                + "; ".join(f"{owner}={message}" for owner, message in sorted(failures.items()))
            )

        sheets.ensure_tabs()
        sheets.refresh_latest_views(all_records)
        sheets.refresh_sector_views(
            all_records,
            captured_at=captured_at,
            timezone=config.timezone,
        )
        sheets.append_daily_trend(
            all_records,
            captured_at=captured_at,
            timezone=config.timezone,
        )


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value.strip())
    return safe or "account"
