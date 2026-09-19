"""Extract editor completions without importing or executing PyneCore/strategies."""

import ast
import json
import keyword
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def function_entry(node, name=None):
    name = name or node.name
    property_value = any(ast.unparse(d).split(".")[-1] == "module_property" for d in node.decorator_list)
    positional = node.args.posonlyargs + node.args.args
    defaults = [None] * (len(positional) - len(node.args.defaults)) + node.args.defaults
    params, required = [], []
    for arg, default in zip(positional, defaults):
        if arg.arg in {"self", "cls"} or arg.arg.startswith("_"):
            continue
        text = arg.arg + (": " + ast.unparse(arg.annotation) if arg.annotation else "")
        params.append(text + (" = " + ast.unparse(default) if default is not None else ""))
        if default is None:
            required.append(arg.arg)
    if node.args.vararg and not node.args.vararg.arg.startswith("_"):
        params.append("*" + node.args.vararg.arg)
    elif node.args.kwonlyargs:
        params.append("*")
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        if arg.arg.startswith("_"):
            continue
        text = arg.arg + (": " + ast.unparse(arg.annotation) if arg.annotation else "")
        params.append(text + (" = " + ast.unparse(default) if default is not None else ""))
        if default is None:
            required.append(arg.arg + "=" + arg.arg)
    if params and params[-1] == "*":
        params.pop()
    if node.args.kwarg and not node.args.kwarg.arg.startswith("_"):
        params.append("**" + node.args.kwarg.arg)
    signature = name + "(" + ", ".join(params) + ")"
    if node.returns:
        signature += " -> " + ast.unparse(node.returns)
    doc = (ast.get_docstring(node) or "").split("\n\n")[0]
    summary = " ".join(doc.split())[:400]
    return {
        "label": name,
        "type": "property" if property_value else "function",
        "detail": ast.unparse(node.returns) if property_value and node.returns else "(" + ", ".join(params) + ")",
        "info": signature + ("\n\n" + summary if summary else ""),
        "required": [] if property_value else required,
    }


def entries(body):
    result = {}
    functions = {n.name: n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            result[node.name] = function_entry(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            if any(ast.unparse(base).endswith("CallableModule") for base in node.bases):
                result.update(entries(node.body))
            else:
                result[node.name] = {"label": node.name, "type": "class"}
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name) or target.id.startswith("_"):
                    continue
                name = target.id
                if isinstance(node.value, ast.Name) and node.value.id in functions:
                    result[name] = function_entry(functions[node.value.id], name)
                else:
                    result[name] = {"label": name, "type": "constant" if name.isupper() else "variable"}
    return result


def generate():
    paths = [ROOT / "pynecore/__init__.py", ROOT / "pynecore/types/__init__.py"]
    paths += sorted((ROOT / "pynecore/lib").rglob("*.py"))
    namespaces, trees, exports = {}, {}, {}
    for path in paths:
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        namespaces[name] = entries(tree.body)
        trees[name] = (tree, name if path.name == "__init__.py" else name.rsplit(".", 1)[0])
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                exports[name] = ast.literal_eval(node.value)

    core = ast.parse((ROOT / "pynecore/core/script.py").read_text(encoding="utf-8"))
    for cls, exported in (("Script", "script"), ("_Input", "input")):
        node = next(n for n in core.body if isinstance(n, ast.ClassDef) and n.name == cls)
        members = entries(node.body)
        if cls == "Script":
            members = {key: members[key] for key in ("indicator", "strategy", "library")}
        namespaces["pynecore.lib." + exported] = members
        if cls == "_Input":
            call = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__call__")
            namespaces["pynecore.lib"]["input"] = function_entry(call, "input")

    # Resolve public relative imports using syntax only, including re-exported namespaces.
    for _ in range(3):
        for namespace, (tree, package) in trees.items():
            for node in tree.body:
                if not isinstance(node, ast.ImportFrom):
                    continue
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - node.level + 1]) if node.level else ""
                base = ".".join(filter(None, (base, node.module)))
                if not base.startswith("pynecore"):
                    continue
                for alias in node.names:
                    label = alias.asname or alias.name
                    if label.startswith("_"):
                        continue
                    path = base + "." + alias.name
                    existing = namespaces.get(base, {}).get(alias.name)
                    if existing:
                        namespaces[namespace].setdefault(label, {**existing, "label": label})
                    elif path in namespaces:
                        namespaces[namespace].setdefault(label, {"label": label, "type": "namespace", "path": path})
                    elif base == "pynecore.types" or base.startswith("pynecore.types."):
                        namespaces[namespace].setdefault(label, {"label": label, "type": "class"})

    # Python permits explicit submodule imports even when __all__ omits them.
    for path in namespaces:
        if "." not in path:
            continue
        parent, label = path.rsplit(".", 1)
        if parent not in namespaces:
            continue
        entry = namespaces[parent].setdefault(label, {"label": label, "type": "namespace"})
        callable_entry = namespaces[path].get(label)
        if callable_entry and callable_entry["type"] == "function":
            entry.update(callable_entry)
        entry["path"] = path
    return {"namespaces": namespaces, "exports": exports, "keywords": keyword.kwlist + keyword.softkwlist}


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=True, separators=(",", ":")))
