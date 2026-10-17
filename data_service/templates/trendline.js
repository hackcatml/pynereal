var App = window.App || (window.App = {});

// Drawings belong to the browser chart, not to the strategy's plot series.
App.trendline = {
  lines: [],
  draft: null,
  gesture: null,
  selectedId: null,
  active: false,
  nextId: 1,
  suppressUntil: 0,
  requestUpdate: null,
  toolbarDrag: null,
  toolbarPosition: null,

  init() {
    this.button = document.getElementById("trendline-tool-toggle");
    this.deleteButton = document.getElementById("trendline-delete");
    this.toolbar = document.getElementById("trendline-toolbar");
    this.toolbarHandle = document.getElementById("trendline-drag-handle");
    this.colorInput = document.getElementById("trendline-color");
    this.leftButton = document.getElementById("trendline-extend-left");
    this.rightButton = document.getElementById("trendline-extend-right");
    if (!this.button || !this.deleteButton || !App.chart?.candleSeries) return;
    this.views = [{ renderer: () => this, zOrder: () => "top" }];
    App.chart.candleSeries.attachPrimitive(this);
    this.button.addEventListener("click", (event) => {
      if (App.measure.shouldSuppressToolbarClick(event)) return;
      event.stopPropagation();
      App.measure.closePalette();
      this.setActive(!this.active);
    });
    this.deleteButton.addEventListener("click", (event) => {
      event.stopPropagation();
      this.deleteSelected();
    });
    this.colorInput.addEventListener("input", () => this.changeStyle("color", this.colorInput.value));
    this.leftButton.addEventListener("click", () => this.changeStyle("extendLeft", !this.selectedLine()?.extendLeft));
    this.rightButton.addEventListener("click", () => this.changeStyle("extendRight", !this.selectedLine()?.extendRight));
    this.toolbar.addEventListener("dblclick", event => event.preventDefault());
    this.toolbarHandle.addEventListener("pointerdown", event => this.startToolbarDrag(event));
    this.toolbarHandle.addEventListener("click", event => this.consume(event));
    this.toolbarHandle.addEventListener("lostpointercapture", event => this.endToolbarDrag(event));
    document.addEventListener("pointerdown", event => this.onPointerDown(event), { capture: true, passive: false });
    window.addEventListener("pointermove", event => {
      if (this.toolbarDrag) this.moveToolbarDrag(event);
      else this.onPointerMove(event);
    }, { capture: true, passive: false });
    window.addEventListener("pointerup", event => {
      if (this.toolbarDrag) { this.moveToolbarDrag(event); this.endToolbarDrag(event); }
      else this.onPointerUp(event);
    }, { capture: true, passive: false });
    window.addEventListener("pointercancel", event => {
      this.endToolbarDrag(event);
      this.onPointerCancel(event);
    }, { capture: true, passive: false });
    // Lightweight Charts also consumes touch/mouse compatibility events.
    for (const type of ["touchstart", "touchmove", "touchend", "click", "dblclick"]) {
      document.addEventListener(type, event => {
        // A new unclaimed touch must reach the chart with its matching release.
        if (type === "touchstart" && !this.gesture) this.suppressUntil = 0;
        const isRelease = type === "touchend" || type === "click" || type === "dblclick";
        if (App.chart.container.contains(event.target) &&
            (this.gesture || (isRelease && performance.now() < this.suppressUntil))) this.consume(event);
      }, { capture: true, passive: false });
    }
    document.addEventListener("keydown", event => {
      if (event.target.closest?.("input, textarea, select, [contenteditable=true]")) return;
      if (event.key === "Escape" && (this.active || this.selectedId != null)) {
        event.preventDefault();
        this.setActive(false);
      } else if ((event.key === "Delete" || event.key === "Backspace") && this.selectedId != null) {
        event.preventDefault();
        this.deleteSelected();
      }
    });
    window.addEventListener("blur", () => this.cancelGesture());
    window.addEventListener("resize", () => this.positionToolbar());
    window.visualViewport?.addEventListener("resize", () => this.positionToolbar());
    window.visualViewport?.addEventListener("scroll", () => this.positionToolbar());
    if (window.ResizeObserver) {
      this.layoutObserver = new ResizeObserver(() => this.positionToolbar());
      this.layoutObserver.observe(App.chart.container);
    }
  },

  attached({ requestUpdate }) { this.requestUpdate = requestUpdate; },
  detached() { this.requestUpdate = null; },
  paneViews() { return this.views; },

  refresh() {
    this.button?.classList.toggle("active", this.active);
    this.button?.setAttribute("aria-pressed", String(this.active));
    const line = this.selectedLine();
    const visible = !!line && !this.active && !this.draft;
    this.toolbar?.classList.toggle("hidden", !visible);
    if (!visible) this.endToolbarDrag();
    if (line) {
      this.colorInput.value = line.color || "#2962ff";
      for (const [button, enabled] of [[this.leftButton, line.extendLeft], [this.rightButton, line.extendRight]]) {
        button.classList.toggle("active", !!enabled);
        button.setAttribute("aria-pressed", String(!!enabled));
      }
      this.positionToolbar();
    }
    document.body.classList.toggle("trendline-active", this.active);
    document.body.classList.toggle("trendline-dragging", !!this.gesture);
    App.measure.toolsButton?.classList.toggle("active", this.active || !!App.state.measureToolActive);
    this.requestUpdate?.();
  },

  selectedLine() { return this.lines.find(line => line.id === this.selectedId); },
  changeStyle(key, value) {
    const line = this.selectedLine();
    if (!line) return;
    if (key === "color" && /^#[0-9a-f]{6}$/i.test(value)) line.color = value;
    else if (key === "extendLeft" || key === "extendRight") line[key] = !!value;
    this.refresh();
  },
  positionToolbar() {
    if (!this.toolbar || this.toolbar.classList.contains("hidden")) return;
    const plot = App.measure.plotBoundsClient();
    if (!plot) return;
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
    const rect = this.toolbar.getBoundingClientRect();
    const info = document.getElementById("chart-info")?.getBoundingClientRect();
    const desired = this.toolbarPosition || {
      left: (plot.left + plot.right - rect.width) / 2, top: Math.max(plot.top + 12, (info?.bottom || plot.top) + 8)
    };
    this.toolbar.style.left = `${Math.max(Math.max(left, plot.left) + 8, Math.min(desired.left, Math.min(left + width, plot.right) - rect.width - 8))}px`;
    this.toolbar.style.top = `${Math.max(Math.max(top, plot.top) + 8, Math.min(desired.top, Math.min(top + height, plot.bottom) - rect.height - 8))}px`;
  },
  startToolbarDrag(event) {
    if (event.button !== 0 || event.isPrimary === false || this.toolbarDrag) return;
    const rect = this.toolbar.getBoundingClientRect();
    this.toolbarDrag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
    this.toolbar.classList.add("dragging");
    try { this.toolbarHandle.setPointerCapture(event.pointerId); } catch {}
    this.consume(event);
  },
  moveToolbarDrag(event) {
    const drag = this.toolbarDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    this.consume(event);
    this.toolbarPosition = { left: drag.left + event.clientX - drag.x, top: drag.top + event.clientY - drag.y };
    this.positionToolbar();
  },
  endToolbarDrag(event) {
    const drag = this.toolbarDrag;
    if (!drag || (event && drag.pointerId !== event.pointerId)) return;
    this.toolbarDrag = null;
    this.toolbarPosition = { left: parseFloat(this.toolbar.style.left), top: parseFloat(this.toolbar.style.top) };
    this.toolbar.classList.remove("dragging");
    try { this.toolbarHandle.releasePointerCapture(drag.pointerId); } catch {}
  },

  setActive(active) {
    this.cancelGesture();
    this.active = Boolean(active);
    this.draft = null;
    this.selectedId = null;
    if (this.active) {
      App.volumeProfile?.setActive(false);
      App.measure.setActive(false);
      App.ui.closeManualAlertConfirm?.();
      App.ui.closeManualAlertMenu?.();
      App.chart.setMagnetMode(false);
    } else if (!App.state.measureToolActive) {
      App.chart.restoreMagnetMode();
    }
    this.refresh();
  },

  deleteSelected() {
    const selectedId = this.selectedId;
    this.cancelGesture();
    this.lines = this.lines.filter(line => line.id !== selectedId);
    this.selectedId = null;
    this.refresh();
  },

  consume(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  },

  claim(event) {
    this.suppressUntil = performance.now() + 400;
    App.chart.manualAlertLastTap = null;
    App.chart.manualAlertLineSuppressTapUntil = this.suppressUntil;
    this.consume(event);
  },

  interval() {
    return App.state.timeframeInterval || App.state.configuredTimeframeSec || 60;
  },

  // Use the chart's shared timeline: plots can contain times without candles.
  // LWC 5.0.9 only converts integer logical indexes, so interpolate explicitly.
  timeAtIndex(index) {
    const scale = App.chart.chart.timeScale();
    const x = scale.logicalToCoordinate(index);
    return x == null ? null : App.measure.normalizeTime(scale.coordinateToTime(x));
  },

  timeAt(logical) {
    const bars = App.collections.ohlcvData;
    if (!bars.length || !Number.isFinite(logical)) return null;
    const index = Math.floor(logical);
    const before = this.timeAtIndex(index), after = this.timeAtIndex(index + 1);
    if (before != null) return before + (logical - index) * (after == null ? this.interval() : after - before);
    if (after != null) return after - (index + 1 - logical) * this.interval();
    const edge = logical < 0 ? bars[0] : bars[bars.length - 1];
    const edgeIndex = App.chart.chart.timeScale().timeToIndex(edge.time, false);
    return edgeIndex == null ? null : edge.time + (logical - edgeIndex) * this.interval();
  },

  logicalAt(time) {
    const index = App.chart.chart.timeScale().timeToIndex(time, true);
    if (index == null) return null;
    const nearest = this.timeAtIndex(index);
    if (nearest == null) return null;
    const previous = time < nearest ? this.timeAtIndex(index - 1) : null;
    const step = previous == null ? this.interval() : nearest - previous;
    return index + (time - nearest) / step;
  },

  pointFromEvent(event) {
    const point = App.measure.pointFromClient(event.clientX, event.clientY);
    if (!point || point.logical == null || point.price == null) return null;
    let logical = point.logical;
    const scale = App.chart.chart.timeScale();
    const center = scale.logicalToCoordinate(logical);
    const next = scale.logicalToCoordinate(logical + 1);
    if (center != null && next != null && next !== center) {
      const x = event.clientX - App.chart.container.getBoundingClientRect().left;
      logical += (x - center) / (next - center);
    }
    const time = this.timeAt(logical);
    if (time == null || !Number.isFinite(point.price)) return null;
    return { time, price: point.price };
  },

  hasModifier(event) {
    return event.pointerType !== "touch" && (event.metaKey || event.ctrlKey);
  },

  snapPoint(point, event) {
    if (!point || !this.hasModifier(event)) return point;
    const bars = App.collections.ohlcvData;
    let low = 0, high = bars.length;
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (bars[mid].time < point.time) low = mid + 1;
      else high = mid;
    }
    const rect = App.chart.container.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    const fields = ["open", "high", "low", "close"];
    let nearest = null, bestX = Infinity, bestY = Infinity;
    // Skip pagination whitespace, and compare actual candle positions on the
    // shared timeline. Screen-space prices also work on a logarithmic scale.
    for (const direction of [-1, 1]) {
      for (let index = direction < 0 ? low - 1 : low; index >= 0 && index < bars.length; index += direction) {
        const bar = bars[index];
        if (!fields.every(field => bar[field] != null && Number.isFinite(Number(bar[field])))) continue;
        const candleX = App.chart.chart.timeScale().timeToCoordinate(bar.time);
        if (candleX == null) continue;
        const distanceX = Math.abs(candleX - x);
        for (const field of fields) {
          const price = Number(bar[field]);
          const candleY = App.chart.candleSeries.priceToCoordinate(price);
          if (candleY == null) continue;
          const distanceY = Math.abs(candleY - y);
          if (distanceX < bestX || (distanceX === bestX && distanceY < bestY)) {
            nearest = { time: bar.time, price };
            bestX = distanceX;
            bestY = distanceY;
          }
        }
        break;
      }
    }
    return nearest || point;
  },

  coordinate(point) {
    const logical = this.logicalAt(point.time);
    if (logical == null) return null;
    const scale = App.chart.chart.timeScale();
    const index = Math.floor(logical);
    const left = scale.logicalToCoordinate(index), right = scale.logicalToCoordinate(index + 1);
    const x = left == null || right == null ? null : left + (right - left) * (logical - index);
    const y = App.chart.candleSeries.priceToCoordinate(point.price);
    return x == null || y == null || !Number.isFinite(x) || !Number.isFinite(y) ? null : { x, y };
  },

  segment(line, start, end, width, height) {
    // Extension is chronological (left/right), independent of drawing direction.
    let a = start, b = end;
    if (a.x > b.x || (a.x === b.x && a.y > b.y)) [a, b] = [b, a];
    const dx = b.x - a.x, dy = b.y - a.y;
    if (dx === 0 && dy === 0) return { start: a, end: b };
    let from = line.extendLeft ? -Infinity : 0;
    let to = line.extendRight ? Infinity : 1;
    for (const [origin, delta, limit] of [[a.x, dx, width], [a.y, dy, height]]) {
      if (delta === 0) { if (origin < 0 || origin > limit) return null; continue; }
      const low = -origin / delta, high = (limit - origin) / delta;
      from = Math.max(from, Math.min(low, high));
      to = Math.min(to, Math.max(low, high));
      if (from > to) return null;
    }
    return { start: { x: a.x + dx * from, y: a.y + dy * from }, end: { x: a.x + dx * to, y: a.y + dy * to } };
  },

  hitTest(event) {
    const rect = App.chart.container.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    const tolerance = event.pointerType === "mouse" ? 8 : 18;
    const bounds = App.measure.plotBoundsClient();
    if (!bounds) return null;
    for (let i = this.lines.length - 1; i >= 0; i--) {
      const line = this.lines[i];
      const start = this.coordinate(line.start), end = this.coordinate(line.end);
      if (!start || !end) continue;
      if (Math.hypot(x - start.x, y - start.y) <= tolerance) return { line, part: "start" };
      if (Math.hypot(x - end.x, y - end.y) <= tolerance) return { line, part: "end" };
      const segment = this.segment(line, start, end, bounds.width, bounds.height);
      if (!segment) continue;
      const a = segment.start, b = segment.end;
      const dx = b.x - a.x, dy = b.y - a.y;
      const length = dx * dx + dy * dy;
      const t = length ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / length)) : 0;
      if (Math.hypot(x - a.x - t * dx, y - a.y - t * dy) <= tolerance) return { line, part: "move" };
    }
    return null;
  },

  onPointerDown(event) {
    if (event.button !== 0 || this.toolbar?.contains(event.target)) return;
    if (!App.chart.container.contains(event.target)) {
      if (this.selectedId != null) { this.selectedId = null; this.refresh(); }
      return;
    }
    if (event.isPrimary === false) {
      if (this.active || this.gesture) this.setActive(false);
      return;
    }
    if (!this.active && !this.lines.length) return;
    if (App.volumeProfile?.active || App.state.measureToolActive || App.state.sourcePanelOpen ||
        App.state.manualAlertMenuOpen || App.state.manualAlertConfirmOpen ||
        !App.measure.isClientInPlot(event.clientX, event.clientY)) return;
    const point = this.pointFromEvent(event);
    if (!point) return;
    const hit = this.active ? null : this.hitTest(event);
    if (!this.active && !hit) {
      this.selectedId = null;
      this.refresh();
      return;
    }
    this.claim(event);
    if (hit) {
      App.volumeProfile?.setActive(false);
      this.selectedId = hit.line.id;
      this.gesture = { kind: hit.part, line: hit.line,
        sourceId: hit.line.id, copyRequested: hit.part === "move" && this.hasModifier(event),
        original: { start: { ...hit.line.start }, end: { ...hit.line.end } } };
    } else {
      const kind = this.draft ? "draw-finish" : "draw-start";
      const snapped = this.snapPoint(point, event);
      if (!this.draft) this.draft = { start: snapped, end: snapped };
      this.draft.end = snapped;
      this.gesture = { kind };
    }
    Object.assign(this.gesture, { pointerId: event.pointerId, origin: point,
      clientX: event.clientX, clientY: event.clientY, moved: false });
    try { App.chart.container.setPointerCapture(event.pointerId); } catch {}
    this.refresh();
  },

  onPointerMove(event) {
    const gesture = this.gesture;
    if (!gesture) {
      if (this.active && this.draft && event.pointerType === "mouse" && App.chart.container.contains(event.target)) {
        const point = this.snapPoint(this.pointFromEvent(event), event);
        if (point) { this.draft.end = point; this.requestUpdate?.(); }
      }
      return;
    }
    if (event.pointerId !== gesture.pointerId) return;
    this.claim(event);
    if (Math.hypot(event.clientX - gesture.clientX, event.clientY - gesture.clientY) > 4) gesture.moved = true;
    const clamped = App.measure.clampToPlot(event.clientX, event.clientY);
    const pointer = clamped ? { ...clamped, metaKey: event.metaKey, ctrlKey: event.ctrlKey, pointerType: event.pointerType } : null;
    let point = pointer ? this.pointFromEvent(pointer) : null;
    if (!point || !gesture.moved) return;
    if (gesture.kind !== "move") point = this.snapPoint(point, pointer);
    if (gesture.kind === "draw-start" || gesture.kind === "draw-finish") this.draft.end = point;
    else if (gesture.kind === "move") {
      if (!gesture.copyId && (gesture.copyRequested || this.hasModifier(event))) {
        Object.assign(gesture.line, { start: { ...gesture.original.start }, end: { ...gesture.original.end } });
        gesture.line = { ...gesture.line, id: this.nextId++, start: { ...gesture.original.start }, end: { ...gesture.original.end } };
        gesture.copyId = gesture.line.id;
        this.lines.push(gesture.line);
        this.selectedId = gesture.copyId;
      }
      const shift = this.logicalAt(point.time) - this.logicalAt(gesture.origin.time);
      for (const part of ["start", "end"]) {
        const original = gesture.original[part];
        gesture.line[part] = { time: this.timeAt(this.logicalAt(original.time) + shift),
          price: original.price + point.price - gesture.origin.price };
      }
    } else gesture.line[gesture.kind] = point;
    this.requestUpdate?.();
  },

  releasePointer() {
    try { App.chart.container.releasePointerCapture(this.gesture.pointerId); } catch {}
    this.gesture = null;
  },

  onPointerUp(event) {
    const gesture = this.gesture;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    this.onPointerMove(event);
    this.claim(event);
    this.releasePointer();
    if ((gesture.kind === "draw-start" && gesture.moved) || gesture.kind === "draw-finish") {
      const start = this.coordinate(this.draft.start), end = this.coordinate(this.draft.end);
      if (start && end && Math.hypot(end.x - start.x, end.y - start.y) >= 4) {
        const line = { ...this.draft, id: this.nextId++ };
        this.lines.push(line);
        this.selectedId = line.id;
        this.draft = null;
        this.active = false;
        App.chart.restoreMagnetMode();
      }
    }
    this.refresh();
  },

  onPointerCancel(event) {
    if (this.gesture?.pointerId !== event.pointerId) return;
    this.claim(event);
    this.cancelGesture();
  },

  cancelGesture() {
    this.endToolbarDrag();
    if (this.gesture?.copyId) {
      this.lines = this.lines.filter(line => line.id !== this.gesture.copyId);
      this.selectedId = this.gesture.sourceId;
    } else if (this.gesture?.original) Object.assign(this.gesture.line, this.gesture.original);
    if (this.gesture) this.releasePointer();
    this.draft = null;
    this.refresh();
  },

  draw(target) {
    if (!this.lines.length && !this.draft) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: rx, verticalPixelRatio: ry, bitmapSize }) => {
      ctx.save();
      ctx.lineWidth = 2 * ry;
      ctx.lineCap = "round";
      for (const line of this.draft ? [...this.lines, this.draft] : this.lines) {
        const start = this.coordinate(line.start), end = this.coordinate(line.end);
        if (!start || !end) continue;
        const segment = this.segment(line, start, end, bitmapSize.width / rx, bitmapSize.height / ry);
        ctx.strokeStyle = line.color || "#2962ff";
        ctx.setLineDash(line === this.draft ? [5 * rx, 4 * rx] : []);
        if (segment) {
          ctx.beginPath();
          ctx.moveTo(segment.start.x * rx, segment.start.y * ry);
          ctx.lineTo(segment.end.x * rx, segment.end.y * ry);
          ctx.stroke();
        }
        if (line.id === this.selectedId || line === this.draft) {
          ctx.setLineDash([]);
          for (const point of [start, end]) {
            ctx.beginPath();
            ctx.ellipse(point.x * rx, point.y * ry, 4 * rx, 4 * ry, 0, 0, Math.PI * 2);
            ctx.fillStyle = "#ffffff";
            ctx.fill();
            ctx.stroke();
          }
        }
      }
      ctx.restore();
    });
  }
};
