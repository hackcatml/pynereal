from typing import TypeVar, TypeAlias
from pynecore.types.na import NA
from .series import Series

T = TypeVar('T')

# Public type alias that allows both T and Persistent[T]
Persistent: TypeAlias = T | NA[T] | Series[T]
