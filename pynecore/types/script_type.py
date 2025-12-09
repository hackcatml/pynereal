from .base import IntEnum

__all__ = [
    'ScriptType',
    'indicator', 'strategy', 'library',
]


class ScriptType(IntEnum):
    ...


indicator = ScriptType()
strategy = ScriptType()
library = ScriptType()
