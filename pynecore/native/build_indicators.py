"""Build the optional indicator extension, used by default when available on the import path."""
import argparse
import os
from pathlib import Path
import tempfile

from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    flags = ['/O2', '/std:c++17', '/fp:strict'] if os.name == 'nt' else [
        '-O3', '-std=c++17', '-ffp-contract=off', '-fno-fast-math', '-fno-unsafe-math-optimizations']
    source = Path(__file__).with_name('indicators.cpp')
    extension = Extension('_pynereal_native_ta', [str(source)], language='c++', extra_compile_args=flags,
                          depends=[str(source.with_name('finite_fsum.h'))])
    with tempfile.TemporaryDirectory(prefix='pynereal-native-ta-') as temporary:
        command = build_ext(Distribution({'name': 'pynereal-native-ta', 'ext_modules': [extension]}))
        command.initialize_options()
        command.build_lib = str(args.output.resolve())
        command.build_temp = temporary
        command.finalize_options()
        command.run()
        for output in command.get_outputs():
            print(output)


if __name__ == '__main__':
    main()
