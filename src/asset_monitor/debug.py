from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page


def prepare_debug_dir(base_dir: Path, captured_at: str) -> Path:
    timestamp = captured_at.replace(":", "").replace("-", "").replace("T", "_").replace("+", "_")
    path = base_dir / timestamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_page_debug(page: Page, debug_dir: Path, name: str) -> None:
    screenshot_path = debug_dir / f"{name}.png"
    html_path = debug_dir / f"{name}.html"
    try:
        page.screenshot(path=str(screenshot_path), full_page=False, timeout=5000)
    except Exception:
        pass
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
