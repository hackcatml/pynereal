from __future__ import annotations

import time


POST_BAR_TASK_OFFSET_SECONDS = 10.0
POST_BAR_TASK_WINDOW_SECONDS = 10.0


def seconds_until_post_bar_task_slot(
    now_seconds: float | None = None,
) -> float:
    """Return the delay until the next minute boundary plus 10 seconds."""

    current = time.time() if now_seconds is None else now_seconds
    second = current % 60.0
    delay = (POST_BAR_TASK_OFFSET_SECONDS - second) % 60.0
    return 0.0 if delay < 0.001 else delay


def seconds_until_post_bar_task_window(
    now_seconds: float | None = None,
) -> float:
    """Return the delay until the post-bar background-task window."""

    current = time.time() if now_seconds is None else now_seconds
    second = current % 60.0
    window_start = POST_BAR_TASK_OFFSET_SECONDS
    window_end = window_start + POST_BAR_TASK_WINDOW_SECONDS

    if window_start <= second < window_end:
        return 0.0
    if second < window_start:
        return window_start - second
    return 60.0 - second + window_start
