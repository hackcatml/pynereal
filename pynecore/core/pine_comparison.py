"""Pine comparisons use an inclusive, absolute 1e-10 tolerance, not rounding.

Only user comparison operators use these helpers; stored values and TA
internals do not. Integer representation is intentionally unchanged.
"""

from pynecore.types.na import NA

EPSILON = 1e-10


# Mixed comparisons outside the exact-double integer range stay native: their
# minimum nonzero distance exceeds EPSILON and subtraction could lose that gap.
# Keep the guards inline on the hot path instead of calling another helper.
def lt(left, right):
    lt, rt = type(left), type(right)
    if (lt is float and rt is float
            or lt is float and rt is int and -2**53 < right < 2**53
            or lt is int and rt is float and -2**53 < left < 2**53):
        return -EPSILON > left - right
    return left < right


def le(left, right):
    lt, rt = type(left), type(right)
    if (lt is float and rt is float
            or lt is float and rt is int and -2**53 < right < 2**53
            or lt is int and rt is float and -2**53 < left < 2**53):
        return left <= right or EPSILON >= left - right
    return left <= right


def gt(left, right):
    lt, rt = type(left), type(right)
    if (lt is float and rt is float
            or lt is float and rt is int and -2**53 < right < 2**53
            or lt is int and rt is float and -2**53 < left < 2**53):
        return EPSILON < left - right
    return left > right


def ge(left, right):
    lt, rt = type(left), type(right)
    if (lt is float and rt is float
            or lt is float and rt is int and -2**53 < right < 2**53
            or lt is int and rt is float and -2**53 < left < 2**53):
        return left >= right or -EPSILON <= left - right
    return left >= right


def eq(left, right):
    lt, rt = type(left), type(right)
    if (lt is float and rt is float
            or lt is float and rt is int and -2**53 < right < 2**53
            or lt is int and rt is float and -2**53 < left < 2**53):
        return left == right or -EPSILON <= left - right <= EPSILON
    return left == right


def ne(left, right):
    lt, rt = type(left), type(right)
    if (lt is float and rt is float
            or lt is float and rt is int and -2**53 < right < 2**53
            or lt is int and rt is float and -2**53 < left < 2**53):
        difference = left - right
        return EPSILON < difference or -EPSILON > difference
    if (lt is float and rt is int and left != left
            or lt is int and rt is float and right != right):
        return False
    # Keep NA behavior scoped to script comparisons, not the engine's NA type.
    if isinstance(left, NA) or isinstance(right, NA):
        return False
    return left != right


class comparison_value:
    """Pairwise chain comparisons, preserving Python's single evaluation order.

    No walrus temporaries: chains remain valid in comprehension iterables and
    class bodies, without capturing or overwriting any user variable.
    """

    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return lt(self.value, other.value)

    def __le__(self, other):
        return le(self.value, other.value)

    def __gt__(self, other):
        return gt(self.value, other.value)

    def __ge__(self, other):
        return ge(self.value, other.value)

    def __eq__(self, other):
        return eq(self.value, other.value)

    def __ne__(self, other):
        return ne(self.value, other.value)
