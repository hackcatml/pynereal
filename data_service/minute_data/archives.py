from __future__ import annotations

import csv
import io
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import quote

from .core import Market, valid_bar


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class Archive:
    url: str
    start: int
    end: int


def archive_plan(market: Market, market_info: dict, start: int, end: int, *, now: datetime | None = None) -> list[Archive]:
    exchange = market.exchange
    if exchange not in {"binance", "bitget", "okx"}:
        return []
    if exchange != "binance" and not (market_info.get("swap") and market_info.get("linear")):
        return []
    zone = UTC if exchange == "binance" else timezone(timedelta(hours=8))
    first = datetime.fromtimestamp(start, zone).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today = (now or datetime.now(UTC)).astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    last = min(datetime.fromtimestamp(end, zone), today)
    symbol = quote(market_info["id"], safe="-_")
    result = []
    while first < last:
        next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        # A closed month's archive also serves a request ending midway through it.
        monthly = next_month <= today
        finish = next_month if monthly else first + timedelta(days=1)
        if finish.timestamp() <= start:
            first = finish
            continue
        period = "monthly" if monthly else "daily"
        dashed = first.strftime("%Y-%m" if monthly else "%Y-%m-%d")
        compact = first.strftime("%Y%m" if monthly else "%Y%m%d")
        if exchange == "binance":
            kind = "spot" if market_info.get("spot") else "futures/cm" if market_info.get("inverse") else "futures/um"
            url = f"https://data.binance.vision/data/{kind}/{period}/klines/{symbol}/1m/{symbol}-1m-{dashed}.zip"
        elif exchange == "bitget":
            if market_info.get("settle") != "USDT":
                return []
            folder = "kline_month" if monthly else "kline"
            url = f"https://img.bitgetimg.com/online/{folder}/{symbol}/UMCBL/{symbol}_UMCBL_1min_{compact}.zip"
        else:
            url = f"https://static.okx.com/cdn/okex/traderecords/candlesticks/{period}/{compact}/{symbol}-candlesticks-{dashed}.zip"
        result.append(Archive(url, int(first.timestamp()), int(finish.timestamp())))
        first = finish
    return result


def _check_zip(archive: zipfile.ZipFile) -> None:
    if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
        raise ValueError("historical archive expands beyond the size limit")


def _xlsx_rows(data: bytes):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        _check_zip(archive)
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(node.itertext()) for node in root.findall("m:si", ns)]
        # The public Bitget daily export has a single worksheet of scalar cells.
        with archive.open("xl/worksheets/sheet1.xml") as stream:
            for _, node in ET.iterparse(stream, events=("end",)):
                if node.tag != "{" + ns["m"] + "}row":
                    continue
                row = []
                for cell in node.findall("m:c", ns):
                    column = 0
                    for letter in cell.get("r", ""):
                        if not letter.isalpha():
                            break
                        column = column * 26 + ord(letter.upper()) - 64
                    while len(row) < column:
                        row.append("")
                    value = cell.findtext("m:v", default="", namespaces=ns)
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(cell.find("m:is", ns).itertext())
                    if column:
                        row[column - 1] = value
                yield row
                node.clear()


def _seconds(value) -> int:
    value = int(value)
    while value >= 100_000_000_000:
        value //= 1000
    return value


def parse_archive(data: bytes, market: Market, start: int, end: int) -> list[list]:
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        _check_zip(archive)
        for name in archive.namelist():
            if name.lower().endswith(".xlsx"):
                rows = _xlsx_rows(archive.read(name))
            elif name.lower().endswith(".csv"):
                rows = csv.reader(io.StringIO(archive.read(name).decode("utf-8-sig")))
            else:
                continue
            header = None
            for row in rows:
                if not row:
                    continue
                if header is None and market.exchange != "binance":
                    header = {v.strip().lower(): i for i, v in enumerate(row)}
                    continue
                if market.exchange == "binance":
                    try:
                        ts = _seconds(row[0])
                    except ValueError:
                        if header is not None:
                            raise ValueError("invalid Binance candle timestamp")
                        header = {}
                        continue
                    volume = row[7] if market.market_type == "inverse" else row[5]
                    values = [ts, *row[1:5], volume]
                    header = {}
                else:
                    ts = _seconds(row[header["open_time" if market.exchange == "okx" else "timestamp"]])
                    if market.exchange == "okx" and row[header["confirm"]] != "1":
                        continue
                    volume = "vol_ccy" if market.exchange == "okx" else "basevolume"
                    values = [ts, *(row[header[k]] for k in ("open", "high", "low", "close", volume))]
                if not start <= ts < end:
                    continue
                bar = valid_bar(values)
                if bar is None:
                    raise ValueError(f"invalid {market.exchange} archive candle at {ts}")
                result[ts] = bar
    if not result:
        return []
    return [result[ts] for ts in sorted(result)]


def prepare_archive(data: bytes, market: Market, archive: Archive, start: int, end: int,
                    previous=None) -> tuple[list, list]:
    start, end = max(start, archive.start), min(end, archive.end)
    if start >= end:
        return [], []
    sparse = market.exchange == "bitget" and market.market_type == "linear" and market.symbol.endswith(":USDT")
    # Keep earlier file rows as a price seed when the requested range starts mid-file.
    rows = parse_archive(data, market, archive.start if sparse else start, end)
    actual = [row for row in rows if row[0] >= start]
    if not sparse or not rows:
        return actual, []

    prior = next((row for row in reversed(rows) if row[0] < start), None)
    if previous is not None and previous[0] == start - 60 and previous[6] != "trades":
        prior = previous
    close = prior[4] if prior is not None else None
    cursor, no_trade = start, []
    # Bitget USDT-M files omit no-trade minutes; other exchanges keep zero rows.
    for row in actual:
        if close is not None:
            no_trade.extend([ts, close, close, close, close, 0.0] for ts in range(cursor, row[0], 60))
        cursor, close = row[0] + 60, row[4]
    if close is not None:
        no_trade.extend([ts, close, close, close, close, 0.0] for ts in range(cursor, end, 60))
    return actual, no_trade
