from __future__ import annotations

import sys

from .config import load_config
from .pipeline import run_pipeline


def main() -> int:
    try:
        config = load_config()
        run_pipeline(config)
        return 0
    except Exception as exc:
        print(f"asset-monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
