from ..core.module_property import module_property

__all__ = [
    'isfirst',
    'islast', 
    'isconfirmed',
    'ishistory',
    'islastconfirmedhistory',
    'isnew',
    'isrealtime'
]

# TODO: support live trading

isfirst = True
""" Returns true if current bar is first bar in barset, false otherwise."""

islast = False
""" Returns true if current bar is the last bar in barset, false otherwise. """


@module_property
def isconfirmed() -> bool:
    """
    Returns true if the script is calculating the last (closing) update of the current bar

    :return: True if the script is calculating the last (closing) update of the current bar
    """
    # TODO: now it is always true, but if we implement bar magnifier, it should be calculated
    return True


@module_property
def ishistory() -> bool:
    """
    Returns true if script is calculating on historical bars, false otherwise.

    :return: True if script is calculating on historical bars, false otherwise
    """
    # TODO: now it is always true, but for live trading it should be implemented
    return True


@module_property
def islastconfirmedhistory() -> bool:
    """
    Returns true if script is executing on the dataset's last bar when market is closed, or script
    is executing on the bar immediately preceding the real-time bar, if market is open.

    :return: True if script is executing on the dataset's last bar when market is closed, or script
             is executing on the bar immediately preceding the real-time bar, if market is open
    """
    # TODO: now is always false, but for live trading it should be implemented
    return False


@module_property
def isnew() -> bool:
    """
    Returns true if script is currently calculating on new bar, false otherwise.

    :return: True if script is currently calculating on new bar, false otherwise
    """
    # TODO: now it is always false, but if we implement bar magnifier, it should be calculated
    return False


@module_property
def isrealtime() -> bool:
    """
    Returns true if script is calculating on real-time bars, false otherwise.

    :return: True if script is calculating on real-time bars, false otherwise
    """
    # TODO: now it is always false, but for live trading it should be implemented
    return False
