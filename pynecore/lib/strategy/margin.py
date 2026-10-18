"""Broker-emulator margin checkpoints, adapted from PyneCore's TV-tested rules."""
from math import floor

from pynecore import lib
from pynecore.lib import syminfo


class MarginState:
    __slots__ = ('deferred', 'live_calls', 'fills', 'last_fill_price', 'bar_size', 'bar_equity')

    def __init__(self):
        self.deferred = None
        self.live_calls = []
        self.fills = 0
        self.last_fill_price = None
        self.bar_size = self.bar_equity = 0.0

    def begin_bar(self, position):
        self.bar_size, self.bar_equity = position.size, position.equity

    @staticmethod
    def _exceeds(position, order, price, base_size, equity):
        script = lib._script
        ratio = (script.margin_short if order.sign < 0 else script.margin_long) * 0.01
        if ratio <= 0:
            return False
        required = abs(base_size + order.size) * price * syminfo.pointvalue * ratio
        tick = syminfo.mintick
        if equity >= 1e7 and tick > 0:
            return floor(equity / tick + 0.5) < floor(required / tick)
        return required - equity > abs(equity) * 1e-11

    def allows_fill(self, position, order, price):
        from . import _order_type_entry
        remaining = position.size + order.size
        if position.size * order.size < 0 and remaining * position.size >= 0:
            return True
        if not self._exceeds(position, order, price, position.size, position.equity):
            return True
        if order.order_type == _order_type_entry and self.bar_size * order.sign < 0:
            return True
        return position.size != self.bar_size and not self._exceeds(
            position, order, price, self.bar_size, self.bar_equity)

    def reset_flat(self):
        self.live_calls.clear()
        self.deferred = None

    def check(self, position, price, *, can_defer=False, fill_mark=None):
        if not position.size:
            return False
        script = lib._script
        ratio = (script.margin_short if position.sign < 0 else script.margin_long) * 0.01
        if ratio <= 0:
            return False
        fills = self.fills
        deferred = self._check_at(position, price, ratio, can_defer)
        if fill_mark is not None and not deferred and self.fills == fills and position.size:
            self._check_at(position, price, ratio, False, fill_mark)
        if not deferred and position.size and self.live_calls:
            for size, done in tuple(self.live_calls):
                for trade in tuple(position.open_trades):
                    if trade not in done and trade in position.open_trades:
                        done.add(trade)
                        self._fill(position, min(size, abs(trade.size)), price, trade)
        return deferred

    def _check_at(self, position, price, ratio, can_defer, mark_price=None):
        quantity = abs(position.size)
        mark = price if mark_price is None else mark_price
        pv = syminfo.pointvalue
        if mark <= 0 or pv <= 0:
            return False
        money_spent = quantity * position.avg_price * pv
        value = quantity * mark * pv
        equity = lib._script.initial_capital + position.netprofit + position.sign * (value - money_spent)
        required = value * ratio
        available = equity - required
        mintick = syminfo.mintick
        big_margin = False
        if equity >= 1e7 and mintick > 0:
            equity_ticks = floor(equity / mintick)
            margin_ticks = required / mintick
            big_margin = margin_ticks >= 1e10
            margin_ticks = 10.0 * round(margin_ticks / 10.0) if big_margin else floor(margin_ticks + 0.5)
            if equity_ticks >= margin_ticks:
                return False
            if big_margin:
                available = (equity_ticks - margin_ticks) * mintick
        elif available >= 0:
            return False

        factor = syminfo._size_round_factor
        cover_lots = max(0, int(abs(available) / ratio / (mark * pv) * factor))
        if cover_lots == 0 and mark_price is not None:
            return False
        if cover_lots == 0 and factor > 1:
            lots, size = 0, min(1.0, quantity)
        else:
            lots = max(1, cover_lots * 4)
            size = min(lots / factor, quantity)
        if can_defer and lots == 1 and price != position.c:
            close_value = quantity * position.c * pv
            close_equity = lib._script.initial_capital + position.netprofit + position.sign * (close_value - money_spent)
            if close_equity - close_value * ratio < 0:
                self.deferred = position.sign
                return True
        self._liquidate(position, size, price)
        return False

    def _liquidate(self, position, size, price):
        if lib._script.close_entries_rule == 'ANY':
            trades = tuple(position.open_trades)
            self.live_calls.append((size, set(trades)))
            for trade in trades:
                if trade in position.open_trades:
                    self._fill(position, min(size, abs(trade.size)), price, trade)
        else:
            self._fill(position, size, price)

    @staticmethod
    def _fill(position, size, price, trade=None):
        from . import Order, _order_type_close
        order = Order(None, -position.sign * size, order_type=_order_type_close,
                      exit_id='Margin call', comment='Margin call')
        order.target_trade = trade
        order.bar_index = int(lib.bar_index)
        order.is_market_order = False
        price -= position.sign * lib._script.slippage * syminfo.mintick
        position._fill_order(order, price, price, price)

    def after_script(self, position):
        start = len(position.new_closed_trades)
        deferred, self.deferred = self.deferred, None
        if deferred is not None and position.sign == deferred:
            self.check(position, position.c)
        if position.size:
            self.check(position, position.c)
        if len(position.new_closed_trades) > start:
            position._mark_close_fills(start)
