from typing import cast, Callable
from types import ModuleType
import sys


class CallableModule:
    """
    Callable wrapper for a module
    """

    def __init__(self, module_name: str):
        self._module: ModuleType = sys.modules[module_name]
        self._func: Callable = getattr(self._module, self._module.__name__.split(".")[-1])
        sys.modules[module_name] = cast(ModuleType, self)

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)
