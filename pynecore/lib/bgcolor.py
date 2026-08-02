import sys

from ..core.callable_module import CallableModule
from ..types.color import Color
from ..types.na import NA


def _color_value(value: Color | str | NA | None) -> int | NA:
    if value is None or isinstance(value, NA):
        return NA()
    if isinstance(value, str):
        value = Color(value)
    if not isinstance(value, Color):
        raise TypeError("bgcolor color must be a Color, hex string, na, or None")
    return value.value


# noinspection PyProtectedMember
def bgcolor(color: Color | str | NA | None, offset: int = 0, editable: bool = True,
            show_last: int | None = None, title: str | None = None,
            force_overlay: bool = False, *_, **__) -> None:
    """Record a per-bar background color for chart rendering."""
    from .. import lib

    if lib._lib_semaphore:
        return

    if lib.bar_index == 0 and sys._getframe(2).f_code.co_name != 'main':  # noqa
        raise RuntimeError("The bgcolor function can only be called from the main function!")

    if not isinstance(offset, int) or isinstance(offset, bool):
        raise TypeError("bgcolor offset must be an int")
    if not isinstance(editable, bool):
        raise TypeError("bgcolor editable must be a bool")
    if show_last is not None and (
        not isinstance(show_last, int) or isinstance(show_last, bool) or show_last < 0
    ):
        raise ValueError("bgcolor show_last must be a non-negative int or None")
    if not isinstance(force_overlay, bool):
        raise TypeError("bgcolor force_overlay must be a bool")
    if title is not None and not isinstance(title, str):
        raise TypeError("bgcolor title must be a string or None")

    base_title = title if title is not None else "Background"
    resolved_title = base_title
    suffix = 0
    while resolved_title in lib._plot_data:
        resolved_title = f"{base_title} {suffix}"
        suffix += 1

    order = len(lib._plot_data)
    encoded_color = NA() if color is lib.na else _color_value(color)
    if isinstance(encoded_color, int) and not 0 <= encoded_color <= 0xFFFFFFFF:
        raise ValueError("bgcolor color must fit the 0xRRGGBBAA format")
    lib._plot_data[resolved_title] = encoded_color

    if lib.bar_index == 0 and lib._script and lib._script.on_plot_callback:
        lib._script.on_plot_callback({
            "title": resolved_title,
            "kind": "bgcolor",
            "offset": offset,
            "editable": editable,
            "show_last": show_last,
            "force_overlay": force_overlay,
            "order": order,
        })


CallableModule(__name__)
