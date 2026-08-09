# AI Scripts And Tools

This directory contains deterministic data collectors and the server-controlled
tools used by Dashboard AI workflows under `ai/workflows/`.

## Dashboard AI Runtime

- `dynamic_tools.py` routes app-server tool calls.
- `file_tools.py` restricts explicit file edits to approved repository paths.
- `manual_alert_tool.py` manages persisted Manual Alert templates and triggers.
- `calendar_tool.py` reads calendar context, adds a verified single event
  without replacing existing entries, and refreshes persisted session schedules.
- `telegram_tool.py` sends explicitly requested results through server-held
  credentials.

These shared modules are imported by provider implementations under
`ai/provider/`; they are not standalone command-line scripts. Their
corresponding `*_test.py` files keep tool behavior isolated from live accounts
and destinations.

## Interface

New scripts should:

- accept identifiers such as `--session-id`, `--provider`, and `--symbol`
- support machine-readable JSON output
- print diagnostics to standard error
- return a non-zero exit status for incomplete required data
- avoid accepting secrets as command-line arguments
- load credentials through the project's local provider configuration
- redact sensitive values from all output

Standalone command-line tools that change external state must use a separate
filename and require both `--execute` and an explicit confirmation value.
Server-controlled dynamic tools instead enforce explicit user intent and
validate every mutation inside the data-service process; they do not expose a
command-line execution switch.

## Asset balances

`asset.py` reads API credentials from `workdir/config/providers.toml` and uses
CCXT plus supported exchange-specific read-only asset endpoints. It never
prints the configured credentials or raw exchange responses.

Use named account tables when the same exchange has multiple accounts:

```toml
[ccxt_accounts.binance_main]
exchange = "binance"
apiKey = "..."
secret = "..."

[ccxt_accounts.binance_sub1]
exchange = "binance"
apiKey = "..."
secret = "..."
```

Exchange-nested tables are also accepted. The exchange ID is added to their
account name, so these become `bitget_main` and `bitget_sub1`:

```toml
[ccxt.bitget.main]
apiKey = "..."
secret = "..."
password = "..."

[ccxt.bitget.sub1]
apiKey = "..."
secret = "..."
password = "..."
```

Each named account must contain its own credentials. It inherits non-credential
exchange options from `[ccxt.<exchange>]`, but never inherits that table's API
key, secret, password, wallet address, or other account identity fields.

```bash
# One named account
python ai/scripts/asset.py --account binance_sub1

# Every configured account for one exchange
python ai/scripts/asset.py --exchange binance

# A specific account scope and currency
python ai/scripts/asset.py --account binance_main --account-type swap --currency USDT

# Every legacy and named account found in providers.toml
python ai/scripts/asset.py
```

Without `--account-type`, each account is checked for `spot`, `swap`, `margin`,
`funding`, and `earn` assets. Binance Simple Earn and Bitget Earn use their
dedicated read-only APIs. Supplying one or more `--account-type` options limits
the request to those types.

Repeat `--account`, `--exchange`, `--account-type`, or `--currency` to query
multiple values. Existing `[ccxt.<exchange>]` tables remain supported and use
the exchange ID as their account name. If both a legacy table and named
accounts exist for one exchange, `--exchange` queries all of them.
Only non-zero balances are returned unless `--include-zero` is supplied. JSON is
written to standard output and progress or sanitized errors go to standard
error. Account types that do not exist are reported as `unavailable`; types the
exchange does not expose are reported as `unsupported`. Neither condition makes
otherwise successful collection fail. Hyperliquid balance lookup uses its
public info endpoint and only needs `walletAddress` in the selected account
table; do not add a private key solely for this script.

## Open positions

`position.py` reads the same provider configuration and uses read-only exchange
position endpoints. Without `--account-type`, it queries every supported
derivative scope described below and only returns non-zero positions.

```bash
# One account's complete derivative positions
python ai/scripts/position.py --account binance_sub1

# Every configured Binance account
python ai/scripts/position.py --exchange binance

# One symbol or a different derivative account type
python ai/scripts/position.py --account bitget_sub1 --symbol BTC/USDT:USDT
python ai/scripts/position.py --account binance_main --account-type delivery
python ai/scripts/position.py --account bitget_sub1 --account-type usdc

# Hyperliquid HIP-3 positions
python ai/scripts/position.py --account hyperliquid_xyz --dex xyz

# Every configured account
python ai/scripts/position.py
```

Repeat `--account`, `--exchange`, `--account-type`, or `--symbol` to query
multiple values. Every result includes both `account` and `exchange`, so output
from multiple accounts remains distinguishable.
The default Binance query combines USD-M (USDT-M and USDC-M) and COIN-M
positions. The default Bitget query combines `USDT-FUTURES`, `USDC-FUTURES`,
and `COIN-FUTURES`.
Supplying `--account-type` intentionally limits these default scopes. Each
position includes `market_scope` when the exchange has multiple derivative
families, and each result lists every successfully queried scope in
`queried_scopes`.
When Hyperliquid is queried without `--dex` or `--symbol`, the script takes one
`allDexsClearinghouseState` WebSocket snapshot. It returns positions from the
default Perp DEX and every HIP-3 DEX included in that snapshot without issuing
one REST request per DEX. Each Hyperliquid position includes its `dex`.
Its `percentage` uses the signed exchange `returnOnEquity` value and falls back
to signed unrealized PnL divided by initial margin when that value is missing.
Supplying `--dex` limits the query to that DEX through CCXT's REST endpoint.
Use `--include-closed` only when zero-size records are needed for diagnostics.

## Recent order history

`order_history.py` reads recent orders for one unified session symbol across
configured accounts. It uses read-only CCXT order endpoints, returns normalized
orders without raw exchange payloads, and reports unsupported account/symbol
combinations as unavailable.

```bash
# Every configured account that supports this symbol
python ai/scripts/order_history.py --symbol CL/USDT:USDT --market-type linear

# One explicitly selected account
python ai/scripts/order_history.py --account bitget_sub2 \
  --symbol CL/USDT:USDT --market-type linear
```

Dashboard session evaluation invokes this collector together with `position.py`.
An explicitly named account is used as-is. Otherwise, current same-symbol
positions and recent same-symbol orders are used to rank configured accounts;
the match is not persisted in session configuration.
