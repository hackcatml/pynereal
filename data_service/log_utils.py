from __future__ import annotations

from datetime import datetime


def log_with_time(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")
