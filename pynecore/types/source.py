from __future__ import annotations
from .series import Series


class Source(Series[float]):
    """
    Represents a built-in source like "open", "high", "low", "close", "hl2", etc.

    DESIGN NOTES:
    =============

    This class provides type-safe source placeholders for IDE support while enabling
    dynamic runtime resolution through AST transformation:

    1. INITIALIZATION: Source objects store the source name for type hints
    2. INPUT DETECTION: InputTransformer detects Source objects in input() calls
    3. AST INJECTION: Adds `var = getattr(lib, var, lib.na)` at function start
    4. RUNTIME: ScriptRunner dynamically sets lib.close = actual_price per candle

    This allows `input(defval=close)` to work with proper types while being fast
    and compatible with both Source objects and string literals in input.source().

    FALLBACK OPERATORS:
    ===================
    Comparison and arithmetic operators are implemented as fallbacks for cases where
    AST transformation is missed (e.g., nested functions, closures). When called,
    they resolve the actual value from lib module dynamically.
    """

    def __new__(cls, name: str) -> Source:
        obj = object.__new__(cls)
        setattr(obj, "name", name)
        return obj

    def __repr__(self) -> str:
        return f"Source({getattr(self, 'name')})"

    def __str__(self) -> str:
        return getattr(self, 'name')

    def _get_value(self):
        """Get actual value from lib module"""
        from pynecore import lib
        return getattr(lib, getattr(self, 'name'), self)

    def _resolve(self, other):
        """Resolve self and other to actual values"""
        self_val = self._get_value()
        other_val = other._get_value() if isinstance(other, Source) else other
        return self_val, other_val

    # Comparison operators
    def __gt__(self, other):
        a, b = self._resolve(other)
        return a > b

    def __lt__(self, other):
        a, b = self._resolve(other)
        return a < b

    def __ge__(self, other):
        a, b = self._resolve(other)
        return a >= b

    def __le__(self, other):
        a, b = self._resolve(other)
        return a <= b

    def __eq__(self, other):
        a, b = self._resolve(other)
        return a == b

    def __ne__(self, other):
        a, b = self._resolve(other)
        return a != b

    # Arithmetic operators
    def __add__(self, other):
        a, b = self._resolve(other)
        return a + b

    def __radd__(self, other):
        a, b = self._resolve(other)
        return b + a

    def __sub__(self, other):
        a, b = self._resolve(other)
        return a - b

    def __rsub__(self, other):
        a, b = self._resolve(other)
        return b - a

    def __mul__(self, other):
        a, b = self._resolve(other)
        return a * b

    def __rmul__(self, other):
        a, b = self._resolve(other)
        return b * a

    def __truediv__(self, other):
        a, b = self._resolve(other)
        return a / b

    def __rtruediv__(self, other):
        a, b = self._resolve(other)
        return b / a

    def __neg__(self):
        return -self._get_value()

    def __abs__(self):
        return abs(self._get_value())

    # Type conversion
    def __float__(self):
        return float(self._get_value())

    def __int__(self):
        return int(self._get_value())
