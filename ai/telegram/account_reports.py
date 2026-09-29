"""Plain-text reports from Account Center snapshots, without model inference."""
from __future__ import annotations

import math
from datetime import UTC, datetime

from .commands import session_label


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _number(value, *, places: int = 8, signed: bool = False) -> str:
    if value is None or isinstance(value, bool):
        return "Unavailable"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "Unavailable"
    if not math.isfinite(number):
        return "Unavailable"
    prefix = "+" if signed and number > 0 else ""
    if number != 0 and abs(number) < 10 ** -places:
        return prefix + f"{number:.6g}"
    result = f"{number if number else 0:,.{places}f}"
    return prefix + (result.rstrip("0").rstrip(".") if places == 8 else result)


def _money(value, currency: str, *, signed: bool = False) -> str:
    places = 2 if currency.upper() in {"USD", "USDT", "USDC", "EUR", "KRW"} else 8
    amount = _number(value, places=places, signed=signed)
    return amount if amount == "Unavailable" else f"{amount} {currency}".strip()


def _header(title: str, snapshot: dict) -> list[str]:
    try:
        stamp = datetime.fromisoformat(snapshot.get("collected_at") or "")
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        timestamp = stamp.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        timestamp = "Unavailable"
    return [title, f"As of (UTC): {timestamp}",
            "Source: Account Center" + (" (cached)" if snapshot.get("cached") else "")]


def _account(row: dict) -> str:
    return f"{_text(row.get('exchange')).upper()} | {_text(row.get('account'))}"


def _below_asset_threshold(holding: dict, portfolio: dict) -> bool:
    quote = _text(portfolio.get("quote_currency")).upper()
    dollar_quotes = {"USD", "USDT", "USDC", "BUSD", "DAI", "FDUSD", "PYUSD", "TUSD", "USDP"}
    # Reuse an available USD-stablecoin price for non-dollar portfolios; never fetch FX here.
    rate = 1 if quote in dollar_quotes else next((row.get("price") for row in portfolio.get("assets", [])
        if _text(row.get("currency")).upper() in dollar_quotes and row.get("price")), None)
    try:
        value, rate = float(holding["value"]), float(rate)
        return math.isfinite(value) and math.isfinite(rate) and rate > 0 and abs(value / rate) < 10
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _assets(snapshot: dict, exchange: str | None = None) -> str:
    portfolios = snapshot["portfolios"]
    totals = snapshot.get("totals_by_quote", [])
    if exchange:
        portfolios = [row for row in portfolios if _text(row.get("exchange")).lower() == exchange.lower()]
        # Keep the shared snapshot intact; aggregate only the selected exchange's accounts.
        scoped_totals: dict[str, float] = {}
        for row in portfolios:
            if row.get("status") not in {"ok", "empty"} or _number(row.get("total_value")) == "Unavailable":
                continue
            quote = _text(row.get("quote_currency"))
            scoped_totals[quote] = scoped_totals.get(quote, 0.0) + float(row["total_value"])
        totals = [{"currency": quote, "value": value} for quote, value in sorted(scoped_totals.items())]
    lines = _header("Assets" + (f" | {exchange.upper()}" if exchange else ""), snapshot)
    if not portfolios:
        if exchange:
            return "\n".join([*lines, "No account data available for this exchange."])
        failed = snapshot.get("error") or (snapshot.get("summary") or {}).get("failed")
        return "\n".join([*lines, "Asset lookup failed." if failed else "No accounts configured."])
    partial = any(row.get("status") not in {"ok", "empty"} or row.get("unpriced_asset_count") or row.get("warnings")
                  for row in portfolios)
    if partial:
        lines.append("Partial data: some account scopes or prices are unavailable.")
    if any(_below_asset_threshold(holding, row) for row in portfolios for holding in row.get("assets", [])):
        lines.append("Holdings below 10 USD equivalent omitted; totals unchanged.")
    available_quotes = {_text(row.get("quote_currency")) for row in portfolios if row.get("status") in {"ok", "empty"}}
    for total in totals:
        if _text(total.get("currency")) in available_quotes:
            lines.append("Total" + (" (available values)" if partial else "") + ": "
                         + _money(total.get("value"), _text(total.get("currency"))))
    for row in portfolios:
        lines.extend(["", _account(row)])
        if row.get("status") not in {"ok", "empty"}:
            lines.append("Unavailable: account lookup failed.")
            continue
        quote = _text(row.get("quote_currency"))
        lines.append("Value: " + _money(row.get("total_value"), quote))
        if row.get("warnings") or row.get("unpriced_asset_count"):
            lines.append("Partial data for this account.")
        for holding in row.get("assets", []):
            value = _money(holding.get("value"), quote)
            if value == "Unavailable" or _below_asset_threshold(holding, row):
                continue
            lines.append(f"{_text(holding.get('currency'))}: {_number(holding.get('amount'))}"
                         f" | Value: {value}")
        if not row.get("assets"):
            lines.append("No nonzero assets reported.")
    return "\n".join(lines)


def _positions(snapshot: dict) -> str:
    results = snapshot["results"]
    count = sum(len(row.get("positions", [])) for row in results if row.get("status") == "ok")
    failed = any(row.get("status") != "ok" for row in results)
    failed = failed or bool(snapshot.get("error") or (snapshot.get("summary") or {}).get("failed"))
    reported_count = count if not failed or any(row.get("status") == "ok" for row in results) else "Unavailable"
    lines = _header(f"Open positions: {reported_count}", snapshot)
    if failed:
        lines.append("Partial data: failed account scopes are not counted.")
    if not results:
        return "\n".join([*lines, "Position lookup failed." if failed else "No accounts configured."])
    if not count:
        lines.append("No open positions in successfully queried accounts." if failed else "No open positions.")
    for row in results:
        label = _account(row)
        if row.get("account_type"):
            label += " | " + _text(row["account_type"])
        if row.get("status") != "ok":
            lines.extend(["", label, "Unavailable: position lookup failed."])
            continue
        for position in row.get("positions", []):
            symbol = _text(position.get("symbol"))
            breakdown = position.get("realized_pnl_breakdown") or {}
            currency = _text(breakdown.get("currency"))
            if not currency:
                currency = symbol.rsplit(":", 1)[-1].split("-", 1)[0] if ":" in symbol else ""
            side = _text(position.get("side")).upper() or "UNKNOWN"
            leverage = _number(position.get("leverage"))
            lines.extend(["", label, f"{symbol} | {side}" + (f" | {leverage}x" if leverage != "Unavailable" else "")])
            quantity = position.get("quantity")
            lines.append("Size: " + _number(quantity) if quantity is not None
                         else "Contracts: " + _number(position.get("contracts")))
            lines.append(f"Entry: {_number(position.get('entry_price'))} | Mark: {_number(position.get('mark_price'))}")
            lines.append("Unrealized PnL: " + _money(position.get("unrealized_pnl"), currency, signed=True))
            rate = _number(position.get("percentage"), places=2, signed=True)
            lines.append("Return: " + rate + ("%" if rate != "Unavailable" else ""))
            lines.append("Realized PnL: " + _money(position.get("realized_pnl"), currency, signed=True))
            if breakdown and not breakdown.get("complete"):
                lines.append("Realized PnL breakdown is incomplete.")
    return "\n".join(lines)


def _pnl(snapshot: dict) -> str:
    period = snapshot.get("period") or {}
    days = period.get("days")
    label = f"{days} days" if days is not None else "All cached history"
    lines = _header(f"PnL | {label}", snapshot)
    lines.append("Cached history may be partial. Unrealized PnL is the current cached value.")
    if not snapshot.get("results"):
        return "\n".join([*lines, "No cached PnL records for this period."])
    for title, rows in (("Total", snapshot.get("totals", [])), ("", snapshot["results"])):
        for row in rows:
            currency = _text(row.get("currency"))
            lines.extend(["", title or _account(row)])
            for key, name in (("net_pnl", "Net PnL"), ("realized_pnl", "Realized"), ("unrealized_pnl", "Unrealized")):
                lines.append(name + ": " + _money(row.get(key), currency, signed=True))
            if row.get("complete") is False:
                lines.append("Partial data: some PnL values are unavailable.")
    return "\n".join(lines)


def format_alerts(context: dict) -> str:
    count = sum(len(row.get("active_triggers", [])) for row in context.get("sessions", []))
    lines = _header(f"Active Manual Alerts: {count}", context)
    lines[2] = "Source: session price triggers"
    if not count:
        lines.append("No active Manual Alert price triggers.")
    for session in context.get("sessions", []):
        triggers = session.get("active_triggers", [])
        if not triggers:
            continue
        lines.extend(["", session_label(session)])
        for trigger in triggers:
            lines.append(f"{_text(trigger.get('template_title')) or 'Manual Alert'} | Price: {_number(trigger.get('price'))}")
    return "\n".join(lines)


def format_account_snapshot(view: str, snapshot: dict, *, exchange: str | None = None) -> str:
    if view == "assets":
        return _assets(snapshot, exchange)
    if view == "positions":
        return _positions(snapshot)
    if view == "pnl":
        return _pnl(snapshot)
    raise ValueError("Unsupported account report")
