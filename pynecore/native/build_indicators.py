"""Build the optional indicator extension, used by default when available on the import path."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import tempfile


MODULE = '_pynereal_native_ta'
MANIFEST = MODULE + '.build.json'


def build(output: Path) -> Path:
    from setuptools import Distribution, Extension
    from setuptools.command.build_ext import build_ext

    flags = ['/O2', '/std:c++17', '/fp:strict'] if os.name == 'nt' else [
        '-O3', '-std=c++17', '-ffp-contract=off', '-fno-fast-math', '-fno-unsafe-math-optimizations']
    source = Path(__file__).with_name('indicators.cpp')
    extension = Extension(MODULE, [str(source)], language='c++', extra_compile_args=flags,
                          depends=[str(source.with_name('finite_fsum.h'))])
    with tempfile.TemporaryDirectory(prefix='pynereal-native-ta-') as temporary:
        command = build_ext(Distribution({'name': 'pynereal-native-ta', 'ext_modules': [extension]}))
        command.initialize_options()
        command.build_lib = str(output.resolve())
        command.build_temp = temporary
        command.finalize_options()
        command.run()
        return Path(command.get_ext_fullpath(MODULE))


def fingerprint() -> str:
    digest = hashlib.sha256()
    for name in ('build_indicators.py', 'indicators.cpp', 'finite_fsum.h'):
        digest.update(Path(__file__).with_name(name).read_bytes())
    environment = [sys.version, sysconfig.get_platform(), platform.machine(),
                   sysconfig.get_config_var('EXT_SUFFIX')]
    for name in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS'):
        environment.extend((sysconfig.get_config_var(name), os.environ.get(name)))
    digest.update(json.dumps(environment).encode())
    return digest.hexdigest()


def verify(binary: Path) -> None:
    # A fresh interpreter checks the new binary, not a previously imported module.
    subprocess.run([sys.executable, '-I', '-c',
                    'import importlib.util, sys; '
                    'sys.path.insert(0, sys.argv[2]); '
                    's = importlib.util.spec_from_file_location("_pynereal_native_ta", sys.argv[1]); '
                    'm = importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                    'assert m.State(0, 501).step(1.0, 1, 0, None) == 1.0',
                    str(binary), str(Path(__file__).resolve().parents[2])],
                   check=True, timeout=30, capture_output=True, text=True)


def remove_installed(output: Path) -> None:
    for pattern in (MODULE + '*.so', MODULE + '*.pyd', MANIFEST):
        for path in output.glob(pattern):
            path.unlink(missing_ok=True)


def install(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    binary = output / (MODULE + sysconfig.get_config_var('EXT_SUFFIX'))
    manifest = output / MANIFEST
    current = fingerprint()
    try:
        previous = json.loads(manifest.read_text(encoding='utf-8'))
        if (previous.get('source') == current and binary.is_file()
                and previous.get('binary') == hashlib.sha256(binary.read_bytes()).hexdigest()):
            verify(binary)
            print(f'[native] up to date: {binary}', flush=True)
            return binary
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        pass

    # Never leave an older kernel active if rebuilding changed sources fails.
    remove_installed(output)
    try:
        with tempfile.TemporaryDirectory(prefix='pynereal-native-install-', dir=output) as temporary:
            built = build(Path(temporary))
            verify(built)
            metadata = Path(temporary) / MANIFEST
            metadata.write_text(json.dumps({
                'source': current,
                'binary': hashlib.sha256(built.read_bytes()).hexdigest(),
            }), encoding='utf-8')
            built.replace(binary)
            metadata.replace(manifest)
    except Exception:
        remove_installed(output)
        raise
    print(f'[native] installed: {binary}', flush=True)
    return binary


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path)
    mode.add_argument('--install', action='store_true', help='Install into this Python environment')
    args = parser.parse_args()
    if args.install:
        try:
            install(Path(sysconfig.get_path('platlib')))
        except Exception as exc:
            detail = (getattr(exc, 'stderr', None) or str(exc))[-4000:]
            print(f'[native] build failed: {detail}\n'
                  '[native] Python indicators will be used. Install a C++17 compiler and matching '
                  'Python development headers, then retry with --install.', file=sys.stderr)
            return 1
    else:
        print(build(args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
