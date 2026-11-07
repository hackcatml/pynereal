"""Opt-in whole numerical helper compilation; unsupported functions stay Python.

This experimental path is deliberately disabled unless PYNECORE_NATIVE_FUNCTIONS=1.
Generated extensions are cached separately from installed indicator extensions.
"""
from __future__ import annotations

import ast
import atexit
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import tempfile

from .native_codegen import plans

VERSION = 'nf1'
PROGRAMS = {}
REPORTS = []
NATIVE = Path(__file__).resolve().parents[1] / 'native'


def enabled():
    return os.environ.get('PYNECORE_NATIVE_FUNCTIONS') == '1'


def restore_indicator(state):
    """Reuse the existing lossless native-to-Python indicator handoff."""
    from pynecore import lib
    from .native_indicators import _LegacyIndicator
    names=('sma','ema','rma','rsi','wma','highest','lowest','valuewhen','sum','variance','stdev')
    name=names[state['kind']]
    function=getattr(lib.math if name=='sum' else lib.ta,name)
    if name in ('highest','lowest'):
        from .overload import _registry
        function=next(item.func for item in _registry['pynecore.lib.ta.'+name] if item.min_args==2)
    originals=function.__globals__['_originals']
    return _LegacyIndicator(name,originals,state)


def fingerprint():
    digest=hashlib.sha256()
    for path in (Path(__file__), Path(__file__).with_name('native_codegen.py'),
                 *sorted(NATIVE.glob('function_*.hpp')), NATIVE/'indicators.cpp', NATIVE/'finite_fsum.h'):
        digest.update(path.read_bytes())
    digest.update(repr((sys.version,platform.machine(),platform.system(),sysconfig.get_config_var('SOABI'))).encode())
    return digest.hexdigest()


def build(name, code):
    cache=Path(os.environ.get('PYNECORE_NATIVE_FUNCTION_CACHE',str(Path(tempfile.gettempdir())/'pynereal-native-functions')))
    cache.mkdir(parents=True,exist_ok=True)
    target=cache/(name+sysconfig.get_config_var('EXT_SUFFIX'))
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix='build-',dir=cache) as temporary:
            folder=Path(temporary)
            source=folder/'functions.cpp'
            source.write_text(code)
            command='''import sys
from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext
name,source,include,output=sys.argv[1:]
extension=Extension(name,[source],include_dirs=[include],language='c++',extra_compile_args=['-O3','-std=c++17','-ffp-contract=off','-fno-fast-math','-fno-unsafe-math-optimizations'])
cmd=build_ext(Distribution({'name':'pynereal-native-functions','ext_modules':[extension]}))
cmd.initialize_options()
cmd.build_lib=output
cmd.build_temp=output+'/objects'
cmd.finalize_options()
cmd.run()
'''
            result=subprocess.run([sys.executable,'-c',command,name,str(source),str(NATIVE),str(folder)],capture_output=True,text=True,timeout=120)
            if result.returncode:
                raise RuntimeError(result.stderr[-6000:])
            os.replace(folder/target.name,target)
    spec=importlib.util.spec_from_file_location(name,target)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Program:
    def __init__(self, definitions, report):
        self.definitions,self.report=definitions,report
        key=hashlib.sha256((fingerprint()+json.dumps(definitions,sort_keys=True)).encode()).hexdigest()
        name='_pyne_fn_'+key
        code='#include "function_runtime.hpp"\n'+ '\n'.join(p['cpp'] for p in definitions)
        code+='\nstatic nf::Base *make_function(int id,int cap){switch(id){'
        code+=''.join(f'case {i}:return new F{i}(cap);' for i in range(len(definitions)))
        code+='default:throw std::runtime_error("Unknown function");}}\n'
        code+=f'#define FUNCTION_COUNT {len(definitions)}\n#define FUNCTION_MODULE_NAME "{name}"\n#define FUNCTION_INIT PyInit_{name}\n'
        code+='static const int input_counts[]={'+','.join(str(len(p['inputs'])) for p in definitions)+'};\n'
        code+='#include "function_module.hpp"\n'
        self.module=None
        try:self.module=build(name,code)
        except (OSError,RuntimeError,subprocess.SubprocessError) as error:
            report['build_error']=str(error)
        self.step=self.module.step if self.module else None

    def new(self,index):
        # Check only once per isolated invocation, never on the hot bar path.
        frame=sys._getframe(1)
        while frame:
            if frame.f_globals.get('__name__')=='pynecore.lib.request' and frame.f_code.co_name=='evaluate':
                self.report['security_fallbacks']+=1
                return None
            frame=frame.f_back
        if self.module is None:return None
        from .series import SeriesImpl
        return self.module.new(index,max(502,SeriesImpl.DEFAULT_MAX_BARS_BACK+1))


def load(definitions, source, rejected):
    key=hashlib.sha256(json.dumps(definitions,sort_keys=True).encode()).hexdigest()
    if key not in PROGRAMS:
        report={'source':source,'functions':[{k:v for k,v in p.items() if k not in ('cpp','inputs')} for p in definitions],
                'rejected':rejected,'security_fallbacks':0}
        REPORTS.append(report)
        PROGRAMS[key]=Program(definitions,report)
    return PROGRAMS[key]


def transform(tree,path):
    from .native_indicators import State
    if State is None:
        REPORTS.append({'source':str(path),'functions':[],'fallback':'native indicators unavailable or disabled'})
        return tree
    definitions,rejected=plans(tree)
    if not definitions:
        REPORTS.append({'source':str(path),'functions':[],'rejected':rejected})
        return tree
    imports=ast.parse('from pynecore import lib\nfrom pynecore.types import Persistent\nfrom pynecore.core.native_functions import load as __nf_load\n').body
    setup=ast.parse(f'__nf_program=__nf_load({definitions!r},{str(path)!r},{rejected!r})\n__nf_new=__nf_program.new\n__nf_step=__nf_program.step\n').body
    # Persistent's transformer recognizes the canonical annotation name.
    imports[1].names[0].asname='Persistent'
    by_name={p['name']:(i,p) for i,p in enumerate(definitions)}
    output=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name in by_name:
            index,plan=by_name[node.name]
            original=copy.deepcopy(node)
            original.name='native_python_'+node.name
            output.append(original)
            params=[p.arg for p in node.args.posonlyargs+node.args.args]
            fallback=f'native_python_{node.name}('+','.join(params)+')'
            expr=f'__nf_step(__nf_handle, lib.bar_index, ({",".join(plan["inputs"])},))' if plan['inputs'] else '__nf_step(__nf_handle, lib.bar_index, ())'
            if plan['shape']=='scalar':expr+='[0]'
            elif plan['constructor']:expr=plan['constructor']+'(*'+expr+')'
            elif plan['shape']=='list':expr='list('+expr+')'
            node.body=ast.parse(f'__nf_handle: Persistent = __nf_new({index})\nif __nf_handle is None:\n    return {fallback}\nreturn {expr}').body
        output.append(node)
    # Keep docstring and future imports at the beginning.
    insert=1
    while insert<len(output) and isinstance(output[insert],ast.ImportFrom) and output[insert].module=='__future__':insert+=1
    tree.body=output[:insert]+imports+setup+output[insert:]
    return tree


def write_report():
    path=os.environ.get('PYNECORE_NATIVE_FUNCTION_REPORT')
    if not path:return
    for program in PROGRAMS.values():
        program.report['counts']=list(program.module.counts()) if program.module else []
    Path(path).write_text(json.dumps(REPORTS,ensure_ascii=False,indent=2)+'\n')


atexit.register(write_report)
