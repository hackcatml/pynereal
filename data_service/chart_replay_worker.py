"""Private, sequential replay engine. Only JSON protocol records go to stdout."""
from __future__ import annotations

import argparse
import bisect
import contextlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

from data_service.backtest_worker import _copy_script_snapshot, _plot_option


def wire(value):
    if isinstance(value, dict):
        return {str(k): wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return str(value) or None


class Replay:
    def __init__(self, request: dict, emit):
        from pynecore.core.exchange_policy import tradingview_hides_zero_volume
        from pynecore.core.ohlcv_file import OHLCVReader
        from pynecore.core.script_runner import ScriptRunner
        from pynecore.core.syminfo import SymInfo

        self.emit = emit
        self.runner = None
        self.index = -1
        self.cursor = -1
        self.frames = []
        self.plot_versions = []
        self.options = {}
        self.events = []
        self.root = Path(request['root'])
        source = Path(request['data'])
        snapshot = self.root / 'data.ohlcv'
        # Live OHLCV files can be replaced or extended while the worker starts.
        for _ in range(10):
            before = source.stat()
            shutil.copyfile(source, snapshot)
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) == (
                    after.st_ino, after.st_size, after.st_mtime_ns):
                break
            time.sleep(.05)
        else:
            raise ValueError('Candle history is changing; retry replay shortly')
        syminfo = SymInfo.load_toml(Path(request['metadata']))
        with OHLCVReader(snapshot) as reader:
            self.candles = list(reader.read_from(
                int(reader.start_datetime.timestamp()), int(reader.end_datetime.timestamp()),
                skip_zero_volume=tradingview_hides_zero_volume(syminfo.prefix)))
        # The final on-disk candle may still be open. Only completed candles are replayed.
        from pynecore.core.candle_timeframe import candle_timeframe
        tf = candle_timeframe(request['market']['timeframe'])
        now = int(request['now'])
        self.candles = [c for c in self.candles if tf.shift(c.timestamp) <= now]
        if not self.candles:
            raise ValueError('No completed candles available for replay')
        self.times = [int(c.timestamp) for c in self.candles]
        target = bisect.bisect_right(self.times, int(request['time'])) - 1
        if target < 0 or int(request['time']) > self.times[-1]:
            raise ValueError('Select a completed candle within the available history')
        self.visible_from = self.times[target] - 60 * 86400
        self.start_index = target
        self.first_frame_index = bisect.bisect_left(self.times, self.visible_from)

        scripts_root = Path(request['scripts'])
        script = Path(request['script'])
        _copy_script_snapshot(scripts_root, script, self.root / 'scripts')
        script = self.root / 'scripts' / script.relative_to(scripts_root)
        sys.path.insert(0, str(self.root / 'scripts' / 'lib'))
        config = {'pyne': {'no_progress': True, 'no_report': True},
                  'realtime': {'enabled': False},
                  'webhook': {'enabled': False, 'telegram_notification': False}}
        # Warm up as of the selected cursor, then reveal one new candle per step.
        # Keep a separate prefix: SecurityContext appends to this list during replay.
        self.runner = ScriptRunner(script, iter(self.candles), syminfo,
                                   last_bar_index=target, realtime_config=config,
                                   custom_inputs={}, preload_ohlcv=self.candles[:target + 1])
        self.runner.script.last_bar_index = target
        self.runner.script.on_plot_callback = self.on_plot
        self.runner.script.on_plotchar_callback = lambda event: self.events.append(
            {'type': 'plotchar', **event})
        position = self.runner.script.position
        if position is not None:
            position.on_entry_callback = lambda trade: self.on_trade(trade, False)
            position.on_close_callback = lambda trade: self.on_trade(trade, True)
        if self.runner.script.use_bar_magnifier:
            from data_service.backtest_magnifier import attach_shared_minutes
            attach_shared_minutes(self.runner, scripts_root.parent / 'data', request['market'],
                                  self.times[0], now)
        self.emit({'type': 'preparing', 'total': target + 1, 'done': 0,
                   'first': self.times[0], 'last': self.times[-1]})
        batch = []
        for index in range(target + 1):
            capture = self.times[index] >= self.visible_from
            frame = self.step(capture=capture)
            if capture:
                batch.append(frame)
            if len(batch) >= 200:
                self.emit({'type': 'history', 'frames': batch})
                batch = []
            if index % 2000 == 0:
                self.emit({'type': 'preparing', 'total': target + 1, 'done': index + 1})
        if batch:
            self.emit({'type': 'history', 'frames': batch})
        self.emit_ready()

    @property
    def finished(self):
        return self.cursor + 1 >= len(self.candles)

    @property
    def current_plots(self):
        return self.plot_versions[self.cursor - self.start_index]

    def emit_ready(self):
        self.emit({'type': 'ready', 'time': self.times[self.cursor], 'done': self.finished,
                   'plots': self.current_plots, 'title': self.runner.script.title,
                   'rewind_from': self.times[self.start_index], 'rewind_to': self.times[self.index]})

    def seek(self, timestamp):
        target = bisect.bisect_right(self.times, timestamp) - 1
        # Warm-up history can contain later lookahead values. Only revisit the
        # actual replay timeline, preserving the engine at its computed frontier.
        if target < self.start_index or timestamp > self.times[self.index]:
            raise ValueError('Replay seek is outside the computed replay range')
        self.cursor = target
        self.emit({'type': 'history_reset'})
        begin = bisect.bisect_left(self.times, self.times[target] - 60 * 86400)
        end = target - self.first_frame_index + 1
        for offset in range(begin - self.first_frame_index, end, 200):
            self.emit({'type': 'history', 'frames': self.frames[offset:min(offset + 200, end)]})
        self.emit_ready()

    def on_plot(self, event):
        title = str(event.get('title') or 'Plot')
        order = self.options.get(title, {}).get('order', len(self.options))
        self.options[title] = _plot_option(event, order)

    def on_trade(self, trade, closing):
        self.events.append({
            'type': 'trade_close' if closing else 'trade_entry',
            'time': int((trade.exit_time if closing else trade.entry_time) / 1000),
            'price': float(trade.exit_price if closing else trade.entry_price),
            'size': float(trade.size), 'id': trade.entry_id,
            'exit_id': trade.exit_id if closing else '',
            'comment': (trade.exit_comment if closing else trade.entry_comment) or '',
        })

    def step(self, *, capture=True):
        if self.finished:
            return None
        if self.cursor < self.index:
            self.cursor += 1
            return self.frames[self.cursor - self.first_frame_index]
        self.events = []
        self.index += 1
        # A cursor is the last known bar, not the last bar of the saved data file.
        if self.index > self.runner.last_bar_index:
            self.runner.last_bar_index = self.index
            self.runner.script.last_bar_index = self.index
        result = self.runner.step()
        if result is None:
            raise RuntimeError('Replay iterator ended unexpectedly')
        candle, plots = result[:2]
        self.cursor = self.index
        for title in plots:
            if title not in self.options:
                self.options[title] = _plot_option({'title': title}, len(self.options))
        if not capture:
            return None
        frame = wire({'bar': dict(zip(('time', 'open', 'high', 'low', 'close', 'volume'), candle[:6])),
                      'values': {k: None if v == '' else v for k, v in plots.items()},
                      'events': self.events})
        self.frames.append(frame)
        if self.index >= self.start_index:
            options = wire(list(self.options.values()))
            if self.plot_versions and self.plot_versions[-1] == options:
                options = self.plot_versions[-1]
            self.plot_versions.append(options)
        return frame

    def close(self):
        if self.runner is not None:
            self.runner.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('request')
    args = parser.parse_args()
    protocol = sys.stdout

    def emit(payload):
        protocol.write(json.dumps(wire(payload), ensure_ascii=False, allow_nan=False) + '\n')
        protocol.flush()

    os.environ['PYNE_NO_COLOR_LOG'] = '1'
    replay = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            replay = Replay(json.loads(Path(args.request).read_text()), emit)
            for line in sys.stdin:
                command = json.loads(line)
                if command.get('type') == 'step':
                    frame = replay.step()
                    emit({'type': 'frame', 'frame': frame, 'done': replay.finished,
                          'plots': replay.current_plots})
                elif command.get('type') == 'seek':
                    replay.seek(command['time'])
                elif command.get('type') == 'stop':
                    break
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit({'type': 'error', 'error': f'{type(exc).__name__}: {exc}'})
        return 1
    finally:
        if replay is not None:
            with contextlib.redirect_stdout(sys.stderr):
                replay.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
