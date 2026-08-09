"""
@pyne
"""
from pynecore import Persistent
from pynecore.lib import script, close, ta, strategy, time, plot, color, na, plotchar, request, syminfo, low, barmerge, \
    log, bar_index, is_na, bgcolor, alert
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
    avgEntry: Persistent[float] = 0.0
    bbBasis, bbUpper, bbLower = ta.bb(close, 20, 2)

    # request.security example
    macro_low = request.security(syminfo.tickerid, '1D', low[2], lookahead=barmerge.lookahead_on)
    _, _, bb_5_lower = request.security(syminfo.tickerid, '5', ta.bb(close, 20, 2), lookahead=barmerge.lookahead_on)

    # alert() example
    # Use alert() for a notification that must not create or modify a strategy order.
    # In realtime trade, it is suppressed during pre_run and sends Webhook/Telegram
    # only when called on the latest confirmed bar. A regular pyne run prints historical alerts.
    # alert("Custom alert", alert.freq_once_per_bar_close)

    # Execute the strategy
    if not entered1 and rsi < 70 and (time - lastTpTime) >= 1 * 60 * 1000 * 2:
        entered1 = True
        entered1Time = time
        avgEntry = close
        # If the record option is true, it will write entry and close records to a file in the records directory.
        strategy.entry("Long 1", strategy.long, alert_message=f'{{"signal": "Long 1", "price": {close}}}',
                       comment=f"Long 1 at rsi: {rsi}", record=False,
                       ai=(
                           f"Remove all existing Manual Alert triggers and set a close all trigger "
                           f"at {close * 1.002}. If the close all template does not exist, create it "
                           'with {"signal": "CLOSE TP3"}. Send the result to Telegram.'
                       ))
        # alert("Long condition confirmed", alert.freq_once_per_bar_close)

    if entered1 and (time - entered1Time) >= 1 * 60 * 1000 * 2:
        entered1 = False
        lastTpTime = time
        strategy.close("Long 1", alert_message=f'{{"signal": "Close 1"}}',
                        comment=f"Close 1 at price: {close}", record=False)
        # alert("Close condition confirmed", alert.freq_once_per_bar_close)

    # Plot Example
    # plot(avgEntry if entered1 else na, title="avgEntry", color=color.yellow, linewidth=1, style=plot.style_cross)
    plot(bbUpper, title="BB Upper", color=color.red, linewidth=1, style=plot.style_line)
    plot(bbBasis, title="BB Basis", color=color.blue, linewidth=1, style=plot.style_line)
    plot(bbLower, title="BB Lower", color=color.green, linewidth=1, style=plot.style_line)

    # Background Color Example
    bgcolor(
        color.new(color.red, 85) if rsi > 70
        else color.new(color.green, 85) if rsi < 30
        else na,
        title="RSI Zone",
    )

    # Plotchar Example
    plotchar(rsi < 30, title="RSI Low", text="RSI Low", location=plot.location_belowbar, color=color.green)
