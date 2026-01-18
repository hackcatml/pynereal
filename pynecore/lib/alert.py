"""
Alert

This is a callable module, so the module itself is both a function and a namespace
"""
from __future__ import annotations

import datetime
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

def alert(
        message: str,
        freq: AlertEnum = AlertModule.freq_once_per_bar
) -> None:
    """
    Display alert message. Uses rich formatting if available, falls back to print.

    :param message: Alert message to display
    :param freq: Alert frequency (currently ignored)
    """
    try:
        # Try to use typer for nice colored output
        import typer
        import json
        import re

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

#
# Module initialization
#

AlertModule(__name__)
