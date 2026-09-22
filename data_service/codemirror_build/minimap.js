import { EditorView, ViewPlugin } from "@codemirror/view";
import { ensureSyntaxTree, language, syntaxTree } from "@codemirror/language";

const mobileQuery = "(max-width: 720px), (hover: none) and (pointer: coarse)";
const colors = { added: "#2ea043", modified: "#1f6feb", deleted: "#f85149" };
const widthStorageKey = "pyne.editor.minimapWidth";
const defaultWidth = 104;

export function minimapWidth(width, editorWidth) {
  const maximum = Math.max(64, Math.min(360, Math.floor(editorWidth * 0.45)));
  return Math.round(Math.max(64, Math.min(maximum, Number.isFinite(width) ? width : defaultWidth)));
}

export function minimapRegions(state, tree = syntaxTree(state)) {
  const regions = [];
  // Use parsed comments so region-like text inside strings is not a heading.
  tree.iterate({ enter(node) {
    if (node.name !== "Comment") return;
    const line = state.doc.lineAt(node.from);
    if (state.sliceDoc(line.from, node.from).trim()) return;
    const match = /^#\s*region\b\s*:?(.*)$/i.exec(state.sliceDoc(node.from, node.to));
    const title = match?.[1].trim();
    if (title) regions.push({ line: line.number, title });
  } });
  return regions;
}

export function minimapLine(y, height, lines) {
  const step = Math.min(2, height / Math.max(1, lines));
  return Math.max(1, Math.min(lines, Math.floor(y / Math.max(step, 0.001)) + 1));
}

export function drawMinimap(context, doc, width, height, markers, tabSize = 4) {
  const step = Math.min(2, height / doc.lines);
  const columns = Math.max(1, Math.floor(width - 12));
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#6b7785";
  let row = 0;
  for (const text of doc.iterLines()) {
    let column = 0;
    let start = -1;
    for (let index = 0; index <= text.length && column <= columns; index++) {
      const char = text[index];
      const space = char === " " || char === "\t" || char === undefined;
      if (!space && start < 0) start = column;
      if (start >= 0 && (space || column === columns)) {
        context.fillRect(10 + start, row * step, column - start, Math.max(0.4, step * 0.65));
        start = -1;
      }
      column += char === "\t" ? tabSize - column % tabSize : 1;
    }
    row++;
  }
  for (let cursor = markers.iter(); cursor.value; cursor.next()) {
    const line = doc.lineAt(cursor.from).number;
    context.fillStyle = colors[cursor.value.type] || colors.modified;
    context.fillRect(1, Math.max(0, Math.min(height - 2, (line - 1) * step)), 4, Math.max(2, step));
  }
}

export function desktopMinimap(changedLines, openChange = null) {
  return ViewPlugin.fromClass(class {
    constructor(view) {
      this.view = view;
      this.frame = null;
      this.redraw = true;
      this.pointer = null;
      this.resize = null;
      this.preferredWidth = defaultWidth;
      try {
        const saved = Number(window.localStorage.getItem(widthStorageKey));
        if (Number.isFinite(saved) && saved > 0) this.preferredWidth = saved;
      } catch {}
      this.size = "";
      this.dom = view.dom.ownerDocument.createElement("div");
      this.dom.className = "cm-pyne-minimap";
      this.dom.tabIndex = 0;
      this.dom.setAttribute("role", "slider");
      this.dom.setAttribute("aria-label", "Code overview");
      this.dom.setAttribute("aria-orientation", "vertical");
      this.dom.setAttribute("aria-valuemin", "1");
      this.canvas = view.dom.ownerDocument.createElement("canvas");
      this.canvas.setAttribute("aria-hidden", "true");
      this.viewport = view.dom.ownerDocument.createElement("div");
      this.viewport.className = "cm-pyne-minimap-viewport";
      this.regionLabels = view.dom.ownerDocument.createElement("div");
      this.regionLabels.className = "cm-pyne-minimap-regions";
      this.dom.append(this.canvas, this.viewport, this.regionLabels);
      this.resizer = view.dom.ownerDocument.createElement("div");
      this.resizer.className = "cm-pyne-minimap-resizer";
      this.resizer.tabIndex = 0;
      this.resizer.setAttribute("role", "separator");
      this.resizer.setAttribute("aria-label", "Resize code overview");
      this.resizer.setAttribute("aria-orientation", "vertical");
      this.resizer.setAttribute("aria-valuemin", "64");
      view.dom.append(this.resizer, this.dom);
      this.media = window.matchMedia(mobileQuery);
      this.onMedia = () => {
        this.dom.hidden = this.media.matches;
        this.resizer.hidden = this.media.matches;
        if (this.media.matches) this.finishResize();
        this.schedule(true);
      };
      this.onScroll = () => this.schedule();
      this.observer = new ResizeObserver(() => this.schedule(true));
      this.observer.observe(this.dom);
      this.observer.observe(view.dom);
      this.media.addEventListener("change", this.onMedia);
      view.scrollDOM.addEventListener("scroll", this.onScroll, { passive: true });
      this.resizer.addEventListener("pointerdown", event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        this.resize = { id: event.pointerId, x: event.clientX, width: this.dom.clientWidth };
        this.resizer.setAttribute("data-resizing", "true");
        this.resizer.setPointerCapture(event.pointerId);
      });
      this.resizer.addEventListener("pointermove", event => {
        if (!this.resize || event.pointerId !== this.resize.id) return;
        event.preventDefault();
        this.preferredWidth = minimapWidth(this.resize.width + this.resize.x - event.clientX, view.dom.clientWidth);
        this.applyWidth();
        this.schedule(true);
      });
      for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
        this.resizer.addEventListener(type, event => this.finishResize(event));
      }
      this.resizer.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        const width = event.key === "Home" ? 64 : event.key === "End" ? 360
          : this.dom.clientWidth + (event.key === "ArrowLeft" ? 10 : -10);
        this.preferredWidth = minimapWidth(width, view.dom.clientWidth);
        this.applyWidth();
        this.saveWidth();
        this.schedule(true);
      });
      this.resizer.addEventListener("dblclick", event => {
        event.preventDefault();
        event.stopPropagation();
        this.preferredWidth = defaultWidth;
        this.applyWidth();
        this.saveWidth();
        this.schedule(true);
      });
      this.dom.addEventListener("pointerdown", event => {
        if (event.button !== 0) return;
        if (event.target.closest?.(".cm-pyne-minimap-region")) return;
        event.preventDefault();
        const rect = this.dom.getBoundingClientRect();
        if (openChange && rect.height > 0 && event.clientX - rect.left <= 7) {
          const line = minimapLine(event.clientY - rect.top, rect.height, view.state.doc.lines);
          const tolerance = Math.ceil(2 / Math.min(2, rect.height / view.state.doc.lines));
          if (openChange(view, line, tolerance, true)) return;
        }
        this.pointer = event.pointerId;
        this.dom.setPointerCapture(event.pointerId);
        this.jumpAt(event.clientY);
      });
      this.dom.addEventListener("pointermove", event => {
        if (event.pointerId === this.pointer) this.jumpAt(event.clientY);
      });
      const release = event => {
        if (event.pointerId !== this.pointer) return;
        this.pointer = null;
        if (this.dom.hasPointerCapture(event.pointerId)) this.dom.releasePointerCapture(event.pointerId);
      };
      this.dom.addEventListener("pointerup", release);
      this.dom.addEventListener("pointercancel", release);
      this.dom.addEventListener("lostpointercapture", () => { this.pointer = null; });
      this.dom.addEventListener("keydown", event => {
        if (event.target !== this.dom) return;
        const current = this.firstLine || 1;
        const page = Math.max(1, (this.lastLine || current) - current);
        const target = {
          ArrowUp: current - 1, ArrowDown: current + 1,
          PageUp: current - page, PageDown: current + page,
          Home: 1, End: view.state.doc.lines,
        }[event.key];
        if (target == null) return;
        event.preventDefault();
        event.stopPropagation();
        this.jump(target, "start");
      });
      this.onMedia();
    }

    applyWidth() {
      const editorWidth = this.view.dom.clientWidth;
      if (!editorWidth) return;
      const width = minimapWidth(this.preferredWidth, editorWidth);
      if (width !== this.width) {
        this.width = width;
        this.view.dom.style.setProperty("--pyne-minimap-width", `${width}px`);
        this.view.requestMeasure();
      }
      this.resizer.setAttribute("aria-valuemax", String(minimapWidth(360, editorWidth)));
      this.resizer.setAttribute("aria-valuenow", String(width));
    }

    saveWidth() {
      try { window.localStorage.setItem(widthStorageKey, String(this.preferredWidth)); } catch {}
    }

    finishResize(event = null) {
      if (!this.resize || (event && event.pointerId !== this.resize.id)) return;
      const { id } = this.resize;
      this.resize = null;
      this.resizer.removeAttribute("data-resizing");
      if (this.resizer.hasPointerCapture(id)) this.resizer.releasePointerCapture(id);
      this.saveWidth();
    }

    renderRegions(height) {
      const state = this.view.state;
      // The normal highlighter may stop before the end of a large file.
      const complete = ensureSyntaxTree(state, state.doc.length, 5);
      const tree = complete || syntaxTree(state);
      this.regionPending = !!state.facet(language) && !complete;
      if (this.regionPending) this.schedule();
      if (this.regionDoc !== state.doc || this.regionTree !== tree) {
        this.regions = minimapRegions(state, tree);
        this.regionDoc = state.doc;
        this.regionTree = tree;
      }
      const step = Math.min(2, height / state.doc.lines);
      const labels = [];
      let bottom = -1;
      for (const region of this.regions) {
        const top = Math.max(0, Math.min(height - 12, (region.line - 1) * step));
        // Keep nearby headings from overlapping at overview scale.
        if (height < 12 || top < bottom) continue;
        const label = this.dom.ownerDocument.createElement("button");
        label.type = "button";
        label.className = "cm-pyne-minimap-region";
        label.textContent = region.title;
        label.style.top = `${top}px`;
        label.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          this.jump(region.line);
        });
        labels.push(label);
        bottom = top + 12;
      }
      this.regionLabels.replaceChildren(...labels);
    }

    jumpAt(clientY) {
      const rect = this.dom.getBoundingClientRect();
      if (!rect.height) return;
      this.jump(minimapLine(clientY - rect.top, rect.height, this.view.state.doc.lines));
    }

    jump(line, align = "center") {
      const doc = this.view.state.doc;
      const position = doc.line(Math.max(1, Math.min(doc.lines, line))).from;
      this.view.dispatch({ effects: EditorView.scrollIntoView(position, { y: align }) });
    }

    schedule(redraw = false) {
      this.redraw ||= redraw;
      if (this.media.matches || this.frame !== null) return;
      this.frame = requestAnimationFrame(() => {
        this.frame = null;
        if (!this.media.matches) this.render();
      });
    }

    render() {
      const { view } = this;
      this.applyWidth();
      const width = this.dom.clientWidth;
      const height = this.dom.clientHeight;
      if (!width || !height) return;
      const scale = window.devicePixelRatio || 1;
      const size = `${width}:${height}:${scale}`;
      // Scrolling only moves the viewport overlay; the code image stays cached.
      const redraw = this.redraw || size !== this.size;
      if (redraw) {
        const context = this.canvas.getContext("2d");
        if (!context) return;
        this.canvas.width = Math.round(width * scale);
        this.canvas.height = Math.round(height * scale);
        context.setTransform(scale, 0, 0, scale, 0, 0);
        drawMinimap(context, view.state.doc, width, height, view.state.field(changedLines), view.state.tabSize);
        this.size = size;
        this.redraw = false;
      }
      if (redraw || this.regionPending) this.renderRegions(height);
      const rect = view.scrollDOM.getBoundingClientRect();
      const top = Math.max(0, rect.top - view.documentTop);
      const bottom = Math.max(top, top + view.scrollDOM.clientHeight - 1);
      this.firstLine = view.state.doc.lineAt(view.lineBlockAtHeight(top).from).number;
      this.lastLine = view.state.doc.lineAt(view.lineBlockAtHeight(bottom).to).number;
      const step = Math.min(2, height / view.state.doc.lines);
      const viewportHeight = Math.min(height, Math.max(4, (this.lastLine - this.firstLine + 1) * step));
      this.viewport.style.height = `${viewportHeight}px`;
      this.viewport.style.top = `${Math.min(height - viewportHeight, (this.firstLine - 1) * step)}px`;
      this.dom.setAttribute("aria-valuemax", String(view.state.doc.lines));
      this.dom.setAttribute("aria-valuenow", String(this.firstLine));
    }

    update(update) {
      const marksChanged = update.startState.field(changedLines) !== update.state.field(changedLines);
      const syntaxChanged = syntaxTree(update.startState) !== syntaxTree(update.state);
      if (syntaxChanged) this.regionPending = true;
      if (update.docChanged || marksChanged || syntaxChanged || update.geometryChanged || update.viewportChanged) {
        this.schedule(update.docChanged || marksChanged);
      }
    }

    destroy() {
      this.finishResize();
      if (this.frame !== null) cancelAnimationFrame(this.frame);
      this.observer.disconnect();
      this.media.removeEventListener("change", this.onMedia);
      this.view.scrollDOM.removeEventListener("scroll", this.onScroll);
      this.view.dom.style.removeProperty("--pyne-minimap-width");
      this.resizer.remove();
      this.dom.remove();
    }
  }, {
    provide: () => EditorView.editorAttributes.of({ class: "cm-pyne-has-minimap" }),
  });
}
