from __future__ import annotations
from typing import TypeVar, Generic

__all__ = (
    'Series',
    'PersistentSeries',
)

T = TypeVar('T')


class Series(Generic[T]):
    """
    This is the runtime, do nothing implementation of the Series type. The actual Series behavior is
    implented in AST Transformers and the SeriesImpl class.
    """

    def __new__(cls, val: T) -> T:
        return val


# This is only for the AST transformer to mark a variable as Pine Script like persistent series
PersistentSeries = Series
