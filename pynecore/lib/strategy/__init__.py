from typing import cast, TYPE_CHECKING

from datetime import datetime, UTC
from collections import deque, defaultdict
from copy import copy
from bisect import insort, bisect_left
from math import ceil, floor

from ...core.module_property import module_property
from ... import lib
from .. import syminfo, alert

from ...types.strategy import QtyType
from ...types.base import IntEnum
from ...types.na import NA, na_float, na_str

from . import direction as direction
from . import commission as _commission
from . import oca as _oca

from . import closedtrades, opentrades

__all__ = [
    "fixed", "cash", "percent_of_equity",
    "long", "short", 'direction',

    'Trade', 'Order', 'Position',
    "cancel", "cancel_all", "close", "close_all", "entry", "exit", "order",

    "closedtrades", "opentrades",
]

#
# Callable modules
#

if TYPE_CHECKING:
    from closedtrades import closedtrades
    from opentrades import opentrades


#
# Types
#

class _OrderType(IntEnum):
    """ Order type """


#
# Constants
#

fixed = QtyType("fixed")
cash = QtyType("cash")
percent_of_equity = QtyType("percent_of_equity")

long = direction.long
short = direction.short

# Possible order types
_order_type_normal = _OrderType()
_order_type_entry = _OrderType()
_order_type_close = _OrderType()

#
# Imports after constants
#

if True:
    # We need to import this here to avoid circular imports
    from . import risk


#
# Classes
#

class _ExitBinding:
    """Logical entry quantity, independent of FIFO trade-row attribution."""

    __slots__ = ('entry_id', 'sign', 'initial_size', 'size', 'price', 'trade', 'orders')

    def __init__(self, entry_id, sign, size, trade=None):
        self.entry_id, self.sign = entry_id, sign
        self.initial_size = self.size = size
        self.trade = trade
        self.price = trade.entry_price if trade is not None else None
        self.orders = {}


def _order_key(order):
    if order.order_type == _order_type_close and order.exit_binding is not None:
        return order.exit_id, order.order_id, order.exit_binding
    return order.order_id


def _market_key(order):
    return ('close', _order_key(order)) if order.order_type == _order_type_close else order.order_id


class Order:
    """
    Represents an order
    """

    __slots__ = (
        "order_id", "size", "sign", "order_type", "limit", "stop", "exit_id", "oca_name", "oca_type",
        "comment", "alert_message", "ai_instruction",
        "trail_price", "trail_offset",
        "trail_triggered", "trail_stop",
        "profit_ticks", "loss_ticks", "trail_points_ticks",  # Store tick values for later calculation
        "is_market_order",  # Flag to check if this is a market order
        "cancelled",  # Flag to mark order as cancelled by OCA
        "bar_index",  # Bar index when the order was placed
        "qty_budget", "qty_price", "flip_qty", "follow_entry_qty",
        "exit_binding", "reserved_size", "consumed", "exit_qty", "exit_percent",
    )

    def __init__(
            self,
            order_id: str | None,
            size: float,
            *,
            order_type: _OrderType = _order_type_normal,
            exit_id: str | None = None,
            limit: float | None = None,
            stop: float | None = None,
            oca_name: str | None = None,
            oca_type: _oca.Oca = _oca.none,
            comment: str | None = None,
            alert_message: str | None = None,
            ai_instruction: str | None = None,
            trail_price: float | None = None,
            trail_offset: float | None = None,
            profit_ticks: float | None = None,
            loss_ticks: float | None = None,
            trail_points_ticks: float | None = None
    ):
        self.order_id = order_id
        self.size = size
        self.sign = 0.0 if size == 0.0 else 1.0 if size > 0.0 else -1.0
        self.limit = limit
        self.stop = stop
        self.order_type = order_type

        self.exit_id = exit_id

        self.oca_name = oca_name
        self.oca_type = oca_type if oca_type is not None else _oca.none

        self.comment = comment
        self.alert_message = alert_message
        self.ai_instruction = ai_instruction

        self.trail_price = trail_price
        self.trail_offset = None if isinstance(trail_offset, NA) else trail_offset  # in ticks
        self.trail_triggered = False
        self.trail_stop = None

        self.profit_ticks = profit_ticks
        self.loss_ticks = loss_ticks
        self.trail_points_ticks = trail_points_ticks

        # Check if this is a market order (no limit, stop, or trail price)
        self.is_market_order = self.limit is None and self.stop is None

        self.cancelled = False
        self.bar_index = -1  # Will be set when order is added to position
        self.qty_budget = None
        self.qty_price = None
        self.flip_qty = 0.0
        self.follow_entry_qty = False
        self.exit_binding = None
        self.reserved_size = 0.0
        self.consumed = False
        self.exit_qty = self.exit_percent = None

    def __repr__(self):
        return f"Order(order_id={self.order_id}; exit_id={self.exit_id}; size={self.size}; type: {self.order_type}; " \
               f"limit={self.limit}; stop={self.stop}; " \
               f"trail_price={self.trail_price}; trail_offset={self.trail_offset}; " \
               f"oca_name={self.oca_name}; comment={self.comment}; bar_index={self.bar_index})"


class Trade:
    """
    Represents a trade
    """

    __slots__ = (
        "size", "sign", "entry_id", "entry_bar_index", "entry_time", "entry_price", "entry_comment", "entry_equity",
        "exit_id", "exit_bar_index", "exit_time", "exit_price", "exit_comment", "exit_equity",
        "commission", "max_drawdown", "max_drawdown_percent", "max_runup", "max_runup_percent",
        "profit", "profit_percent", "cum_profit", "cum_profit_percent",
        "cum_max_drawdown", "cum_max_runup"
    )

    # noinspection PyShadowingNames
    def __init__(self, *, size: float, entry_id: str, entry_bar_index: int, entry_time: int, entry_price: float,
                 commission: float, entry_comment: str, entry_equity: float):
        self.size: float = size
        self.sign = 0.0 if size == 0.0 else 1.0 if size > 0.0 else -1.0

        self.entry_id: str = entry_id
        self.entry_bar_index: int = entry_bar_index
        self.entry_time: int = entry_time
        self.entry_price: float = entry_price
        self.entry_equity: float = entry_equity
        self.entry_comment: str = entry_comment

        self.exit_id: str = ""
        self.exit_bar_index: int = -1
        self.exit_time: int = -1
        self.exit_price: float = 0.0
        self.exit_comment: str = ''
        self.exit_equity: float | NA = na_float

        self.commission = commission

        self.max_drawdown: float | NA[float] = 0.0
        self.max_drawdown_percent: float | NA[float] = 0.0
        self.max_runup: float | NA[float] = 0.0
        self.max_runup_percent: float | NA[float] = 0.0
        self.profit: float | NA[float] = 0.0
        self.profit_percent: float | NA[float] = 0.0

        self.cum_profit: float | NA[float] = 0.0
        self.cum_profit_percent: float | NA[float] = 0.0
        self.cum_max_drawdown: float | NA[float] = 0.0
        self.cum_max_runup: float | NA[float] = 0.0

    def __repr__(self):
        return f"Trade(entry_id={self.entry_id}; size={self.size}; entry_bar_index: {self.entry_bar_index}; " \
               f"entry_price={self.entry_price}; exit_price={self.exit_price}; commission={self.commission}; " \
               f"entry_equity={self.entry_equity}; exit_equity={self.exit_equity}"

    #
    # Support csv.DictWriter
    #

    def keys(self):
        return self.__dict__.keys()

    def get(self, key: str, default=None):
        v = getattr(self, key, default)
        if key in ('entry_time', 'exit_time'):
            v = datetime.fromtimestamp(v / 1000.0, tz=UTC)
        elif isinstance(v, float):
            v = round(v, 10)
        return v


# noinspection PyShadowingNames
class PriceOrderBook:
    """
    Price-based sorted order storage.
    An order can appear multiple times at different prices.
    """

    __slots__ = ('price_levels', 'orders_at_price', 'order_prices')

    def __init__(self):
        self.price_levels = []  # Sorted list of prices
        self.orders_at_price = defaultdict(list)  # price -> [Order]
        self.order_prices = defaultdict(set)  # Order -> {prices}

    def add_order(self, order: Order):
        """Add order to all its relevant price levels"""
        # Add to stop price if exists
        if order.stop is not None:
            price = order.stop
            if price not in self.orders_at_price:
                insort(self.price_levels, price)
            self.orders_at_price[price].append(order)
            self.order_prices[order].add(price)

        # Add to limit price if exists
        if order.limit is not None:
            price = order.limit
            if price not in self.orders_at_price:
                insort(self.price_levels, price)
            self.orders_at_price[price].append(order)
            self.order_prices[order].add(price)

        # Add to trail price if exists
        if order.trail_price is not None and order.trail_price not in self.order_prices[order]:
            price = order.trail_price
            if price not in self.orders_at_price:
                insort(self.price_levels, price)
            self.orders_at_price[price].append(order)
            self.order_prices[order].add(price)

    def remove_order(self, order: Order):
        """Remove order from all price levels"""
        for price in list(self.order_prices[order]):
            self.orders_at_price[price].remove(order)
            if not self.orders_at_price[price]:
                idx = bisect_left(self.price_levels, price)
                if idx < len(self.price_levels) and self.price_levels[idx] == price:
                    del self.price_levels[idx]
                del self.orders_at_price[price]
        del self.order_prices[order]

    def update_order_stop(self, order: Order, new_stop: float):
        """Update the stop price of an order in the order book"""
        # Remove the order from the old stop price level if it exists
        if order.stop is not None and order.stop in self.order_prices[order]:
            old_stop = order.stop
            self.orders_at_price[old_stop].remove(order)
            self.order_prices[order].remove(old_stop)
            if not self.orders_at_price[old_stop]:
                idx = bisect_left(self.price_levels, old_stop)
                if idx < len(self.price_levels) and self.price_levels[idx] == old_stop:
                    del self.price_levels[idx]
                del self.orders_at_price[old_stop]

        # Update the order's stop price
        order.stop = new_stop

        # Add the order to the new stop price level
        if new_stop not in self.orders_at_price:
            insort(self.price_levels, new_stop)
        self.orders_at_price[new_stop].append(order)
        self.order_prices[order].add(new_stop)

    def iter_orders(self, *, desc=False, min_price: float | None = None, max_price: float | None = None):
        """
        Iterate over orders within price range.

        Examples:
            iter_orders()  # All orders, ascending
            iter_orders(desc=True)  # All orders, descending
            iter_orders(min_price=50.0)  # 50, 51, 52, ... (ascending)
            iter_orders(max_price=60.0)  # 60, 59, 58, ... (descending)
            iter_orders(min_price=50.0, max_price=60.0)  # 50, 51, ..., 60 (ascending)
            iter_orders(min_price=50.0, max_price=60.0, desc=True)  # 60, 59, ..., 50

        :param desc: Descending order when both bounds or neither bound are set
        :param min_price: If set, iterate from this price upward (ascending)
        :param max_price: If set, iterate from this price downward (descending)
        :return: Generator yielding Order objects
        """
        if min_price is not None and max_price is not None:
            min_idx = bisect_left(self.price_levels, min_price)
            max_idx = bisect_left(self.price_levels, max_price)
            # Include max_price if it matches exactly
            if max_idx < len(self.price_levels) and self.price_levels[max_idx] == max_price:
                max_idx += 1
            # Create a copy of price levels to avoid iteration issues when levels are removed
            levels = self.price_levels[min_idx:max_idx]
            if desc:
                levels.reverse()
            for p in levels:
                # Create a copy to avoid iteration issues when orders are removed during iteration
                yield from list(self.orders_at_price.get(p, ()))

        elif min_price is not None:
            # Ascending from min_price
            min_idx = bisect_left(self.price_levels, min_price)
            # Create a copy of price levels to avoid iteration issues when levels are removed
            for p in list(self.price_levels[min_idx:]):
                # Create a copy to avoid iteration issues when orders are removed during iteration
                yield from list(self.orders_at_price[p])

        elif max_price is not None:
            # Descending from max_price
            max_idx = bisect_left(self.price_levels, max_price)
            # Include max_price if it matches exactly
            if max_idx < len(self.price_levels) and self.price_levels[max_idx] == max_price:
                max_idx += 1
            # Iterate in reverse order (high to low prices)
            # Create a copy of price levels to avoid iteration issues when levels are removed
            # Note: reversed() already creates an iterator over a copy of the slice
            for p in reversed(list(self.price_levels[:max_idx])):
                # Create a copy to avoid iteration issues when orders are removed during iteration
                yield from list(self.orders_at_price[p])

        elif desc:
            # All orders, descending
            # Create a copy of price levels to avoid iteration issues when levels are removed
            for p in reversed(list(self.price_levels)):
                # Create a copy to avoid iteration issues when orders are removed during iteration
                yield from list(self.orders_at_price[p])
        else:
            # All orders, ascending
            # Create a copy of price levels to avoid iteration issues when levels are removed
            for p in list(self.price_levels):
                # Create a copy to avoid iteration issues when orders are removed during iteration
                yield from list(self.orders_at_price[p])

    def clear(self):
        """Clear all orders"""
        self.price_levels.clear()
        self.orders_at_price.clear()
        self.order_prices.clear()


# noinspection PyProtectedMember,PyShadowingNames
class Position:
    """
    This holds data about positions and trades

    This is the main class for strategies
    """

    __slots__ = (
        'h', 'l', 'c', 'o',
        'netprofit', 'openprofit', 'grossprofit', 'grossloss',
        'entry_orders', 'exit_orders', 'market_orders', 'orderbook',
        'open_trades', 'closed_trades', 'new_closed_trades',
        'closed_trades_count', 'wintrades', 'eventrades', 'losstrades',
        'size', 'sign', 'avg_price', 'aggregate_avg_price', 'aggregate_openprofit',
        'position_open_time', 'position_open_bar_index', 'cum_profit',
        'min_equity', 'max_realized_equity',
        'drawdown_summ', 'runup_summ', 'current_drawdown', 'current_drawdown_percent',
        'max_drawdown', 'max_drawdown_percent',
        'max_runup', 'max_runup_percent',
        'entry_summ', 'open_commission',
        'risk_allowed_direction', 'risk_max_cons_loss_days', 'risk_max_cons_loss_days_alert',
        'risk_max_drawdown_value', 'risk_max_drawdown_type', 'risk_max_drawdown_alert',
        'risk_max_intraday_filled_orders', 'risk_max_intraday_filled_orders_alert',
        'risk_max_intraday_loss_value', 'risk_max_intraday_loss_type', 'risk_max_intraday_loss_alert',
        'risk_max_position_size',
        'risk_cons_loss_days', 'risk_last_day_index', 'risk_last_day_equity',
        'risk_intraday_filled_orders', 'risk_intraday_start_equity', 'risk_halt_trading',
        'on_entry_callback', 'on_close_callback', 'on_alert_callback', 'on_ai_callback',
        'on_order_signal_callback',
        '_entry_open_ledger', '_close_tick_orders', '_entry_fill_phases', '_fill_phase', '_exit_entries'
    )

    def __init__(self):
        # OHLC values
        self.h: float = 0.0
        self.l: float = 0.0
        self.c: float = 0.0
        self.o: float = 0.0

        # Profit/loss tracking
        self.netprofit: float | NA[float] = 0.0
        self.openprofit: float | NA[float] = 0.0
        self.grossprofit: float | NA[float] = 0.0
        self.grossloss: float | NA[float] = 0.0

        # Order books
        self.market_orders = {}  # Market orders from strategy.market()
        self.entry_orders = {}  # Entry orders from strategy.entry()
        self.exit_orders = {}  # Exit orders from strategy.exit(), strategy.close(), etc.
        self.orderbook = PriceOrderBook()
        self._close_tick_orders: list[Order] = []

        # Trades
        self.open_trades: list[Trade] = []
        self.closed_trades: deque[Trade] = deque(maxlen=9000)  # 9000 is the limit of TV
        self.new_closed_trades: list[Trade] = []
        # Exit orders remain bound to their entry IDs even though the default
        # close_entries_rule attributes closed trade rows FIFO.
        self._entry_open_ledger: dict[str, float] = {}
        self._exit_entries: dict[str, list[_ExitBinding]] = {}
        self._entry_fill_phases: dict[Trade, int] = {}
        self._fill_phase = 3

        # Trade statistics
        self.closed_trades_count: int = 0
        self.wintrades: int = 0
        self.eventrades: int = 0
        self.losstrades: int = 0
        self.size: float = 0.0
        self.sign: float = 0.0
        self.avg_price: float | NA[float] = na_float
        self.aggregate_avg_price: float | NA[float] = na_float
        self.aggregate_openprofit: float | NA[float] = 0.0
        self.position_open_time: int | None = None
        self.position_open_bar_index: int | None = None
        self.cum_profit: float | NA[float] = 0.0
        self.min_equity: float = float("inf")
        self.max_realized_equity: float = -float("inf")
        self.drawdown_summ: float = 0.0
        self.runup_summ: float = 0.0
        self.current_drawdown: float = 0.0
        self.current_drawdown_percent: float = 0.0
        self.max_drawdown: float = 0.0
        self.max_drawdown_percent: float = 0.0
        self.max_runup: float = 0.0
        self.max_runup_percent: float = 0.0
        self.entry_summ: float = 0.0
        self.open_commission: float = 0.0

        # Risk management settings
        self.risk_allowed_direction: direction.Direction | None = None
        self.risk_max_cons_loss_days: int | None = None
        self.risk_max_cons_loss_days_alert: str | None = None
        self.risk_max_drawdown_value: float | None = None
        self.risk_max_drawdown_type: QtyType | None = None
        self.risk_max_drawdown_alert: str | None = None
        self.risk_max_intraday_filled_orders: int | None = None
        self.risk_max_intraday_filled_orders_alert: str | None = None
        self.risk_max_intraday_loss_value: float | None = None
        self.risk_max_intraday_loss_type: QtyType | None = None
        self.risk_max_intraday_loss_alert: str | None = None
        self.risk_max_position_size: float | None = None

        # Risk management state tracking
        self.risk_cons_loss_days: int = 0
        self.risk_last_day_index: int = -1
        self.risk_last_day_equity: float = 0.0
        self.risk_intraday_filled_orders: int = 0
        self.risk_intraday_start_equity: float = 0.0
        self.risk_halt_trading: bool = False

        # Event callbacks
        self.on_entry_callback = None
        self.on_close_callback = None
        self.on_alert_callback = None
        self.on_ai_callback = None
        self.on_order_signal_callback = None

    @property
    def equity(self) -> float | NA[float]:
        """ The current equity """
        return lib._script.initial_capital + self.netprofit + self.openprofit

    def _size_flippable_by_entry(self) -> float:
        """Position a priced entry would reverse after earlier market requests."""
        if not self.market_orders:
            return self.size
        size = self.size
        for order in self.market_orders.values():
            if order.cancelled:
                continue
            if order.order_type is _order_type_close and size * order.size < 0.0:
                remaining = _size_add(size, order.size)
                size = remaining if remaining * size > 0.0 else 0.0
            elif order.order_type is _order_type_entry:
                size = _size_add(size, order.size) if size * order.size >= 0.0 else order.size
        return size

    def _add_order(self, order: Order):
        """ Add an order to the strategy """
        # Set the bar_index when the order is placed
        order.bar_index = lib.bar_index
        if lib._script.process_orders_on_close:
            self._close_tick_orders.append(order)

        # Check if an order with this ID already exists and remove it first
        if order.order_type == _order_type_close:
            key = _order_key(order)
            existing_order = self.exit_orders.get(key)
            self.exit_orders[key] = order
        else:
            # Both entry and normal orders are stored in entry_orders dict
            existing_order = self.entry_orders.get(order.order_id)
            self.entry_orders[order.order_id] = order
            if existing_order is not None and existing_order.exit_binding is not None:
                order.exit_binding = existing_order.exit_binding
                self._resize_pending_binding(order)

        # Remove existing order from order book before adding new one
        if existing_order is not None:
            existing_order.cancelled = True
            self.orderbook.remove_order(existing_order)
            if self.market_orders.get(_market_key(existing_order)) is existing_order:
                self.market_orders.pop(_market_key(existing_order))

        if order.is_market_order:
            self.market_orders[_market_key(order)] = order

        # Add order to order book (automatically adds to all relevant prices)
        self.orderbook.add_order(order)

    def _remove_order(self, order: Order, *, filled=False):
        """ Remove an order from the strategy """
        order.cancelled = True
        if order.order_type == _order_type_close:
            key = _order_key(order)
            if self.exit_orders.get(key) is order:
                self.exit_orders.pop(key)
            binding = order.exit_binding
            if binding is not None:
                if filled:
                    order.consumed = True
                elif not order.consumed and binding.orders.get(order.exit_id) is order:
                    binding.orders.pop(order.exit_id)
        else:
            # Both entry and normal orders are stored in entry_orders dict
            if self.entry_orders.get(order.order_id) is order:
                self.entry_orders.pop(order.order_id)
            if not filled and order.exit_binding is not None and order.exit_binding.price is None:
                for leg in list(order.exit_binding.orders.values()):
                    self._remove_order(leg)
        # Remove market order from market orders dict
        if self.market_orders.get(_market_key(order)) is order:
            self.market_orders.pop(_market_key(order))
        # Remove order from order book
        self.orderbook.remove_order(order)

    def _remove_order_by_id(self, order_id: str):
        """ Remove order by id """
        for order in list(self.exit_orders.values()):
            if order.exit_id == order_id or (order.exit_binding is None and order.order_id == order_id):
                self._remove_order(order)

        # Then check in entry orders
        order = self.entry_orders.get(order_id)
        if order:
            self._remove_order(order)

    def _resize_pending_binding(self, order, *, filled_size=None):
        binding = order.exit_binding
        binding.initial_size = binding.size = (filled_size if filled_size is not None else
                                               max(0.0, _size_add(abs(order.size), -order.flip_qty)))
        binding.sign = order.sign
        available = binding.size
        for leg in binding.orders.values():
            qty = leg.exit_qty
            if qty is None:
                qty = binding.size * leg.exit_percent if leg.exit_percent is not None else available
            leg.reserved_size = _size_round(max(0.0, min(qty, available)))
            leg.size, leg.sign = -binding.sign * leg.reserved_size, -binding.sign
            available = _size_add(available, -leg.reserved_size)

    def _settle_exit_bindings(self, entry_id, qty, binding=None):
        entries = self._exit_entries.get(entry_id, [])
        for entry in ([binding] if binding is not None else list(entries)):
            taken = min(entry.size, qty)
            entry.size = _size_add(entry.size, -taken)
            qty = _size_add(qty, -taken)
            if _size_round(entry.size) == 0.0:
                for leg in list(entry.orders.values()):
                    self._remove_order(leg)
                entry.orders.clear()
                if entry in entries:
                    entries.remove(entry)
            if qty <= 0.0:
                break
        if not entries:
            self._exit_entries.pop(entry_id, None)

    def _cancel_oca_group(self, oca_name: str, executed_order: Order):
        """Cancel all orders in the same OCA group except the executed one"""
        # Cancel entry orders in the same OCA group
        for order in list(self.entry_orders.values()):
            if order.oca_name == oca_name and order != executed_order:
                self._remove_order(order)

        # Cancel exit orders in the same OCA group
        for order in list(self.exit_orders.values()):
            if order.oca_name == oca_name and order != executed_order:
                self._remove_order(order)

    def _reduce_oca_group(self, oca_name: str, filled_size: float):
        """Reduce the size of all orders in the same OCA group"""
        reduction = abs(filled_size)

        # Reduce entry orders
        for order in list(self.entry_orders.values()):
            if order.oca_name == oca_name and not order.cancelled:
                new_size = abs(order.size) - reduction
                if new_size <= 0:
                    # Mark order as cancelled if size would be 0 or negative
                    self._remove_order(order)
                else:
                    # Keep original sign
                    order.size = new_size * order.sign
                    # OCA has now explicitly fixed the remaining contract quantity.
                    order.qty_budget = None

        # Reduce exit orders
        for order in list(self.exit_orders.values()):
            if order.oca_name == oca_name and not order.cancelled:
                new_size = abs(order.size) - reduction
                if new_size <= 0:
                    self._remove_order(order)
                else:
                    order.size = new_size * order.sign

    def _reduce_entry_ledger(self, entry_id: str | None, qty: float, binding=None) -> None:
        """Settle a closing fill against the entry ID that sized the order."""
        if entry_id is None:
            return
        left = self._entry_open_ledger.get(entry_id)
        if left is None:
            return
        self._settle_exit_bindings(entry_id, qty, binding)
        left = _size_add(left, -qty)
        if _size_round(left) <= 0.0:
            del self._entry_open_ledger[entry_id]
            for exit_order in list(self.exit_orders.values()):
                if exit_order.order_id == entry_id and exit_order.exit_binding is None:
                    self._remove_order(exit_order)
        else:
            self._entry_open_ledger[entry_id] = left

    def _trade_bar_range(self, trade: Trade, end_price: float | None = None) -> tuple[float, float]:
        """Return the price range visited while the trade was open in this bar."""
        start = self._entry_fill_phases.get(trade)
        if start is None:
            if end_price is None:
                return self.h, self.l
            start, start_price = 0, self.o
        else:
            start_price = trade.entry_price
        end = 3 if end_price is None else self._fill_phase
        price = self.c if end_price is None else end_price
        high = max(start_price, price)
        low = min(start_price, price)
        first, second = (self.h, self.l) if self.h - self.o < self.o - self.l else (self.l, self.h)
        if start <= 1 < end:
            high, low = max(high, first), min(low, first)
        if start <= 2 < end:
            high, low = max(high, second), min(low, second)
        return high, low

    @staticmethod
    def _update_trade_excursions(trade: Trade, high: float, low: float) -> None:
        high_profit = trade.size * (high - trade.entry_price) - trade.commission
        low_profit = trade.size * (low - trade.entry_price) - trade.commission
        trade.max_drawdown = max(-min(high_profit, low_profit, 0.0), trade.max_drawdown)
        trade.max_runup = max(max(high_profit, low_profit, 0.0), trade.max_runup)
        entry_cost = abs(trade.size) * trade.entry_price + trade.commission
        if entry_cost > 0.0:
            trade.max_drawdown_percent = trade.max_drawdown / entry_cost * 100.0
            trade.max_runup_percent = trade.max_runup / entry_cost * 100.0

    def _fill_order(self, order: Order, price: float, h: float, l: float):
        """
        Fill an order (actually)

        :param order: The order to fill
        :param price: The price to fill at
        :param h: The high price
        :param l: The low price
        """
        price = _tick_snap(price)
        position_size_before = float(self.size)
        # Save the original order size before any modifications
        filled_size = abs(order.size)

        script = lib._script
        commission_type = script.commission_type
        commission_value = script.commission_value

        new_closed_trades = []
        closed_trade_size = 0.0

        # Close order - if it is an exit order or a normal order
        if self.size and order.sign != self.sign:
            delete = False
            initial_capital = script.initial_capital

            # TradingView attributes closed trades FIFO by default. With ANY,
            # an entry-bound close consumes only that entry's trade rows.
            close_any = (order.order_type == _order_type_close and order.order_id is not None
                         and getattr(script, 'close_entries_rule', 'FIFO') == 'ANY')
            bound_trade = order.exit_binding.trade if close_any and order.exit_binding is not None else None
            if bound_trade is not None and bound_trade not in self.open_trades:
                bound_trade = None
            new_open_trades = []
            for trade in self.open_trades:
                if order.size != 0.0 and (not close_any or
                        (trade is bound_trade if bound_trade is not None else trade.entry_id == order.order_id)):
                    delete = True

                    size = order.size if abs(order.size) <= abs(trade.size) else -trade.size
                    pnl = -size * (price - trade.entry_price)

                    # An intrabar exit must include extrema visited before its fill.
                    if self._fill_phase > 0 or trade in self._entry_fill_phases:
                        self._update_trade_excursions(trade, *self._trade_bar_range(trade, price))

                    # Copy and modify actual trade, because it can be partially filled
                    closed_trade = copy(trade)

                    size_ratio = 1 + size / closed_trade.size
                    if closed_trade.size != -size:
                        # Modify commission
                        trade.commission *= size_ratio
                        closed_trade.commission *= (1 - size_ratio)
                        # Modify drawdown and runup
                        trade.max_drawdown *= size_ratio
                        trade.max_runup *= size_ratio
                        closed_trade.max_drawdown *= (1 - size_ratio)
                        closed_trade.max_runup *= (1 - size_ratio)

                    closed_trade.size = -size
                    closed_trade.exit_id = order.exit_id if order.exit_id is not None else order.order_id
                    closed_trade.exit_bar_index = int(lib.bar_index)
                    closed_trade.exit_time = lib._time
                    closed_trade.exit_price = price
                    closed_trade.profit = pnl

                    # Include the fill endpoint before adding exit fees, using
                    # the same entry-fee basis as open-trade excursions.
                    exit_profit = pnl - closed_trade.commission
                    entry_cost = abs(closed_trade.size) * closed_trade.entry_price + closed_trade.commission
                    if -exit_profit > closed_trade.max_drawdown:
                        closed_trade.max_drawdown = -exit_profit
                        if entry_cost > 0.0:
                            closed_trade.max_drawdown_percent = closed_trade.max_drawdown / entry_cost * 100.0
                    elif exit_profit > closed_trade.max_runup:
                        closed_trade.max_runup = exit_profit
                        if entry_cost > 0.0:
                            closed_trade.max_runup_percent = closed_trade.max_runup / entry_cost * 100.0

                    # Add to closed trade
                    new_closed_trades.append(closed_trade)
                    self.closed_trades.append(closed_trade)
                    self.closed_trades_count += 1

                    if order.comment:
                        # TODO: implement comment_profit, comment_loss, comment_trailing...
                        closed_trade.exit_comment = order.comment

                    # Commission summ
                    self.open_commission -= closed_trade.commission

                    # Only per-order fees need allocation across multiple trades.
                    if commission_type == _commission.cash_per_order:
                        closed_trade_size += abs(size)
                    else:
                        # Calculate exit commission based on commission type
                        if commission_type == _commission.percent:
                            # For percentage commission, multiply by exit price
                            commission = abs(size) * price * commission_value * 0.01
                        else:
                            # Contract fees are charged on the quantity closed.
                            commission = abs(size) * commission_value

                        closed_trade.commission += commission
                        # Realize commission
                        self.netprofit -= commission
                        closed_trade.profit -= closed_trade.commission

                    # Profit percent
                    try:
                        closed_trade.profit_percent = (closed_trade.profit / entry_cost) * 100.0
                    except ZeroDivisionError:
                        closed_trade.profit_percent = 0.0

                    # Realize profit or loss
                    self.netprofit += pnl
                    # Entry fees were already paid. Freeze each fill's realized mark
                    # before later closes or entries on this bar change netprofit.
                    closed_trade.cum_profit = self.netprofit
                    closed_trade.cum_profit_percent = (
                        self.netprofit / initial_capital * 100.0 if initial_capital else 0.0
                    )

                    # Modify sizes
                    self.size = _size_add(self.size, size)
                    # Handle too small sizes because of floating point inaccuracy and rounding
                    position_flat = _size_round(self.size) == 0.0
                    if position_flat:
                        size -= self.size
                        self.size = 0.0
                    self.sign = 0.0 if self.size == 0.0 else 1.0 if self.size > 0.0 else -1.0
                    trade.size = _size_add(trade.size, size)
                    if position_flat:
                        trade.size = 0.0
                    order.size = _size_add(order.size, -size)

                    # Gross P/L and counters
                    if closed_trade.profit == 0.0:
                        self.eventrades += 1
                    elif closed_trade.profit > 0.0:
                        self.wintrades += 1
                        self.grossprofit += closed_trade.profit
                    else:
                        self.losstrades += 1
                        self.grossloss -= closed_trade.profit

                    # Average entry price
                    if self.size:
                        self.entry_summ -= closed_trade.entry_price * abs(closed_trade.size)
                        self.avg_price = self.entry_summ / abs(self.size)

                        # Unrealized P&L
                        self.openprofit = self.size * (self.c - self.avg_price)
                        self.aggregate_openprofit = self.size * (
                            self.c - self.aggregate_avg_price
                        )
                    else:
                        # If position has just closed
                        self.avg_price = na_float
                        self.openprofit = 0.0
                        self.aggregate_avg_price = na_float
                        self.aggregate_openprofit = 0.0

                    # Exit equity
                    closed_trade.exit_equity = self.equity

                    # Remove from open trades if it is fully filled
                    if trade.size == 0.0:
                        continue

                new_open_trades.append(trade)

            self.open_trades = new_open_trades

            # Trade rows follow FIFO/ANY attribution, while this ledger follows
            # the entry ID that the closing order was bound to.
            closed_qty = _size_add(filled_size, -abs(order.size))
            if closed_qty > 0.0:
                if order.order_type == _order_type_close and order.order_id is not None:
                    if order.exit_binding is not None:
                        order.consumed = True
                    self._reduce_entry_ledger(order.order_id, closed_qty, order.exit_binding)
                else:
                    for entry_id in list(self._entry_open_ledger):
                        if closed_qty <= 0.0:
                            break
                        taken = min(self._entry_open_ledger[entry_id], closed_qty)
                        self._reduce_entry_ledger(entry_id, taken)
                        closed_qty = _size_add(closed_qty, -taken)

            if delete:
                # A reducing normal order never opens the entry its predeclared
                # exits awaited. Cancel those bindings instead of retaining them.
                self._remove_order(order, filled=order.order_type == _order_type_close)

                if commission_type == _commission.cash_per_order:
                    # Realize commission
                    self.netprofit -= commission_value
                    allocated_commission = 0.0
                    for trade in new_closed_trades:
                        commission = (commission_value * abs(trade.size)) / closed_trade_size
                        trade.commission += commission
                        allocated_commission += commission
                        trade.cum_profit -= allocated_commission
                        trade.cum_profit_percent = (
                            trade.cum_profit / initial_capital * 100.0 if initial_capital else 0.0
                        )

            self.new_closed_trades.extend(new_closed_trades)

            # Call close callback for each closed trade
            if self.on_close_callback and new_closed_trades:
                for closed_trade in new_closed_trades:
                    try:
                        self.on_close_callback(closed_trade)
                    except Exception as e:
                        print(f"Error in on_close_callback: {e}")

        # New trade
        elif order.order_type != _order_type_close:
            # Calculate commission
            if commission_value:
                if commission_type == _commission.cash_per_order:
                    commission = commission_value
                elif commission_type == _commission.percent:
                    commission = abs(order.size) * price * commission_value * 0.01
                elif commission_type == _commission.cash_per_contract:
                    commission = abs(order.size) * commission_value
                else:  # Should not be here!
                    assert False, 'Wrong commission type: ' + str(commission_type)
            else:
                commission = 0.0

            before_equity = self.equity

            # Realize commission
            self.netprofit -= commission

            # Run-up starts from the equity mark left by each entry fill. Existing
            # open profit only lifts the anchor when it is positive.
            entry_mark = float(lib._script.initial_capital) + float(self.netprofit)
            if self.size:
                open_mark = self.size * (price - self.avg_price)
                if open_mark > 0.0:
                    entry_mark += open_mark
            if entry_mark < self.min_equity:
                self.min_equity = entry_mark

            assert order.order_id is not None

            trade = Trade(
                size=order.size,
                entry_id=order.order_id, entry_bar_index=cast(int, lib.bar_index),
                entry_time=lib._time, entry_price=price,
                commission=commission, entry_comment=order.comment,  # type: ignore
                entry_equity=before_equity
            )

            self.open_trades.append(trade)
            binding = order.exit_binding
            if binding is None:
                binding = _ExitBinding(order.order_id, order.sign, abs(order.size), trade)
            else:
                self._resize_pending_binding(order, filled_size=abs(order.size))
                binding.price, binding.trade = price, trade
            self._exit_entries.setdefault(order.order_id, []).append(binding)
            for leg in binding.orders.values():
                self._resolve_close_order_prices(leg)
            self._entry_fill_phases[trade] = self._fill_phase
            self._entry_open_ledger[order.order_id] = (
                _size_add(self._entry_open_ledger.get(order.order_id, 0.0), abs(order.size))
            )
            previous_size = abs(self.size)
            if previous_size == 0.0:
                self.position_open_time = int(lib._time)
                self.position_open_bar_index = int(lib.bar_index)
            self.size = _size_add(self.size, trade.size)
            self.sign = 0.0 if self.size == 0.0 else 1.0 if self.size > 0.0 else -1.0

            # Average entry price
            self.entry_summ += price * abs(order.size)
            try:
                self.avg_price = self.entry_summ / abs(self.size)
            except ZeroDivisionError:
                self.avg_price = na_float
            if previous_size == 0.0 or isinstance(self.aggregate_avg_price, NA):
                self.aggregate_avg_price = price
            else:
                self.aggregate_avg_price = (
                    self.aggregate_avg_price * previous_size + price * abs(order.size)
                ) / abs(self.size)
            # Unrealized P&L
            self.openprofit = self.size * (self.c - self.avg_price)
            self.aggregate_openprofit = self.size * (
                self.c - self.aggregate_avg_price
            )
            # Commission summ
            self.open_commission += commission

            # Remove order
            self._remove_order(order, filled=True)

            # Call entry callback for the new trade
            if self.on_entry_callback:
                try:
                    self.on_entry_callback(trade)
                except Exception as e:
                    print(f"Error in on_entry_callback: {e}")

        # If position has just closed
        if not self.open_trades:
            # Reset position variables
            self.entry_summ = 0.0
            self.avg_price = na_float
            self.openprofit = 0.0
            self.aggregate_avg_price = na_float
            self.aggregate_openprofit = 0.0
            self.position_open_time = None
            self.position_open_bar_index = None
            self.open_commission = 0.0
            self._entry_open_ledger.clear()
            self._exit_entries.clear()

            # Cancel all exit orders when position is closed (TradingView behavior)
            # Exit orders without from_entry are canceled when position is flat
            exit_orders_to_remove = list(self.exit_orders.values())
            for exit_order in exit_orders_to_remove:
                if exit_order.exit_binding is None or exit_order.exit_binding.price is not None:
                    self._remove_order(exit_order)

        # Increment intraday filled orders counter for ALL filled orders
        # TradingView counts ALL filled orders (entry, exit, normal) toward the limit
        # This is done after successful fill to match TradingView behavior
        self.risk_intraday_filled_orders += 1

        # Handle OCA groups after order execution
        # This is done here to avoid code duplication in fill_order()
        if order.oca_name and order.oca_type:
            if order.oca_type == _oca.cancel:
                self._cancel_oca_group(order.oca_name, order)
            elif order.oca_type == _oca.reduce:
                # Use the saved original filled_size from the beginning of this method
                self._reduce_oca_group(order.oca_name, filled_size)

        self._dispatch_order_notifications(
            order,
            filled_size=filled_size,
            fill_price=price,
            position_size_before=position_size_before,
            position_size_after=float(self.size),
        )

    @staticmethod
    def _order_signal_action(
            order: Order,
            position_size_before: float,
            position_size_after: float,
    ) -> str:
        if order.order_type == _order_type_close:
            return "close"
        if order.order_type == _order_type_entry:
            return "entry"
        if position_size_before != 0.0:
            same_direction = position_size_before * position_size_after > 0.0
            if position_size_after == 0.0 or (
                    same_direction
                    and abs(position_size_after) < abs(position_size_before)
            ):
                return "close"
        return "entry"

    def _dispatch_order_notifications(
            self,
            order: Order,
            *,
            filled_size: float,
            fill_price: float,
            position_size_before: float,
            position_size_after: float,
    ) -> None:
        if not realtime_trade() and order.alert_message:
            alert(order.alert_message, _dispatch_callback=False)
        elif realtime_trade() and not pre_run():
            # print(f"order.bar_index: {order.bar_index}, last_bar_index: {last_bar_index()}")
            # Real time trade 에서는 최종 봉이 확정되고 새로운 봉이 생길때에만
            # alert 및 AI instruction을 전달함.
            if order.bar_index == last_bar_index() - 1:
                if self.on_order_signal_callback:
                    try:
                        self.on_order_signal_callback({
                            "action": self._order_signal_action(
                                order,
                                position_size_before,
                                position_size_after,
                            ),
                            "time": int(lib._time / 1000),
                            "order_id": order.order_id or "",
                            "exit_id": order.exit_id or "",
                            "size": float(filled_size),
                            "direction": (
                                "buy" if order.sign > 0.0
                                else "sell" if order.sign < 0.0
                                else ""
                            ),
                            "comment": order.comment or "",
                            "alert_message": order.alert_message or "",
                            "fill_price": float(fill_price),
                            "order_bar_index": int(order.bar_index),
                            "position_size_before": position_size_before,
                            "position_size_after": position_size_after,
                        })
                    except Exception as e:
                        print(f"Error in on_order_signal_callback: {e}")
                if order.alert_message:
                    alert(order.alert_message, _dispatch_callback=False)
                if order.alert_message and self.on_alert_callback:
                    try:
                        self.on_alert_callback(order.alert_message)
                    except Exception as e:
                        print(f"Error in on_alert_callback: {e}")
                if order.ai_instruction and self.on_ai_callback:
                    try:
                        self.on_ai_callback(order.ai_instruction, order, lib._time)
                    except Exception as e:
                        print(f"Error in on_ai_callback: {e}")

    def _resolve_price_order_qty(self, order: Order, price: float) -> bool:
        budget, order.qty_budget = order.qty_budget, None
        sizing_price = price
        if order.is_market_order and order.stop is not None and order.limit is None:
            sizing_price = order.qty_price + lib._script.slippage * syminfo.mintick * order.sign
        qty = _budget_quantity(budget, sizing_price)
        if not 0.0 < qty < float("inf"):
            self._remove_order(order)
            return False
        order.size = _size_round((qty + order.flip_qty) * order.sign)
        if order.size == 0.0:
            self._remove_order(order)
            return False
        if order.exit_binding is not None:
            self._resize_pending_binding(order)
        return True

    def fill_order(self, order: Order, price: float, h: float, l: float) -> bool:
        """
        Fill an order

        :param order: The order to fill
        :param price: The price to fill at
        :param h: The high price
        :param l: The low price
        :return: True if the side of the position has changed
        """
        if order.cancelled:
            return False
        if order.order_type == _order_type_close and order.exit_binding is not None:
            binding = order.exit_binding
            if binding.price is None or not self.size or order.sign == self.sign:
                return False
            order.size = order.sign * min(abs(order.size), binding.size, abs(self.size))
            if order.size == 0.0:
                self._remove_order(order)
                return False
        close_only = False
        if order.qty_budget is not None and not self._resolve_price_order_qty(order, price):
            return False
        # Apply risk management only to entry orders, not normal orders from strategy.order()
        if order.order_type == _order_type_entry or order.order_type == _order_type_normal:
            # Risk management: Check max intraday filled orders
            if self.risk_max_intraday_filled_orders is not None:
                if self.risk_intraday_filled_orders >= self.risk_max_intraday_filled_orders:
                    # Max intraday filled orders reached - don't fill the entry order
                    self._remove_order(order)
                    return False

            # Risk management: Check max position size
            if self.risk_max_position_size is not None:
                new_position_size = abs(self.size + order.size)
                if new_position_size > self.risk_max_position_size:
                    # Adjust order size to not exceed max position size
                    max_allowed_size = self.risk_max_position_size - abs(self.size)
                    if max_allowed_size <= 0:
                        # Can't add to position - remove order
                        self._remove_order(order)
                        return False
                    # Adjust the order size
                    order.size = max_allowed_size * order.sign

            # Check risk allowed direction for new positions (when no current position)
            if self.size == 0.0 and self.risk_allowed_direction is not None:
                if (order.sign > 0 and self.risk_allowed_direction != long) or \
                        (order.sign < 0 and self.risk_allowed_direction != short):
                    # Direction not allowed - don't fill the entry order
                    self._remove_order(order)
                    return False

            if order.order_type == _order_type_entry:
                # If we have an existing position
                if self.size != 0.0:
                    # Check if the order has the same direction
                    if self.sign == order.sign:
                        # Check pyramiding limit for entry orders adding to existing position
                        if lib._script.pyramiding <= len(self.open_trades):
                            # Pyramiding limit reached - don't fill the entry order
                            self._remove_order(order)
                            return False

        # For normal orders (_order_type_normal), no special risk management or pyramiding limits apply
        # They simply add to or subtract from the position as requested

        # If position direction is about to change, we split it into two separate orders
        # This is necessary to create a new average entry price
        # Note: The flip quantity is already calculated in entry() for entry orders
        new_size = _size_add(self.size, order.size)
        if _size_round(new_size) == 0.0:
            new_size = 0.0
        new_sign = 0.0 if new_size == 0.0 else 1.0 if new_size > 0.0 else -1.0
        if self.size != 0.0 and new_sign != self.sign and new_size != 0.0:
            # Exit orders should never reverse position direction
            # Only entry orders can open new positions or reverse direction
            if order.order_type == _order_type_close or close_only:
                # Limit the exit order size to just close the position.
                # Partial close 가 안되어서 수정. (original: order.size = -self.size)
                order.size = _size_add(order.size, self.size)
                self._fill_order(order, price, h, l)
                return False

            # Create a copy for closing existing position
            order1 = copy(order)
            order1.order_type = _order_type_close
            order1.exit_binding = None
            # The AI instruction belongs to the requested entry, not the
            # internal close leg used to reverse an existing position.
            order1.ai_instruction = None
            order1.size = -self.size
            # Set order_id to None so it will close any open trades
            order1.order_id = None
            # The exit_id will be the order_id of the original order
            order1.exit_id = order.order_id
            # Fill the closing order first
            self._fill_order(order1, price, h, l)

            # Check if new direction is allowed by risk management
            # According to Pine Script docs: "long exit trades will be made instead of reverse trades"
            new_direction_sign = 1.0 if new_size > 0.0 else -1.0
            if self.risk_allowed_direction is not None:
                if (new_direction_sign > 0 and self.risk_allowed_direction != long) or \
                        (new_direction_sign < 0 and self.risk_allowed_direction != short):
                    # Direction not allowed - convert entry to exit only
                    # Don't open new position in restricted direction
                    self._remove_order(order)
                    return False

            # Modify the original order to open a position in the new direction
            order.size = new_size
            # Fill the entry order
            self._fill_order(order, price, h, l)
            return True

        # If position direction is not about to change, we can fill the order directly
        else:
            self._fill_order(order, price, h, l)

            # After filling, check if we need to close positions due to risk management
            if (self.risk_max_intraday_filled_orders is not None and
                    self.risk_intraday_filled_orders >= self.risk_max_intraday_filled_orders and self.size != 0.0):
                # Max intraday filled orders reached - close all positions immediately
                # Cancel all pending orders first
                for pending in list(self.entry_orders.values()) + list(self.exit_orders.values()):
                    self._remove_order(pending)

                # Create an immediate close order with special comment
                close_comment = "Close Position (Max number of filled orders in one day)"
                close_order = Order(
                    None, -self.size,
                    exit_id='Risk management close',
                    order_type=_order_type_close,
                    comment=close_comment
                )
                # Fill the close order immediately at current price
                self._fill_order(close_order, price, h, l)

                # Halt trading for the rest of the day
                self.risk_halt_trading = True

            return False

    def _fill_immediate_close(self, order: Order) -> None:
        closed_start = len(self.new_closed_trades)
        self.fill_order(order, self.c, self.h, self.l)
        if len(self.new_closed_trades) == closed_start:
            return

        # Immediate closes happen after process_orders() finalized cumulative stats.
        # Trade marks are captured by _fill_order; only refresh the position mark.
        initial_capital = lib._script.initial_capital
        self.cum_profit = self.equity - initial_capital - self.openprofit

    def _check_already_filled(self, order: Order) -> bool:
        """
        Check if a stop or limit order would be immediately fillable due to a gap.
        This is called during process_orders when we have the current bar's OHLC values.

        When there's a gap, orders that would normally wait for price movement
        should execute immediately at the open price.

        :param order: The order to check
        :return: True if the order should be filled immediately at open price
        """
        if order.order_type == _order_type_close and order.exit_binding is not None and order.exit_binding.price is None:
            return False
        # Check stop orders with gaps
        if order.stop is not None:
            # Long stop order (size > 0): triggers if open gaps above stop level
            if order.size > 0 and self.o >= order.stop:
                return True
            # Short stop order (size < 0): triggers if open gaps below stop level
            if order.size < 0 and self.o <= order.stop:
                return True

        if order.trail_stop is not None and order.sign * (self.o - order.trail_stop) >= 0.0:
            return True

        # Check limit orders with gaps
        if order.limit is not None:
            # Long limit order (size > 0): triggers if open gaps below limit level
            if order.size > 0 and self.o <= order.limit:
                return True
            # Short limit order (size < 0): triggers if open gaps above limit level
            if order.size < 0 and self.o >= order.limit:
                return True

        return False

    def _check_high_stop(self, order: Order) -> bool:
        """ Check high stop and trailing trigger """
        if order.stop is None:
            return False
        # Long stop order (size > 0) triggers when price rises to stop level
        if order.size > 0 and order.stop <= self.h:
            p = max(order.stop, self.o)
            p += lib._script.slippage * syminfo.mintick
            self.fill_order(order, p, p, self.l)
            return True
        return False

    def _check_high(self, order: Order) -> bool:
        """ Check high limit """
        if order.limit is not None:
            # Short limit order (size < 0) triggers when price rises to limit level
            if order.size < 0 and order.limit <= self.h:
                p = max(order.limit, self.o)
                self.fill_order(order, p, p, self.l)
                return True
        return False

    def _check_low_stop(self, order: Order) -> bool:
        """ Check low stop """
        if order.stop is None:
            return False
        # Short stop order (size < 0) triggers when price falls to stop level
        if order.size < 0 and order.stop >= self.l:
            p = min(self.o, order.stop)
            p -= lib._script.slippage * syminfo.mintick
            self.fill_order(order, p, self.h, p)
            return True
        return False

    def _check_low(self, order: Order) -> bool:
        """ Check low limit """
        if order.limit is not None:
            # Long limit order (size > 0) triggers when price falls to limit level
            if order.size > 0 and order.limit >= self.l:
                p = min(self.o, order.limit)
                self.fill_order(order, p, self.h, p)
                return True
        return False

    @staticmethod
    def _advance_trailing(order: Order, price: float) -> None:
        """Arm at a visited tick and ratchet only in the favorable direction."""
        if order.trail_price is None or order.trail_offset is None:
            return
        if not order.trail_triggered:
            if order.sign * (price - order.trail_price) > 0.0:
                return
            order.trail_triggered = True
        candidate = price + order.sign * syminfo.mintick * order.trail_offset
        stop = floor(candidate / syminfo.mintick + 0.5) * syminfo.minmove / syminfo.pricescale
        if order.trail_stop is None or order.sign * (stop - order.trail_stop) < 0.0:
            order.trail_stop = stop

    def _process_trailing_path(self, ohlc: bool) -> None:
        """Walk trailing and competing price orders in intrabar time order."""
        first, second = (self.h, self.l) if ohlc else (self.l, self.h)
        cursor = self.o
        attempted = set()
        for phase, end in enumerate((self.o, first, second, self.c)):
            self._fill_phase = phase
            while True:
                orders = [order for order in dict.fromkeys(self.orderbook.iter_orders())
                          if not order.cancelled and order not in attempted and
                          (order.order_type != _order_type_close or order.exit_binding is None or
                           (order.exit_binding.price is not None and self.size and order.sign != self.sign))]
                events = []
                for order in orders:
                    self._advance_trailing(order, cursor)
                    levels = [(order.stop, order.sign, 'stop'),
                              (order.limit, -order.sign, 'limit'),
                              (order.trail_stop, order.sign, 'trailing')]
                    if order.trail_offset is not None and not order.trail_triggered:
                        levels.append((order.trail_price, -order.sign, 'activate'))
                    for level, direction, kind in levels:
                        if level is None:
                            continue
                        if direction * (cursor - level) >= 0.0:
                            tick = cursor
                        elif direction * (end - level) >= 0.0:
                            tick = level
                        else:
                            continue
                        events.append((abs(tick - cursor), tick, order, kind))
                if not events:
                    for order in orders:
                        self._advance_trailing(order, end)
                    cursor = end
                    break
                _, cursor, order, kind = min(events, key=lambda event: event[0])
                # All surviving trails see this tick before an OCA fill can cancel them.
                for pending in orders:
                    self._advance_trailing(pending, cursor)
                if kind == 'activate':
                    continue
                price = cursor
                if kind != 'limit':
                    price += lib._script.slippage * syminfo.mintick * order.sign
                attempted.add(order)
                self.fill_order(order, price, price, price)

    def process_orders(self):
        """ Process orders """
        if lib._script.process_orders_on_close:
            self._close_tick_orders.clear()
        # Match order-level decimal prices for exact stop/limit touches. Keep
        # _tick_snap for fills so their existing tick-rounding behavior is unchanged.
        mintick, minmove, pricescale = syminfo.mintick, syminfo.minmove, syminfo.pricescale
        self.o = floor(lib.open / mintick + 0.5) * minmove / pricescale
        self.h = floor(lib.high / mintick + 0.5) * minmove / pricescale
        self.l = floor(lib.low / mintick + 0.5) * minmove / pricescale
        self.c = floor(lib.close / mintick + 0.5) * minmove / pricescale
        self._entry_fill_phases.clear()
        self._fill_phase = 0

        # Check if we're in a new trading day for intraday risk management
        # TradingView tracks intraday based on trading session, not calendar day
        current_day = lib.dayofmonth()
        if current_day != self.risk_last_day_index:
            # New trading day - reset intraday counters
            self.risk_last_day_index = current_day
            self.risk_intraday_filled_orders = 0
            # TODO: Also reset intraday loss tracking here when implemented

        # Get script reference for slippage
        script = lib._script

        # If the order is open → high → low → close or open → low → high → close
        ohlc = self.h - self.o < self.o - self.l

        self.drawdown_summ = self.runup_summ = 0.0
        self.new_closed_trades.clear()

        exit_orders = list(self.exit_orders.values())

        # Skip exit order processing if there's no open position (TradingView behavior)
        if not self.open_trades:
            # Remove all exit orders when position is flat
            for order in exit_orders:
                if order.exit_binding is None or order.exit_binding.price is not None:
                    self._remove_order(order)
            exit_orders = []

        # For exit orders, calculate limit/stop from entry price if ticks are specified
        for order in exit_orders:
            if order.exit_binding is not None:
                self._resolve_close_order_prices(order)
                continue
            # Try to find the trade with matching entry_id
            entry_price = None
            for trade in self.open_trades:
                if trade.entry_id == order.order_id:
                    entry_price = trade.entry_price
                    break

            # If we found the entry price and have tick values, calculate the actual prices
            if entry_price is not None:
                # Determine direction from the order
                direction = 1.0 if order.size < 0 else -1.0  # Exit order size is negative of position

                # Calculate limit from profit_ticks if specified
                if order.profit_ticks is not None and order.limit is None:
                    order.limit = entry_price + direction * syminfo.mintick * order.profit_ticks
                    order.limit = _price_round(order.limit, direction)

                # Calculate stop from loss_ticks if specified
                if order.loss_ticks is not None and order.stop is None:
                    order.stop = entry_price - direction * syminfo.mintick * order.loss_ticks
                    order.stop = _price_round(order.stop, -direction)

                # Calculate trail_price from trail_points_ticks if specified
                if order.trail_points_ticks is not None and order.trail_price is None:
                    order.trail_price = entry_price + direction * syminfo.mintick * order.trail_points_ticks
                    order.trail_price = _price_round(order.trail_price, direction)

        # Check for stop/limit orders that should be converted to market orders due to gaps
        # This must happen BEFORE processing market orders
        for order in self.orderbook.iter_orders():
            # Check if the order would be filled immediately (e.g. due to a gap)
            if self._check_already_filled(order):
                # Convert to market order
                order.is_market_order = True
                # Add to market orders dict
                self.market_orders[_market_key(order)] = order

        # Process Market orders
        for order in list(self.market_orders.values()):
            if order.cancelled:
                continue
            if order.order_type == _order_type_entry and order.limit is None and order.stop is None:
                # Normal orders bypass the existing entry/close quantity normalization.
                # Check pyramiding limit for entry orders adding to existing position
                if self.sign == order.sign:
                    if lib._script.pyramiding <= len(self.open_trades):
                        # Pyramiding limit reached - don't add the order
                        self._remove_order(order)
                        continue
                elif self.size != 0.0:
                    # TradingView calculates the flip quantity 1st order processing
                    # then open a new one in the opposite direction.
                    order.size = _size_add(order.size, -self.size)

            # Apply slippage to market orders
            fill_price = self.o
            limit_fill = (order.limit is not None and
                          (self.o <= order.limit if order.sign > 0 else self.o >= order.limit))
            if script.slippage > 0 and not limit_fill:
                # Slippage is in ticks, always adverse to trade direction
                # For long orders (buying), slippage increases the price
                # For short orders (selling), slippage decreases the price
                slippage_amount = syminfo.mintick * script.slippage * order.sign
                fill_price = self.o + slippage_amount

            # open → high → low → close
            if ohlc:
                self.fill_order(order, fill_price, self.o, self.l)
            # open → low → high → close
            else:
                self.fill_order(order, fill_price, self.l, self.o)

        # Process orders: open → high → low → close
        if any(order.trail_offset is not None and
               (order.trail_price is not None or order.trail_points_ticks is not None)
               for order in self.exit_orders.values()):
            self._process_trailing_path(ohlc)
        elif ohlc:
            # open -> high
            self._fill_phase = 1
            for order in self.orderbook.iter_orders(min_price=self.o, max_price=self.h):
                if self._check_high_stop(order):
                    continue
                if self._check_high(order):
                    continue

            # open -> low
            self._fill_phase = 2
            for order in self.orderbook.iter_orders(max_price=self.o, min_price=self.l, desc=True):
                if self._check_low_stop(order):
                    continue
                if self._check_low(order):
                    continue

        # Process orders: open → low → high → close
        else:
            # open -> low
            self._fill_phase = 1
            for order in self.orderbook.iter_orders(max_price=self.o, min_price=self.l, desc=True):
                if self._check_low_stop(order):
                    continue
                if self._check_low(order):
                    continue

            # open -> high
            self._fill_phase = 2
            for order in self.orderbook.iter_orders(min_price=self.o, max_price=self.h):
                if self._check_high_stop(order):
                    continue
                if self._check_high(order):
                    continue

        self._fill_phase = 3

        # Calculate average entry price, unrealized P&L, drawdown and runup...
        if self.open_trades:
            # Unrealized P&L
            self.openprofit = self.size * (self.c - self.avg_price)
            self.aggregate_openprofit = self.size * (
                self.c - self.aggregate_avg_price
            )

            # Calculate open drawdowns and runups
            for trade in self.open_trades:
                # Profit of trade
                trade.profit = trade.size * (self.c - trade.entry_price) - 2 * trade.commission

                # Position excursion is measured from the position average. Fees
                # already live in netprofit, so these marks remain gross.
                hprofit = trade.size * (self.h - self.avg_price)
                lprofit = trade.size * (self.l - self.avg_price)
                drawdown = -min(hprofit, lprofit, 0.0)
                runup = max(hprofit, lprofit, 0.0)

                self._update_trade_excursions(trade, *self._trade_bar_range(trade))

                # Drawdown summ runup summ
                self.drawdown_summ += drawdown
                self.runup_summ += runup

        # Position excursions use realized equity endpoints. Open paper profit
        # does not raise the drawdown high-water mark.
        initial_capital = float(lib._script.initial_capital)
        realized_equity = initial_capital + float(self.netprofit)
        if realized_equity > self.max_realized_equity:
            self.max_realized_equity = realized_equity

        peak = max(initial_capital, self.max_realized_equity)
        equity_drawdown = peak - realized_equity + self.drawdown_summ
        self.current_drawdown = equity_drawdown
        self.current_drawdown_percent = (
            equity_drawdown / peak * 100.0 if peak > 0.0 else 0.0
        )
        self.max_drawdown = max(self.max_drawdown, equity_drawdown)
        self.max_drawdown_percent = max(
            self.max_drawdown_percent,
            self.current_drawdown_percent,
        )

        if self.min_equity < float("inf"):
            equity_runup = realized_equity + self.runup_summ - self.min_equity
            self.max_runup = max(self.max_runup, equity_runup)
            top = self.min_equity + equity_runup
            if top > 0.0:
                self.max_runup_percent = max(
                    self.max_runup_percent,
                    equity_runup / top * 100.0,
                )

        # Cumulative stats
        if self.new_closed_trades:
            self.cum_profit = self.equity - lib._script.initial_capital - self.openprofit
            for closed_trade in self.new_closed_trades:
                closed_trade.cum_max_drawdown = self.max_drawdown
                closed_trade.cum_max_runup = self.max_runup

    def process_close_orders(self) -> None:
        """Process this calculation's surviving orders at the closing tick only."""
        if not self._close_tick_orders:
            return
        orders, self._close_tick_orders = self._close_tick_orders, []
        for order in orders:
            pending = self.exit_orders if order.order_type == _order_type_close else self.entry_orders
            if order.cancelled or pending.get(_order_key(order)) is not order:
                # Cancellation/replacement may have left an older market-queue entry.
                if self.market_orders.get(_market_key(order)) is order:
                    self.market_orders.pop(_market_key(order))
                continue
            if order.order_type == _order_type_close:
                if not self.open_trades:
                    continue
                self._resolve_close_order_prices(order, update_trailing=True)
                if order.exit_binding is not None and not any(value is not None for value in
                        (order.limit, order.stop, order.trail_price)):
                    continue

            market = (order.limit is None and order.stop is None and order.trail_price is None)
            stop_hit = order.stop is not None and order.sign * (self.c - order.stop) >= 0.0
            stop_hit = stop_hit or (order.trail_stop is not None and
                                    order.sign * (self.c - order.trail_stop) >= 0.0)
            limit_hit = order.limit is not None and order.sign * (self.c - order.limit) <= 0.0
            # A stop-limit entry activates its limit; an exit's stop/limit are alternatives.
            if order.order_type != _order_type_close and order.stop is not None and order.limit is not None:
                if not stop_hit:
                    continue
                self.orderbook.remove_order(order)
                order.stop = None
                self.orderbook.add_order(order)
                stop_hit = False
            if not (market or stop_hit or limit_hit):
                continue

            if market and order.order_type == _order_type_entry:
                if self.sign != order.sign and self.size:
                    order.size = _size_add(order.size, -self.size)
            price = self.c
            if not limit_hit:
                price += lib._script.slippage * syminfo.mintick * order.sign
            closed_start = len(self.new_closed_trades)
            filled_before = self.risk_intraday_filled_orders
            self.fill_order(order, price, self.c, self.c)
            if self.risk_intraday_filled_orders != filled_before:
                self._mark_close_fills(closed_start)

    def _resolve_close_order_prices(self, order: Order, *, update_trailing=False) -> None:
        """Resolve entry-relative exit prices without replaying the bar's high/low."""
        binding = order.exit_binding
        trade = (binding.trade if binding is not None else
                 next((trade for trade in self.open_trades if trade.entry_id == order.order_id), None))
        if trade is not None and any(ticks is not None and getattr(order, field) is None for field, ticks in
                (("limit", order.profit_ticks), ("stop", order.loss_ticks), ("trail_price", order.trail_points_ticks))):
            self.orderbook.remove_order(order)
            for field, ticks, sign in (("limit", order.profit_ticks, -order.sign),
                                       ("stop", order.loss_ticks, order.sign),
                                       ("trail_price", order.trail_points_ticks, -order.sign)):
                if ticks is not None and getattr(order, field) is None:
                    setattr(order, field, _price_round(trade.entry_price + sign * syminfo.mintick * ticks, sign))
            self.orderbook.add_order(order)
        if order.limit is not None or order.stop is not None or order.trail_price is not None:
            if self.market_orders.get(_market_key(order)) is order:
                self.market_orders.pop(_market_key(order))
            order.is_market_order = False
        if update_trailing:
            self._advance_trailing(order, self.c)

    def _mark_close_fills(self, closed_start: int) -> None:
        """Mark post-calculation fills at close, preserving earlier intrabar excursions."""
        drawdown = runup = 0.0
        for trade in self.open_trades:
            profit = trade.size * (self.c - trade.entry_price) - trade.commission
            trade.profit = profit - trade.commission
            trade.max_drawdown = max(trade.max_drawdown, -min(profit, 0.0))
            trade.max_runup = max(trade.max_runup, max(profit, 0.0))
            cost = abs(trade.size) * trade.entry_price + trade.commission
            if cost > 0.0:
                trade.max_drawdown_percent = trade.max_drawdown / cost * 100.0
                trade.max_runup_percent = trade.max_runup / cost * 100.0
            gross = trade.size * (self.c - self.avg_price)
            drawdown += -min(gross, 0.0)
            runup += max(gross, 0.0)

        initial_capital = float(lib._script.initial_capital)
        realized = initial_capital + float(self.netprofit)
        self.max_realized_equity = max(self.max_realized_equity, realized)
        peak = max(initial_capital, self.max_realized_equity)
        close_drawdown = peak - realized + drawdown
        close_drawdown_percent = close_drawdown / peak * 100.0 if peak > 0.0 else 0.0
        self.current_drawdown = max(self.current_drawdown, close_drawdown)
        self.current_drawdown_percent = max(self.current_drawdown_percent, close_drawdown_percent)
        self.max_drawdown = max(self.max_drawdown, close_drawdown)
        self.max_drawdown_percent = max(self.max_drawdown_percent, close_drawdown_percent)
        if self.min_equity < float("inf"):
            close_runup = realized + runup - self.min_equity
            self.max_runup = max(self.max_runup, close_runup)
            top = self.min_equity + close_runup
            if top > 0.0:
                self.max_runup_percent = max(self.max_runup_percent, close_runup / top * 100.0)

        if len(self.new_closed_trades) > closed_start:
            self.cum_profit = self.equity - initial_capital - self.openprofit
            for trade in self.new_closed_trades[closed_start:]:
                trade.cum_max_drawdown = self.max_drawdown
                trade.cum_max_runup = self.max_runup

#
# Functions
#

def _price_order_budget(limit, stop, sign):
    """Freeze money and unit-cost terms only for default money-sized price orders."""
    limit = None if isinstance(limit, NA) else limit
    stop = None if isinstance(stop, NA) else stop
    script = lib._script
    if (limit is None and stop is None) or script.default_qty_type == fixed:
        return None, None
    factor, fee = float(syminfo.pointvalue), 0.0
    if script.default_qty_type == cash:
        money = float(script.default_qty_value)
    elif script.default_qty_type == percent_of_equity:
        money = float(script.position.equity) * script.default_qty_value * 0.01
        if script.commission_type == _commission.percent:
            factor *= 1.0 + script.commission_value * 0.01
        elif script.commission_type == _commission.cash_per_contract:
            fee = float(script.commission_value)
        elif script.commission_type == _commission.cash_per_order:
            money = max(0.0, money - script.commission_value)
    else:
        return None, None
    price = float(script.position.c)
    if stop is not None:
        stop = _price_round(stop, sign)
        price = max(price, stop) if sign > 0 else min(price, stop)
    if limit is not None:
        limit = _price_round(limit, -sign)
        price = min(price, limit) if sign > 0 else max(price, limit)
    return (money, factor, fee), price


def _budget_quantity(budget, price):
    money, factor, fee = budget
    cost = price * factor + fee
    return money / cost if 0.0 < cost < float("inf") else 0.0

# noinspection PyProtectedMember
def _size_add(qty_a: float, qty_b: float) -> float:
    """Combine lot-normalized quantities without leaving floating-point dust."""
    rfactor = syminfo._size_round_factor  # noqa
    lots_a, lots_b = qty_a * rfactor, qty_b * rfactor
    if not (abs(lots_a) < 9007199254740992.0 and abs(lots_b) < 9007199254740992.0):
        return qty_a + qty_b
    rounded_a, rounded_b = round(lots_a), round(lots_b)
    # Risk limits can supply off-grid sizes; do not change those quantities here.
    if (abs(lots_a - rounded_a) > abs(lots_a) * 1e-12 + 1e-9
            or abs(lots_b - rounded_b) > abs(lots_b) * 1e-12 + 1e-9):
        return qty_a + qty_b
    # Keep _size_round's existing float representation, not a new unit grid.
    return (rounded_a + rounded_b) / rfactor


# noinspection PyProtectedMember
def _size_round(qty: float) -> float:
    """
    Round a size down to the nearest tradable lot.

    :param qty: The quantity to round
    :return: The rounded quantity
    """
    rfactor = syminfo._size_round_factor  # noqa
    scaled = abs(qty) * rfactor
    nearest = round(scaled)
    lots = nearest if abs(scaled - nearest) <= scaled * 1e-12 + 1e-9 else int(scaled)
    sign = 1 if qty > 0 else -1
    return sign * lots / rfactor


# noinspection PyShadowingNames
def _tick_snap(price: float) -> float:
    """Round simulated prices to ticks without changing raw indicator inputs."""
    if not (price == price):
        return price
    mintick = syminfo.mintick
    return floor(price / mintick + 0.5) * mintick


def _price_round(price: float | NA[float], direction: int | float) -> float | NA[float]:
    """
    Round price down/up to the tick grid, allowing near-boundary float residue.

    :param price: The price to round
    :param direction: The direction of the price
    :return:
    """
    if isinstance(price, NA):
        return na_float
    pricescale = syminfo.pricescale
    minmove = syminfo.minmove
    ticks = price * pricescale / minmove
    # Upstream's TV-measured tolerance is in ticks, not absolute price units.
    if direction < 0:
        return floor(ticks + 1e-4) * minmove / pricescale
    return ceil(ticks - 1e-4) * minmove / pricescale


# noinspection PyShadowingBuiltins,PyProtectedMember
def cancel(id: str):
    """
    Cancels a pending or unfilled order with a specific identifier

    :param id: The identifier of the order to cancel
    """
    if lib._lib_semaphore:
        return

    position = lib._script.position
    position._remove_order_by_id(id)


# noinspection PyProtectedMember
def cancel_all():
    """
    Cancels all pending or unfilled orders
    """
    if lib._lib_semaphore:
        return
    position = lib._script.position
    for order in list(position.entry_orders.values()) + list(position.exit_orders.values()):
        position._remove_order(order)


# noinspection PyProtectedMember,PyShadowingBuiltins,PyShadowingNames
def close(id: str, comment: str | NA[str] = na_str, qty: float | NA[float] = na_float,
          qty_percent: float | NA[float] = na_float, alert_message: str | NA[str] = na_str,
          immediately: bool = False,
          record: bool = False, record_data: str | None = None,
          ai: str | NA[str] = na_str):
    """
    Creates an order to exit from the part of a position opened by entry orders with a specific identifier.

    :param id: The identifier of the entry order to close
    :param comment: Additional notes on the filled order
    :param qty: The number of contracts/lots/shares/units to close when an exit order fills
    :param qty_percent: A value between 0 and 100 representing the percentage of the open trade
                        quantity to close when an exit order fills
    :param alert_message: Custom text for the alert that fires when an order fills.
    :param immediately: If true, the closing order executes on the same tick when the strategy places it
    :param ai: Instruction sent to the configured AI service when the order fills
    """
    if lib._lib_semaphore:
        return

    position = lib._script.position

    if not isinstance(qty, NA) and qty <= 0.0:
        return

    if position.size == 0.0:
        return

    bound_size = position.sign * position._entry_open_ledger.get(id, 0.0)
    if isinstance(qty, NA):
        size = _size_round(-bound_size * (qty_percent * 0.01)) \
            if not isinstance(qty_percent, NA) else -bound_size
    else:
        size = _size_round(-position.sign * min(qty, abs(bound_size)))

    if size == 0.0:
        return

    exit_id = f"Close entry(s) order {id}"
    bar_time = lib._time
    alert_message = f'{{"timestamp": {bar_time}, "message": {alert_message}}}' if alert_message else None
    ai_instruction = None if isinstance(ai, NA) else str(ai).strip() or None
    order = Order(id, size, exit_id=exit_id, order_type=_order_type_close,
                  comment=None if isinstance(comment, NA) else comment,
                  alert_message=None if isinstance(alert_message, NA) else alert_message,
                  ai_instruction=ai_instruction)

    # Add order to position (this will handle orderbook and exit_orders)
    position._add_order(order)
    if immediately:
        position._fill_immediate_close(order)

    if record:
        record_message = (f'{{"time": {str(datetime.fromtimestamp(int(bar_time / 1000)))}, '
                          f'"bar": {order.bar_index}, '
                          f'"comment": "{comment}", '
                          f'"alert": {alert_message}, '
                          f'"data": {record_data}}}')
        write_record(record_message)


# noinspection PyProtectedMember,PyShadowingNames
def close_all(comment: str | NA[str] = na_str, alert_message: str | NA[str] = na_str,
              immediately: bool = False,
              record: bool = False, record_data: str | None = None,
              ai: str | NA[str] = na_str):
    """
    Creates an order to close an open position completely, regardless of the identifiers of the entry
    orders that opened or added to it.

    :param comment: Additional notes on the filled order
    :param alert_message: Custom text for the alert that fires when an order fills
    :param immediately: If true, the closing order executes on the same tick when the strategy places it
    :param record: Whether to record the close data or not
    :param record_data: Extra data to record
    :param ai: Instruction sent to the configured AI service when the order fills
    """
    if lib._lib_semaphore:
        return

    position = lib._script.position
    if position.size == 0.0:
        return

    exit_id = 'Close position order'
    bar_time = lib._time
    alert_message = f'{{"timestamp": {bar_time}, "message": {alert_message}}}' if alert_message else None
    ai_instruction = None if isinstance(ai, NA) else str(ai).strip() or None
    order = Order(None, -position.size, exit_id=exit_id, order_type=_order_type_close,
                  comment=None if isinstance(comment, NA) else comment,
                  alert_message=None if isinstance(alert_message, NA) else alert_message,
                  ai_instruction=ai_instruction)

    # Add order to position (this will handle orderbook and exit_orders)
    position._add_order(order)
    if immediately:
        position._fill_immediate_close(order)

    if record:
        record_message = (f'{{"time": {str(datetime.fromtimestamp(int(bar_time / 1000)))}, '
                          f'"bar": {order.bar_index}, '
                          f'"comment": "{comment}", '
                          f'"alert": {alert_message}, '
                          f'"data": {record_data}}}')
        write_record(record_message)


# noinspection PyProtectedMember,PyShadowingNames,PyShadowingBuiltins
def entry(id: str, direction: direction.Direction, qty: int | float | NA[float] = na_float,
          limit: int | float | None = None, stop: int | float | None = None,
          oca_name: str | None = None, oca_type: _oca.Oca | None = None,
          comment: str | None = None, alert_message: str | None = None,
          record: bool = False, record_data: str | None = None,
          ai: str | None = None):
    """
    Creates a new order to open or add to a position. If an order with the same id already exists
    and is unfilled, this command will modify that order.

    :param id: The identifier of the order
    :param direction: The direction of the order (long or short)
    :param qty: The number of contracts/lots/shares/units to buy or sell
    :param limit: The price at which the order is filled
    :param stop: The price at which the order is filled
    :param oca_name: The name of the order cancel/replace group
    :param oca_type: The type of the order cancel/replace group
    :param comment: Additional notes on the filled order
    :param alert_message: Custom text for the alert that fires when an order fills
    :param record: Whether to record the entry data or not
    :param record_data: Extra data to record
    :param ai: Instruction sent to the configured AI service when the order fills
    """
    if lib._lib_semaphore:
        return

    script = lib._script
    position = script.position

    # Risk management: Check if trading is halted
    if position.risk_halt_trading:
        return

    direction_sign: float = (-1.0 if direction == short else 1.0)

    # Get default qty by script parameters if no qty is specified
    default_qty = isinstance(qty, NA)
    if default_qty:
        default_qty_type = script.default_qty_type
        sizing_price = position.c
        if (default_qty_type != fixed and script.slippage > 0
                and (limit is None or isinstance(limit, NA))
                and (stop is None or isinstance(stop, NA))):
            # Market quantities are fixed at the slipped placement price, not the next open.
            sizing_price += script.slippage * syminfo.mintick * direction_sign
            if sizing_price <= 0.0:
                return
        if default_qty_type == fixed:
            qty = script.default_qty_value

        elif default_qty_type == percent_of_equity:
            default_qty_value = script.default_qty_value
            # TradingView calculates position size so that the total investment
            # (position value + commission) equals the specified percentage of equity
            #
            # For percent commission: total_cost = qty * price * (1 + commission_rate)
            # For cash per contract: total_cost = qty * price + qty * commission_value
            #
            # We want: total_cost = equity * percent
            # So: qty = (equity * percent) / (price * (1 + commission_factor))

            equity_percent = default_qty_value * 0.01
            target_investment = script.position.equity * equity_percent

            # Calculate the commission factor based on commission type
            if script.commission_type == _commission.percent:
                # For percentage commission: qty * price * (1 + commission%)
                commission_multiplier = 1.0 + script.commission_value * 0.01
                qty = target_investment / (sizing_price * syminfo.pointvalue * commission_multiplier)

            elif script.commission_type == _commission.cash_per_contract:
                # For cash per contract: qty * price + qty * commission_value
                # qty * (price + commission_value) = target_investment
                price_plus_commission = sizing_price * syminfo.pointvalue + script.commission_value
                qty = target_investment / price_plus_commission

            elif script.commission_type == _commission.cash_per_order:
                # For cash per order: qty * price + commission_value = target_investment
                # qty = (target_investment - commission_value) / price
                qty = (target_investment - script.commission_value) / (sizing_price * syminfo.pointvalue)
                qty = max(0.0, qty)  # Ensure non-negative

            else:
                # No commission
                qty = target_investment / (sizing_price * syminfo.pointvalue)

        elif default_qty_type == cash:
            default_qty_value = script.default_qty_value
            qty = default_qty_value / (sizing_price * syminfo.pointvalue)

        else:
            raise ValueError("Unknown default qty type: ", default_qty_type)

    qty_budget, qty_price = _price_order_budget(limit, stop, direction_sign) if default_qty else (None, None)
    if qty_budget is not None:
        qty = _budget_quantity(qty_budget, qty_price)

    # qty must be greater than 0
    if qty <= 0.0:
        return

    # We need a signed size instead of qty, the sign is the direction
    size = qty * direction_sign

    size = _size_round(size)
    if size == 0.0:
        return

    flip_qty = 0.0
    if isinstance(limit, NA):
        limit = None
    elif limit is not None:
        # We need negative direction for entry limit orders - NOTE: it is tested
        limit = _price_round(limit, -direction_sign)
    if isinstance(stop, NA):
        stop = None
    elif stop is not None:
        stop = _price_round(stop, direction_sign)

    # If it is not a market order, we should check pyramiding and flip conditions here
    # Market orders are checked at the order processing time
    if limit is not None or stop is not None:
        flippable = position._size_flippable_by_entry()
        # Check if the order has the same direction
        if flippable * direction_sign > 0.0:
            # Check pyramiding limit for entry orders adding to existing position
            if lib._script.pyramiding <= len(position.open_trades):
                # Pyramiding limit reached - don't add the order
                return

        elif flippable != 0.0:
            # TradingView calculates the flip quantity at order creation time,
            # not at execution time. If we have an opposite direction position,
            # we need to add the position size to the order size to flip it.
            # This means the order will first close the existing position,
            # then open a new one in the opposite direction.
            size -= flippable
            flip_qty = abs(flippable)

    bar_time = lib._time
    alert_message = f'{{"timestamp": {bar_time}, "message": {alert_message}}}' if alert_message else None
    ai_instruction = str(ai).strip() if ai is not None else None
    order = Order(id, size, order_type=_order_type_entry, limit=limit, stop=stop,
                  oca_name=oca_name, oca_type=oca_type,
                  comment=None if isinstance(comment, NA) else comment,
                  alert_message=None if isinstance(alert_message, NA) else alert_message,
                  ai_instruction=ai_instruction or None)
    order.qty_budget, order.qty_price, order.flip_qty = qty_budget, qty_price, flip_qty
    # Store in entry_orders dict
    position._add_order(order)

    if record:
        record_message = (f'{{"time": {str(datetime.fromtimestamp(int(bar_time / 1000)))}, '
                          f'"bar": {order.bar_index}, '
                          f'"comment": "{comment}", '
                          f'"alert": {alert_message}, '
                          f'"data": {record_data}}}')
        write_record(record_message)


# noinspection PyShadowingBuiltins,PyProtectedMember,PyShadowingNames,PyUnusedLocal
def exit(id: str, from_entry: str = "",
         qty: float | NA[float] = na_float, qty_percent: float | NA[float] = na_float,
         profit: float | NA[float] = na_float, limit: float | NA[float] = na_float,
         loss: float | NA[float] = na_float, stop: float | NA[float] = na_float,
         trail_price: float | NA[float] = na_float, trail_points: float | NA[float] = na_float,
         trail_offset: float | NA[float] = na_float,
         oca_name: str | NA[str] = na_str, oca_type: _oca.Oca | None = None,
         comment: str | NA[str] = na_str, comment_profit: str | NA[str] = na_str,
         comment_loss: str | NA[str] = na_str, comment_trailing: str | NA[str] = na_str,
         alert_message: str | NA[str] = na_str, alert_profit: str | NA[str] = na_str,
         alert_loss: str | NA[str] = na_str, alert_trailing: str | NA[str] = na_str,
         disable_alert: bool = False):
    """
    Creates an order to exit from a position. If an order with the same id already exists and is unfilled,

    :param id: The identifier of the order
    :param from_entry: The identifier of the entry order to close
    :param qty: The number of contracts/lots/shares/units to close when an exit order fills
    :param qty_percent: A value between 0 and 100 representing the percentage of the open trade quantity to close
    :param profit: The take-profit distance, expressed in ticks
    :param limit: The take-profit price
    :param loss: The stop-loss distance, expressed in ticks
    :param stop: The stop-loss price
    :param trail_price: The price of the trailing stop activation level
    :param trail_points: The trailing stop activation distance, expressed in ticks
    :param trail_offset: The trailing stop offset
    :param oca_name: The name of the order cancel/replace group
    :param oca_type: The type of the order cancel/replace group
    :param comment: Additional notes on the filled order
    :param comment_profit: Additional notes on the filled order
    :param comment_loss: Additional notes on the filled order
    :param comment_trailing: Additional notes on the filled order
    :param alert_message: Custom text for the alert that fires when an order fills
    :param alert_profit: Custom text for the alert that fires when an order fills
    :param alert_loss: Custom text for the alert that fires when an order fills
    :param alert_trailing: Custom text for the alert that fires when an order fills
    :param disable_alert: If true, the alert will not fire when the order fills
    """
    if lib._lib_semaphore:
        return

    script = lib._script
    position = script.position

    if qty < 0.0:
        return

    # noinspection PyProtectedMember,PyShadowingNames
    def _exit(binding):
        existing = binding.orders.get(id)
        if existing is not None and existing.consumed:
            return
        # Consumed siblings keep their share until this entry closes. Reissuing
        # an exit changes its levels, not the size basis of its original entry.
        available = binding.initial_size
        for sibling in binding.orders.values():
            if sibling is not existing:
                available = _size_add(available, -sibling.reserved_size)
        requested = (qty if not isinstance(qty, NA) else
                     binding.initial_size * qty_percent * 0.01 if not isinstance(qty_percent, NA) else available)
        reserved = _size_round(max(0.0, min(requested, available)))
        if reserved == 0.0:
            return
        direction = binding.sign
        size = -direction * reserved

        # Store tick values for later calculation when entry price is known
        profit_ticks = None if isinstance(profit, NA) else profit
        loss_ticks = None if isinstance(loss, NA) else loss
        trail_points_ticks = None if isinstance(trail_points, NA) else trail_points

        limit_price = None if isinstance(limit, NA) or limit is None else _price_round(limit, direction)
        stop_price = None if isinstance(stop, NA) or stop is None else _price_round(stop, -direction)
        trailing_price = None if isinstance(trail_price, NA) or trail_price is None else _price_round(trail_price, -direction)
        trailing_offset = trail_offset
        if isinstance(trail_offset, NA) or trail_offset is None:
            if (trailing_price is not None or trail_points_ticks is not None) and (
                    limit_price is not None or stop_price is not None or
                    profit_ticks is not None or loss_ticks is not None):
                # A fixed exit permits an omitted trailing offset, interpreted as zero ticks.
                trailing_offset = 0.0
            else:
                trailing_price = trail_points_ticks = None

        group = None if isinstance(oca_name, NA) else oca_name

        # Add order
        order = Order(
            binding.entry_id, size, exit_id=id, order_type=_order_type_close,
            limit=limit_price, stop=stop_price,
            trail_price=trailing_price, trail_offset=trailing_offset,
            profit_ticks=profit_ticks, loss_ticks=loss_ticks, trail_points_ticks=trail_points_ticks,
            oca_name=group, oca_type=oca_type if oca_type is not None else _oca.reduce,
            comment=comment, alert_message=alert_message
        )
        # The stop and limit are alternatives of this reserved slice. Sibling
        # slices do not reduce each other unless an explicit OCA group is used.
        order.is_market_order = False
        order.exit_binding, order.reserved_size = binding, reserved
        order.exit_qty = None if isinstance(qty, NA) else qty
        order.exit_percent = None if isinstance(qty_percent, NA) else qty_percent * 0.01
        order.follow_entry_qty = isinstance(qty, NA) and isinstance(qty_percent, NA)
        binding.orders[id] = order
        position._add_order(order)
        position._resolve_close_order_prices(order)
        if existing is not None and order.trail_offset is not None and (
                existing.trail_offset == order.trail_offset and
                existing.trail_points_ticks == order.trail_points_ticks and
                existing.trail_price == order.trail_price):
            order.trail_triggered, order.trail_stop = existing.trail_triggered, existing.trail_stop
        # A new exit first sees the issue tick, never the bar's earlier extrema.
        if binding.price is not None:
            position._advance_trailing(order, position.c)

    entry_ids = [from_entry] if from_entry else list(dict.fromkeys(
        (*position._exit_entries, *position.entry_orders)))
    for entry_id in entry_ids:
        for binding in position._exit_entries.get(entry_id, ()):
            _exit(binding)
        pending = position.entry_orders.get(entry_id)
        if pending is not None:
            if pending.exit_binding is None:
                pending.exit_binding = _ExitBinding(entry_id, pending.sign,
                    max(0.0, _size_add(abs(pending.size), -pending.flip_qty)))
            _exit(pending.exit_binding)


# noinspection PyProtectedMember,PyShadowingNames,PyShadowingBuiltins,PyUnusedLocal
def order(id: str, direction: direction.Direction, qty: int | float | NA[float] = na_float,
          limit: int | float | None = None, stop: int | float | None = None,
          oca_name: str | None = None, oca_type: _oca.Oca | None = None,
          comment: str | None = None, alert_message: str | None = None,
          disable_alert: bool = False):
    """
    Creates a new order to open, add to, or exit from a position. If an unfilled order with
    the same id exists, a call to this command modifies that order.

    Unlike strategy.entry, orders from this command are not affected by the pyramiding parameter
    of the strategy declaration. Strategies can open any number of trades in the same direction
    with calls to this function.

    This command does not automatically reverse open positions. For example, if there is an open
    long position of five shares, an order from this command with a qty of 5 and a direction
    of strategy.short triggers the sale of five shares, which closes the position.

    :param id: The identifier of the order
    :param direction: The direction of the trade (strategy.long or strategy.short)
    :param qty: The number of contracts/shares/lots/units to trade when the order fills
    :param limit: The limit price of the order (creates limit or stop-limit order)
    :param stop: The stop price of the order (creates stop or stop-limit order)
    :param oca_name: The name of the One-Cancels-All (OCA) group
    :param oca_type: Specifies how an unfilled order behaves when another order in the same OCA group executes
    :param comment: Additional notes on the filled order
    :param alert_message: Custom text for the alert that fires when an order fills
    :param disable_alert: If true, the strategy does not trigger an alert when the order fills
    """
    if lib._lib_semaphore:
        return

    script = lib._script
    position = script.position

    # Risk management: Check if trading is halted
    # TODO: investigate if it should be checked here
    if position.risk_halt_trading:
        return

    # Get default qty by script parameters if no qty is specified
    default_qty = isinstance(qty, NA)
    if isinstance(qty, NA):
        default_qty_type = script.default_qty_type
        if default_qty_type == fixed:
            qty = script.default_qty_value

        elif default_qty_type == percent_of_equity:
            default_qty_value = script.default_qty_value
            equity_percent = default_qty_value * 0.01
            target_investment = script.position.equity * equity_percent

            # Calculate the commission factor based on commission type
            if script.commission_type == _commission.percent:
                commission_multiplier = 1.0 + script.commission_value * 0.01
                qty = target_investment / (lib.close * syminfo.pointvalue * commission_multiplier)

            elif script.commission_type == _commission.cash_per_contract:
                price_plus_commission = lib.close * syminfo.pointvalue + script.commission_value
                qty = target_investment / price_plus_commission

            elif script.commission_type == _commission.cash_per_order:
                qty = (target_investment - script.commission_value) / (lib.close * syminfo.pointvalue)
                qty = max(0.0, qty)  # Ensure non-negative

            else:
                # No commission
                qty = target_investment / (lib.close * syminfo.pointvalue)

        elif default_qty_type == cash:
            default_qty_value = script.default_qty_value
            qty = default_qty_value / (lib.close * syminfo.pointvalue)

        else:
            raise ValueError("Unknown default qty type: ", default_qty_type)

    direction_sign: float = (-1.0 if direction == short else 1.0)
    qty_budget, qty_price = _price_order_budget(limit, stop, direction_sign) if default_qty else (None, None)
    if qty_budget is not None:
        qty = _budget_quantity(qty_budget, qty_price)

    # qty must be greater than 0
    if qty <= 0.0:
        return

    # We need a signed size instead of qty, the sign is the direction
    size = qty * direction_sign

    # NOTE: Unlike strategy.entry, strategy.order is NOT affected by pyramiding limit
    # This is a key difference - strategy.order can open unlimited trades in the same direction
    # It uses _order_type_normal to distinguish it from entry/exit orders

    size = _size_round(size)
    if size == 0.0:
        return

    if isinstance(limit, NA):
        limit = None
    elif limit is not None:
        limit = _price_round(limit, -direction_sign)
    if isinstance(stop, NA):
        stop = None
    elif stop is not None:
        stop = _price_round(stop, direction_sign)

    # Create the order with _order_type_normal
    # This is a "normal" order that simply adds to or subtracts from position
    # It doesn't follow entry/exit rules and can freely modify positions
    order = Order(id, size, order_type=_order_type_normal, limit=limit, stop=stop,
                  oca_name=oca_name, oca_type=oca_type, comment=comment,
                  alert_message=alert_message)
    order.qty_budget, order.qty_price = qty_budget, qty_price
    position._add_order(order)


def write_record(message: str) -> None:
    import os
    from datetime import datetime

    if not message:
        return

    file_name = f"record_{datetime.now().strftime('%y-%m-%d')}.txt"
    file_path = os.path.join(os.getcwd(), f"records/{file_name}")

    rt = realtime_trade()

    if not rt:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(message + "\n")
        return

    if rt and lib.bar_index == last_bar_index() - 1:
        last_line = ""
        if os.path.exists(file_path):
            try:
                buffer_size = 1024
                file_size = os.path.getsize(file_path)

                with open(file_path, "rb") as f:
                    if file_size > buffer_size:
                        f.seek(-buffer_size, os.SEEK_END)
                    else:
                        f.seek(0)

                    lines = f.read().splitlines()
                    if lines:
                        last_line = lines[-1].decode("utf-8", errors="ignore")
            except Exception:
                last_line = ""

        if message != last_line:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(message + "\n")

#
# Properties
#

# noinspection PyProtectedMember
@module_property
def equity() -> float | NA[float]:
    return lib._script.position.equity


# noinspection PyProtectedMember
@module_property
def eventrades() -> int | NA[int]:
    return lib._script.position.eventrades


# noinspection PyProtectedMember
@module_property
def initial_capital() -> float:
    return lib._script.initial_capital


# noinspection PyProtectedMember
@module_property
def grossloss() -> float | NA[float]:
    return lib._script.position.grossloss + lib._script.position.open_commission


# noinspection PyProtectedMember
@module_property
def grossprofit() -> float | NA[float]:
    return lib._script.position.grossprofit


# noinspection PyProtectedMember
@module_property
def losstrades() -> int:
    return lib._script.position.losstrades


# noinspection PyProtectedMember
@module_property
def max_drawdown() -> float | NA[float]:
    return lib._script.position.max_drawdown


# noinspection PyProtectedMember
@module_property
def max_drawdown_percent() -> float | NA[float]:
    return lib._script.position.max_drawdown_percent


# noinspection PyProtectedMember
@module_property
def max_runup() -> float | NA[float]:
    return lib._script.position.max_runup


# noinspection PyProtectedMember
@module_property
def max_runup_percent() -> float | NA[float]:
    return lib._script.position.max_runup_percent


# noinspection PyProtectedMember
@module_property
def netprofit() -> float | NA[float]:
    return lib._script.position.netprofit


# noinspection PyProtectedMember
@module_property
def openprofit() -> float | NA[float]:
    return lib._script.position.openprofit


# noinspection PyProtectedMember
@module_property
def position_size() -> float | NA[float]:
    return lib._script.position.size


# noinspection PyProtectedMember
@module_property
def position_avg_price() -> float | NA[float]:
    return lib._script.position.avg_price


# noinspection PyProtectedMember
@module_property
def wintrades() -> int | NA[int]:
    return lib._script.position.wintrades


# noinspection PyProtectedMember
@module_property
def realtime_trade() -> bool | NA[bool]:
    return lib._script.realtime_trade


# noinspection PyProtectedMember
@module_property
def last_bar_index() -> int | NA[int]:
    return lib._script.last_bar_index


# noinspection PyProtectedMember
@module_property
def get_webhook_url() -> str | NA[str]:
    return lib._script.webhook_url


# noinspection PyProtectedMember
@module_property
def get_custom_inputs() -> dict | NA[dict]:
    return lib._script.custom_inputs

# noinspection PyProtectedMember
@module_property
def get_script_title() -> str | NA[str]:
    return lib._script.title


# noinspection PyProtectedMember
@module_property
def telegram_notification() -> tuple[bool | NA[bool], str | NA[str], str | NA[str]]:
    return lib._script.telegram_notification, lib._script.telegram_token, lib._script.telegram_chat_id


# noinspection PyProtectedMember
@module_property
def pre_run() -> bool | NA[bool]:
    return lib._script.pre_run
