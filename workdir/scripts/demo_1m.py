"""
@pyne
"""
from pynecore import Persistent
from pynecore.lib import script, close, ta, strategy, time
from pynecore.types import Series


@script.strategy("test", overlay=True)
def main():
    # -------------------------------------------------------------
    # Custom Inputs
    # -------------------------------------------------------------
    # # bb1d / weekly high, low calculation
    # custom_inputs: dict = strategy.get_custom_inputs()
    # bb1d_lower: list[float] = custom_inputs.get('bb1d_lower', []) if custom_inputs is not {} else []
    # macro_high: list[float] = custom_inputs.get('macro_high', []) if custom_inputs is not {} else []
    # macro_low: list[float] = custom_inputs.get('macro_low', []) if custom_inputs is not {} else []

    rsi: Series[float] = ta.rsi(close, 14)
    entered1: Persistent[bool] = False
    entered1Time: Persistent[int] = 0
    lastTpTime: Persistent[int] = 0

    # Execute the strategy
    if not entered1 and rsi < 70 and (time - lastTpTime) >= 1 * 60 * 1000:
        entered1 = True
        entered1Time = time
        # If the record option is true, it will write entry and close records to a file in the records directory.
        strategy.entry("Long 1", strategy.long, alert_message=f'{{"signal": "Long 1", "price": {close}}}',
                       comment=f"Long 1 at rsi: {rsi}", record=False)

    if entered1 and (time - entered1Time) >= 1 * 60 * 1000 * 2:
        entered1 = False
        lastTpTime = time
        strategy.close("Long 1", alert_message=f'{{"signal": "Close 1"}}',
                        comment=f"Close 1 at price: {close}", record=False)
