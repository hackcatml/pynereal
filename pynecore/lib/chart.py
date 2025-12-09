from datetime import timedelta

from ..types.color import Color
from ..core.module_property import module_property

from .. import lib
from .timeframe import in_seconds

__all__ = [
    'bg_color',
    'fg_color',
    'is_heikinashi',
    'is_kagi',
    'is_linebreak',
    'is_pnf',
    'is_range',
    'is_renko',
    'is_standard',
    'left_visible_bar_time',
    'right_visible_bar_time'
]

_visible_bars = 20

bg_color = Color('#000000')
fg_color = Color('#FFFFFF')

is_heikinashi = False
is_kagi = False
is_linebreak = False
is_pnf = False
is_range = False
is_renko = False
is_standard = True


# noinspection PyProtectedMember
@module_property
def left_visible_bar_time() -> int:
    return int((lib._datetime - timedelta(seconds=in_seconds(lib.syminfo.period) * _visible_bars)).timestamp() * 1000)


# noinspection PyProtectedMember
@module_property
def right_visible_bar_time() -> int:
    return int(lib._datetime.timestamp() * 1000)
