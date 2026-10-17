"""Shared, DB-only minute candles; never a source of primary runner events."""

from .manager import MinuteDataService

__all__ = ["MinuteDataService"]
