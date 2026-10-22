#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <cmath>
#include <new>
#include <vector>
#include "finite_fsum.h"

// Keep the Python operation order; build with contraction and fast-math disabled.
struct Value {
    double number = 0.0;
    bool missing = true;
    Value() = default;
    explicit Value(double n) : number(n), missing(false) {}
};

static PyObject *na_value, *na_int_value, *na_type, *sqrt_method, *builtin_round;

static PyObject *object(Value value) {
    return value.missing ? Py_NewRef(na_value) : PyFloat_FromDouble(value.number);
}

struct Sum {
    std::vector<double> buffer;
    Py_ssize_t size = 0, write = 0;
    long long last_bar = -1, count = 0;
    Value total{0.0}, compensation{0.0};

    explicit Sum(Py_ssize_t capacity) : buffer(capacity) {}

    void add(double source, long long bar) {
        if (last_bar == bar) {
            if (size) buffer[(write + buffer.size() - 1) % buffer.size()] = source;
            return;
        }
        if (write >= static_cast<Py_ssize_t>(buffer.size())) write = 0;
        buffer[write++] = source;
        if (size < static_cast<Py_ssize_t>(buffer.size())) ++size;
        last_bar = bar;
    }

    void accumulate(Value delta) {
        if (total.missing || compensation.missing || delta.missing) {
            total = compensation = Value();
            return;
        }
        double terms[4] = {total.number, -compensation.number, delta.number};
        double precise, residual;
        if (finite_fsum(terms, 3, precise)) {
            terms[3] = -precise;
            if (finite_fsum(terms, 4, residual)) {
                total = Value(precise);
                compensation = Value(-residual);
                return;
            }
        }
        double corrected = delta.number - compensation.number;
        double updated = total.number + corrected;
        compensation = Value((updated - total.number) - corrected);
        total = Value(updated);
    }

    Value step(Value source, long long length, long long bar) {
        if (length == 1) return source;
        if (!source.missing) add(source.number, bar);
        if (count < length - 1) {
            if (!source.missing) {
                ++count;
                accumulate(source);
            }
            return {};
        }
        if (count == length - 1) {
            if (source.missing) return {};
            ++count;
        } else {
            if (source.missing) return total;
            Value old;
            if (length < size) {
                auto pos = (write - 1 - length + buffer.size()) % buffer.size();
                old = Value(-buffer[pos]);
            }
            accumulate(old);
        }
        accumulate(source);
        return total;
    }

    Value sma(Value source, long long length, long long bar) {
        Value result = step(source, length, bar);
        if (!result.missing) result.number /= static_cast<double>(length);
        return result;
    }
};

struct Smooth {
    Sum sum;
    double alpha = 0.0;
    bool initialized = false;
    Value last;

    explicit Smooth(Py_ssize_t capacity) : sum(capacity) {}

    Value step(Value source, long long length, long long bar, double coefficient) {
        if (length == 1) return source;
        if (source.missing) return {};
        if (!initialized) {
            alpha = coefficient;
            initialized = true;
        }
        if (last.missing) {
            last = sum.sma(source, length, bar);
        } else {
            last = Value(alpha * source.number + (1.0 - alpha) * last.number);
        }
        return last;
    }
};

struct Wma {
    std::vector<Value> buffer;
    Py_ssize_t size = 0, write = 0;
    long long last_bar = -1, count = 0;
    Value last;
    bool initialized = false;
    double denom = 0.0, summ = 0.0, sum_low = 0.0, weighted_summ = 0.0, weighted_low = 0.0;

    explicit Wma(Py_ssize_t capacity) : buffer(capacity) {}

    void add(Value source, long long bar) {
        if (last_bar == bar) {
            if (size) buffer[(write + buffer.size() - 1) % buffer.size()] = source;
            return;
        }
        if (write >= static_cast<Py_ssize_t>(buffer.size())) write = 0;
        buffer[write++] = source;
        if (size < static_cast<Py_ssize_t>(buffer.size())) ++size;
        last_bar = bar;
    }

    bool step(Value source, long long length, long long bar, Value &result) {
        // Unsupported cases must hand off before any history or scalar is changed.
        if (length >= static_cast<long long>(buffer.size()) ||
            (!source.missing && !std::isfinite(source.number))) return false;
        if (length == 1 || (source.missing && last.missing)) {
            add(source, bar);
            result = source;
            return true;
        }
        bool missing = source.missing;
        if (missing) source = last;
        bool warming = count < length;
        double weight = static_cast<double>(warming ? count + 1 : length);
        double product = source.number * weight;
        int exponent;
        double fraction = std::frexp(source.number, &exponent);
        double split = 134217729.0 * fraction;
        double high = std::ldexp(split - (split - fraction), exponent);
        double low = source.number - high;
        double product_low = (high * weight - product) + low * weight;
        double sum_terms[5] = {summ, sum_low, source.number};
        double weighted_terms[7] = {weighted_summ, weighted_low, product, product_low};
        size_t ns = 3, nw = 4;
        if (!warming) {
            Py_ssize_t next_size = size + (bar != last_bar && size < static_cast<Py_ssize_t>(buffer.size()));
            Py_ssize_t next_write = bar == last_bar ? write : write % buffer.size() + 1;
            if (length >= next_size) return false;
            const Value &old = buffer[(next_write - 1 - length + buffer.size()) % buffer.size()];
            if (old.missing) return false;
            sum_terms[2] = -old.number;
            sum_terms[3] = source.number;
            ns = 4;
            weighted_terms[2] = -summ;
            weighted_terms[3] = -sum_low;
            weighted_terms[4] = product;
            weighted_terms[5] = product_low;
            nw = 6;
        }
        double updated_sum, updated_sum_low, updated_weighted, updated_weighted_low;
        if (!finite_fsum(sum_terms, ns, updated_sum)) return false;
        sum_terms[ns] = -updated_sum;
        if (!finite_fsum(sum_terms, ns + 1, updated_sum_low)) return false;
        if (!finite_fsum(weighted_terms, nw, updated_weighted)) return false;
        weighted_terms[nw] = -updated_weighted;
        if (!finite_fsum(weighted_terms, nw + 1, updated_weighted_low)) return false;
        add(source, bar);
        last = source;
        if (!initialized) {
            // Capacity is <= 1e6: the integer product is exactly representable.
            denom = static_cast<double>(length * (length + 1)) / 2.0;
            initialized = true;
        }
        if (warming) ++count;
        summ = updated_sum;
        sum_low = updated_sum_low;
        weighted_summ = updated_weighted;
        weighted_low = updated_weighted_low;
        result = missing || count < length ? Value() : Value((weighted_summ + weighted_low) / denom);
        return true;
    }
};

struct Extrema {
    std::vector<Value> buffer;
    Py_ssize_t size = 0, write = 0;
    long long last_bar = -1, index = 0;
    Value last;

    explicit Extrema(Py_ssize_t capacity) : buffer(capacity) {}

    void step(Value source, long long length, long long bar, bool highest, bool check_eq) {
        if (last_bar == bar) {
            if (size) buffer[(write + buffer.size() - 1) % buffer.size()] = source;
        } else {
            if (write >= static_cast<Py_ssize_t>(buffer.size())) write = 0;
            buffer[write++] = source;
            if (size < static_cast<Py_ssize_t>(buffer.size())) ++size;
            last_bar = bar;
        }
        if (last.missing || (!source.missing &&
                ((highest ? last.number < source.number : last.number > source.number) ||
                 (check_eq && last.number == source.number)))) {
            last = source;
            index = 0;
        }
        if (index >= length) {
            last = source;
            index = 0;
            for (long long i = 1; i < length && i < size; ++i) {
                const Value &value = buffer[(write - 1 - i + buffer.size()) % buffer.size()];
                if (value.missing || last.missing) continue;
                if (highest ? value.number > last.number : value.number < last.number) {
                    last = value;
                    index = i;
                } else if (!check_eq && value.number == last.number) {
                    // Keep the selected value, including its signed zero; only update age.
                    index = i;
                }
            }
        }
        ++index;
    }
};

struct ValueWhen {
    // Only immutable builtin numbers are stored, so these references cannot form cycles.
    std::vector<PyObject *> buffer;
    Py_ssize_t size = 0, write = 0;
    ValueWhen() = default;
    ValueWhen(const ValueWhen &other) : buffer(other.buffer), size(other.size), write(other.write) {
        for (PyObject *value : buffer) Py_XINCREF(value);
    }
    ValueWhen &operator=(const ValueWhen &) = delete;
    ~ValueWhen() {
        for (PyObject *value : buffer) Py_XDECREF(value);
    }

    PyObject *step(bool condition, PyObject *source, Py_ssize_t occurrence) {
        // Like the original persistent deque, capacity is fixed by the first non-NA input.
        if (buffer.empty()) buffer.resize(occurrence + 1, nullptr);
        if (condition) {
            PyObject *value = Py_NewRef(source);
            Py_XSETREF(buffer[write], value);
            if (++write == static_cast<Py_ssize_t>(buffer.size())) write = 0;
            if (size < static_cast<Py_ssize_t>(buffer.size())) ++size;
        }
        if (size == occurrence + 1)
            return Py_NewRef(buffer[size < static_cast<Py_ssize_t>(buffer.size()) ? 0 : write]);
        return Py_NewRef(na_value);
    }
};

struct Variance {
    std::vector<Value> buffer;
    Py_ssize_t size = 0, write = 0;
    long long last_bar = -1, count = 0;
    double sum_val = 0.0, sum_val_c = 0.0, sum_sq = 0.0, sum_sq_c = 0.0;

    explicit Variance(Py_ssize_t capacity) : buffer(capacity) {}

    bool supported(Value source, long long length, long long bar) const {
        if (length == 1 || source.missing || count < length) return true;
        Py_ssize_t next_size = size + (bar != last_bar && size < static_cast<Py_ssize_t>(buffer.size()));
        Py_ssize_t next_write = bar == last_bar ? write : write % buffer.size() + 1;
        // Missing historical operands preserve their Python NA type via the cold path.
        return length < next_size && !buffer[(next_write - 1 - length + buffer.size()) % buffer.size()].missing;
    }

    static void accumulate(double delta, double &total, double &compensation) {
        double y = delta - compensation;
        double t = total + y;
        compensation = (t - total) - y;
        total = t;
    }

    Value step(Value source, long long length, long long bar, bool biased) {
        if (last_bar == bar) {
            if (size) buffer[(write + buffer.size() - 1) % buffer.size()] = source;
        } else {
            if (write >= static_cast<Py_ssize_t>(buffer.size())) write = 0;
            buffer[write++] = source;
            if (size < static_cast<Py_ssize_t>(buffer.size())) ++size;
            last_bar = bar;
        }
        if (length == 1) return Value(0.0);
        if (source.missing) return {};
        accumulate(source.number, sum_val, sum_val_c);
        accumulate(source.number * source.number, sum_sq, sum_sq_c);
        if (count < length) {
            if (++count < length) return {};
        } else {
            ++count;
            double old = buffer[(write - 1 - length + buffer.size()) % buffer.size()].number;
            accumulate(-old, sum_val, sum_val_c);
            accumulate(-(old * old), sum_sq, sum_sq_c);
        }
        double n = static_cast<double>(length);
        double mean = sum_val / n;
        if (biased) return Value(sum_sq / n - mean * mean);
        return Value(sum_sq / (n - 1.0) - (n / (n - 1.0)) * mean * mean);
    }
};

struct State {
    int kind;
    Sum sum;
    Smooth up, down;
    Value previous;
    Wma wma;
    Extrema extrema;
    ValueWhen valuewhen;
    Variance variance;
    State(int kind, Py_ssize_t capacity) : kind(kind), sum(capacity), up(capacity), down(capacity),
        wma(kind == 4 ? capacity : 0), extrema(kind == 5 || kind == 6 ? capacity : 0),
        variance(kind >= 9 ? capacity : 0) {}
};

typedef struct {
    PyObject_HEAD
    State *state;
} Indicator;

static PyTypeObject IndicatorType = {PyVarObject_HEAD_INIT(nullptr, 0)};

static PyObject *indicator_new(PyTypeObject *type, PyObject *args, PyObject *) {
    int kind;
    Py_ssize_t capacity;
    if (!PyArg_ParseTuple(args, "in", &kind, &capacity)) return nullptr;
    if (kind < 0 || kind > 11 || capacity <= 0 || capacity > 1000000) {
        PyErr_SetString(PyExc_ValueError, "Invalid indicator kind or history capacity");
        return nullptr;
    }
    auto *self = reinterpret_cast<Indicator *>(type->tp_alloc(type, 0));
    if (!self) return nullptr;
    try {
        self->state = new State(kind, capacity);
    } catch (const std::bad_alloc &) {
        Py_DECREF(self);
        return PyErr_NoMemory();
    }
    return reinterpret_cast<PyObject *>(self);
}

static void indicator_free(Indicator *self) {
    delete self->state;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
}

static PyObject *indicator_copy(Indicator *self, PyObject *) {
    auto *result = reinterpret_cast<Indicator *>(IndicatorType.tp_alloc(&IndicatorType, 0));
    if (!result) return nullptr;
    try {
        result->state = new State(*self->state);
    } catch (const std::bad_alloc &) {
        Py_DECREF(result);
        return PyErr_NoMemory();
    }
    return reinterpret_cast<PyObject *>(result);
}

static PyObject *indicator_step(Indicator *self, PyObject *const *args, Py_ssize_t nargs) {
    if (self->state->kind >= 5) {
        PyErr_SetString(PyExc_TypeError, "This indicator requires its dedicated step method");
        return nullptr;
    }
    if (nargs != 4) {
        PyErr_SetString(PyExc_TypeError, "step expects source, length, bar index, alpha");
        return nullptr;
    }
    // Decline before mutating state; the adapter transfers it to the original Python functions.
    bool missing = Py_TYPE(args[0]) == reinterpret_cast<PyTypeObject *>(na_type);
    if ((!PyFloat_CheckExact(args[0]) && !missing) || !PyLong_CheckExact(args[1]) ||
        !PyLong_CheckExact(args[2]) || (args[3] != Py_None && !PyFloat_CheckExact(args[3]))) {
        return Py_NewRef(Py_NotImplemented);
    }
    int overflow = 0;
    long long length = PyLong_AsLongLongAndOverflow(args[1], &overflow);
    if (overflow || length <= 0 || length >= (1LL << 53)) return Py_NewRef(Py_NotImplemented);
    long long bar = PyLong_AsLongLongAndOverflow(args[2], &overflow);
    if (overflow) return Py_NewRef(Py_NotImplemented);
    Value source = missing ? Value() : Value(PyFloat_AS_DOUBLE(args[0]));
    State &s = *self->state;
    Value result;
    if (s.kind == 0) {
        result = s.sum.sma(source, length, bar);
    } else if (s.kind == 1 || s.kind == 2) {
        if (length == 1) return Py_NewRef(args[0]);
        double alpha = s.kind == 2 ? 1.0 / static_cast<double>(length) :
            (args[3] != Py_None && PyFloat_AS_DOUBLE(args[3]) != 0.0 ?
                PyFloat_AS_DOUBLE(args[3]) : 2.0 / static_cast<double>(length + 1));
        result = s.up.step(source, length, bar, alpha);
    } else if (s.kind == 4) {
        if (missing && args[0] != na_value) return Py_NewRef(Py_NotImplemented);
        if (!s.wma.step(source, length, bar, result)) return Py_NewRef(Py_NotImplemented);
    } else if (!source.missing) {
        if (!s.previous.missing) {
            double u = source.number - s.previous.number;
            double d = s.previous.number - source.number;
            // Python max keeps its first argument for NaN and signed zero.
            Value up = s.up.step(Value(u < 0.0 ? 0.0 : u), length, bar, 1.0 / length);
            Value down = s.down.step(Value(d < 0.0 ? 0.0 : d), length, bar, 1.0 / length);
            if (!up.missing && !down.missing && down.number != 0.0) {
                double denominator = 1.0 + up.number / down.number;
                if (denominator != 0.0) result = Value(100.0 - 100.0 / denominator);
            }
        }
        s.previous = source;
    }
    if (PyErr_Occurred()) return nullptr;
    return object(result);
}

static PyObject *extrema_step(Indicator *self, PyObject *const *args, Py_ssize_t nargs) {
    if ((self->state->kind != 5 && self->state->kind != 6) || nargs != 6) {
        PyErr_SetString(PyExc_TypeError, "extrema_step expects source, length, bar index, bars, tuple, check_eq");
        return nullptr;
    }
    bool missing = args[0] == na_value;
    if ((!PyFloat_CheckExact(args[0]) && !missing) || !PyLong_CheckExact(args[1]) ||
        !PyLong_CheckExact(args[2]) || !PyBool_Check(args[3]) ||
        !PyBool_Check(args[4]) || !PyBool_Check(args[5])) return Py_NewRef(Py_NotImplemented);
    int overflow = 0;
    long long length = PyLong_AsLongLongAndOverflow(args[1], &overflow);
    Extrema &s = self->state->extrema;
    if (overflow || length <= 0 || length > static_cast<long long>(s.buffer.size()))
        return Py_NewRef(Py_NotImplemented);
    long long bar = PyLong_AsLongLongAndOverflow(args[2], &overflow);
    if (overflow) return Py_NewRef(Py_NotImplemented);
    bool highest = self->state->kind == 5;
    s.step(missing ? Value() : Value(PyFloat_AS_DOUBLE(args[0])), length, bar,
           highest, args[5] == Py_True);
    if (bar < length - 1) {
        if (args[4] == Py_True)
            return PyTuple_Pack(2, na_value, highest ? na_value : na_int_value);
        return Py_NewRef(na_value);
    }
    if (args[3] == Py_True) return PyLong_FromLongLong(1 - s.index);
    if (args[4] == Py_True) return Py_BuildValue("(NL)", object(s.last), 1 - s.index);
    return object(s.last);
}

static PyObject *valuewhen_step(Indicator *self, PyObject *const *args, Py_ssize_t nargs) {
    if (self->state->kind != 7 || nargs != 3) {
        PyErr_SetString(PyExc_TypeError, "valuewhen_step expects condition, source, occurrence");
        return nullptr;
    }
    if (!PyLong_CheckExact(args[2])) return Py_NewRef(Py_NotImplemented);
    int overflow = 0;
    long long occurrence = PyLong_AsLongLongAndOverflow(args[2], &overflow);
    if (overflow || occurrence < 0 || occurrence >= 1000000) return Py_NewRef(Py_NotImplemented);
    // NA skips both deque initialization and condition evaluation in Python.
    if (Py_TYPE(args[1]) == reinterpret_cast<PyTypeObject *>(na_type)) return Py_NewRef(na_value);
    if (!PyBool_Check(args[0]) || (!PyFloat_CheckExact(args[1]) && !PyLong_CheckExact(args[1]) &&
                                 !PyBool_Check(args[1]))) return Py_NewRef(Py_NotImplemented);
    try {
        return self->state->valuewhen.step(args[0] == Py_True, args[1], occurrence);
    } catch (const std::bad_alloc &) {
        return PyErr_NoMemory();
    }
}

static PyObject *statistics_step(Indicator *self, PyObject *const *args, Py_ssize_t nargs) {
    State &s = *self->state;
    if (s.kind < 8 || nargs != 4) {
        PyErr_SetString(PyExc_TypeError, "statistics_step expects source, length, bar index, option");
        return nullptr;
    }
    if (!PyLong_CheckExact(args[1])) return Py_NewRef(Py_NotImplemented);
    int overflow = 0;
    long long length = PyLong_AsLongLongAndOverflow(args[1], &overflow);
    if (overflow || length <= 0 || length >= (1LL << 53)) return Py_NewRef(Py_NotImplemented);
    // sum(length=1) returns the original object without evaluating its type.
    if (s.kind == 8 && length == 1) return Py_NewRef(args[0]);
    bool missing = args[0] == na_value;
    if ((!PyFloat_CheckExact(args[0]) && !missing) || !PyLong_CheckExact(args[2]))
        return Py_NewRef(Py_NotImplemented);
    long long bar = PyLong_AsLongLongAndOverflow(args[2], &overflow);
    if (overflow) return Py_NewRef(Py_NotImplemented);
    Value source = missing ? Value() : Value(PyFloat_AS_DOUBLE(args[0]));
    if (s.kind == 8) return object(s.sum.step(source, length, bar));
    bool biased = true;
    double mult = 0.0;
    if (s.kind == 11) {
        if (PyFloat_CheckExact(args[3])) mult = PyFloat_AS_DOUBLE(args[3]);
        else if (PyLong_CheckExact(args[3])) {
            long long value = PyLong_AsLongLongAndOverflow(args[3], &overflow);
            if (overflow || value <= 0 || value >= (1LL << 53)) return Py_NewRef(Py_NotImplemented);
            mult = static_cast<double>(value);
        } else return Py_NewRef(Py_NotImplemented);
        if (!(mult > 0.0)) return Py_NewRef(Py_NotImplemented);
    } else {
        if (!PyBool_Check(args[3])) return Py_NewRef(Py_NotImplemented);
        biased = args[3] == Py_True;
    }
    if (!s.variance.supported(source, length, bar)) return Py_NewRef(Py_NotImplemented);
    Value result = s.variance.step(source, length, bar, biased);
    if (s.kind == 9) return object(result);
    if (!result.missing) {
        if (result.number < 0.0 && result.number > -1e-9) result.number = 0.0;
        if (result.number < 0.0) {
            // Preserve the interpreter version's exception text after updating variance state.
            PyObject *input = object(result);
            if (!input) return nullptr;
            PyObject *error = PyObject_CallOneArg(sqrt_method, input);
            Py_DECREF(input);
            return error;
        }
        result.number = std::sqrt(result.number);
    }
    if (s.kind == 10) return object(result);
    Value middle = s.sum.sma(source, length, bar);
    if (PyErr_Occurred()) return nullptr;
    if (middle.missing) return PyTuple_Pack(3, na_value, na_value, na_value);
    if (result.missing) return Py_BuildValue("(NOO)", object(middle), na_value, na_value);
    double deviation = result.number * mult;
    return Py_BuildValue("(ddd)", middle.number, middle.number + deviation, middle.number - deviation);
}

static PyObject *variance_export(const Variance &v) {
    PyObject *buffer = PyList_New(v.buffer.size());
    if (!buffer) return nullptr;
    for (size_t i = 0; i < v.buffer.size(); ++i) {
        PyObject *value = i < static_cast<size_t>(v.size) ? object(v.buffer[i]) : Py_NewRef(Py_None);
        if (!value) { Py_DECREF(buffer); return nullptr; }
        PyList_SET_ITEM(buffer, i, value);
    }
    return Py_BuildValue("{s:N,s:n,s:n,s:L,s:L,s:d,s:d,s:d,s:d}",
        "buffer", buffer, "size", v.size, "write", v.write, "bar", v.last_bar,
        "count", v.count, "sum_val", v.sum_val, "sum_val_c", v.sum_val_c,
        "sum_sq", v.sum_sq, "sum_sq_c", v.sum_sq_c);
}

static PyObject *sum_export(const Sum &s) {
    PyObject *buffer = PyList_New(s.buffer.size());
    if (!buffer) return nullptr;
    for (size_t i = 0; i < s.buffer.size(); ++i) {
        PyObject *value = i < static_cast<size_t>(s.size) ? PyFloat_FromDouble(s.buffer[i]) : Py_NewRef(Py_None);
        if (!value) { Py_DECREF(buffer); return nullptr; }
        PyList_SET_ITEM(buffer, i, value);
    }
    return Py_BuildValue("{s:N,s:n,s:n,s:L,s:L,s:N,s:N}",
        "buffer", buffer, "size", s.size, "write", s.write, "bar", s.last_bar,
        "count", s.count, "total", object(s.total), "compensation", object(s.compensation));
}

static PyObject *smooth_export(const Smooth &s) {
    return Py_BuildValue("{s:N,s:d,s:O,s:N}", "sum", sum_export(s.sum), "alpha", s.alpha,
        "initialized", s.initialized ? Py_True : Py_False, "last", object(s.last));
}

static PyObject *indicator_export(Indicator *self, PyObject *) {
    State &s = *self->state;
    if (s.kind >= 8) {
        return Py_BuildValue("{s:i,s:N,s:N}", "kind", s.kind,
            "sum", sum_export(s.sum), "variance", variance_export(s.variance));
    }
    if (s.kind == 7) {
        const ValueWhen &v = s.valuewhen;
        PyObject *values = PyList_New(v.size);
        if (!values) return nullptr;
        Py_ssize_t start = v.size < static_cast<Py_ssize_t>(v.buffer.size()) ? 0 : v.write;
        for (Py_ssize_t i = 0; i < v.size; ++i)
            PyList_SET_ITEM(values, i, Py_NewRef(v.buffer[(start + i) % v.buffer.size()]));
        return Py_BuildValue("{s:i,s:O,s:n,s:N}", "kind", s.kind,
            "initialized", v.buffer.empty() ? Py_False : Py_True,
            "maxlen", static_cast<Py_ssize_t>(v.buffer.size()), "values", values);
    }
    if (s.kind >= 5) {
        Extrema &e = s.extrema;
        PyObject *buffer = PyList_New(e.buffer.size());
        if (!buffer) return nullptr;
        for (size_t i = 0; i < e.buffer.size(); ++i) {
            PyObject *value = i < static_cast<size_t>(e.size) ? object(e.buffer[i]) : Py_NewRef(Py_None);
            if (!value) { Py_DECREF(buffer); return nullptr; }
            PyList_SET_ITEM(buffer, i, value);
        }
        return Py_BuildValue("{s:i,s:N,s:n,s:n,s:L,s:L,s:N}",
            "kind", s.kind, "buffer", buffer, "size", e.size, "write", e.write,
            "bar", e.last_bar, "index", e.index, "last", object(e.last));
    }
    if (s.kind == 4) {
        Wma &w = s.wma;
        PyObject *buffer = PyList_New(w.buffer.size());
        if (!buffer) return nullptr;
        for (size_t i = 0; i < w.buffer.size(); ++i) {
            PyObject *value = i < static_cast<size_t>(w.size) ? object(w.buffer[i]) : Py_NewRef(Py_None);
            if (!value) { Py_DECREF(buffer); return nullptr; }
            PyList_SET_ITEM(buffer, i, value);
        }
        return Py_BuildValue("{s:i,s:N,s:n,s:n,s:L,s:L,s:N,s:O,s:d,s:d,s:d,s:d,s:d}",
            "kind", s.kind, "buffer", buffer, "size", w.size, "write", w.write, "bar", w.last_bar,
            "count", w.count, "last", object(w.last), "initialized", w.initialized ? Py_True : Py_False,
            "denom", w.denom, "summ", w.summ, "sum_low", w.sum_low,
            "weighted_summ", w.weighted_summ, "weighted_low", w.weighted_low);
    }
    return Py_BuildValue("{s:i,s:N,s:N,s:N,s:N}", "kind", s.kind, "sum", sum_export(s.sum),
        "up", smooth_export(s.up), "down", smooth_export(s.down), "previous", object(s.previous));
}

static PyMethodDef methods[] = {
    {"step", reinterpret_cast<PyCFunction>(indicator_step), METH_FASTCALL, nullptr},
    {"extrema_step", reinterpret_cast<PyCFunction>(extrema_step), METH_FASTCALL, nullptr},
    {"valuewhen_step", reinterpret_cast<PyCFunction>(valuewhen_step), METH_FASTCALL, nullptr},
    {"statistics_step", reinterpret_cast<PyCFunction>(statistics_step), METH_FASTCALL, nullptr},
    {"export", reinterpret_cast<PyCFunction>(indicator_export), METH_NOARGS, nullptr},
    {"__copy__", reinterpret_cast<PyCFunction>(indicator_copy), METH_NOARGS, nullptr},
    {"__deepcopy__", reinterpret_cast<PyCFunction>(indicator_copy), METH_O, nullptr},
    {nullptr, nullptr, 0, nullptr}
};

static int scalar_is_na(PyObject *value) {
    if (PyFloat_CheckExact(value) || PyLong_CheckExact(value) || PyBool_Check(value)) return 0;
    // Keep isinstance semantics for NA subclasses and objects exposing __class__.
    return PyObject_IsInstance(value, na_type);
}

static PyObject *scalar_minmax(PyObject *const *args, Py_ssize_t nargs, bool highest) {
    if (nargs == 0) {
        PyErr_SetString(PyExc_AssertionError, "At least one number is necessary!");
        return nullptr;
    }
    // The original checks every argument for NA before it starts comparing.
    for (Py_ssize_t i = 0; i < nargs; ++i) {
        int missing = scalar_is_na(args[i]);
        if (missing < 0) return nullptr;
        if (missing) return Py_NewRef(na_value);
    }
    PyObject *best = args[0];
    for (Py_ssize_t i = 1; i < nargs; ++i) {
        int better;
        if (PyFloat_CheckExact(best) && PyFloat_CheckExact(args[i])) {
            better = highest ? PyFloat_AS_DOUBLE(args[i]) > PyFloat_AS_DOUBLE(best) :
                               PyFloat_AS_DOUBLE(args[i]) < PyFloat_AS_DOUBLE(best);
        } else {
            // Do not narrow integers to double or bypass custom comparison methods.
            better = PyObject_RichCompareBool(args[i], best, highest ? Py_GT : Py_LT);
            if (better < 0) return nullptr;
        }
        if (better) best = args[i];
    }
    return Py_NewRef(best);
}

static PyObject *scalar_min(PyObject *, PyObject *const *args, Py_ssize_t nargs) {
    return scalar_minmax(args, nargs, false);
}

static PyObject *scalar_max(PyObject *, PyObject *const *args, Py_ssize_t nargs) {
    return scalar_minmax(args, nargs, true);
}

static PyObject *scalar_round(PyObject *, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs < 1 || nargs > 2) {
        PyErr_SetString(PyExc_TypeError, "round_number expects number and optional precision");
        return nullptr;
    }
    int missing = scalar_is_na(args[0]);
    if (missing < 0) return nullptr;
    if (missing) return Py_NewRef(na_value);
    if (nargs == 2) {
        missing = scalar_is_na(args[1]);
        if (missing < 0) return nullptr;
        if (missing) nargs = 1;
    }
    return PyObject_Vectorcall(builtin_round, args, static_cast<size_t>(nargs), nullptr);
}

static PyMethodDef scalar_methods[] = {
    {"minimum", reinterpret_cast<PyCFunction>(scalar_min), METH_FASTCALL, nullptr},
    {"maximum", reinterpret_cast<PyCFunction>(scalar_max), METH_FASTCALL, nullptr},
    {"round_number", reinterpret_cast<PyCFunction>(scalar_round), METH_FASTCALL, nullptr},
    {nullptr, nullptr, 0, nullptr}
};

static PyModuleDef module = {PyModuleDef_HEAD_INIT, "_pynereal_native_ta", nullptr, -1, scalar_methods};

PyMODINIT_FUNC PyInit__pynereal_native_ta() {
    PyObject *na_module = PyImport_ImportModule("pynecore.types.na");
    if (!na_module) return nullptr;
    na_value = PyObject_GetAttrString(na_module, "na_float");
    na_int_value = PyObject_GetAttrString(na_module, "na_int");
    na_type = PyObject_GetAttrString(na_module, "NA");
    Py_DECREF(na_module);
    PyObject *math_module = PyImport_ImportModule("math");
    if (!math_module) return nullptr;
    sqrt_method = PyObject_GetAttrString(math_module, "sqrt");
    Py_DECREF(math_module);
    PyObject *builtins = PyImport_ImportModule("builtins");
    if (!builtins) return nullptr;
    builtin_round = PyObject_GetAttrString(builtins, "round");
    Py_DECREF(builtins);
    if (!na_value || !na_int_value || !na_type || !sqrt_method || !builtin_round)
        return nullptr;
    IndicatorType.tp_name = "_pynereal_native_ta.State";
    IndicatorType.tp_basicsize = sizeof(Indicator);
    IndicatorType.tp_flags = Py_TPFLAGS_DEFAULT;
    IndicatorType.tp_new = indicator_new;
    IndicatorType.tp_dealloc = reinterpret_cast<destructor>(indicator_free);
    IndicatorType.tp_methods = methods;
    if (PyType_Ready(&IndicatorType) < 0) return nullptr;
    PyObject *result = PyModule_Create(&module);
    if (!result) return nullptr;
    if (PyModule_AddObjectRef(result, "State", reinterpret_cast<PyObject *>(&IndicatorType)) < 0) {
        Py_DECREF(result);
        return nullptr;
    }
    if (PyModule_AddIntConstant(result, "WMA_SUPPORTED", 1) < 0) {
        Py_DECREF(result);
        return nullptr;
    }
    if (PyModule_AddIntConstant(result, "EXTREMA_SUPPORTED", 1) < 0) {
        Py_DECREF(result);
        return nullptr;
    }
    if (PyModule_AddIntConstant(result, "VALUEWHEN_SUPPORTED", 1) < 0) {
        Py_DECREF(result);
        return nullptr;
    }
    if (PyModule_AddIntConstant(result, "STATISTICS_SUPPORTED", 1) < 0) {
        Py_DECREF(result);
        return nullptr;
    }
    if (PyModule_AddIntConstant(result, "SCALAR_MATH_SUPPORTED", 1) < 0) {
        Py_DECREF(result);
        return nullptr;
    }
    if (PyModule_AddIntConstant(result, "ACCURATE_SUM_SUPPORTED", 1) < 0) {
        Py_DECREF(result);
        return nullptr;
    }
    return result;
}
