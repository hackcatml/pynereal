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
    """

    def __new__(cls, name: str) -> Source:
        obj = object.__new__(cls)
        setattr(obj, "name", name)
        return obj

    def __repr__(self) -> str:
        return f"Source({getattr(self, 'name')})"

    def __str__(self) -> str:
        return getattr(self, 'name')
