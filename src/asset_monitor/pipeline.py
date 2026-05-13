from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .brokers import BrokerPartialCollectionError, create_broker_collector
from .config import AppConfig
from .debug import prepare_debug_dir
from .lockfile import FileLock
from .models import AssetRecord, RunLogEntry
from .parsing import clean_text
from .raw_log import LocalSnapshotLogger
from .sheets import GoogleSheetsWriter, build_run_log_message


def run_pipeline(config: AppConfig) -> None:
    captured_at = datetime.now(ZoneInfo(config.timezone)).isoformat(timespec="seconds")
    raw_logger = LocalSnapshotLogger(config.logs_dir)
    sheets = GoogleSheetsWriter(config.google_service_account_info, config.spreadsheet_id)

    with FileLock(config.lock_file):
        all_records: list[AssetRecord] = []
        failures: dict[str, str] = {}
        failed_account_keys: set[tuple[str, str]] = set()

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
                failed_account_keys.add((account.broker, account.name))
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
                failed_account_keys.add((account.broker, account.name))
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

        _canonicalize_records(all_records)
        raw_logger.append(captured_at, all_records)

        sheet_records = all_records
        if failed_account_keys:
            sheet_records = all_records + raw_logger.latest_records_for_accounts(
                failed_account_keys,
                before_captured_at=captured_at,
            )
            _canonicalize_records(sheet_records)

        sheets.ensure_tabs()
        sheets.refresh_latest_views(sheet_records)
        sheets.refresh_sector_views(
            sheet_records,
            captured_at=captured_at,
            timezone=config.timezone,
        )
        sheets.append_daily_trend(
            sheet_records,
            captured_at=captured_at,
            timezone=config.timezone,
        )

        if failures:
            raise RuntimeError(
                "일부 계정 수집이 실패했습니다: "
                + "; ".join(f"{owner}={message}" for owner, message in sorted(failures.items()))
            )


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value.strip())
    return safe or "account"


def _canonicalize_records(records: list[AssetRecord]) -> None:
    symbols_by_name: dict[str, str] = {}
    for record in records:
        if record.asset_group == "cash_equivalent":
            continue
        name = clean_text(record.name)
        symbol = clean_text(record.symbol)
        if name and symbol and not _is_internal_product_symbol(symbol):
            symbols_by_name.setdefault(name, symbol)

    for record in records:
        if record.asset_group == "cash_equivalent":
            continue
        canonical_symbol = symbols_by_name.get(clean_text(record.name))
        if canonical_symbol and (not record.symbol or _is_internal_product_symbol(record.symbol)):
            record.symbol = canonical_symbol


def _is_internal_product_symbol(value: object) -> bool:
    symbol = clean_text(value)
    return len(symbol) == 12 and symbol.isdigit()
