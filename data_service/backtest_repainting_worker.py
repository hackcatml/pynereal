"""No live feeds or delivery callbacks: a single prefix-only verification process."""
from __future__ import annotations

import argparse
import bisect
from contextlib import nullcontext, redirect_stdout
import json
from pathlib import Path
import sys
import tempfile
import time

from data_service.backtest_repainting import (
    RESULT, SNAPSHOT, attach_fill_recorder, compare_fills, extract_snapshot, write_json,
)


def verify(job_dir: Path, start: int | None, emit, work_dir: Path | None = None):
    from pynecore.core.ohlcv_file import OHLCVReader
    from pynecore.core.script_runner import ScriptRunner
    from pynecore.core.syminfo import SymInfo

    began = time.monotonic()
    emit({'status': 'preparing', 'done': 0})
    workspace = nullcontext(str(work_dir)) if work_dir else tempfile.TemporaryDirectory(prefix='pynereal-repainting-')
    with workspace as temporary:
        root = Path(temporary)
        manifest = extract_snapshot(job_dir / SNAPSHOT, root)
        baseline = json.loads((root / 'baseline.json').read_text())
        with OHLCVReader(root / 'data.ohlcv') as reader:
            # This file already contains exactly the candles used by the backtest.
            candles = list(reader.read_from(int(reader.start_datetime.timestamp()),
                                           int(reader.end_datetime.timestamp()), skip_zero_volume=False))
        if not candles:
            raise ValueError('The saved backtest has no candles')
        times = [int(c.timestamp) for c in candles]
        if start is not None and not times[0] <= start <= times[-1]:
            raise ValueError('Select a start time within the backtested range')
        first = 0 if start is None else bisect.bisect_left(times, start)
        syminfo = SymInfo.load_toml(root / 'data.toml')
        script = root / 'scripts' / manifest['script_path']
        sys.path.insert(0, str(root / 'scripts' / 'lib'))
        events = []
        runner = None
        try:
            # Warm up only before the requested start. SecurityContext extends this
            # private prefix when each subsequent chart candle becomes available.
            runner = ScriptRunner(script, iter(candles), syminfo,
                                  last_bar_index=max(0, first - 1), custom_inputs={},
                                  preload_ohlcv=candles[:first],
                                  realtime_config={'pyne': {'no_progress': True, 'no_report': True},
                                                   'realtime': {'enabled': False},
                                                   'webhook': {'enabled': False, 'telegram_notification': False}})
            if runner.script.position is None:
                raise ValueError('Repainting verification requires a strategy with entry/exit records')
            attach_fill_recorder(runner, events)
            if manifest['magnifier']:
                def minutes():
                    with OHLCVReader(root / 'minutes.ohlcv') as reader:
                        yield from reader.read_from(int(reader.start_datetime.timestamp()),
                                                    int(reader.end_datetime.timestamp()), skip_zero_volume=False)
                runner.set_bar_magnifier(minutes(), source_seconds=60, source='saved-backtest')
            last_progress = 0.0
            for index in range(len(candles)):
                if index >= first:
                    runner.last_bar_index = runner.script.last_bar_index = index
                if runner.step() is None:
                    raise ValueError('Sequential calculation ended before the saved data')
                now = time.monotonic()
                if now - last_progress >= .5 or index + 1 == len(candles):
                    emit({'status': 'running', 'phase': 'warmup' if index < first else 'sequential',
                          'done': index + 1, 'total': len(candles), 'time': times[index]})
                    last_progress = now
            findings = compare_fills(baseline, events, times[first])
            return {'status': 'completed', 'mode': 'full' if start is None else 'from_time',
                    'start': times[first], 'end': times[-1], 'warmup_bars': first,
                    'total': len(candles), 'done': len(candles),
                    'historical_fills': sum(e['time'] >= times[first] for e in baseline),
                    'sequential_fills': sum(e['time'] >= times[first] for e in events),
                    'findings': findings, 'first_difference': findings[0]['time'] if findings else None,
                    'elapsed_seconds': time.monotonic() - began,
                    'scope': 'Historical vs sequential executed entries/exits; not live pre_run replay or intrabar reconstruction.'}
        finally:
            if runner is not None:
                runner.destroy()
            sys.path.remove(str(root / 'scripts' / 'lib'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-dir', type=Path, required=True)
    parser.add_argument('--start', type=int)
    parser.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    protocol = sys.stdout
    def emit(value):
        protocol.write(json.dumps(value, allow_nan=False) + '\n')
        protocol.flush()
    try:
        with redirect_stdout(sys.stderr):
            result = verify(args.job_dir, args.start, emit, args.work_dir)
        write_json(args.job_dir / RESULT, result)
        emit({'status': 'completed'})
        return 0
    except Exception as exc:
        emit({'status': 'failed', 'error': str(exc)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
