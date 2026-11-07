"""Conservative AST-to-C++ compiler for numerical helper functions.

Unsupported constructs reject the whole helper; they never change its Python body.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import math
import operator

UNSET = object()


class Unsupported(ValueError):
    pass


@dataclass
class Value:
    code: str = ''
    kind: str = 'number'
    constant: object = UNSET
    items: list[Value] = field(default_factory=list)
    constructor: str | None = None


def annotation(node):
    if isinstance(node, ast.Subscript):
        return ast.unparse(node.value).split('.')[-1], ast.unparse(node.slice).split('.')[-1]
    return (ast.unparse(node).split('.')[-1], '') if node else ('', '')


class Compiler:
    def __init__(self, tree, root):
        self.tree, self.root = tree, root
        self.aliases = {}
        self.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.namedtuples = {}
        self.globals = {}
        mutable={name for n in ast.walk(tree) if isinstance(n,ast.Global) for name in n.names}
        assignments=Counter(n.id for statement in tree.body if isinstance(statement,(ast.Assign,ast.AnnAssign,ast.AugAssign))
                            for n in ast.walk(statement) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store))
        bindings=assignments.copy()
        for statement in tree.body:
            if isinstance(statement,(ast.Import,ast.ImportFrom)):
                bindings.update(n.asname or n.name.split('.')[0] for n in statement.names)
            elif isinstance(statement,(ast.FunctionDef,ast.ClassDef)):
                bindings[statement.name]+=1
        self.shadowed=set(bindings)|mutable
        self.functions={name:fn for name,fn in self.functions.items() if bindings[name]==1 and name not in mutable}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if node.module in ('pynecore.lib', 'pynecore', 'pynecore.types'):
                    for name in node.names:
                        local=name.asname or name.name
                        if bindings[local]==1 and local not in mutable:
                            self.aliases[local] = name.name
            elif isinstance(node, ast.ClassDef) and any(ast.unparse(b).endswith('NamedTuple') for b in node.bases):
                self.namedtuples[node.name] = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if len(node.targets) == 1 else None
                if isinstance(target, ast.Name) and target.id not in mutable and bindings[target.id]==1:
                    try:
                        self.globals[target.id] = self.literal(ast.literal_eval(node.value))
                    except (ValueError, TypeError, Unsupported):
                        pass
        self.counter = 0
        self.fields, self.initializers, self.locals, self.lines = [], [], [], []
        self.inputs, self.input_keys = [], {}
        self.history, self.persistent = {}, set()
        self.active, self.conditional = [], 0
        self.operations = 0
        self.state_count = 0

    def unique(self, prefix='v'):
        self.counter += 1
        return f'{prefix}{self.counter}'

    def emit(self, line):
        self.lines.append(line)

    def local(self):
        name = self.unique()
        self.locals.append(f'N {name};')
        return name

    def state(self, kind, init):
        name = self.unique('s')
        self.fields.append(f'{kind} {name};')
        self.initializers.append(f'{name}({init})')
        self.state_count += 1
        return name

    @staticmethod
    def literal(value):
        if type(value) is bool: return Value(f'N::boolean({str(value).lower()})','bool',value)
        if type(value) is int and abs(value) < 2**53: return Value(f'N::integer({value}LL)','int',value)
        if type(value) is float and math.isfinite(value): return Value(f'N::real({value!r})','float',value)
        if type(value) is str: return Value('', 'str', value)
        raise Unsupported('unsupported literal')

    def input(self, node, kind='float'):
        key = ast.dump(node)
        if key not in self.input_keys:
            self.input_keys[key] = len(self.inputs)
            self.inputs.append(ast.unparse(node))
        return Value(f'v[{self.input_keys[key]}]',kind)

    def path(self, node):
        if isinstance(node, ast.Name): return self.aliases.get(node.id, '?'+node.id)
        if isinstance(node, ast.Attribute):
            parent = self.path(node.value)
            return node.attr if parent == 'lib' else parent+'.'+node.attr
        return ''

    def resolve(self, name, env):
        if name in env: return env[name]
        if name in self.globals: return self.globals[name]
        path = self.aliases.get(name)
        if path in ('open','high','low','close','volume','hl2','hlc3','ohlc4','hlcc4','time','bar_index'):
            return self.input(ast.Name(id=name,ctx=ast.Load()), 'int' if path in ('time','bar_index') else 'float')
        if path == 'na': return Value('N::missing(nf::NI)','number')
        raise Unsupported('unresolved variable: '+name)

    def materialize(self, code, kind='number', constant=UNSET):
        name = self.local()
        self.emit(f'{name} = {code};')
        return Value(name,kind,constant)

    def expr(self, node, env, functions, scope):
        if isinstance(node, ast.Constant): return self.literal(node.value)
        if isinstance(node, ast.Name): return self.resolve(node.id,env)
        if isinstance(node, ast.Attribute):
            path = self.path(node)
            if path in ('open','high','low','close','volume','hl2','hlc3','ohlc4','hlcc4','time','bar_index'):
                return self.input(node,'int' if path in ('time','bar_index') else 'float')
            raise Unsupported('attribute: '+path)
        if isinstance(node, (ast.Tuple,ast.List)):
            return Value(kind='list' if isinstance(node,ast.List) else 'tuple',items=[self.expr(n,env,functions,scope) for n in node.elts])
        if isinstance(node, ast.Subscript) and isinstance(node.slice,ast.Constant) and type(node.slice.value) is int:
            index = node.slice.value
            if not 0 <= index <= 500: raise Unsupported('history index outside supported range')
            if isinstance(node.value,ast.Name):
                name=node.value.id
                binding=env.get(name)
                if binding and binding.items:
                    if index>=len(binding.items):raise Unsupported('tuple index')
                    return binding.items[index]
                hist=self.history.get((scope,name))
                if hist:return Value(f'{hist}.get({index})')
            if self.path(node.value) in ('open','high','low','close','volume','hl2','hlc3','ohlc4','hlcc4'):
                return self.input(node)
            raise Unsupported('unregistered history')
        if isinstance(node,ast.BinOp):
            a=self.expr(node.left,env,functions,scope);b=self.expr(node.right,env,functions,scope)
            ops={ast.Add:('add',operator.add),ast.Sub:('sub',operator.sub),ast.Mult:('mul',operator.mul),ast.Div:('div',operator.truediv)}
            if type(node.op) not in ops or a.kind=='str' or b.kind=='str':raise Unsupported('binary operation')
            op,fn=ops[type(node.op)];constant=UNSET
            if a.constant is not UNSET and b.constant is not UNSET:
                try: constant=fn(a.constant,b.constant)
                except (ValueError,ZeroDivisionError,OverflowError):pass
            self.operations+=1
            kind='float' if type(node.op) is ast.Div or 'float' in (a.kind,b.kind) else 'int' if a.kind==b.kind=='int' else 'number'
            return self.materialize(f'nf::{op}({a.code},{b.code})',kind,constant)
        if isinstance(node,ast.UnaryOp):
            a=self.expr(node.operand,env,functions,scope)
            if isinstance(node.op,ast.Not):return self.materialize(f'N::boolean(!{a.code}.truth())','bool',not a.constant if a.constant is not UNSET else UNSET)
            if isinstance(node.op,ast.USub):return self.materialize(f'nf::neg({a.code})',a.kind,-a.constant if a.constant is not UNSET else UNSET)
            if isinstance(node.op,ast.UAdd):return a
            raise Unsupported('unary operator')
        if isinstance(node,ast.Compare):
            ops={ast.Eq:0,ast.NotEq:1,ast.Lt:2,ast.LtE:3,ast.Gt:4,ast.GtE:5}
            a=self.expr(node.left,env,functions,scope)
            result=self.materialize('N::boolean(true)','bool')
            constants=[]
            for i,(node_right,op) in enumerate(zip(node.comparators,node.ops)):
                if i:self.emit(f'if({result.code}.truth()){{');self.conditional+=1
                b=self.expr(node_right,env,functions,scope)
                if type(op) not in ops:raise Unsupported('comparison operator')
                if a.kind==b.kind=='str':
                    answer=[operator.eq,operator.ne,operator.lt,operator.le,operator.gt,operator.ge][ops[type(op)]](a.constant,b.constant)
                    comparison=str(answer).lower();constants.append(answer)
                else:comparison=f'nf::cmp({a.code},{b.code},{ops[type(op)]})';constants.append(UNSET)
                self.emit(f'{result.code}=N::boolean({comparison});')
                if i:self.emit('}');self.conditional-=1
                a=b
            return Value(result.code,'bool',all(constants) if UNSET not in constants else UNSET)
        if isinstance(node,ast.BoolOp):
            first=self.expr(node.values[0],env,functions,scope)
            result=self.materialize(first.code,first.kind)
            kinds=[first.kind]
            for n in node.values[1:]:
                test=result.code+'.truth()'
                if isinstance(node.op,ast.Or):test='!'+test
                self.emit(f'if({test}){{');self.conditional+=1
                try:
                    value=self.expr(n,env,functions,scope)
                    self.emit(f'{result.code}={value.code};}}');kinds.append(value.kind)
                finally:self.conditional-=1
            return Value(result.code,'bool' if all(k=='bool' for k in kinds) else 'number')
        if isinstance(node,ast.IfExp):
            condition=self.expr(node.test,env,functions,scope)
            if condition.constant is not UNSET:return self.expr(node.body if condition.constant else node.orelse,env,functions,scope)
            output=self.local();self.emit(f'if({condition.code}.truth()) {{');self.conditional+=1
            try:
                a=self.expr(node.body,env,functions,scope);self.emit(f'{output}={a.code}; }} else {{')
                b=self.expr(node.orelse,env,functions,scope);self.emit(f'{output}={b.code}; }}')
            finally:self.conditional-=1
            if a.items or b.items or a.kind=='str' or b.kind=='str':raise Unsupported('conditional tuple/string')
            return Value(output,a.kind if a.kind==b.kind else 'number')
        if isinstance(node,ast.Call):return self.call(node,env,functions,scope)
        raise Unsupported(type(node).__name__)

    def call(self,node,env,functions,scope):
        path=self.path(node.func)
        if isinstance(node.func,ast.Name) and node.func.id in env:
            raise Unsupported('locally shadowed callable')
        if isinstance(node.func,ast.Name):
            name=node.func.id
            if name in functions or name in self.namedtuples:
                path=name
            elif name in ('int','float') and name not in self.shadowed:
                path=name
        else:
            root=node.func
            while isinstance(root,ast.Attribute):root=root.value
            if isinstance(root,ast.Name) and root.id in env:
                raise Unsupported('locally shadowed library')
        if path=='na' and len(node.args)==1 and isinstance(node.args[0],ast.Name) and node.args[0].id in ('float','int','bool'):
            return Value('N::missing('+{'float':'nf::NF','int':'nf::NI','bool':'nf::NB'}[node.args[0].id]+')',node.args[0].id)
        if path=='timestamp' and len(node.args)==1 and isinstance(node.args[0],ast.Constant):
            dt=datetime.fromisoformat(node.args[0].value)
            if dt.tzinfo is None:raise Unsupported('timezone-free timestamp')
            return self.literal(int(dt.timestamp()*1000))
        args=[self.expr(n,env,functions,scope) for n in node.args]
        kwargs={k.arg:self.expr(k.value,env,functions,scope) for k in node.keywords}
        if path in functions:
            if self.conditional:raise Unsupported('conditionally invoked helper')
            return self.frame(functions[path],args,kwargs,env,functions)
        if path in self.namedtuples:
            if kwargs or len(args)!=len(self.namedtuples[path]):raise Unsupported('namedtuple arguments')
            return Value(kind='tuple',items=args,constructor=path)
        if kwargs:raise Unsupported('keyword arguments: '+path)
        if path=='na' and len(args)==1:return self.materialize(f'N::boolean({args[0].code}.na())','bool')
        if path=='nz' and len(args) in (1,2):
            a=args[0];b=args[1] if len(args)==2 else self.literal(0)
            return self.materialize(f'({a.code}.na()?{b.code}:{a.code})')
        if path in ('math.min','math.max') and len(args)>=2:
            out=args[0]
            for b in args[1:]:out=self.materialize(f'nf::{"minimum" if path.endswith("min") else "maximum"}({out.code},{b.code})')
            return out
        if path in ('math.round','math.abs') and len(args)==1:
            return self.materialize(f'nf::{"rounded" if path.endswith("round") else "absolute"}({args[0].code})','int' if path.endswith('round') else args[0].kind)
        if path in ('int','float') and len(args)==1 and args[0].constant is not UNSET:
            return self.literal((int if path=='int' else float)(args[0].constant))
        kernels={'ta.sma':0,'ta.ema':1,'ta.rma':2,'ta.rsi':3,'ta.wma':4,'ta.highest':5,'ta.lowest':6,'math.sum':8,'ta.variance':9,'ta.stdev':10}
        if path=='ta.valuewhen' and len(args)==3:
            if self.conditional:raise Unsupported('conditional valuewhen')
            occurrence=args[2].constant
            if type(occurrence)is not int or not 0<=occurrence<=500:raise Unsupported('dynamic occurrence')
            state=self.state('nf::When',str(occurrence))
            return self.materialize(f'{state}.step({args[0].code},{args[1].code})',args[1].kind)
        if path in kernels or path=='ta.bb':
            if self.conditional:raise Unsupported('conditional indicator')
            if len(args)!=(3 if path=='ta.bb' else 2):raise Unsupported('indicator arity')
            length=args[1].constant
            if type(length)is not int or not 1<=length<=500:raise Unsupported('nonconstant indicator length')
            if path=='ta.bb' and (type(args[2].constant) not in (int,float) or not args[2].constant>0):
                raise Unsupported('Bollinger multiplier must be a positive constant')
            def kernel(kind):
                state=self.state('nf::Kernel',f'{kind},cap')
                return self.materialize(f'nf::ta({state},{args[0].code},{length},bar)','float')
            if path!='ta.bb':return kernel(kernels[path])
            middle=kernel(0);deviation=kernel(10)
            scaled=self.materialize(f'nf::mul({deviation.code},{args[2].code})','float')
            upper=self.materialize(f'nf::add({middle.code},{scaled.code})','float')
            lower=self.materialize(f'nf::sub({middle.code},{scaled.code})','float')
            return Value(kind='tuple',items=[middle,upper,lower])
        raise Unsupported('call: '+path)

    def bind(self,target,value,env,scope,mode=''):
        if isinstance(target,(ast.Tuple,ast.List)):
            if len(target.elts)!=len(value.items):raise Unsupported('tuple assignment')
            for t,v in zip(target.elts,value.items):self.bind(t,v,env,scope)
            return
        if not isinstance(target,ast.Name):raise Unsupported('assignment target')
        name=target.id;old=env.get(name)
        if value.items or value.kind=='str':
            if self.conditional:raise Unsupported('conditional structured assignment')
            env[name]=value;return
        if old is None or old.code.startswith('v['):
            code=self.local();old=Value(code);env[name]=old
        hist=self.history.get((scope,name))
        code=value.code
        if hist:code=f'{hist}.add({code},bar)' if mode=='Series' else f'{hist}.set({code})'
        self.emit(f'{old.code}={code};')
        env[name]=Value(old.code,value.kind,UNSET if self.conditional or (scope,name) in self.persistent else value.constant)

    def statements(self,body,env,functions,scope):
        for node in body:
            if isinstance(node,ast.FunctionDef):continue
            if isinstance(node,ast.Pass):continue
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):continue
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                target=node.target if isinstance(node,ast.AnnAssign) else node.targets[0] if len(node.targets)==1 else None
                mode,kind=annotation(node.annotation) if isinstance(node,ast.AnnAssign) else ('','')
                if node.value is None:continue
                if mode=='Persistent':
                    if self.conditional or not isinstance(target,ast.Name):raise Unsupported('conditional persistent declaration')
                    key=(scope,target.id)
                    code=self.state('N','');flag=self.state('bool','false')
                    self.emit(f'if(!{flag}){{')
                    self.conditional+=1
                    try:value=self.expr(node.value,env,functions,scope)
                    finally:self.conditional-=1
                    self.emit(f'{code}={value.code};{flag}=true;}}')
                    env[target.id]=Value(code,value.kind);self.persistent.add(key)
                elif mode in ('PersistentSeries',):raise Unsupported('PersistentSeries')
                else:self.bind(target,self.expr(node.value,env,functions,scope),env,scope,mode)
            elif isinstance(node,ast.If):
                condition=self.expr(node.test,env,functions,scope)
                if condition.constant is not UNSET:
                    self.statements(node.body if condition.constant else node.orelse,env,functions,scope);continue
                # Locals must already exist when a branch assigns them.
                assigned={n.id for branch in (node.body,node.orelse) for s in branch for n in ast.walk(s) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)}
                if any(n not in env for n in assigned):raise Unsupported('branch-local initialization')
                for n in assigned:env[n].constant=UNSET
                self.emit(f'if({condition.code}.truth()){{');self.conditional+=1
                try:
                    self.statements(node.body,env,functions,scope);self.emit('}else{')
                    self.statements(node.orelse,env,functions,scope);self.emit('}')
                finally:self.conditional-=1
                for n in assigned:env[n].constant=UNSET
            else:raise Unsupported('statement: '+type(node).__name__)

    def frame(self,fn,args,kwargs,parent,functions):
        if fn.name in self.active:raise Unsupported('recursive helper')
        if fn.decorator_list or fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs:raise Unsupported('function signature')
        self.active.append(fn.name)
        try:
            scope=self.unique('frame')
            # Module-level functions do not close over their caller's locals.
            module_scope=self.functions.get(fn.name) is fn
            env={} if module_scope else dict(parent)
            functions=dict(self.functions if module_scope else functions)
            functions.update({n.name:n for n in fn.body if isinstance(n,ast.FunctionDef)})
            formals=fn.args.posonlyargs+fn.args.args
            if len(args)>len(formals):raise Unsupported('argument count')
            defaults={p.arg:d for p,d in zip(formals[-len(fn.args.defaults):],fn.args.defaults)} if fn.args.defaults else {}
            bound={p.arg:v for p,v in zip(formals,args)}
            for k,v in kwargs.items():
                if k in bound or k not in {p.arg for p in formals}:raise Unsupported('keyword binding')
                bound[k]=v
            for p in formals:
                if p.arg not in bound:
                    if p.arg not in defaults:raise Unsupported('missing argument')
                    bound[p.arg]=self.expr(defaults[p.arg],{} if module_scope else parent,functions,scope)
                    if bound[p.arg].constant is UNSET:raise Unsupported('nonconstant default argument')
            # Do not mistake an enclosing binding for a new local variable.
            for n in ast.walk(ast.Module(body=[s for s in fn.body if not isinstance(s,ast.FunctionDef)],type_ignores=[])):
                if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store):env.pop(n.id,None)
            env.update(bound)
            for n in fn.body:
                if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and annotation(n.annotation)[0]=='Series':
                    self.history[scope,n.target.id]=self.state('nf::History','cap')
            for p in formals:
                if annotation(p.annotation)[0]=='Series':
                    hist=self.state('nf::History','cap');self.history[scope,p.arg]=hist
                    self.emit(f'{hist}.add({bound[p.arg].code},bar);')
            body=[n for n in fn.body if not isinstance(n,ast.FunctionDef)]
            if not body or not isinstance(body[-1],ast.Return):raise Unsupported('final return required')
            if any(isinstance(n,ast.Return) for s in body[:-1] for n in ast.walk(s)):raise Unsupported('early return')
            self.statements(body[:-1],env,functions,scope)
            return self.expr(body[-1].value,env,functions,scope)
        finally:self.active.pop()

    def compile(self, index):
        if self.functions.get(self.root.name) is not self.root:raise Unsupported('rebound function')
        args=[]
        for p in self.root.args.posonlyargs+self.root.args.args:
            outer,inner=annotation(p.annotation)
            kind=inner if outer=='Series' else outer
            if kind not in ('float','bool'):raise Unsupported('root parameters require float/bool annotations')
            args.append(self.input(ast.Name(id=p.arg,ctx=ast.Load()),kind))
        value=self.frame(self.root,args,{}, {},self.functions)
        if self.state_count==0 and self.operations<8:raise Unsupported('too small to amortize native call')
        values=value.items or [value]
        if any(v.items or v.kind=='str' for v in values):raise Unsupported('nested/unsupported return')
        initializers=', '.join(self.initializers)
        ctor=f':{initializers}' if initializers else ''
        cpp=f'''struct F{index}: nf::Base {{
using N=nf::N;
{' '.join(self.fields)}
explicit F{index}(int cap){ctor}{{}}
nf::Base *clone() const override {{return new F{index}(*this);}}
std::vector<N> run(long long bar,const std::vector<N>&v) override {{
{' '.join(self.locals)}
{' '.join(self.lines)}
return {{{','.join(v.code for v in values)}}};
}}
}};'''
        return {'name':self.root.name,'cpp':cpp,'inputs':self.inputs,'shape':value.kind if value.items else 'scalar',
                'constructor':value.constructor,'states':self.state_count,'operations':self.operations,'outputs':len(values)}


def security_names(tree):
    """Exclude all local helpers transitively referenced by security expressions."""
    functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    blocked=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='security':
            blocked.update(n.id for n in ast.walk(node) if isinstance(n,ast.Name))
    pending=list(blocked)
    while pending:
        node=functions.get(pending.pop())
        if node:
            for child in ast.walk(node):
                if isinstance(child,ast.Name) and child.id not in blocked:
                    blocked.add(child.id);pending.append(child.id)
    return blocked


def plans(tree):
    result=[]; rejected={};blocked=security_names(tree)
    names=Counter(n.name for n in tree.body if isinstance(n,ast.FunctionDef))
    for node in tree.body:
        if not isinstance(node,ast.FunctionDef) or node.name.startswith('__'):continue
        if names[node.name]!=1 or node.name in blocked or node.name=='main' or node.decorator_list:
            rejected[node.name]='entry point, decorated function, or security dependency';continue
        try:result.append(Compiler(tree,node).compile(len(result)))
        except (Unsupported,ValueError,TypeError,IndexError) as error:rejected[node.name]=str(error)
    return result,rejected
