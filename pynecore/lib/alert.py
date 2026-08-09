"""
Alert

This is a callable module, so the module itself is both a function and a namespace
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from json import JSONDecodeError

from ..core.callable_module import CallableModule

from ..types.alert import AlertEnum

#
# Module object
#

class AlertModule(CallableModule):
    #
    # Constants
    #

    freq_all = AlertEnum()
    freq_once_per_bar = AlertEnum()
    freq_once_per_bar_close = AlertEnum()


#
# Callable module function
#

def _wrap_runtime_message(message: str, bar_time: int) -> str:
    if re.match(r'^\s*\{\s*"timestamp"\s*:', message):
        return message

    try:
        payload = json.loads(message)
    except (JSONDecodeError, TypeError):
        payload = message
    return json.dumps({"timestamp": bar_time, "message": payload}, ensure_ascii=False)


def _prepare_runtime_alert(message: str, dispatch_callback: bool):
    lib = sys.modules.get(__package__)
    script = getattr(lib, "_script", None) if lib else None
    if script is None or not getattr(script, "realtime_trade", False):
        return message, None, False

    if getattr(script, "pre_run", False):
        return message, None, True

    # Order alerts are already gated by their placement bar in Position.
    if not dispatch_callback:
        return message, None, False

    bar_index = int(getattr(lib, "bar_index", -1))
    last_bar_index = int(getattr(script, "last_bar_index", 0))
    if bar_index != last_bar_index - 1:
        return message, None, True

    runtime_message = _wrap_runtime_message(message, int(getattr(lib, "_time", 0)))
    position = getattr(script, "position", None)
    callback = getattr(position, "on_alert_callback", None)
    return runtime_message, callback, False


def _print_alert(message: str) -> None:
    try:
        # Try to use typer for nice colored output
        import typer

        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Wrap the string in double quotes if it is passed without being enclosed in double quotes.
        s = re.sub(r'"message"\s*:\s*(?![{["0-9])([A-Za-z][A-Za-z0-9 ]*)',
                   r'"message": "\1"',
                   message)
        data = json.loads(s)
        timestamp = int(int(data.get('timestamp', 0)) / 1000)
        bar_time = datetime.datetime.fromtimestamp(timestamp) if timestamp else None
        bar_time_str = f"[{bar_time}]" if bar_time else ""

        message = data.get('message', '')

        typer.secho(f"[{current_time}] {bar_time_str} 🚨  {message}",
                    fg=typer.colors.BRIGHT_YELLOW, bold=True)
    except ImportError:
        # Fallback to simple print
        print(f"🚨 {message}")
    except (JSONDecodeError, KeyError):
        print(f"🚨 {message}")
    except Exception as e:
        print(f"🚨 {e}")


def alert(
        message: str,
        freq: AlertEnum = AlertModule.freq_once_per_bar,
        *,
        _dispatch_callback: bool = True
) -> None:
    """
    Display an alert and dispatch realtime alerts through the configured callback.

    :param message: Alert message to display
    :param freq: Alert frequency (currently ignored)
    """
    message, callback, suppressed = _prepare_runtime_alert(message, _dispatch_callback)
    if suppressed:
        return

    _print_alert(message)
    if callback:
        try:
            callback(message)
        except Exception as e:
            print(f"Error in on_alert_callback: {e}")

#
# Module initialization
#

AlertModule(__name__)
