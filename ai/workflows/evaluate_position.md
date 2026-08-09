# Evaluate Position

## Goal

Evaluate the current state and risk of a running strategy position using fresh,
read-only evidence.

## Inputs

- PyneReal session identifier
- Strategy name and script path
- Exchange account or provider identifier
- Symbol and timeframe

## Procedure

1. Call `get_session_evaluation_context` without a session ID when the user's
   description must be resolved. Select exactly one active session using its
   symbol, exchange, timeframe, strategy title, and script path.
2. Call `get_session_evaluation_context` for that session with
   `wait_for_ready=true`. Pass `account=<human-readable account>` only when the
   user explicitly names one. If pre-run is active, wait for it instead of
   reading a partial generation. Treat confirmed bars and the engine simulation
   snapshot as authoritative; keep the forming bar separate. Call the session
   list and exact-session context only once each in the user turn, then reuse
   that snapshot unless the tool rejects its generation.
3. When visual confirmation is useful, call `capture_session_chart` with the
   exact ready generation returned by the context tool. Capture it only once in
   the user turn and reuse that image.
4. Read `account_match` from the same context response. The server already
   queried current positions and recent order history. A user-selected account
   is authoritative. Without one, accept the automatic match only when status is
   `matched`; report `ambiguous` or `no_match` without guessing. Do not rerun the
   account collectors through shell.
5. Verify that timestamps, symbols, position sides, and quantities agree across
   the collected sources. For live-risk exposure and unrealized PnL, use the
   aggregate average-cost fields. Keep the Pine FIFO fields for trade attribution
   and backtest statistics; do not treat a difference between those bases as a
   broken strategy state or mix aggregate unrealized PnL into Pine equity. A
   script-specific variable such as `avgEntry` is supplemental, not the generic
   evaluation basis. Calculate continuous holding time only from
   `position_lifecycle`, and determine an entry ID's remaining quantity from
   `entry_open_ledger`, not from surviving FIFO trade rows.
6. Evaluate exposure, trade history, drawdown/run-up, active orders, plotted
   levels, invalidation conditions, and plausible response choices. Infer a
   strategy-specific entry stage or regime only from explicit, source-supported
   state in logs or plots. Do not use an `open_trades` entry ID as the stage:
   Pine FIFO attribution can leave a later entry's trade row after that entry's
   strategy quantity has already been closed. If explicit state is unavailable,
   report the stage as unknown.

## Response

Report the observation time, factual position summary, strategy state, primary
risks, missing evidence, and a concise evaluation. Separate facts from judgment.
During collection, report only the account, exchange, or market currently being
queried and material partial failures. Do not narrate instruction reading,
repository exploration, workflow selection, script option checks, or plans for
the final response. Start the final response with the observation time and
position result rather than a generic preamble.
