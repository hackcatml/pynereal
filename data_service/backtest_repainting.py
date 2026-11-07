"""Frozen backtest inputs and comparison of historical versus prefix-only fills."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import zipfile


SNAPSHOT = 'repainting_inputs.zip'
RESULT = 'repainting_result.json'


def write_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def engine_identity():
    import pynecore
    from pynecore.core import native_indicators
    root = Path(pynecore.__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    # A native binary and its opt-out flag are part of the calculation identity.
    for path in sorted(root.rglob('*.so')) + sorted(root.rglob('*.pyd')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    if native_indicators.State is not None:
        import _pynereal_native_ta
        digest.update(Path(_pynereal_native_ta.__file__).read_bytes())
    return {'source': digest.hexdigest(), 'python': sys.version,
            'native_loaded': native_indicators.State is not None,
            'native': os.environ.get('PYNECORE_NATIVE_INDICATORS', '1')}


def attach_fill_recorder(runner, events: list):
    def record(trade, closing):
        event = {
            'action': 'exit' if closing else 'entry',
            'time': int((trade.exit_time if closing else trade.entry_time) / 1000),
            'order_id': str(trade.entry_id or ''),
            'exit_id': str(trade.exit_id or '') if closing else '',
            'size': float(trade.size),
            'price': float(trade.exit_price if closing else trade.entry_price),
            'entry_time': int(trade.entry_time / 1000),
            'comment': str((trade.exit_comment if closing else trade.entry_comment) or ''),
        }
        if not all(math.isfinite(event[key]) for key in ('size', 'price')):
            raise ValueError('Non-finite execution record')
        events.append(event)

    position = runner.script.position
    if position is not None:
        position.on_entry_callback = lambda trade: record(trade, False)
        position.on_close_callback = lambda trade: record(trade, True)


def save_snapshot(job_dir, runtime, script_path, events, engine, *, magnifier=False):
    """Retain only the files actually used, never the live workspace or credentials."""
    manifest = {'version': 1, 'script_path': script_path, 'engine': engine,
                'magnifier': magnifier, 'comparison': 'executed_entries_and_exits'}
    paths = [runtime / 'data.ohlcv', runtime / 'data.toml']
    paths.extend(path for path in (runtime / 'scripts').rglob('*')
                 if path.is_file() and path.suffix in ('.py', '.toml') and '__pycache__' not in path.parts)
    if magnifier:
        paths.append(runtime / 'minutes.ohlcv')
    destination = job_dir / SNAPSHOT
    temporary = destination.with_suffix('.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in paths:
                archive.write(path, str(path.relative_to(runtime)))
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr('baseline.json', json.dumps(events, allow_nan=False))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def extract_snapshot(archive_path, destination):
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or info.is_dir():
                raise ValueError('Invalid repainting snapshot path')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
    manifest = json.loads((destination / 'manifest.json').read_text())
    script = (destination / 'scripts' / manifest['script_path']).resolve()
    if manifest.get('version') != 1 or not script.is_relative_to((destination / 'scripts').resolve()):
        raise ValueError('Unsupported repainting snapshot')
    if manifest['engine'] != engine_identity():
        raise ValueError('The engine has changed since this backtest. Run the backtest again before verification.')
    return manifest


def compare_fills(baseline, sequential, start):
    """Pair identical fills first; never guess that unrelated orders were moved."""
    groups = defaultdict(lambda: [[], []])
    for source, events in enumerate((baseline, sequential)):
        for event in events:
            if event['time'] >= start:
                key = (event['time'], event['action'], event['order_id'], event['exit_id'])
                groups[key][source].append(event)

    def differences(a, b):
        changed = {}
        for key in ('size', 'price'):
            if not math.isclose(a[key], b[key], rel_tol=1e-10, abs_tol=1e-12):
                changed[key] = {'historical': a[key], 'sequential': b[key]}
        if a['action'] == 'exit' and a['entry_time'] != b['entry_time']:
            changed['entry_time'] = {'historical': a['entry_time'], 'sequential': b['entry_time']}
        return changed

    findings = []
    for key, (left, right) in sorted(groups.items()):
        remaining = []
        for a in left:
            match = next((i for i, b in enumerate(right) if not differences(a, b)), None)
            if match is None:
                remaining.append(a)
            else:
                right.pop(match)
        count = max(len(remaining), len(right))
        for i in range(count):
            a = remaining[i] if i < len(remaining) else None
            b = right[i] if i < len(right) else None
            findings.append({'time': key[0], 'kind': 'changed' if a and b else
                             'historical_only' if a else 'sequential_only',
                             'historical': a, 'sequential': b,
                             'differences': differences(a, b) if a and b else {}})
    for index, finding in enumerate(findings):
        finding['id'] = index
        finding['first'] = index == 0
    return findings
