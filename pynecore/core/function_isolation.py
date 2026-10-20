from typing import Callable
from types import FunctionType
from dataclasses import is_dataclass, replace as dataclass_replace
from copy import copy
from weakref import WeakSet
from .pine_export import Exported
from .series import SeriesImpl

__all__ = ['isolate_function', 'FunctionCallSlot', 'reset']

# Store all function instances
_function_cache: dict[str | tuple, FunctionType] = {}
_root_function_cache = _function_cache
_call_slots = WeakSet()


class FunctionCallSlot:
    """Direct access to a call site's original isolated function and loop instances."""

    __slots__ = ('call_id', 'call_counter', '_scope', '_cached', '_iterations', '__weakref__')

    def __init__(self, call_id, call_counter=1):
        self.call_id = call_id
        self.call_counter = call_counter
        self.clear()
        _call_slots.add(self)

    def clear(self):
        self._scope = self._cached = None
        self._iterations = {}

    def resolve(self, func, parent_scope, closure_argument_count=-1, call_counter=1):
        # request.security swaps caches and may restore earlier state in place.
        # Keep that entire evaluation on the existing isolation path.
        if _function_cache is not _root_function_cache:
            return isolate_function(func, self.call_id, parent_scope, closure_argument_count, call_counter)
        if call_counter != self.call_counter:
            slot = self._iterations.get(call_counter)
            if slot is None:
                slot = self._iterations[call_counter] = FunctionCallSlot(self.call_id, call_counter)
            return slot.resolve(func, parent_scope, closure_argument_count, call_counter)

        cached = self._cached
        if cached is not None and parent_scope is self._scope:
            if closure_argument_count != -1:
                return cached
            if type(func) is not FunctionType and isinstance(func, Exported):
                func = func.__fn__
                if func is None:
                    raise ValueError("Exported proxy has not been initialized with a function yet")
            if (cached.__code__ is func.__code__ and cached.__closure__ is func.__closure__
                    and cached.__defaults__ is func.__defaults__):
                return cached
            # Match isolate_function: rebind, but never replace the original cache entry.
            return FunctionType(func.__code__, cached.__globals__, func.__name__,
                                func.__defaults__, func.__closure__)

        resolved = isolate_function(func, self.call_id, parent_scope, closure_argument_count, call_counter)
        self._scope = parent_scope
        self._cached = _function_cache.get((parent_scope, self.call_id, call_counter))
        return resolved


def reset():
    """
    Reset all function instances
    """
    _function_cache.clear()
    for slot in list(_call_slots):
        slot.clear()


def isolate_function(
        func: FunctionType | Callable, call_id: str | None, parent_scope: str,
        closure_argument_count: int = -1, call_counter: int = 0
) -> FunctionType | Callable:
    """
    Create a new function instance with isolated globals if the function has persistent or series globals.

    :param func: The function to create an instance of
    :param call_id: The unique call ID
    :param parent_scope: The parent scope ID
    :param closure_argument_count: Whether the function has closure arguments
    :param call_counter: The current call counter value for this call_id
    :return: The new function instance if there are any persistent or series globals otherwise the original function
    """
    # If there is no call ID, return the function as is
    if call_id is None:
        return func  # type: ignore

    # If it is an overloaded function, returned by the dispatcher
    is_overloaded = call_id == '__overloaded__?'

    # Create full call ID from parent scope and call ID
    if call_id and not is_overloaded:
        # call_id_key = f"{parent_scope}→{call_id}#{call_counter}"
        call_id_key: str | tuple = (parent_scope, call_id, call_counter)
    else:
        call_id_key = parent_scope

    # If the function is overloaded, we need to remove the dispatcher from the cache to override it with implementation
    if is_overloaded:
        del _function_cache[call_id_key]

    # Cache hit is the common case in the bar loop, so it is checked before any
    # type/proxy inspection of func (those only matter when building a new entry)
    isolated_function = _function_cache.get(call_id_key)
    if isolated_function is not None:
        if closure_argument_count != -1:
            return isolated_function

        if isinstance(func, Exported):
            unwrapped_func = func.__fn__
            if unwrapped_func is None:
                raise ValueError("Exported proxy has not been initialized with a function yet")
            func = unwrapped_func

        # Functions inside main are redefined every bar with fresh closure cells, so the
        # cached instance must be rebound to the current closure. Module-level functions
        # and overload dispatchers keep the same code/closure/defaults objects, so the
        # cached instance can be returned as is - no FunctionType recreation needed.
        if (isolated_function.__code__ is func.__code__
                and isolated_function.__closure__ is func.__closure__
                and isolated_function.__defaults__ is func.__defaults__):
            return isolated_function

        # Create a new function with original closure and isolated globals
        return FunctionType(
            func.__code__,
            isolated_function.__globals__,
            func.__name__,
            func.__defaults__,
            func.__closure__
        )

    # If it is a type object, return it as is
    if isinstance(func, type):
        return func  # type: ignore

    # If it is a classmethod (bound method where __self__ is a class), return it as is
    if hasattr(func, '__self__') and isinstance(func.__self__, type):
        return func  # type: ignore

    # Check if this is an Exported proxy and unwrap it
    if isinstance(func, Exported):
        unwrapped_func = func.__fn__
        if unwrapped_func is None:
            raise ValueError("Exported proxy has not been initialized with a function yet")
        func = unwrapped_func

    # Builtin objects have no __globals__ attribute
    try:
        new_globals = dict(func.__globals__)
    except AttributeError:  # This is a builtin function (it should be filtered in the transformer)
        return func  # type: ignore

    # The qualified name of the function, this name is used in the globals registry by transformer
    qualname = func.__qualname__.replace('<locals>.', '')

    for name in new_globals.get('__function_call_slots__', {}).get(qualname, ()):
        slot = new_globals[name]
        new_globals[name] = FunctionCallSlot(slot.call_id, slot.call_counter)

    # If globals are registered, we can use them
    registry_found = False
    try:
        persistent_vars = new_globals['__persistent_function_vars__']
        registry_found = True
    except KeyError:
        persistent_vars = {}
    try:
        series_vars = new_globals['__series_function_vars__']
        registry_found = True
    except KeyError:
        series_vars = {}

    try:
        for key in persistent_vars[qualname]:
            old_value = new_globals[key]
            if isinstance(old_value, (dict, list)):
                new_globals[key] = old_value.copy()
            elif is_dataclass(old_value):
                new_globals[key] = dataclass_replace(old_value)  # type: ignore
            else:
                new_globals[key] = copy(old_value)
    except KeyError:
        pass
    try:
        for key in series_vars[qualname]:
            old_value = new_globals[key]
            new_globals[key] = SeriesImpl(old_value._max_bars_back)  # noqa
    except KeyError:
        pass

    # Fallback, if globals are not registered
    if not registry_found:
        # Create new globals with isolated persistent and series
        for key in new_globals.keys():
            if key.startswith('__persistent_') and not key.endswith('_vars__'):
                old_value = new_globals[key]
                if isinstance(old_value, (dict, list)):
                    new_globals[key] = old_value.copy()
                elif is_dataclass(old_value):
                    new_globals[key] = dataclass_replace(old_value)  # type: ignore
                else:
                    new_globals[key] = copy(old_value)
            elif key.startswith('__series_') and not key.endswith('_vars__'):
                old_value = new_globals[key]
                new_globals[key] = type(old_value)(old_value._max_bars_back)  # noqa

    new_globals['__scope_id__'] = call_id_key

    # Create a new function with new closure and globals
    isolated_function = FunctionType(
        func.__code__,
        new_globals,
        func.__name__,
        func.__defaults__,
        func.__closure__
    )

    _function_cache[call_id_key] = isolated_function
    return isolated_function
