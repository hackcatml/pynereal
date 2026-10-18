"""Per-bar colors stored as numeric plot columns alongside their metadata."""
from ..types.plot import Plot
from ..types.na import NA
from ..types.color import Color


def record_color(title, value, kind, **options):
    from .. import lib

    if lib._lib_semaphore:
        return
    base = title
    suffix = 0
    while title in lib._plot_data:
        title = f"{base} {suffix}"
        suffix += 1
    if value is None or value is lib.na or isinstance(value, NA):
        encoded = NA()
    else:
        if isinstance(value, str):
            value = Color(value)
        if not isinstance(value, Color):
            raise TypeError("plot color must be a Color, hex string, na, or None")
        encoded = value.value
    order = len(lib._plot_data)
    lib._plot_data[title] = encoded
    if lib.bar_index == 0 and lib._script and lib._script.on_plot_callback:
        lib._script.on_plot_callback(dict(title=title, kind=kind, order=order, **options))


def barcolor(color, offset=0, editable=True, show_last=None, title=None, **_):
    """Color candle bodies, borders and wicks; na keeps the chart's default color."""
    record_color(title or "Bar Color", color, "barcolor", offset=offset,
                 editable=editable, show_last=show_last)


def fill(plot1, plot2, color=None, title=None, editable=True, show_last=None,
         fillgaps=False, **_):
    """Fill between two plot handles, retaining the color's transparency."""
    from .. import lib

    if lib._lib_semaphore:
        return
    if not isinstance(plot1, Plot) or not isinstance(plot2, Plot):
        # Keep unsupported hline/gradient overloads inert, as before.
        return
    record_color(title or "Fill", color, "fill", plot1=plot1.title,
                 plot2=plot2.title, editable=editable, show_last=show_last,
                 fillgaps=fillgaps)
