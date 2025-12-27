from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DataState:
    collected_trades: List[Dict[str, Any]] = field(default_factory=list)

    # bar = [ts_ms, open, high, low, close, volume]
    # 여기에는 미완성 바도 포함해서 시간순으로 쌓인다
    live_bars: List[List[Any]] = field(default_factory=list)

    last_fix_bar_ts: Optional[int] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Pending event to send when client connects (for prerun after history download)
    pending_prerun_event: Optional[Dict[str, Any]] = None
