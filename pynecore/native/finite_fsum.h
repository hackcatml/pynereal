// Finite, bounded adaptation of CPython 3.14 Modules/mathmodule.c:math_fsum.
// Copyright (c) 2001 Python Software Foundation; All Rights Reserved.
// PSF-2.0: see LICENSE.cpython. Nonfinite inputs/overflow use Python fallback.
// https://github.com/python/cpython/blob/v3.14.0/Modules/mathmodule.c
#pragma once
#include <cmath>
#include <cstddef>
#include <utility>

static bool finite_fsum(const double *values, size_t count, double &result) {
    double partials[7];
    if (count > 7) return false;
    size_t n = 0;
    for (size_t k = 0; k < count; ++k) {
        double x = values[k];
        if (!std::isfinite(x)) return false;
        size_t i = 0;
        for (size_t j = 0; j < n; ++j) {
            double y = partials[j];
            if (std::fabs(x) < std::fabs(y)) std::swap(x, y);
            double hi = x + y;
            if (!std::isfinite(hi)) return false;
            double yr = hi - x;
            double lo = y - yr;
            if (lo != 0.0) partials[i++] = lo;
            x = hi;
        }
        n = i;
        if (x != 0.0) partials[n++] = x;
    }
    double hi = 0.0, lo = 0.0;
    if (n) {
        hi = partials[--n];
        while (n) {
            double x = hi;
            double y = partials[--n];
            hi = x + y;
            double yr = hi - x;
            lo = y - yr;
            if (lo != 0.0) break;
        }
        if (n && ((lo < 0.0 && partials[n-1] < 0.0) ||
                  (lo > 0.0 && partials[n-1] > 0.0))) {
            double y = lo * 2.0;
            double x = hi + y;
            double yr = x - hi;
            if (y == yr) hi = x;
        }
    }
    result = hi;
    return std::isfinite(hi);
}
