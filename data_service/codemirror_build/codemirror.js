import {
  Compartment,
  EditorSelection,
  EditorState,
  RangeSet,
  RangeSetBuilder,
  StateEffect,
  StateField,
  Transaction,
} from "@codemirror/state";
import {
  Decoration,
  EditorView,
  GutterMarker,
  ViewPlugin,
  crosshairCursor,
  drawSelection,
  dropCursor,
  gutter,
  highlightActiveLine,
  keymap,
  rectangularSelection,
} from "@codemirror/view";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
  redo,
  redoDepth,
  toggleComment,
  undo,
  undoDepth,
} from "@codemirror/commands";
import {
  HighlightStyle,
  bracketMatching,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { python } from "@codemirror/lang-python";
import { tags } from "@lezer/highlight";

const setSearchEffect = StateEffect.define();
const setDiagnosticsEffect = StateEffect.define();
const setChangedLinesEffect = StateEffect.define();

const EMPTY_SEARCH = Object.freeze({ matches: [], currentIndex: -1 });

const searchState = StateField.define({
  create() {
    return EMPTY_SEARCH;
  },
  update(value, transaction) {
    let next = transaction.docChanged ? EMPTY_SEARCH : value;
    for (const effect of transaction.effects) {
      if (effect.is(setSearchEffect)) next = effect.value;
    }
    return next;
  },
});

function firstVisibleMatch(matches, from) {
  let low = 0;
  let high = matches.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (matches[middle].end < from) low = middle + 1;
    else high = middle;
  }
  return low;
}

function visibleSearchDecorations(view) {
  const search = view.state.field(searchState);
  if (!search.matches.length) return Decoration.none;
  const ranges = [];
  for (const visible of view.visibleRanges) {
    let index = firstVisibleMatch(search.matches, visible.from);
    while (index < search.matches.length) {
      const match = search.matches[index];
      if (match.start > visible.to) break;
      const from = Math.max(0, Math.min(match.start, view.state.doc.length));
      const to = Math.max(from, Math.min(match.end, view.state.doc.length));
      if (to > from) {
        ranges.push(Decoration.mark({
          class: index === search.currentIndex
            ? "cm-pyne-search-match cm-pyne-search-current"
            : "cm-pyne-search-match",
        }).range(from, to));
      }
      index += 1;
    }
  }
  return Decoration.set(ranges, true);
}

const searchDecorations = ViewPlugin.fromClass(class {
  constructor(view) {
    this.decorations = visibleSearchDecorations(view);
  }

  update(update) {
    if (
      update.docChanged
      || update.viewportChanged
      || update.startState.field(searchState) !== update.state.field(searchState)
    ) {
      this.decorations = visibleSearchDecorations(update.view);
    }
  }
}, {
  decorations: (plugin) => plugin.decorations,
});

function diagnosticDecorations(doc, diagnostics) {
  const ranges = [];
  for (const diagnostic of diagnostics) {
    const from = Math.max(0, Math.min(Number(diagnostic.from) || 0, doc.length));
    const requestedTo = Number(diagnostic.to);
    const to = Math.max(
      from,
      Math.min(Number.isFinite(requestedTo) ? requestedTo : from + 1, doc.length),
    );
    if (to <= from) continue;
    const severity = diagnostic.severity === "warning" ? "warning" : "error";
    ranges.push(Decoration.mark({
      class: `cm-pyne-diagnostic cm-pyne-diagnostic-${severity}`,
    }).range(from, to));
  }
  return Decoration.set(ranges, true);
}

const diagnosticState = StateField.define({
  create() {
    return Decoration.none;
  },
  update(value, transaction) {
    let next = value.map(transaction.changes);
    for (const effect of transaction.effects) {
      if (effect.is(setDiagnosticsEffect)) {
        next = diagnosticDecorations(transaction.state.doc, effect.value);
      }
    }
    return next;
  },
  provide: (field) => EditorView.decorations.from(field),
});

class ChangedLineMarker extends GutterMarker {
  constructor(type) {
    super();
    this.type = type;
  }

  eq(other) {
    return other instanceof ChangedLineMarker && other.type === this.type;
  }

  toDOM() {
    const marker = document.createElement("span");
    marker.className = `cm-pyne-diff-marker ${this.type}`;
    return marker;
  }
}

function changedLineMarkers(doc, value) {
  const markers = new Map();
  for (const item of value.lines || []) {
    const line = Math.max(0, Math.min(Number(item.line) || 0, doc.lines - 1));
    markers.set(line, item.type === "added" ? "added" : "modified");
  }
  for (const item of value.deletionLines || []) {
    const line = Math.max(0, Math.min(Number(item) || 0, doc.lines - 1));
    if (!markers.has(line)) markers.set(line, "deleted");
  }
  const builder = new RangeSetBuilder();
  [...markers.entries()].sort((left, right) => left[0] - right[0]).forEach(([line, type]) => {
    const position = doc.line(line + 1).from;
    builder.add(position, position, new ChangedLineMarker(type));
  });
  return builder.finish();
}

const changedLineState = StateField.define({
  create() {
    return RangeSet.empty;
  },
  update(value, transaction) {
    let next = value.map(transaction.changes);
    for (const effect of transaction.effects) {
      if (effect.is(setChangedLinesEffect)) {
        next = changedLineMarkers(transaction.state.doc, effect.value);
      }
    }
    return next;
  },
});

const changedLineGutter = gutter({
  class: "cm-pyne-diff-gutter",
  markers: (view) => view.state.field(changedLineState),
});

const pyneHighlightStyle = HighlightStyle.define([
  { tag: tags.comment, color: "#6a9955" },
  { tag: [tags.string, tags.special(tags.string)], color: "#ce9178" },
  { tag: [tags.number, tags.bool, tags.null], color: "#b5cea8" },
  { tag: [tags.keyword, tags.operatorKeyword], color: "#569cd6" },
  { tag: [tags.typeName, tags.className], color: "#4ec9b0" },
  { tag: [tags.function(tags.variableName), tags.definition(tags.variableName)], color: "#dcdcaa" },
  { tag: tags.meta, color: "#c586c0" },
]);

function languageExtension(language) {
  return String(language || "").toLowerCase() === "python" ? python() : [];
}

function editableExtension(readOnly) {
  return [
    EditorState.readOnly.of(Boolean(readOnly)),
    EditorView.editable.of(!readOnly),
  ];
}

function create(container, options = {}) {
  if (!(container instanceof HTMLElement)) {
    throw new TypeError("Code editor container is required");
  }
  if (container._pyneCodeEditor) return container._pyneCodeEditor;

  const languageCompartment = new Compartment();
  const editableCompartment = new Compartment();
  let language = String(options.language || "python");
  let readOnly = Boolean(options.readOnly);
  let suppressInput = 0;
  let inputScheduled = false;
  let destroyed = false;
  let diagnostics = [];
  let changedLines = { lines: [], deletionLines: [] };
  let search = EMPTY_SEARCH;

  const extensions = () => [
    history(),
    drawSelection(),
    dropCursor(),
    rectangularSelection(),
    crosshairCursor(),
    highlightActiveLine(),
    indentOnInput(),
    bracketMatching(),
    syntaxHighlighting(pyneHighlightStyle),
    EditorState.allowMultipleSelections.of(true),
    EditorView.lineWrapping,
    keymap.of([
      indentWithTab,
      ...historyKeymap,
      ...defaultKeymap,
    ]),
    searchState,
    searchDecorations,
    diagnosticState,
    changedLineState,
    changedLineGutter,
    languageCompartment.of(languageExtension(language)),
    editableCompartment.of(editableExtension(readOnly)),
    EditorView.contentAttributes.of({
      autocapitalize: "off",
      autocomplete: "off",
      autocorrect: "off",
      spellcheck: "false",
      "aria-label": String(options.ariaLabel || "Source code"),
    }),
    EditorView.updateListener.of((update) => {
      if (!update.docChanged || suppressInput || inputScheduled || destroyed) return;
      inputScheduled = true;
      queueMicrotask(() => {
        inputScheduled = false;
        if (!destroyed) container.dispatchEvent(new Event("input"));
      });
    }),
  ];

  const createState = (doc, selection = null) => EditorState.create({
    doc,
    selection: selection || EditorSelection.cursor(0),
    extensions: extensions(),
  });

  container.replaceChildren();
  const view = new EditorView({
    state: createState(String(options.value || "")),
    parent: container,
  });

  container.classList.add("pyne-codemirror-host");

  const silently = (callback) => {
    suppressInput += 1;
    try {
      return callback();
    } finally {
      suppressInput -= 1;
    }
  };

  const adapter = {
    view,
    getValue() {
      return view.state.doc.toString();
    },
    setValue(value, setOptions = {}) {
      const content = String(value ?? "");
      const currentLength = view.state.doc.length;
      const selection = setOptions.selection || null;
      const spec = {
        changes: { from: 0, to: currentLength, insert: content },
        annotations: Transaction.addToHistory.of(setOptions.addToHistory === true),
      };
      if (selection) {
        spec.selection = EditorSelection.single(
          Math.max(0, Math.min(Number(selection.anchor) || 0, content.length)),
          Math.max(0, Math.min(Number(selection.head) || 0, content.length)),
        );
      }
      const dispatch = () => view.dispatch(spec);
      if (setOptions.emitChange === true) dispatch();
      else silently(dispatch);
    },
    getSelection() {
      const main = view.state.selection.main;
      return {
        anchor: main.anchor,
        head: main.head,
        from: main.from,
        to: main.to,
        direction: main.head < main.anchor ? "backward" : "forward",
      };
    },
    setSelection(from, to = from, setOptions = {}) {
      const length = view.state.doc.length;
      const start = Math.max(0, Math.min(Number(from) || 0, length));
      const end = Math.max(0, Math.min(Number(to) || 0, length));
      const backward = setOptions.direction === "backward";
      const transaction = {
        selection: EditorSelection.single(backward ? end : start, backward ? start : end),
      };
      if (setOptions.scroll) {
        transaction.effects = EditorView.scrollIntoView(
          Math.min(start, end),
          { y: setOptions.y || "center" },
        );
      }
      view.dispatch(transaction);
      if (setOptions.focus) view.focus();
    },
    replaceRange(from, to, value, replaceOptions = {}) {
      const length = view.state.doc.length;
      const start = Math.max(0, Math.min(Number(from) || 0, length));
      const end = Math.max(start, Math.min(Number(to) || start, length));
      const insert = String(value ?? "");
      const cursor = start + insert.length;
      const dispatch = () => view.dispatch({
        changes: { from: start, to: end, insert },
        selection: EditorSelection.cursor(cursor),
        annotations: Transaction.addToHistory.of(replaceOptions.addToHistory !== false),
      });
      if (replaceOptions.emitChange === false) silently(dispatch);
      else dispatch();
    },
    revealRange(from, to = from, revealOptions = {}) {
      this.setSelection(from, to, {
        direction: revealOptions.direction,
        scroll: true,
        y: revealOptions.y || "center",
        focus: revealOptions.focus === true,
      });
    },
    scrollToOffset(offset, scrollOptions = {}) {
      const position = Math.max(0, Math.min(Number(offset) || 0, view.state.doc.length));
      view.dispatch({
        effects: EditorView.scrollIntoView(position, { y: scrollOptions.y || "center" }),
      });
    },
    scrollToLine(line, scrollOptions = {}) {
      const lineNumber = Math.max(1, Math.min(Number(line) || 1, view.state.doc.lines));
      this.scrollToOffset(view.state.doc.line(lineNumber).from, scrollOptions);
    },
    getScrollPosition() {
      return {
        top: view.scrollDOM.scrollTop,
        left: view.scrollDOM.scrollLeft,
      };
    },
    setScrollPosition(position = {}) {
      view.scrollDOM.scrollTop = Number(position.top) || 0;
      view.scrollDOM.scrollLeft = Number(position.left) || 0;
    },
    setSearchMatches(matches, currentIndex = -1) {
      search = {
        matches: Array.isArray(matches)
          ? matches.map((match) => ({ start: Number(match.start), end: Number(match.end) }))
          : [],
        currentIndex: Number(currentIndex),
      };
      view.dispatch({ effects: setSearchEffect.of(search) });
    },
    clearSearchMatches() {
      search = EMPTY_SEARCH;
      view.dispatch({ effects: setSearchEffect.of(search) });
    },
    setDiagnostics(items) {
      diagnostics = Array.isArray(items) ? items : [];
      view.dispatch({ effects: setDiagnosticsEffect.of(diagnostics) });
    },
    setChangedLines(value = {}) {
      changedLines = {
        lines: Array.isArray(value.lines) ? value.lines : [],
        deletionLines: Array.isArray(value.deletionLines) ? value.deletionLines : [],
      };
      view.dispatch({ effects: setChangedLinesEffect.of(changedLines) });
    },
    setLanguage(value) {
      const next = String(value || "");
      if (next === language) return;
      language = next;
      view.dispatch({ effects: languageCompartment.reconfigure(languageExtension(language)) });
    },
    setReadOnly(value) {
      const next = Boolean(value);
      if (next === readOnly) return;
      readOnly = next;
      view.dispatch({ effects: editableCompartment.reconfigure(editableExtension(readOnly)) });
    },
    canUndo() {
      return undoDepth(view.state) > 0;
    },
    canRedo() {
      return redoDepth(view.state) > 0;
    },
    undo() {
      return undo(view);
    },
    redo() {
      return redo(view);
    },
    toggleComment() {
      return toggleComment(view);
    },
    clearHistory() {
      const selection = view.state.selection;
      const top = view.scrollDOM.scrollTop;
      const left = view.scrollDOM.scrollLeft;
      silently(() => view.setState(createState(view.state.doc.toString(), selection)));
      view.dispatch({
        effects: [
          setSearchEffect.of(search),
          setDiagnosticsEffect.of(diagnostics),
          setChangedLinesEffect.of(changedLines),
        ],
      });
      requestAnimationFrame(() => {
        view.scrollDOM.scrollTop = top;
        view.scrollDOM.scrollLeft = left;
      });
    },
    focus(focusOptions = {}) {
      if (focusOptions.preventScroll) {
        const top = view.scrollDOM.scrollTop;
        const left = view.scrollDOM.scrollLeft;
        view.focus();
        view.scrollDOM.scrollTop = top;
        view.scrollDOM.scrollLeft = left;
      } else {
        view.focus();
      }
    },
    blur() {
      view.contentDOM.blur();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      view.scrollDOM.removeEventListener("scroll", forwardScroll);
      view.destroy();
      container.classList.remove("pyne-codemirror-host");
      delete container._pyneCodeEditor;
    },
  };

  const forwardScroll = () => container.dispatchEvent(new Event("scroll"));
  view.scrollDOM.addEventListener("scroll", forwardScroll, { passive: true });

  container._pyneCodeEditor = adapter;
  return adapter;
}

window.PyneCodeMirror = { create };
