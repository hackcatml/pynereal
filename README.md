# PyneReal
Run your crypto trading strategy in real time without TradingView

---

## Why PyneReal?
If you're an active system trader who relies on TradingView strategies, you've probably experienced:

- **Missed Entries** — Alerts that didn't arrive in time
- **Wrong Price Levels** — Alerts firing at incorrect prices
- **Delayed Alerts** — Notifications arriving 2-3 minutes late
- **Mystery Bugs** — System errors with no way to debug

**PyneReal puts you back in control.**

Run your entire strategy in a **Python environment** under your complete control
<br>**Debug easily** when unexpected issues occur
<br>Leverage **Python's unlimited extensibility** for your trading logic
---

## Prerequisites
- **Python** ≥ 3.11
- **[PyneCore](https://github.com/PyneSys/pynecore)** strategy (converted from PineScript)

---

## Quick Example
### Clone the Repository

```bash
git clone https://github.com/hackcatml/pynereal
cd pynereal
```

### Setup Environment

```bash
chmod 755 setup.sh
source setup.sh
```

### Run Demo Strategy

The `workdir/scripts/demo_1m.py` script runs on **Bitget BTC/USDT Futures** in real-time on the 1-minute timeframe.

**Terminal 1** — Start webhook server:
```bash
python demo_webhook_server.py
```

**Terminal 2** — Start data & chart service:
```bash
python data_service/main.py
```

**Terminal 3** — Start strategy runner:
```bash
python runner_service/main.py
```

You'll see webhook alerts when `strategy.entry` or `strategy.close` triggers\
Also, you can see the strategy running on the chart at http://127.0.0.1:9001/ in your browser in real time.
<img src="https://github.com/user-attachments/assets/456a6f74-031f-4182-b464-dba794807587">


---

## How to run your strategy?
1. Prepare your [PyneCore strategy](https://pynecore.org/docs/strategy/) file.
2. Download OHLCV data for the trading pair.\
e.g., `pyne data download ccxt --symbol "BITGET:BTC/USDT:USDT" --timeframe 5 --from "2025-09-01"`
3. Thoroughly test your strategy in backtesting mode.\
When you backtest your strategy, set `enabled = false` under the `realtime` section in `realtime_trade.toml`, then run:\
`pyne run <your strategy.py> <ohlcv file>`
4. Fill the `realtime_trade.toml` in the `workdir/config` directory.
5. Finally, start the program:\
`python data_service/main.py` \
`python runner_service/main.py`

---

## Features
### Webhook Signals

Enable webhook alerts for instant trade notifications:

**Configuration:**
```toml
# realtime_trade.toml

[webhook]
enabled = true
url = "http://your-webhook-url.com"
```

**Usage in Strategy:**
```python
strategy.entry(
    "Long 1",
    strategy.long,
    alert_message=f'{{"signal": "Long 1", "price": {close}}}',
    comment="test"
)
``` 


### Telegram Notifications

Get trade alerts directly in Telegram:

**Setup:**
1. Enable in `realtime_trade.toml`:
   ```toml
   telegram_notification = true
   ```
2. Add credentials to `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```


### Custom input
PyneCore does not yet support Pine Script’s `request.security` feature.\
To use higher-timeframe (HTF) values, you must calculate them before running the script.\
In the modules directory, you will find several examples:\
`request_security.py` — mock implementation of Pine’s request.security\
`weekly_hl_calc.py` — weekly high/low calculator\
`bb1d_calc.py` — daily Bollinger Band calculator

How to apply?
1. If it's just for backtesting
- Go to `pynecore/cli/commands/run.py` and search for the string `module calculation`.
- There’s a bb1d and weekly high–low calculation example. Uncomment it.
- Also uncomment the keys and values in the `custom_inputs` parameter a few lines below.
- Go to the `demo_1m.py` strategy file and uncomment the Custom Inputs section.
- The HTF calculation result will be used in backtesting.

2. If it's for real-time trading
- Go to `main.py` and search for the string `module calculation`.
- There are two places where module calculation occurs:\
one in the `Ready Script Runner` region and another in the `Script Run Loop` region.\
Uncomment the module calculations and the keys and values in the `custom_inputs` parameter.
- Go to the `demo_1m.py` strategy file and uncomment the Custom Inputs section.
- The HTF calculation result will be used in real-time trading.

Yes, it's a little bit annoying to set up.\
Conveniently injecting custom inputs into the script is on my TODO list for now.

### Backtesting
You can still use the standard pyne command for backtesting.\
**Configuration:**
```toml
# realtime_trade.toml

[pyne]
no_report = false

[realtime]
enabled = false
```
**Run:** 
```bash
pyne run <your strategy.py> <ohlcv file>
```

---

## Risk Warning
This project is still in development.\
Cannot guarantee it works properly.\
Use it at your own risk.\
Before you run your strategy with real funds, make sure the trading result matches your expectation.\
I don't take any responsibility for your loss.

---

## License
Apache License Version 2.0

---

# Acknowledgements
- **[PyneCore](https://github.com/PyneSys/pynecore)** — The powerful Pine Script compatible framework that makes this possible
- **[lightweight-charts](https://tradingview.github.io/lightweight-charts/)** — Chart visualization
