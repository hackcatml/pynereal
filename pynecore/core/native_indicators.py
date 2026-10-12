"""Opt-in state-owning indicators. Unsupported inputs retain the original Python semantics."""
import copy
import os
from collections import deque
from types import FunctionType

from .series import SeriesImpl
from ..types import NA

State = None
_supports_wma = False
_supports_extrema = False
_supports_valuewhen = False
_supports_statistics = False
_supports_scalar_math = False
if os.environ.get('PYNECORE_NATIVE_INDICATORS') == '1':
    try:
        import _pynereal_native_ta
        from _pynereal_native_ta import State
        _supports_wma = bool(getattr(_pynereal_native_ta, 'WMA_SUPPORTED', False))
        _supports_extrema = bool(getattr(_pynereal_native_ta, 'EXTREMA_SUPPORTED', False))
        _supports_valuewhen = bool(getattr(_pynereal_native_ta, 'VALUEWHEN_SUPPORTED', False))
        _supports_statistics = bool(getattr(_pynereal_native_ta, 'STATISTICS_SUPPORTED', False))
        _supports_scalar_math = bool(getattr(_pynereal_native_ta, 'SCALAR_MATH_SUPPORTED', False))
    except ImportError:
        pass


def _adapter(source, length, _alpha=None):
    global __persistent_native_indicator__
    # security expressions can call a TA function directly, without isolation.
    # Their module-level persistent state must keep the original snapshot path.
    if '__scope_id__' not in globals():
        return _direct(source, length, _alpha) if _name == 'ema' else _direct(source, length)
    if __persistent_native_indicator__ is None:
        __persistent_native_indicator__ = State(_kind, SeriesImpl.DEFAULT_MAX_BARS_BACK + 1)
    result = __persistent_native_indicator__.step(source, length, _lib.bar_index, _alpha)
    if result is NotImplemented:
        __persistent_native_indicator__ = _LegacyIndicator(_name, _originals, __persistent_native_indicator__.export())
        result = __persistent_native_indicator__.step(source, length, _lib.bar_index, _alpha)
    return result


def _extrema_adapter(source, length, _bars=False, _tuple=False, _check_eq=False):
    global __persistent_native_indicator__
    if '__scope_id__' not in globals():
        return _direct(source, length, _bars, _tuple, _check_eq)
    if __persistent_native_indicator__ is None:
        __persistent_native_indicator__ = State(_kind, _capacity)
    result = __persistent_native_indicator__.extrema_step(source, length, _lib.bar_index,
                                                         _bars, _tuple, _check_eq)
    if result is NotImplemented:
        __persistent_native_indicator__ = _LegacyIndicator(_name, _originals, __persistent_native_indicator__.export())
        result = __persistent_native_indicator__.extrema_step(source, length, _lib.bar_index,
                                                             _bars, _tuple, _check_eq)
    return result


def _valuewhen_adapter(condition, source, occurrence):
    global __persistent_native_indicator__
    if '__scope_id__' not in globals():
        return _direct(condition, source, occurrence)
    if __persistent_native_indicator__ is None:
        __persistent_native_indicator__ = State(7, 1)
    result = __persistent_native_indicator__.valuewhen_step(condition, source, occurrence)
    if result is NotImplemented and isinstance(__persistent_native_indicator__, State):
        __persistent_native_indicator__ = _LegacyIndicator(_name, _originals, __persistent_native_indicator__.export())
        result = __persistent_native_indicator__.valuewhen_step(condition, source, occurrence)
    return result


def _statistics_adapter(source, length, biased=True):
    global __persistent_native_indicator__
    if '__scope_id__' not in globals():
        return _direct(source, length, biased)
    if __persistent_native_indicator__ is None:
        __persistent_native_indicator__ = State(_kind, _capacity)
    result = __persistent_native_indicator__.statistics_step(source, length, _lib.bar_index, biased)
    if result is NotImplemented and isinstance(__persistent_native_indicator__, State):
        __persistent_native_indicator__ = _LegacyIndicator(_name, _originals, __persistent_native_indicator__.export())
        result = __persistent_native_indicator__.statistics_step(source, length, _lib.bar_index, biased)
    return result


def _bb_adapter(source, length, mult):
    global __persistent_native_indicator__
    if '__scope_id__' not in globals():
        return _direct(source, length, mult)
    if __persistent_native_indicator__ is None:
        __persistent_native_indicator__ = State(11, _capacity)
    result = __persistent_native_indicator__.statistics_step(source, length, _lib.bar_index, mult)
    if result is NotImplemented and isinstance(__persistent_native_indicator__, State):
        __persistent_native_indicator__ = _LegacyIndicator(_name, _originals, __persistent_native_indicator__.export())
        result = __persistent_native_indicator__.statistics_step(source, length, _lib.bar_index, mult)
    return result


def _sum_adapter(source, length):
    global __persistent_native_indicator__
    if '__scope_id__' not in globals():
        return _direct(source, length)
    if __persistent_native_indicator__ is None:
        __persistent_native_indicator__ = State(8, _capacity)
    result = __persistent_native_indicator__.statistics_step(source, length, _lib.bar_index, None)
    if result is NotImplemented and isinstance(__persistent_native_indicator__, State):
        __persistent_native_indicator__ = _LegacyIndicator(_name, _originals, __persistent_native_indicator__.export())
        result = __persistent_native_indicator__.statistics_step(source, length, _lib.bar_index, None)
    return result


def _minmax_adapter(*numbers):
    return _native_scalar(*numbers)


def _round_adapter(number, precision=NA(int)):
    return _native_scalar(number, precision)


class _LegacyIndicator:
    """Cold-path handoff to isolated copies of the actual transformed Python functions."""

    def __init__(self, name, originals, state):
        self.name = name
        self.originals = originals
        self.functions = {}
        self.names = {}
        root = self._get(name, ())
        if name == 'sum':
            self._restore_sum(root, state['sum'])
        elif name in ('variance', 'stdev', 'bb'):
            variance = root if name == 'variance' else self._child(
                root if name == 'stdev' else self._child(root, 'stdev'), 'variance')
            self._restore_variance(variance, state['variance'])
            if name == 'bb':
                self._restore_sum(self._child(self._child(root, 'sma'), 'sum'), state['sum'])
        elif name == 'sma':
            self._restore_sum(self._child(root, 'sum'), state['sum'])
        elif name == 'ema':
            self._restore_smooth(root, state['up'])
        elif name == 'rma':
            self._restore_smooth(self._child(root, 'ema'), state['up'])
        elif name in ('highest', 'lowest'):
            suffix = 'max' if name == 'highest' else 'min'
            self._persistent(root, 'last_' + suffix, state['last'])
            self._persistent(root, 'last_' + suffix + '_index', state['index'])
            series = SeriesImpl(len(state['buffer']) - 1)
            series._buffer = state['buffer']
            series._size = state['size']
            series._write_pos = state['write']
            series._last_bar_index = state['bar']
            root.__globals__[f'__series_{name}\u00b7source__'] = series
        elif name == 'wma':
            self._persistent(root, 'last', state['last'])
            if state['initialized']:
                for variable in ('denom', 'count', 'summ', 'sum_low', 'weighted_summ', 'weighted_low'):
                    self._persistent(root, variable, state[variable])
            series = SeriesImpl(len(state['buffer']) - 1)
            series._buffer = state['buffer']
            series._size = state['size']
            series._write_pos = state['write']
            series._last_bar_index = state['bar']
            root.__globals__['__series_wma\u00b7source__'] = series
        elif name == 'valuewhen':
            if state['initialized']:
                self._persistent(root, 'values', deque(state['values'], maxlen=state['maxlen']))
        else:
            if not isinstance(state['previous'], NA):
                self._persistent(root, 'prev_src', state['previous'])
            for number, side in enumerate(('up', 'down')):
                self._restore_smooth(self._child(self._child(root, 'rma', number), 'ema'), state[side])

    def _get(self, name, path):
        if path in self.functions:
            return self.functions[path]
        original = self.originals[name]
        namespace = dict(original.__globals__)
        for variable in namespace.get('__persistent_function_vars__', {}).get(original.__qualname__, ()):
            namespace[variable] = copy.deepcopy(namespace[variable])
        for variable in namespace.get('__series_function_vars__', {}).get(original.__qualname__, ()):
            namespace[variable] = SeriesImpl(namespace[variable]._max_bars_back)
        namespace.update({key: value for key, value in self.originals.items() if key != 'sum'})
        namespace['isolate_function'] = self._isolate
        namespace['__scope_id__'] = path
        function = FunctionType(original.__code__, namespace, original.__name__, original.__defaults__, original.__closure__)
        self.functions[path] = function
        self.names[path] = name
        return function

    def _isolate(self, function, call_id, parent, closure_count=-1, counter=0):
        if isinstance(function, type) or not hasattr(function, '__globals__'):
            return function
        name = function.__name__
        if name not in self.originals:
            raise RuntimeError(f'Unexpected native indicator fallback dependency: {name}')
        return self._get(name, (parent, call_id, counter))

    def _child(self, parent, name, number=0):
        candidates = [value for value in parent.__code__.co_consts if isinstance(value, str)
                      and len(value.split('\u00b7')) == 3 and value.split('\u00b7')[1].split('.')[-1] == name]
        call_id = sorted(candidates, key=lambda value: int(value.split('\u00b7')[-1]))[number]
        return self._get(name, (parent.__globals__['__scope_id__'], call_id, 1))

    @staticmethod
    def _persistent(function, name, value):
        registry = function.__globals__['__persistent_function_vars__'][function.__qualname__]
        variable = next(key for key in registry if key.endswith(f'\u00b7{name}__'))
        function.__globals__[variable] = value
        function.__globals__[variable + '_initialized__'] = True

    def _restore_sum(self, function, state):
        namespace = function.__globals__
        namespace['__persistent_summ_summ__'] = state['total']
        namespace['__persistent_summ_compensation__'] = state['compensation']
        namespace['__persistent_summ_count__'] = state['count']
        series = SeriesImpl(len(state['buffer']) - 1)
        series._buffer = state['buffer']
        series._size = state['size']
        series._write_pos = state['write']
        series._last_bar_index = state['bar']
        namespace['__series_summ_source__'] = series

    def _restore_smooth(self, function, state):
        if state['initialized']:
            self._persistent(function, 'alpha', state['alpha'])
            self._persistent(function, 'last_val', state['last'])
        self._restore_sum(self._child(self._child(function, 'sma'), 'sum'), state['sum'])

    def _restore_variance(self, function, state):
        for variable in ('count', 'sum_val', 'sum_val_c', 'sum_sq', 'sum_sq_c'):
            self._persistent(function, variable, state[variable])
        series = SeriesImpl(len(state['buffer']) - 1)
        series._buffer = state['buffer']
        series._size, series._write_pos = state['size'], state['write']
        series._last_bar_index = state['bar']
        function.__globals__['__series_variance\u00b7source__'] = series

    def step(self, source, length, bar, alpha):
        function = self.functions[()]
        return function(source, length, alpha) if self.name == 'ema' else function(source, length)

    def extrema_step(self, source, length, bar, bars, tuple_, check_eq):
        return self.functions[()](source, length, bars, tuple_, check_eq)

    def valuewhen_step(self, condition, source, occurrence):
        return self.functions[()](condition, source, occurrence)

    def statistics_step(self, source, length, bar, option):
        function = self.functions[()]
        return function(source, length) if self.name == 'sum' else function(source, length, option)

    def __copy__(self):
        result = object.__new__(type(self))
        result.name, result.originals = self.name, self.originals
        result.functions, result.names = {}, {}
        for path, function in self.functions.items():
            restored = result._get(self.names[path], path)
            for key, value in function.__globals__.items():
                if key.startswith(('__persistent_', '__series_')) and not key.endswith('_vars__'):
                    restored.__globals__[key] = copy.deepcopy(value)
        return result

    def __deepcopy__(self, memo):
        return self.__copy__()


def is_native_indicator_state(value):
    return (State is not None and isinstance(value, State)) or isinstance(value, _LegacyIndicator)


def install(namespace):
    if State is None or '__persistent_function_vars__' not in namespace:
        return
    from .. import lib
    names = ('sma', 'ema', 'rma', 'rsi', 'wma')
    originals = {name: namespace[name] for name in names}
    originals['sum'] = lib.math.sum.__globals__.get('_direct', lib.math.sum)
    # Freeze the legacy globals before replacing any aliases in ta.py.
    originals = {name: FunctionType(fn.__code__, dict(fn.__globals__), fn.__name__, fn.__defaults__, fn.__closure__)
                 for name, fn in originals.items()}
    statistics_originals = dict(originals)
    if _supports_statistics:
        for name in ('variance', 'stdev', 'bb'):
            original = namespace[name]
            statistics_originals[name] = FunctionType(original.__code__, dict(original.__globals__), original.__name__,
                                                     original.__defaults__, original.__closure__)
    # The WMA kernel implements the compensated algorithm, not legacy WMA.
    wma_registry = originals['wma'].__globals__.get('__persistent_function_vars__', {}).get('wma', ())
    wma_compatible = all(f'__persistent_wma\u00b7{name}__' in wma_registry
                         for name in ('last', 'sum_low', 'weighted_low'))
    for kind, name in enumerate(names):
        if name == 'wma' and not (_supports_wma and wma_compatible):
            continue
        globals_ = {**globals(), '_kind': kind, '_name': name, '_lib': lib, '_originals': originals,
                    '_direct': namespace[name],
                    '__persistent_native_indicator__': None,
                    '__persistent_function_vars__': {name: ('__persistent_native_indicator__',)},
                    '__series_function_vars__': {}}
        function = FunctionType(_adapter.__code__, globals_, name, (None,))
        function.__qualname__ = name
        function.__doc__ = originals[name].__doc__
        namespace[name] = function

    if _supports_statistics:
        capacity = statistics_originals['variance'].__globals__['__series_variance\u00b7source__']._capacity
        for kind, name in enumerate(('variance', 'stdev', 'bb'), 9):
            original = namespace[name]
            globals_ = {**globals(), '_kind': kind, '_name': name, '_lib': lib,
                        '_capacity': capacity, '_originals': statistics_originals, '_direct': original,
                        '__persistent_native_indicator__': None,
                        '__persistent_function_vars__': {name: ('__persistent_native_indicator__',)},
                        '__series_function_vars__': {}}
            adapter = _bb_adapter if name == 'bb' else _statistics_adapter
            function = FunctionType(adapter.__code__, globals_, name, original.__defaults__)
            function.__qualname__, function.__doc__ = name, original.__doc__
            namespace[name] = function

    if _supports_valuewhen:
        original = namespace['valuewhen']
        frozen = FunctionType(original.__code__, dict(original.__globals__), original.__name__,
                              original.__defaults__, original.__closure__)
        globals_ = {**globals(), '_name': 'valuewhen', '_originals': {'valuewhen': frozen},
                    '_direct': original, '__persistent_native_indicator__': None,
                    '__persistent_function_vars__': {'valuewhen': ('__persistent_native_indicator__',)},
                    '__series_function_vars__': {}}
        function = FunctionType(_valuewhen_adapter.__code__, globals_, 'valuewhen')
        function.__qualname__ = 'valuewhen'
        function.__doc__ = original.__doc__
        namespace['valuewhen'] = function

    if _supports_extrema:
        # Keep overload signatures/dispatch and the implicit high/low wrappers intact.
        from .overload import _registry
        for kind, name in enumerate(('highest', 'lowest'), 5):
            implementation = next(item for item in _registry[f'pynecore.lib.ta.{name}']
                                  if item.min_args == 2)
            original = implementation.func
            frozen = FunctionType(original.__code__, dict(original.__globals__), original.__name__,
                                  original.__defaults__, original.__closure__)
            globals_ = {**globals(), '_kind': kind, '_name': name, '_lib': lib,
                        '_originals': {name: frozen}, '_direct': original,
                        '_capacity': original.__globals__[f'__series_{name}\u00b7source__']._capacity,
                        '__persistent_native_indicator__': None,
                        '__persistent_function_vars__': {name: ('__persistent_native_indicator__',)},
                        '__series_function_vars__': {}}
            function = FunctionType(_extrema_adapter.__code__, globals_, name, original.__defaults__)
            function.__qualname__ = name
            function.__doc__ = original.__doc__
            implementation.func = function


def install_math(namespace):
    if _supports_scalar_math:
        for name, native_name in (('min', 'minimum'), ('max', 'maximum'), ('round', 'round_number')):
            original = namespace[name]
            globals_ = {'__name__': __name__, '_direct': original, '_scalar_math_name': name,
                        '_native_scalar': getattr(_pynereal_native_ta, native_name),
                        '__persistent_function_vars__': {}, '__series_function_vars__': {}}
            adapter = _round_adapter if name == 'round' else _minmax_adapter
            # Preserve the Python signature and the existing function-isolation cache path.
            code = adapter.__code__.replace(co_name=name, co_qualname=name)
            function = FunctionType(code, globals_, name, original.__defaults__)
            function.__qualname__, function.__doc__ = name, original.__doc__
            namespace[name] = function
    if not _supports_statistics:
        return
    from .. import lib
    original = namespace['sum']
    frozen = FunctionType(original.__code__, dict(original.__globals__), original.__name__,
                          original.__defaults__, original.__closure__)
    globals_ = {**globals(), '_name': 'sum', '_lib': lib, '_originals': {'sum': frozen},
                '_direct': original, '_capacity': namespace['__series_summ_source__']._capacity,
                '__persistent_native_indicator__': None,
                '__persistent_function_vars__': {'sum': ('__persistent_native_indicator__',)},
                '__series_function_vars__': {}}
    function = FunctionType(_sum_adapter.__code__, globals_, 'sum')
    function.__qualname__, function.__doc__ = 'sum', original.__doc__
    namespace['sum'] = function
