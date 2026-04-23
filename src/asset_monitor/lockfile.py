from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileLock:
    path: Path

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError as exc:
            raise RuntimeError(f"Lock file already exists: {self.path}") from exc
        os.write(self._fd, str(os.getpid()).encode("utf-8"))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if hasattr(self, "_fd"):
            os.close(self._fd)
        if self.path.exists():
            self.path.unlink()
