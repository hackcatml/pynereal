var App = window.App || (window.App = {});

// A display-only drawing. Its worker reads minute candles independently of
// chart pagination, so selecting a large range never moves or resets the chart.
App.volumeProfile = {
  active: false, selected: false, draft: null, gesture: null, range: null,
  result: null, worker: null, requestId: 0, suppressUntil: 0,
  claimedTouchStart: false,
  toolbarDrag: null, toolbarPosition: null,
  settings: { rows: 48, valueArea: 70, width: 35,
    up: "#1565c0", down: "#e69138", poc: "#c2185b" },

  init() {
    this.button = document.getElementById("frvp-tool-toggle");
    this.menu = document.getElementById("frvp-menu");
    this.toolbar = document.getElementById("frvp-selection-toolbar");
    this.toolbarHandle = document.getElementById("frvp-drag-handle");
    this.settingsButton = document.getElementById("frvp-settings-toggle");
    this.statusElement = document.getElementById("frvp-status");
    if (!this.button || !this.menu) return;
    this.views = [{ renderer: () => this, zOrder: () => "normal" }];
    App.chart.candleSeries.attachPrimitive(this);
    this.button.addEventListener("click", event => {
      if (App.measure.shouldSuppressToolbarClick(event)) return;
      this.close();
      App.measure.closePalette();
      this.setActive(!this.active);
    });
    this.settingsButton.addEventListener("click", event => {
      event.stopPropagation();
      if (!this.range || !this.selected) return;
      if (!this.menu.classList.contains("hidden")) this.close();
      else {
        this.setActive(false);
        App.measure.setActive(false);
        App.trendline.setActive(false);
        App.indicators.close();
        this.selected = true;
        this.menu.classList.remove("hidden");
        App.ui.refreshMobileViewportLock();
        this.settingsButton.setAttribute("aria-expanded", "true");
        this.position();
        this.refresh();
      }
    });
    document.getElementById("frvp-close").addEventListener("click", () => this.close());
    document.getElementById("frvp-delete").addEventListener("click", () => this.remove());
    document.getElementById("frvp-selection-delete").addEventListener("click", () => this.remove());
    document.getElementById("frvp-retry").addEventListener("click", () => this.load());
    this.menu.addEventListener("change", event => this.change(event.target));
    this.menu.addEventListener("dblclick", event => event.preventDefault());
    this.toolbar.addEventListener("dblclick", event => event.preventDefault());
    this.toolbarHandle.addEventListener("pointerdown", event => this.startToolbarDrag(event));
    this.toolbarHandle.addEventListener("click", event => this.consume(event));
    this.toolbarHandle.addEventListener("lostpointercapture", event => this.endToolbarDrag(event));
    window.addEventListener("pointermove", event => this.moveToolbarDrag(event), { capture: true, passive: false });
    window.addEventListener("pointerup", event => {
      this.moveToolbarDrag(event);
      this.endToolbarDrag(event);
    }, { capture: true, passive: false });
    document.addEventListener("pointerdown", event => this.pointerDown(event), { capture: true, passive: false });
    window.addEventListener("pointermove", event => this.pointerMove(event), { capture: true, passive: false });
    window.addEventListener("pointerup", event => this.pointerUp(event), { capture: true, passive: false });
    window.addEventListener("pointercancel", () => this.cancel(), { capture: true });
    for (const type of ["touchstart", "touchmove", "touchend", "click", "dblclick"]) {
      document.addEventListener(type, event => {
        const release = ["touchend", "click", "dblclick"].includes(type);
        const claimedStart = type === "touchstart" && this.claimedTouchStart;
        if (type === "touchstart") this.claimedTouchStart = false;
        if (App.chart.container.contains(event.target) &&
            (this.gesture || claimedStart || (release && performance.now() < this.suppressUntil))) this.consume(event);
      }, { capture: true, passive: false });
    }
    document.addEventListener("keydown", event => {
      if (event.target.closest?.("input, textarea, select, [contenteditable=true]")) return;
      if (event.key === "Escape") { this.setActive(false); this.selected = false; this.close(); this.refresh(); }
      if (["Delete", "Backspace"].includes(event.key) && this.selected && this.range) {
        event.preventDefault(); this.remove();
      }
    });
    window.addEventListener("blur", () => this.cancel());
    window.addEventListener("resize", () => this.position());
    window.visualViewport?.addEventListener("resize", () => this.position());
    window.visualViewport?.addEventListener("scroll", () => this.position());
    if (window.ResizeObserver) {
      this.layoutObserver = new ResizeObserver(() => this.position());
      this.layoutObserver.observe(App.chart.container);
      this.layoutObserver.observe(document.getElementById("chart-info"));
    }
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.stop();
      else if (this.range) this.load();
    });
    window.addEventListener("pagehide", () => this.stop());
    window.addEventListener("pageshow", () => { if (this.range && !this.worker) this.load(); });
    this.restore();
    this.renderSettings();
    this.refresh();
  },

  attached({ requestUpdate }) { this.requestUpdate = requestUpdate; },
  detached() { this.requestUpdate = null; },
  paneViews() { return this.views; },
  storageKey() { return App.config.storageKey("frvp"); },
  save() {
    try { localStorage.setItem(this.storageKey(), JSON.stringify({ range: this.range, settings: this.settings })); } catch {}
  },
  restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(this.storageKey()));
      if (!saved) return;
      for (const key of ["rows", "valueArea", "width", "up", "down", "poc"]) {
        const input = this.menu.querySelector(`[name="${key}"]`);
        if (saved.settings?.[key] != null) {
          input.value = saved.settings[key];
          if (input.checkValidity() && input.value) this.settings[key] = input.type === "number" ? Number(input.value) : input.value;
        }
      }
      const range = saved.range;
      if (range && Number.isSafeInteger(range.from) && Number.isSafeInteger(range.to) &&
          range.from >= 0 && range.to > range.from && Number.isSafeInteger(range.interval) && range.interval >= 60) {
        this.range = range;
        // Session metadata is loaded asynchronously after tool initialization.
        this.restorePending = true;
      }
    } catch {}
  },
  ready() {
    if (!this.button) return;
    this.button.disabled = !App.state.minuteChartAvailable && App.state.configuredTimeframeSec !== 60;
    if (this.restorePending) { this.restorePending = false; this.load(); }
  },
  stop() {
    this.requestId++;
    this.worker?.terminate();
    this.worker = null;
  },
  load() {
    this.stop();
    if (!this.range || document.hidden) return;
    if (!App.state.minuteChartAvailable && App.state.configuredTimeframeSec !== 60) {
      this.status("1m data is unavailable for this session.", true);
      return;
    }
    const id = this.requestId;
    const url = new URL(`${App.config.apiBase}/chart-window`, location.href);
    url.searchParams.set("candles_only", "true");
    if (App.state.configuredTimeframeSec !== 60) url.searchParams.set("timeframe", "1m");
    const socketUrl = new URL(`${App.config.wsPath}/minute-chart`, location.href);
    socketUrl.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    this.status("Loading 1m data...");
    try {
      const worker = this.worker = new Worker("/static/volume_profile_worker.js?v=1", { type: "module" });
      worker.onmessage = ({ data }) => {
        if (data.id !== this.requestId) return;
        if (data.type === "result") {
          this.result = data;
          this.status(data.total ? `${data.candles.toLocaleString("en-US")} candles \u00b7 1m` : "No traded volume in this range.");
          this.renderDetails();
          this.refresh();
        } else if (data.type === "loading") this.status(`Loading 1m data... ${data.candles.toLocaleString("en-US")}`);
        else if (data.type === "error") this.status(data.message, true);
      };
      worker.onerror = () => { this.stop(); this.status("Volume Profile failed to load. Retry.", true); };
      worker.postMessage({ type: "load", id, url: url.href, ...this.range,
        socketUrl: App.state.configuredTimeframeSec === 60 ? null : socketUrl.href,
        ...this.settings });
    } catch { this.status("Volume Profile worker is unavailable.", true); }
  },
  onBar(bar) {
    if (App.state.configuredTimeframeSec === 60 && this.worker && this.range &&
        bar.time >= this.range.from && bar.time < this.range.to) {
      this.worker.postMessage({ type: "bars", id: this.requestId,
        bars: [{ ...bar, source: "live", updated_at: Date.now() / 1000 }] });
    }
  },
  status(text, error = false) {
    this.statusElement.textContent = text;
    this.statusElement.classList.toggle("error", error);
    this.settingsButton.classList.toggle("frvp-error", error);
    this.settingsButton.dataset.tooltip = error ? "FRVP: data unavailable" : text.startsWith("Loading") ? text : "Settings";
  },
  renderSettings() {
    for (const [key, value] of Object.entries(this.settings)) this.menu.querySelector(`[name="${key}"]`).value = value;
    this.renderDetails();
  },
  renderDetails() {
    const format = time => new Date(time * 1000).toISOString().slice(0, 16).replace("T", " ");
    document.getElementById("frvp-range").textContent = this.range ?
      `${format(this.range.from)} - ${format(this.range.to)} UTC` : "";
    const result = this.result;
    document.getElementById("frvp-coverage").textContent = result?.total ?
      `${format(result.first)} - ${format(result.last + 60)} UTC` : "";
    for (const key of ["poc", "vah", "val"]) {
      const step = result?.bins?.[0] ? result.bins[0].high - result.bins[0].low : 1;
      const decimals = Math.min(12, Math.max(2, Math.ceil(-Math.log10(step)) + 1));
      document.getElementById(`frvp-${key}-value`).textContent = result?.total ?
        result[key].toLocaleString("en-US", { maximumFractionDigits: decimals }) : "-";
    }
  },
  change(input) {
    if (!(input.name in this.settings)) return;
    if (!input.value || !input.checkValidity()) { input.value = this.settings[input.name]; return; }
    this.settings[input.name] = input.type === "number" ? Number(input.value) : input.value;
    this.save();
    if (["rows", "valueArea"].includes(input.name)) {
      this.worker?.postMessage({ type: "settings", id: this.requestId, ...this.settings });
    }
    this.refresh();
  },
  isOpen() { return !!this.menu && !this.menu.classList.contains("hidden"); },
  close() {
    this.menu?.classList.add("hidden");
    this.settingsButton?.setAttribute("aria-expanded", "false");
    App.ui.refreshMobileViewportLock();
  },
  startToolbarDrag(event) {
    if (event.button !== 0 || event.isPrimary === false || this.toolbarDrag) return;
    const rect = this.toolbar.getBoundingClientRect();
    this.toolbarDrag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY,
      left: rect.left, top: rect.top, moved: false };
    this.toolbar.classList.add("dragging");
    try { this.toolbarHandle.setPointerCapture(event.pointerId); } catch {}
    this.consume(event);
  },
  moveToolbarDrag(event) {
    const drag = this.toolbarDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    this.consume(event);
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 3) return;
    drag.moved = true;
    this.toolbarPosition = { left: drag.left + dx, top: drag.top + dy };
    this.position();
  },
  endToolbarDrag(event) {
    const drag = this.toolbarDrag;
    if (!drag || (event && drag.pointerId !== event.pointerId)) return;
    this.toolbarDrag = null;
    if (drag.moved) this.toolbarPosition = {
      left: parseFloat(this.toolbar.style.left), top: parseFloat(this.toolbar.style.top)
    };
    this.toolbar.classList.remove("dragging");
    try { this.toolbarHandle.releasePointerCapture(drag.pointerId); } catch {}
  },
  position() {
    if (!this.toolbar || this.toolbar.classList.contains("hidden")) return;
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    const plot = App.measure.plotBoundsClient();
    if (!plot) return;
    const toolbar = this.toolbar.getBoundingClientRect();
    const info = document.getElementById("chart-info").getBoundingClientRect();
    const minLeft = Math.max(left, plot.left) + 8;
    const maxLeft = Math.min(left + width, plot.right) - toolbar.width - 8;
    const minTop = Math.max(top, plot.top) + 8;
    const maxTop = Math.min(top + height, plot.bottom) - toolbar.height - 8;
    const desired = this.toolbarPosition || {
      left: (plot.left + plot.right - toolbar.width) / 2, top: Math.max(plot.top + 12, info.bottom + 8)
    };
    this.toolbar.style.left = `${Math.max(minLeft, Math.min(desired.left, maxLeft))}px`;
    this.toolbar.style.top = `${Math.max(minTop, Math.min(desired.top, maxTop))}px`;
    if (this.menu.classList.contains("hidden")) return;
    const anchor = this.settingsButton.getBoundingClientRect();
    this.menu.style.maxHeight = `${height - 24}px`;
    const rect = this.menu.getBoundingClientRect();
    this.menu.style.left = `${Math.max(left + 8, Math.min(anchor.left, left + width - rect.width - 8))}px`;
    this.menu.style.top = `${Math.max(top + 8, Math.min(anchor.bottom + 10, top + height - rect.height - 8))}px`;
  },
  refresh() {
    this.button?.classList.toggle("active", this.active);
    this.button?.setAttribute("aria-pressed", String(this.active));
    const showToolbar = !!this.range && this.selected && !this.active && !this.draft;
    this.toolbar?.classList.toggle("hidden", !showToolbar);
    if (!showToolbar) this.endToolbarDrag();
    if (!showToolbar && this.isOpen()) this.close();
    this.position();
    document.body.classList.toggle("frvp-active", this.active || !!this.gesture);
    App.measure.toolsButton?.classList.toggle("active", this.active || !!App.trendline.active || !!App.state.measureToolActive);
    this.requestUpdate?.();
  },
  setActive(active) {
    this.cancel();
    this.active = Boolean(active);
    this.selected = false;
    if (this.active) {
      App.measure.setActive(false);
      App.trendline.setActive(false);
      App.indicators.close();
      App.ui.closeManualAlertConfirm?.();
      App.ui.closeManualAlertMenu?.();
      App.chart.setMagnetMode(false);
    } else App.chart.restoreMagnetMode();
    this.refresh();
  },
  remove() {
    this.stop(); this.cancel(); this.range = this.result = null;
    this.active = this.selected = false;
    this.close(); this.save(); this.refresh();
    App.chart.restoreMagnetMode();
  },
  consume(event) { event.preventDefault(); event.stopImmediatePropagation(); },
  claim(event) {
    // Selection taps must not start a chart touch gesture whose release we consume.
    if (event.type === "pointerdown" && event.pointerType === "touch") this.claimedTouchStart = true;
    this.suppressUntil = performance.now() + 400;
    App.chart.manualAlertLastTap = null;
    App.chart.manualAlertLineSuppressTapUntil = this.suppressUntil;
    this.consume(event);
  },
  point(event) {
    const point = App.trendline.pointFromEvent(event);
    if (!point) return null;
    const bars = App.collections.ohlcvData;
    let left = 0, right = bars.length;
    while (left < right) {
      const mid = (left + right) >>> 1;
      if (bars[mid].time < point.time) left = mid + 1;
      else right = mid;
    }
    const candidates = [bars[left - 1], bars[left]].filter(bar => Number.isFinite(bar?.close));
    candidates.sort((a, b) => Math.abs(a.time - point.time) - Math.abs(b.time - point.time));
    return candidates[0]?.time ?? null;
  },
  xAt(time) {
    const scale = App.chart.chart.timeScale();
    const logical = App.trendline.logicalAt(time);
    if (logical == null) return null;
    const index = Math.floor(logical), left = scale.logicalToCoordinate(index), right = scale.logicalToCoordinate(index + 1);
    return left == null || right == null ? null : left + (right - left) * (logical - index);
  },
  edges() {
    const range = this.draft ? { from: Math.min(this.draft.start, this.draft.end),
      to: Math.max(this.draft.start, this.draft.end) + this.draft.interval, interval: this.draft.interval } : this.range;
    if (!range) return null;
    const left = this.xAt(range.from), right = this.xAt(range.to - range.interval);
    return left == null || right == null ? null : { left, right };
  },
  pointerDown(event) {
    if (this.menu.contains(event.target) || this.toolbar.contains(event.target)) return;
    if (event.button !== 0) return;
    if (this.isOpen()) this.close();
    if (!App.chart.container.contains(event.target)) {
      if (this.selected) { this.selected = false; this.refresh(); }
      return;
    }
    if (event.isPrimary === false) { this.setActive(false); return; }
    if (App.state.sourcePanelOpen || App.state.manualAlertMenuOpen || App.state.manualAlertConfirmOpen ||
        App.state.measureToolActive || App.trendline.active || !App.measure.isClientInPlot(event.clientX, event.clientY)) return;
    const rect = App.chart.container.getBoundingClientRect();
    const x = event.clientX - rect.left, edges = this.edges();
    const tolerance = event.pointerType === "touch" ? 16 : 8;
    const part = this.range && this.selected && edges ?
      Math.abs(x - edges.left) <= tolerance ? "start" : Math.abs(x - edges.right) <= tolerance ? "end" : null : null;
    if (!this.active && !part) {
      const y = event.clientY - rect.top, result = this.result;
      const low = result?.bins?.[0]?.low, high = result?.bins?.at(-1)?.high;
      const inProfile = edges && result?.total && x >= edges.left &&
        x <= edges.left + Math.max(12, edges.right - edges.left) * this.settings.width / 100 &&
        y >= App.chart.candleSeries.priceToCoordinate(high) && y <= App.chart.candleSeries.priceToCoordinate(low);
      this.selected = !!inProfile;
      if (inProfile) this.claim(event);
      this.refresh();
      return;
    }
    const time = this.point(event);
    if (time == null) return;
    this.claim(event);
    const kind = part || (this.draft ? "finish" : event.pointerType === "touch" ? "touch-start" : "start-draw");
    if (part) this.draft = { start: this.range.from, end: this.range.to - this.range.interval, interval: this.range.interval };
    else if (!this.draft) this.draft = { start: time, end: time, interval: App.timeframes.seconds() };
    if (!part) this.draft.end = time;
    this.gesture = { kind, pointerId: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
    try { App.chart.container.setPointerCapture(event.pointerId); } catch {}
    this.refresh();
  },
  pointerMove(event) {
    if (!this.gesture) {
      if (this.active && this.draft && event.pointerType === "mouse" && App.chart.container.contains(event.target)) {
        const time = this.point(event);
        if (time != null) { this.draft.end = time; this.refresh(); }
      }
      return;
    }
    if (event.pointerId !== this.gesture.pointerId) return;
    this.claim(event);
    if (Math.hypot(event.clientX - this.gesture.x, event.clientY - this.gesture.y) > 4) this.gesture.moved = true;
    const clamped = App.measure.clampToPlot(event.clientX, event.clientY);
    const time = clamped && this.point(clamped);
    if (time != null) {
      // Touch places one anchor per release; dragging the first touch aims the start.
      if (this.gesture.kind === "touch-start") this.draft.start = this.draft.end = time;
      else this.draft[this.gesture.kind === "start" ? "start" : "end"] = time;
    }
    this.refresh();
  },
  release() {
    try { App.chart.container.releasePointerCapture(this.gesture.pointerId); } catch {}
    this.gesture = null;
  },
  pointerUp(event) {
    if (!this.gesture || event.pointerId !== this.gesture.pointerId) return;
    this.pointerMove(event);
    const gesture = this.gesture;
    this.release();
    if (gesture.kind !== "touch-start" && (gesture.kind !== "start-draw" || gesture.moved)) {
      this.range = { from: Math.min(this.draft.start, this.draft.end),
        to: Math.max(this.draft.start, this.draft.end) + this.draft.interval, interval: this.draft.interval };
      this.draft = null; this.result = null; this.active = false; this.selected = true;
      App.chart.restoreMagnetMode();
      this.save(); this.renderDetails(); this.load();
    }
    this.refresh();
  },
  cancel() {
    this.endToolbarDrag();
    if (this.gesture) this.release();
    this.draft = null;
    this.refresh();
  },
  draw(target) {
    const edges = this.edges();
    if (!edges) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: rx, verticalPixelRatio: ry, bitmapSize }) => {
      const height = bitmapSize.height / ry;
      ctx.save();
      ctx.scale(rx, ry);
      const left = edges.left, right = Math.max(left + 4, edges.right);
      if (this.selected || this.draft) {
        ctx.fillStyle = "rgba(21,101,192,0.045)";
        ctx.fillRect(left, 0, right - left, height);
      }
      const result = this.result;
      if (result?.total && !this.draft) {
        const width = Math.max(12, right - left) * this.settings.width / 100;
        result.bins.forEach((bin, index) => {
          const top = App.chart.candleSeries.priceToCoordinate(bin.high), bottom = App.chart.candleSeries.priceToCoordinate(bin.low);
          if (top == null || bottom == null) return;
          const up = width * bin.up / result.maximum, down = width * bin.down / result.maximum;
          ctx.globalAlpha = index >= result.lower && index <= result.upper ? 0.75 : 0.32;
          ctx.fillStyle = this.settings.up; ctx.fillRect(left, top, up, Math.max(1, bottom - top - 1));
          ctx.fillStyle = this.settings.down; ctx.fillRect(left + up, top, down, Math.max(1, bottom - top - 1));
        });
        ctx.globalAlpha = 1;
        for (const key of ["poc", "vah", "val"]) {
          const y = App.chart.candleSeries.priceToCoordinate(result[key]);
          if (y == null) continue;
          ctx.strokeStyle = key === "poc" ? this.settings.poc : this.settings.up;
          ctx.lineWidth = key === "poc" ? 2 : 1;
          ctx.setLineDash(key === "poc" ? [] : [4, 3]);
          ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
          if (right > 0 && left < bitmapSize.width / rx) {
            ctx.font = "11px system-ui"; ctx.textBaseline = "bottom"; ctx.fillStyle = ctx.strokeStyle;
            ctx.fillText(key.toUpperCase(), Math.max(2, Math.min(right - 28, bitmapSize.width / rx - 28)), y - 3);
          }
        }
      }
      if (this.selected || this.draft) {
        ctx.globalAlpha = 1; ctx.strokeStyle = "#1565c0"; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
        for (const x of [left, right]) {
          ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
          ctx.setLineDash([]); ctx.beginPath(); ctx.arc(x, height - 14, 5, 0, Math.PI * 2);
          ctx.fillStyle = "#fff"; ctx.fill(); ctx.stroke(); ctx.setLineDash([4, 4]);
        }
      }
      ctx.restore();
    });
  }
};
