import { EditorView, ViewPlugin } from "@codemirror/view";

const mobileQuery = "(max-width: 720px), (hover: none) and (pointer: coarse)";
const colors = { added: "#2ea043", modified: "#1f6feb", deleted: "#f85149" };

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
      this.dom.append(this.canvas, this.viewport);
      view.dom.append(this.dom);
      this.media = window.matchMedia(mobileQuery);
      this.onMedia = () => {
        this.dom.hidden = this.media.matches;
        this.schedule(true);
      };
      this.onScroll = () => this.schedule();
      this.observer = new ResizeObserver(() => this.schedule(true));
      this.observer.observe(this.dom);
      this.media.addEventListener("change", this.onMedia);
      view.scrollDOM.addEventListener("scroll", this.onScroll, { passive: true });
      this.dom.addEventListener("pointerdown", event => {
        if (event.button !== 0) return;
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
      const width = this.dom.clientWidth;
      const height = this.dom.clientHeight;
      if (!width || !height) return;
      const scale = window.devicePixelRatio || 1;
      const size = `${width}:${height}:${scale}`;
      // Scrolling only moves the viewport overlay; the code image stays cached.
      if (this.redraw || size !== this.size) {
        const context = this.canvas.getContext("2d");
        if (!context) return;
        this.canvas.width = Math.round(width * scale);
        this.canvas.height = Math.round(height * scale);
        context.setTransform(scale, 0, 0, scale, 0, 0);
        drawMinimap(context, view.state.doc, width, height, view.state.field(changedLines), view.state.tabSize);
        this.size = size;
        this.redraw = false;
      }
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
      if (update.docChanged || marksChanged || update.geometryChanged || update.viewportChanged) {
        this.schedule(update.docChanged || marksChanged);
      }
    }

    destroy() {
      if (this.frame !== null) cancelAnimationFrame(this.frame);
      this.observer.disconnect();
      this.media.removeEventListener("change", this.onMedia);
      this.view.scrollDOM.removeEventListener("scroll", this.onScroll);
      this.dom.remove();
    }
  }, {
    provide: () => EditorView.editorAttributes.of({ class: "cm-pyne-has-minimap" }),
  });
}
