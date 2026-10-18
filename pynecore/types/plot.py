from .base import IntEnum


class PlotEnum(IntEnum):
    ...


class Plot:
    __slots__ = ('title',)

    def __init__(self, title: str | None = None):
        self.title = title
