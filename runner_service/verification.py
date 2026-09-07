from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pynecore import lib
from pynecore.core.script_runner import _set_lib_properties
from pynecore.lib.request import get_security_ctx
from pynecore.types.ohlcv import OHLCV


_MAX_PENDING_RESULTS = 128


@dataclass
class VerificationResumeState:
    generation_id: str = ""
    last_processed_timestamp_ms: int | None = None
    pending_bar_timestamp_ms: int | None = None
    source_hashes: dict[str, str] = field(default_factory=dict)
    pending_results: dict[int, dict[str, Any]] = field(default_factory=dict)
    complete: bool = True

    def payload(self, *, enabled: bool) -> dict[str, Any] | None:
        if not enabled or not self.generation_id:
            return None
        return {
            "generation_id": self.generation_id,
            "last_processed_timestamp_ms": self.last_processed_timestamp_ms,
            "pending_bar_timestamp_ms": self.pending_bar_timestamp_ms,
            "source_hashes": dict(self.source_hashes),
            "pending_results": list(self.pending_results.values()),
            "complete": self.complete,
        }

    def reset(
        self,
        *,
        generation_id: str = "",
        last_processed_timestamp_ms: int | None = None,
        pending_bar_timestamp_ms: int | None = None,
        source_hashes: dict[str, str] | None = None,
    ) -> None:
        self.generation_id = generation_id
        self.last_processed_timestamp_ms = last_processed_timestamp_ms
        self.pending_bar_timestamp_ms = pending_bar_timestamp_ms
        self.source_hashes = dict(source_hashes or {})
        self.pending_results.clear()
        self.complete = True

    def remember(
        self,
        payload: dict[str, Any],
        *,
        pending_bar_timestamp_ms: int,
    ) -> None:
        timestamp_ms = int(payload["candle_timestamp_ms"])
        self.last_processed_timestamp_ms = timestamp_ms
        self.pending_bar_timestamp_ms = int(pending_bar_timestamp_ms)
        self.pending_results[timestamp_ms] = dict(payload)
        while len(self.pending_results) > _MAX_PENDING_RESULTS:
            self.pending_results.pop(next(iter(self.pending_results)))
            self.complete = False

    def acknowledge(self, generation_id: str, candle_timestamp_ms: Any) -> None:
        if generation_id != self.generation_id:
            return
        try:
            timestamp_ms = int(candle_timestamp_ms)
        except (TypeError, ValueError):
            return
        self.pending_results.pop(timestamp_ms, None)


class VerificationContinuityError(RuntimeError):
    def __init__(self, expected_timestamp_ms: int, received_timestamp_ms: int) -> None:
        super().__init__(
            "verification candle continuity mismatch: "
            f"expected={expected_timestamp_ms} received={received_timestamp_ms}"
        )
        self.expected_timestamp_ms = expected_timestamp_ms
        self.received_timestamp_ms = received_timestamp_ms


def calculate_candle(
    ctx: Any,
    confirmed_bar_and_new_bar: list[Any],
    *,
    bar_list_to_ohlcv: Callable[[list[Any]], OHLCV],
    hide_zero_volume_bars: Callable[[str | None], bool],
    is_visible_ohlcv: Callable[..., bool],
    plot_values_from_step: Callable[
        [tuple[Any, ...] | None, int | None],
        dict[str, float | int | None] | None,
    ],
    build_result: Callable[..., dict[str, Any]],
    generation_id: str,
) -> tuple[dict[str, Any], OHLCV, OHLCV]:
    confirmed_ohlcv = bar_list_to_ohlcv(confirmed_bar_and_new_bar[0])
    new_ohlcv = bar_list_to_ohlcv(confirmed_bar_and_new_bar[1])
    expected_timestamp = int(ctx.last_new_bar_ts_sec)
    if int(confirmed_ohlcv.timestamp) != expected_timestamp:
        raise VerificationContinuityError(
            expected_timestamp * 1000,
            int(confirmed_ohlcv.timestamp) * 1000,
        )

    hide_zero_volume = hide_zero_volume_bars(
        getattr(ctx.runner.syminfo, "prefix", None)
    )
    confirmed_visible = is_visible_ohlcv(
        confirmed_ohlcv,
        hide_zero_volume=hide_zero_volume,
    )
    new_visible = is_visible_ohlcv(
        new_ohlcv,
        hide_zero_volume=hide_zero_volume,
    )
    confirmed_plot_values = None

    if confirmed_visible:
        pending = ctx.stream.q
        if pending:
            pending_timestamp = int(pending[-1].timestamp)
            if pending_timestamp != int(confirmed_ohlcv.timestamp):
                raise RuntimeError(
                    "verification pending candle mismatch: "
                    f"expected={confirmed_ohlcv.timestamp} "
                    f"actual={pending_timestamp}"
                )
            ctx.stream.replace_last(confirmed_ohlcv)
        else:
            ctx.stream.append(confirmed_ohlcv)

        # run_iter() increments bar_index before evaluating the queued candle.
        # While paused here, runner.bar_index still points to the prior candle.
        confirmed_bar_index = int(ctx.runner.bar_index) + 1
        if new_visible:
            ctx.stream.append(new_ohlcv)

        ctx.runner.last_bar_index = confirmed_bar_index + (1 if new_visible else 0)
        # The realtime gate treats the evaluated candle as last_bar_index - 1,
        # including exchanges whose zero-volume next candle remains hidden.
        ctx.runner.script.last_bar_index = confirmed_bar_index + 1

        security_ctx = get_security_ctx()
        if security_ctx is not None:
            security_ctx.update_base_bar(confirmed_ohlcv, confirmed_bar_index)
            if new_visible:
                security_ctx.update_base_bar(new_ohlcv, confirmed_bar_index + 1)

        ctx.runner.script.pre_run = False
        step_res = ctx.runner.step()
        if step_res is None:
            raise RuntimeError("verification runner produced no candle")
        step_candle = step_res[0]
        if int(step_candle.timestamp) != int(confirmed_ohlcv.timestamp):
            raise RuntimeError(
                "verification runner stepped unexpected candle: "
                f"expected={confirmed_ohlcv.timestamp} "
                f"actual={step_candle.timestamp}"
            )
        confirmed_plot_values = plot_values_from_step(
            step_res,
            int(confirmed_ohlcv.timestamp),
        )

        # Orders created on the confirmed candle fill on the synthetic next bar
        # without executing that new candle's strategy body.
        if ctx.runner.script.position is not None:
            _set_lib_properties(
                new_ohlcv,
                confirmed_bar_index + 1,
                ctx.runner.tz,
                lib,
            )
            ctx.runner.script.position.process_orders()
    else:
        pending = ctx.stream.q
        if pending and int(pending[-1].timestamp) == int(confirmed_ohlcv.timestamp):
            ctx.stream.discard_last()
        if new_visible:
            ctx.stream.append(new_ohlcv)

    ctx.last_new_bar_ts_sec = int(new_ohlcv.timestamp)
    result = build_result(
        role="verification",
        generation_id=generation_id,
        confirmed_ohlcv=confirmed_ohlcv,
        plot_values=confirmed_plot_values,
    )
    return result, confirmed_ohlcv, new_ohlcv
