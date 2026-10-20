import builtins
from typing import (TypeVar, Callable, get_type_hints, overload as typing_overload,
                    Any, Type, Union, get_args, get_origin, cast)
from inspect import signature
from collections import defaultdict
from types import FunctionType, UnionType

from .function_isolation import FunctionCallSlot
from ..types.na import NA

__all__ = ['overload']

T = TypeVar('T')

__scope_id__ = ""
_implementation_slot = FunctionCallSlot('__overloaded__', 0)
__function_call_slots__ = {'overload.dispatcher': ('_implementation_slot',)}
_BASIC_TYPES = (int, float, bool, str, bytes, type(None))


class Implementation:
    __slots__ = ('func', 'sig', 'type_hints', 'param_types', 'min_args')
    func: Callable
    sig: Any  # Signature object
    type_hints: dict
    param_types: tuple  # Cached parameter types for quick checking
    min_args: int  # Number of parameters without a default value

    def __init__(self, func: Callable, sig: Any, type_hints: dict, param_types: tuple):
        self.func = func
        self.sig = sig
        self.type_hints = type_hints
        self.param_types = param_types
        self.min_args = builtins.sum(
            1 for p in sig.parameters.values() if p.default is p.empty
        )


_registry: dict[str, list[Implementation]] = defaultdict(list)
_implementations: dict[str, Implementation] = {}  # Store implementations separately
_dispatchers: dict[str, Callable] = {}  # Store dispatchers separately


def _cacheable_annotation(annotation):
    if any(annotation is basic for basic in _BASIC_TYPES):
        return True
    if get_origin(annotation) in (Union, UnionType):
        return all(_cacheable_annotation(arg) for arg in get_args(annotation))
    return False


def _check_type(value: Any, expected_type: Type) -> bool:
    """Cached type checking for better performance with Pine Script compatibility"""
    # Direct type match
    if isinstance(value, expected_type):
        return True

    # Pine Script-like int to float conversion
    if expected_type is float and isinstance(value, int):
        return True

    # Handle NA values - Pine Script allows NA for any basic type
    if isinstance(value, NA):
        # Check if expected_type is a Pine Script basic type
        if expected_type in (int, float, str, bool):
            return True

        # For Union types containing basic types, NA is also acceptable
        origin = get_origin(expected_type)
        if origin in (Union, type(None) | type):
            args = get_args(expected_type)
            # If any of the Union members is a basic type, accept NA
            if any(arg in (int, float, str, bool) for arg in args):
                return True

        # For non-basic types, check if NA's type matches
        na_type = value.type
        # Handle the case when na_type is an actual instance and not a type
        if not isinstance(na_type, type):
            if na_type is None:
                return isinstance(None, expected_type)
            na_type = type(na_type)
        return na_type is expected_type

    # Handle Union types
    origin = get_origin(expected_type)
    if origin in (Union, type(None) | type):
        return any(_check_type(value, t) for t in get_args(expected_type))

    if hasattr(expected_type, '__instancecheck__'):
        return expected_type.__instancecheck__(value)

    return False


def overload(func: Callable[..., T]) -> Callable[..., T]:
    """
    Optimized function overloading decorator with:
    - Type checking cache
    - Pre-calculated signatures and type hints
    - Quick parameter matching
    - IDE type checking support via typing.overload
    """
    global __scope_id__

    _func = cast(FunctionType, func)
    qualname = _func.__module__ + '.' + _func.__qualname__
    qualname_with_line = f"{qualname}:{_func.__code__.co_firstlineno}"

    # This caching prevents re-creating the dispatcher if it already exists
    _dispatcher = _dispatchers.get(qualname)
    if _dispatcher:
        try:
            impl = _implementations[qualname_with_line]
            if impl:
                # Change the function implementation to the new one
                impl.func = func
                return _dispatcher
        except KeyError:
            pass

    # Register with typing.overload for IDE support
    typing_overload(func)

    # Pre-calculate and cache implementation info
    impl = Implementation(
        func=func,
        sig=signature(func),
        type_hints=get_type_hints(func),
        param_types=tuple(
            (name, get_type_hints(func).get(name, Any))
            for name in signature(func).parameters
        ),
    )
    _implementations[qualname_with_line] = impl

    if qualname not in _dispatchers:
        positional_cache = {}
        registry_size = -1
        cacheable = False

        # noinspection PyShadowingNames
        def dispatcher(*args: Any, **kwargs: Any) -> Any:
            nonlocal registry_size, cacheable
            implementations = _registry[qualname]
            # Quick path: try direct positional args match first
            # (trailing parameters with defaults may be omitted)
            if not kwargs:
                if registry_size != len(implementations):
                    positional_cache.clear()
                    registry_size = len(implementations)
                    cacheable = all(_cacheable_annotation(type_)
                                    for item in implementations for _, type_ in item.param_types)
                # NA and custom instance checks may depend on values, not just types.
                key = tuple(map(type, args)) if cacheable else None
                if key is not None and any(type_ is not float and type_ is not int and type_ is not bool
                                           and type_ is not str and type_ is not bytes and type_ is not type(None)
                                           for type_ in key):
                    key = None
                if key is not None:
                    impl = positional_cache.get(key)
                    if impl is not None:
                        return _implementation_slot.resolve(impl.func, __scope_id__, -1, 0)(*args)
                for impl in implementations:
                    if impl.min_args <= len(args) <= len(impl.param_types):
                        if all(_check_type(arg, type_)
                               for arg, (_, type_) in zip(args, impl.param_types)):
                            if key is not None:
                                if len(positional_cache) >= 64:
                                    positional_cache.clear()
                                positional_cache[key] = impl
                            return _implementation_slot.resolve(impl.func, __scope_id__, -1, 0)(*args)

            # Slower path: handle mixed args/kwargs
            for impl in implementations:
                try:
                    bound = impl.sig.bind(*args, **kwargs)
                    bound.apply_defaults()

                    if all(_check_type(value, impl.type_hints.get(name, Any))
                           for name, value in bound.arguments.items()):
                        return _implementation_slot.resolve(impl.func, __scope_id__, -1, 0)(*args, **kwargs)
                except TypeError:
                    continue

            raise TypeError(f"No matching implementation found for {qualname}: {args}, {kwargs}")

        # Store implementation and dispatcher
        _registry[qualname].append(impl)

        _dispatcher = dispatcher

        _dispatchers[qualname] = _dispatcher
        return _dispatcher

    # Add additional implementation
    _registry[qualname].append(impl)

    dispatcher = _dispatchers[qualname]

    # Return existing dispatcher
    return dispatcher
