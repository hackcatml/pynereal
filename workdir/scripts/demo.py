"""
@pyne
"""

from pynecore.lib import script, close, ta, strategy, color, plot
from pynecore.types import Series


@script.strategy("Simple Crossover Strategy", overlay=True)
def main():
    # Calculate fast and slow moving averages
    fast_ma: Series[float] = ta.ema(close, 9)
    slow_ma: Series[float] = ta.ema(close, 21)
    # Define entry conditions
    buy_signal = ta.crossover(fast_ma, slow_ma)
    sell_signal = ta.crossunder(fast_ma, slow_ma)

    # Execute the strategy
    if buy_signal:
        strategy.entry("Long 1", strategy.long, alert_message=f'{{"signal": "Long 1", "price": {close}}}')
    elif sell_signal:
        strategy.close("Long 1", strategy.short, alert_message=f'{{"signal": "Close 1", "price": {close}}}')

    # Plot indicators
    plot(fast_ma, "Fast EMA", color=color.blue)
    plot(slow_ma, "Slow EMA", color=color.red)
