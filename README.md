# PyneReal
Run your crypto trading strategy in real time without TradingView

# Prerequisites
python >= 3.11\
[PyneCore](https://github.com/PyneSys/pynecore) strategy (converted from your pinescript strategy)

# Quick Example
Follow the steps below to see how it works.\
The `demo_1m.py` script runs on Bitget BTC/USDT Futures in real time on the 1-minute timeframe.\
You will see webhook alert messages when `strategy.entry` or `strategy.close` is triggered.

1. Clone\
`git clone https://github.com/hackcatml/pynereal` \
`cd pynereal`

2. Setup\
`chmod 755 setup.sh`\
`source setup.sh`

3. Run the webhook server\
`python demo_webhook_server.py`

4. Run the program\
`python main.py`


# How to run your strategy?
1. Prepare your PyneCore strategy file.
2. Download OHLCV data for the trading pair.\
e.g., `pyne data download ccxt --symbol "BITGET:BTC/USDT:USDT" --timeframe 5 --from "2025-09-01"`
3. Thoroughly test your strategy in backtesting mode.\
When you backtest your strategy, set `enabled = false` under the `realtime` section in `realtime_trade.toml`, then run:\
`pyne run <your strategy.py> <ohlcv file>`
4. Fill the `realtime_trade.toml`
5. Finally, start the program:\
`python main.py`


# Features
## Webhook signal
Enable the webhook feature by setting `enabled = true` in the `webhook` section of `realtime_trade.toml`.\
Add your webhook url there.\
When using `strategy.entry` or `strategy.close`, provide an alert_message in JSON format:
e.g., `strategy.entry("Long 1", strategy.long, alert_message=f'{{"signal": "Long 1", "price": {close}}}',
                       comment=f"Long 1 at rsi: {rsi}", record=True)`\
Currently, webhook signals are triggered only on `strategy.entry` and `strategy.close` events. 


## Send Telegram message
If webhook signaling is enabled, you can automatically send Telegram notifications as well.\
Enable it by setting `telegram_notification = true` in `realtime_trade.toml`.\
You must also fill in your `.env` file with your Telegram bot token and chat ID.


## Custom input
PyneCore does not yet support Pine Script’s `request.security` feature.\
To use higher-timeframe (HTF) values, you must calculate them before running the script.\
In the modules directory, you will find several examples:\
`request_security.py` — mock implementation of Pine’s request.security\
`weekly_hl_calc.py` — weekly high/low calculator\
`bb1d_calc.py` — daily Bollinger Band calculator

How to apply?\
(1) If it's just for backtesting
- Go to `pynecore/cli/commands/run.py` and search for the string `module calculation`.
- There’s a bb1d and weekly high–low calculation example. Uncomment it.
- Also uncomment the keys and values in the `custom_inputs` parameter a few lines below.
- Go to the `demo_1m.py` strategy file and uncomment the Custom Inputs section.
- The HTF calculation result will be used in backtesting.

(2) If it's for real-time trading
- Go to `main.py` and search for the string `module calculation`.
- There are two places where module calculation occurs:\
one in the `Ready Script Runner` region and another in the `Script Run Loop` region.\
Uncomment the module calculations and the keys and values in the `custom_inputs` parameter.
- Go to the `demo_1m.py` strategy file and uncomment the Custom Inputs section.
- The HTF calculation result will be used in real-time trading.

Yes, it's a little bit annoying to set up.\
Conveniently injecting custom inputs into the script is on my TODO list for now.

## Backtesting
You can still use the standard pyne command for backtesting.\
Make sure to set:\
`no_report = false` under the `pyne` section\
`enabled = false` under the `realtime` section in `realtime_trade.toml`\
Then run: `pyne run <your strategy.py> <ohlcv file>`


# Risk Warning
This project is still in development.\
Cannot guarantee it works properly.\
Use it at your own risk.\
Before you run your strategy with real funds, make sure the trading result matches your expectation.\
I don't take any responsibility for your loss.


# License
Apache License Version 2.0


# Acknowledgements
- [PyneCore](https://github.com/PyneSys/pynecore)

