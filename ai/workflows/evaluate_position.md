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

1. Collect the current session and strategy state.
2. Run `python ai/scripts/position.py --account <account>` with the relevant
   symbol, account type, or Hyperliquid DEX. Use `--exchange <exchange>` only
   when every configured account for that exchange must be evaluated. Then
   collect balances, open orders, and recent executions from their dedicated
   read-only scripts.
3. Collect the latest confirmed market bars and relevant strategy outputs.
4. Verify that timestamps, symbols, position sides, and quantities agree across
   the collected sources.
5. Evaluate exposure, entry stage, liquidation and margin risk, invalidation
   conditions, and plausible response choices.

## Response

Report the observation time, factual position summary, strategy state, primary
risks, missing evidence, and a concise evaluation. Separate facts from judgment.
During collection, report only the account, exchange, or market currently being
queried and material partial failures. Do not narrate instruction reading,
repository exploration, workflow selection, script option checks, or plans for
the final response. Start the final response with the observation time and
position result rather than a generic preamble.
