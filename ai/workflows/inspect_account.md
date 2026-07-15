# Inspect Account

## Goal

Produce a read-only snapshot of an exchange account for a requested market or
symbol.

## Procedure

1. Resolve the account scope without printing credentials. If no account or
   exchange is specified, select every account configured in
   `workdir/config/providers.toml`. If only an exchange is specified, select
   every configured account for that exchange. Select one account only when its
   configured account name is explicitly given.
2. For exchange asset balances, use the matching command:
   - No account or exchange specified: `python ai/scripts/asset.py`
   - Exchange specified: `python ai/scripts/asset.py --exchange <exchange>`
   - Account specified: `python ai/scripts/asset.py --account <account>`
   The default command collects spot, futures, margin, funding, and earn assets.
   Add `--account-type` only when the request intentionally limits the scope.
3. For derivative positions, use the matching command:
   - No account or exchange specified: `python ai/scripts/position.py`
   - Exchange specified: `python ai/scripts/position.py --exchange <exchange>`
   - Account specified: `python ai/scripts/position.py --account <account>`
   With no `--account-type`, Binance includes USD-M (USDT-M and USDC-M) and
   COIN-M, while Bitget includes USDT-, USDC-, and Coin-M futures. Treat this
   complete product scope as the default for a general position request.
   Add `--symbol`, `--account-type`, or Hyperliquid `--dex` when explicitly
   requested or otherwise required by the named market. Do not add `--dex` to a
   general Hyperliquid position request; omitting it includes the default Perp
   DEX and all HIP-3 DEXs from one `allDexsClearinghouseState` WebSocket
   snapshot.
4. Collect any additionally requested open orders and recent executions with
   their dedicated read-only scripts.
5. Normalize exchange-specific values into the relevant schema under
   `ai/schemas/`.
6. Verify the exchange timestamp and record the collection time.
7. Report unavailable endpoints or partial results instead of fabricating data.

## Response

Return a concise account summary followed by risks or inconsistencies found in
the collected evidence. During collection, report only concrete progress such
as the account currently being queried or a partial failure. Do not mention
instruction files, repository inspection, workflow selection, script option
review, or plans to organize the result. Start the final response with the
observation time and account scope, then present the balances directly.
