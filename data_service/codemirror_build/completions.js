import { snippet } from "@codemirror/autocomplete";
import { globalCompletion, localCompletionSource } from "@codemirror/lang-python";
import { syntaxTree } from "@codemirror/language";
import api from "pynecore-api";

const scopes = new Set(["Script", "FunctionDefinition", "ClassDefinition", "LambdaExpression"]);
const words = /^[\w\u00a1-\uffff]*$/;
const importCache = new WeakMap();
const optionCache = new WeakMap();

function completion(entry, label = entry.label, call = true) {
  let cached = optionCache.get(entry);
  if (!cached) optionCache.set(entry, cached = new Map());
  const key = `${label}:${call}`;
  if (cached.has(key)) return cached.get(key);
  const result = {
    label, type: entry.type,
    detail: entry.detail || entry.path,
    info: entry.info,
    boost: 5,
  };
  if (call && entry.type === "function") {
    const fields = (entry.required || []).map(name => {
      const [keyword, value] = name.split("=");
      return (value ? `${keyword}=` : "") + "${" + (value || keyword) + "}";
    });
    const apply = snippet(`${label}(${fields.join(", ")})\${}`);
    result.apply = (view, option, from, to) => {
      if (view.state.sliceDoc(to, to + 1) === "(") {
        view.dispatch({ changes: { from, to, insert: label }, selection: { anchor: from + label.length },
          userEvent: "input.complete" });
      } else apply(view, option, from, to);
    };
  }
  cached.set(key, result);
  return result;
}

function tokens(node, state) {
  const result = [];
  for (let child = node.firstChild; child; child = child.nextSibling) {
    if (child.name !== "Comment" && !child.type.isError) {
      result.push(state.sliceDoc(child.from, child.to));
    }
  }
  return result;
}

function importBindings(node, state) {
  const parts = tokens(node, state);
  const split = parts.indexOf("import");
  if (split < 0) return [];
  const base = parts[0] === "from" ? parts.slice(1, split).join("") : "";
  const items = parts.slice(split + 1).filter(part => part !== "(" && part !== ")");
  const result = [];
  for (let start = 0; start < items.length;) {
    let end = items.indexOf(",", start);
    if (end < 0) end = items.length;
    const item = items.slice(start, end);
    const as = item.indexOf("as");
    const name = item.slice(0, as < 0 ? item.length : as).join("");
    if (name === "*" && api.namespaces[base]) {
      for (const label of api.exports[base] || Object.keys(api.namespaces[base])) {
        const entry = api.namespaces[base][label];
        if (entry) result.push([label, entry]);
      }
    } else {
      const path = base ? `${base}.${name}` : name;
      const alias = as < 0 ? (base ? name : name.split(".")[0]) : item[as + 1];
      const resolved = !base && as < 0 ? alias : path;
      const entry = api.namespaces[base]?.[name]
        || (api.namespaces[resolved] ? { label: alias, type: "namespace", path: resolved } : null);
      if (alias) result.push([alias, entry]);
    }
    start = end + 1;
  }
  return result;
}

function bindingsIn(scope, state) {
  const key = scope.tree;
  if (key && importCache.has(key)) return importCache.get(key);
  const result = new Map();
  scope.cursor().iterate(node => {
    if (node.from === scope.from && node.to === scope.to && node.name === scope.name) return;
    if (scopes.has(node.name)) {
      const name = node.node.getChild("VariableName");
      if (name) result.set(state.sliceDoc(name.from, name.to), null);
      return false;
    }
    if (node.name === "ImportStatement") {
      for (const [name, entry] of importBindings(node.node, state)) result.set(name, entry);
      return false;
    }
    // Keep API aliases from overriding parameters or local assignments.
    if (node.name === "AssignStatement" || node.name === "ParamList") {
      for (let child = node.node.firstChild; child; child = child.nextSibling) {
        if (child.name === "AssignOp" && node.name === "AssignStatement") break;
        if (child.name === "VariableName") result.set(state.sliceDoc(child.from, child.to), null);
      }
      return false;
    }
    if (node.type.is("Statement") && !node.node.getChild("Body")) return false;
  });
  if (key) importCache.set(key, result);
  return result;
}

function bindingsAt(inner, state) {
  const chain = [];
  for (let node = inner; node; node = node.parent) {
    if (scopes.has(node.name)) chain.unshift(node);
  }
  const result = new Map();
  for (const scope of chain) {
    for (const [name, entry] of bindingsIn(scope, state)) result.set(name, entry);
  }
  return result;
}

function memberEntry(path, bindings) {
  const parts = path.split(".");
  let entry = bindings.get(parts.shift());
  for (const part of parts) entry = api.namespaces[entry?.path]?.[part];
  return entry;
}

export function pyneCompletion(context) {
  if (context.state.readOnly) return null;
  const line = context.state.doc.lineAt(context.pos);
  const trailingSpace = context.state.sliceDoc(line.from, context.pos).match(/[ \t]+$/)?.[0].length || 0;
  const inner = syntaxTree(context.state).resolveInner(context.pos - trailingSpace, -1);
  let importNode = null;
  for (let node = inner; node; node = node.parent) {
    if (["String", "FormatString", "Comment"].includes(node.name)) return null;
    if (node.name === "ImportStatement") importNode = node;
  }
  const word = context.matchBefore(/[\w\u00a1-\uffff]*$/);
  const from = word?.from ?? context.pos;
  if (importNode) {
    const parts = tokens(importNode, context.state);
    const split = parts.indexOf("import");
    const base = parts[0] === "from" && split > 0 ? parts.slice(1, split).join("") : "";
    const before = context.state.sliceDoc(importNode.from, from);
    if (/\bas\s*$/.test(before)) return null;
    let namespace = base;
    if (!base || !before.includes("import")) {
      const path = context.matchBefore(/[\w.]+$/)?.text || "";
      namespace = path.includes(".") ? path.slice(0, path.lastIndexOf(".")) : "";
    }
    const entries = api.namespaces[namespace];
    const options = entries ? Object.values(entries).map(entry => completion(entry, entry.label, false))
      : (!namespace ? [{ label: "pynecore", type: "namespace" }] : []);
    return options.length ? { from, options, validFor: words } : null;
  }

  const bindings = bindingsAt(inner, context.state);
  const member = context.matchBefore(/[\w\u00a1-\uffff]+(?:\.[\w\u00a1-\uffff]+)*\.[\w\u00a1-\uffff]*$/);
  if (member) {
    const path = member.text.slice(0, member.text.lastIndexOf("."));
    const entries = api.namespaces[memberEntry(path, bindings)?.path];
    return entries ? { from, options: Object.values(entries).map(entry => completion(entry)), validFor: words } : null;
  }
  if (inner.name === "PropertyName" || context.state.sliceDoc(Math.max(0, from - 1), from) === ".") return null;
  if (from === context.pos && !context.explicit) return null;
  const options = new Map();
  for (const label of api.keywords) options.set(label, { label, type: "keyword" });
  for (const source of [globalCompletion, localCompletionSource]) {
    for (const entry of source(context)?.options || []) options.set(entry.label, entry);
  }
  for (const [label, entry] of bindings) {
    if (entry) options.set(label, completion(entry, label));
  }
  return { from, options: [...options.values()], validFor: words };
}
