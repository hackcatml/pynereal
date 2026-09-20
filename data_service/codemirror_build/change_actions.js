import { EditorSelection, StateEffect, StateField } from "@codemirror/state";
import { isolateHistory } from "@codemirror/commands";
import { EditorView, showTooltip } from "@codemirror/view";

export const setChangeHunks = StateEffect.define();
const selectChange = StateEffect.define();
const empty = { hunks: [], selected: -1, anchor: 0 };

export function buildChangeHunks(before, after, operations) {
  const original = String(before ?? "").replace(/\r\n?/g, "\n");
  const current = String(after ?? "").replace(/\r\n?/g, "\n");
  const oldLines = original.split("\n");
  const newLines = current.split("\n");
  const offsets = lines => {
    const result = [0];
    for (const line of lines) result.push(result.at(-1) + line.length + 1);
    return result;
  };
  const oldOffsets = offsets(oldLines);
  const newOffsets = offsets(newLines);
  const hunks = [];
  let oldLine = 0;
  let newLine = 0;
  for (let index = 0; index < operations.length;) {
    if (operations[index] === "equal") {
      oldLine++;
      newLine++;
      index++;
      continue;
    }
    const oldStart = oldLine;
    const newStart = newLine;
    while (index < operations.length && operations[index] !== "equal") {
      if (operations[index++] === "insert") newLine++;
      else oldLine++;
    }
    const atEnd = oldLine === oldLines.length && newLine === newLines.length;
    // At EOF the separator belongs to the preceding line, not the removed tail.
    const from = Math.min(current.length, newOffsets[newStart] - (atEnd && newStart > 0 ? 1 : 0));
    const to = Math.min(current.length, newOffsets[newLine]);
    const oldFrom = Math.min(original.length, oldOffsets[oldStart] - (atEnd && oldStart > 0 ? 1 : 0));
    const oldTo = Math.min(original.length, oldOffsets[oldLine]);
    const deletedRemainder = oldLine - oldStart > newLine - newStart;
    hunks.push({
      from, to, insert: original.slice(oldFrom, oldTo), current: current.slice(from, to),
      line: Math.min(newStart + 1, newLines.length),
      endLine: Math.min(Math.max(newStart + 1, newLine + (deletedRemainder ? 1 : 0)), newLines.length),
      removed: oldLines.slice(oldStart, oldLine), added: newLines.slice(newStart, newLine),
    });
  }
  return hunks;
}

export const changeActionState = StateField.define({
  create: () => empty,
  update(value, transaction) {
    // Never allow a revert against offsets from an earlier document snapshot.
    let next = transaction.docChanged ? empty : value;
    for (const effect of transaction.effects) {
      if (effect.is(setChangeHunks)) {
        const previous = next.hunks[next.selected];
        const hunks = effect.value;
        const selected = previous ? hunks.findIndex(hunk => hunk.from === previous.from
          && hunk.to === previous.to && hunk.insert === previous.insert) : -1;
        next = { hunks, selected, anchor: selected >= 0 ? next.anchor : 0 };
      } else if (effect.is(selectChange)) {
        const { index, anchor = 0 } = effect.value;
        next = { ...next, selected: index >= 0 && index < next.hunks.length ? index : -1, anchor };
      }
    }
    return next;
  },
  provide: field => showTooltip.from(field, value => {
    const hunk = value.hunks[value.selected];
    return hunk ? { pos: value.anchor, above: false, create: changeTooltip } : null;
  }),
});

export function closeChange(view) {
  if (view.state.field(changeActionState).selected < 0) return false;
  view.dispatch({ effects: selectChange.of({ index: -1 }) });
  return true;
}

function goToChange(view, index, line = null, reveal = true) {
  const { hunks } = view.state.field(changeActionState);
  const hunk = hunks[index];
  if (!hunk) return false;
  const targetLine = Math.max(hunk.line, Math.min(line ?? hunk.line, hunk.endLine));
  const pos = view.state.doc.line(targetLine).from;
  const effects = [selectChange.of({ index, anchor: pos })];
  if (reveal) effects.push(EditorView.scrollIntoView(pos, { y: "center" }));
  view.dispatch({ effects });
  return true;
}

export function openChangeAtLine(view, line, tolerance = 0, reveal = false) {
  const { hunks, selected } = view.state.field(changeActionState);
  let best = -1;
  let distance = Infinity;
  hunks.forEach((hunk, index) => {
    const gap = Math.max(hunk.line - line, line - hunk.endLine, 0);
    if (gap <= tolerance && gap < distance) { best = index; distance = gap; }
  });
  if (best < 0) return false;
  if (best === selected) return closeChange(view);
  return goToChange(view, best, line, reveal);
}

export function navigateChange(view, direction) {
  const { selected } = view.state.field(changeActionState);
  return selected >= 0 && goToChange(view, selected + direction);
}

export function revertChange(view) {
  if (view.state.readOnly) return false;
  const { hunks, selected } = view.state.field(changeActionState);
  const hunk = hunks[selected];
  if (!hunk || view.state.sliceDoc(hunk.from, hunk.to) !== hunk.current) return false;
  // Undo restores the pre-edit selection; anchor it here, not at an old caret.
  const selection = EditorSelection.cursor(hunk.from);
  view.dispatch({ selection });
  view.dispatch({
    changes: { from: hunk.from, to: hunk.to, insert: hunk.insert },
    selection,
    annotations: isolateHistory.of("full"),
    userEvent: "input.revert", scrollIntoView: true,
  });
  return true;
}

const icons = {
  Revert: ["M9 14 4 9l5-5", "M4 9h11a5 5 0 0 1 0 10h-1"],
  "Previous change": ["m6 12 6-6 6 6", "M12 6v14"],
  "Next change": ["m6 12 6 6 6-6", "M12 4v14"],
  Close: ["m6 6 12 12", "M6 18 18 6"],
};

function changeTooltip(view) {
  const doc = view.dom.ownerDocument;
  const dom = doc.createElement("section");
  dom.className = "cm-pyne-change-tooltip";
  dom.setAttribute("role", "dialog");
  dom.setAttribute("aria-label", "Change from saved source");
  const header = dom.appendChild(doc.createElement("div"));
  header.className = "cm-pyne-change-header";
  const label = header.appendChild(doc.createElement("span"));
  const controls = header.appendChild(doc.createElement("div"));
  controls.className = "cm-pyne-change-controls";
  function button(name, action) {
    const element = controls.appendChild(doc.createElement("button"));
    element.type = "button";
    element.setAttribute("aria-label", name);
    element.dataset.tooltip = name;
    const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    for (const path of icons[name]) {
      const part = svg.appendChild(doc.createElementNS("http://www.w3.org/2000/svg", "path"));
      part.setAttribute("d", path);
    }
    element.append(svg);
    element.addEventListener("mousedown", event => event.preventDefault());
    element.addEventListener("click", () => action(view));
    return element;
  }
  const revert = button("Revert", revertChange);
  const previous = button("Previous change", view => navigateChange(view, -1));
  const next = button("Next change", view => navigateChange(view, 1));
  button("Close", closeChange);
  const preview = dom.appendChild(doc.createElement("div"));
  preview.className = "cm-pyne-change-preview";
  let lastHunk = null;
  function render() {
    const { hunks, selected } = view.state.field(changeActionState);
    const hunk = hunks[selected];
    if (!hunk) return;
    label.textContent = `Change ${selected + 1} of ${hunks.length}`;
    revert.disabled = view.state.readOnly;
    previous.disabled = selected === 0;
    next.disabled = selected === hunks.length - 1;
    if (lastHunk === hunk) return;
    lastHunk = hunk;
    preview.replaceChildren();
    for (const [kind, lines, prefix] of [["removed", hunk.removed, "- "], ["added", hunk.added, "+ "]]) {
      if (!lines.length) continue;
      const block = preview.appendChild(doc.createElement("pre"));
      block.className = kind;
      block.textContent = lines.map(line => prefix + line).join("\n");
    }
  }
  const outside = event => {
    if (dom.contains(event.target) || event.target.closest?.(".cm-pyne-diff-gutter, .cm-pyne-minimap")) return;
    closeChange(view);
  };
  const escape = event => {
    if (event.key !== "Escape" || !(dom.contains(event.target) || view.dom.contains(event.target))) return;
    event.preventDefault();
    event.stopPropagation();
    closeChange(view);
  };
  render();
  return {
    dom,
    mount() {
      doc.addEventListener("pointerdown", outside, { capture: true });
      doc.addEventListener("keydown", escape, { capture: true });
    },
    update: render,
    destroy() {
      doc.removeEventListener("pointerdown", outside, { capture: true });
      doc.removeEventListener("keydown", escape, { capture: true });
    },
  };
}
