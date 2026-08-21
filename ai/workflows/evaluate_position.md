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
   `wait_for_ready=true`. Use `detail_level=standard` for a normal evaluation and
   `detail_level=compact` when the user reports a mismatch, challenges an earlier
   conclusion, or requests verification. Infer that intent from the full
   conversation in any language rather than matching fixed phrases. Pass
   `account=<human-readable account>` only when the user explicitly names one.
   If pre-run is active, wait for it instead of reading a partial generation.
   Treat confirmed bars and the engine simulation snapshot as authoritative;
   keep the forming bar separate. Call the session list and exact-session context
   only once each in the user turn, then reuse that snapshot unless the tool
   rejects its generation.
3. For a diagnostic request, or when
   `evidence_summary.diagnostic_recommended=true`, call
   `compare_session_evidence` with the exact session ID and generation. It uses
   the same-turn cached account and strategy evidence and performs no new REST
   lookup or calculation. Call it once with its default event limit. Read the
   earliest divergence and source excerpts,
   then try to disprove plausible causes before accepting one. Do not present
   the comparison's first divergence as a confirmed cause without direct source
   support, reproduction, or a second independent piece of evidence. An
   unmatched account event is only an account-side execution; confirm whether
   it was manual or external before attributing it to the strategy or webhook.
   Honor `investigation_constraints`: if strategy-source attribution is not
   allowed, stop after reporting the observation and required evidence.
4. When visual confirmation is useful, call `capture_session_chart` with the
   exact ready generation returned by the context tool. Capture it only once in
   the user turn and reuse that image.
5. Read `account_match` from the same context response. The server already read
   Account Center's current positions, order history, and position history for
   the session exchange, refreshing only stale or missing scopes. A user-selected account
   is authoritative. Without one, accept the automatic match only when status is
   `matched`; report `ambiguous` or `no_match` without guessing. Do not rerun the
   account collectors through shell.
6. Verify that timestamps, symbols, position sides, and quantities agree across
   the collected sources. For live-risk exposure and unrealized PnL, use the
   aggregate average-cost fields. Keep the Pine FIFO fields for trade attribution
   and backtest statistics; do not treat a difference between those bases as a
   broken strategy state or mix aggregate unrealized PnL into Pine equity. A
   script-specific variable such as `avgEntry` is supplemental, not the generic
   evaluation basis. Calculate continuous holding time only from
   `position_lifecycle`, and determine an entry ID's remaining quantity from
   `entry_open_ledger`, not from surviving FIFO trade rows.
   Before explaining a historical value supplied by the user, verify that the
   current snapshot reproduces that value and lifecycle. If it does not, clearly
   separate the user's historical report from the current evidence and do not
   diagnose one from the other.
7. Evaluate exposure, trade history, drawdown/run-up, active orders, plotted
   levels, invalidation conditions, and plausible response choices. Infer a
   strategy-specific entry stage or regime only from explicit, source-supported
   state in logs or plots. Do not use an `open_trades` entry ID as the stage:
   Pine FIFO attribution can leave a later entry's trade row after that entry's
   strategy quantity has already been closed. If explicit state is unavailable,
   report the stage as unknown.

## Response

Report the observation time, factual position summary, strategy state, primary
risks, missing evidence, and a concise evaluation. Separate facts from judgment.
For diagnostics, separately report observed differences, the earliest divergence,
the supported explanation or hypotheses, and unresolved evidence. If the user
challenges a prior answer, explicitly correct it when the evidence no longer
supports it rather than defending it by default.
Do not rerun the strategy or use web search to replace missing historical alert,
webhook, or order-origin evidence. State which record is needed instead.
During collection, report only the account, exchange, or market currently being
queried and material partial failures. Do not narrate instruction reading,
repository exploration, workflow selection, script option checks, or plans for
the final response. Start the final response with the observation time and
position result rather than a generic preamble.
