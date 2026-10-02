var App = window.App || (window.App = {});

App.indicators = {
  definitions: [
    { id: "sma", name: "SMA", period: 20, colors: ["#1565c0"] },
    { id: "ema", name: "EMA", period: 20, colors: ["#e65100"] },
    { id: "bb", name: "Bollinger Bands", period: 20, multiplier: 2, colors: ["#d32f2f", "#1565c0", "#00897b"] },
    { id: "rsi", name: "RSI", period: 14, colors: ["#7b1fa2"], pane: true, range: [0, 100], levels: [30, 70] },
    { id: "macd", name: "MACD", fastPeriod: 12, slowPeriod: 26, signalPeriod: 9,
      colors: ["#1565c0", "#e65100", "#00897b", "#d32f2f"], pane: true, levels: [0],
      lineNames: ["MACD", "Signal", "Histogram"], colorNames: ["MACD", "Signal", "Positive", "Negative"] },
    { id: "smi", name: "SMI", period: 10, smooth1: 3, smooth2: 3, signalPeriod: 3,
      colors: ["#1565c0", "#e65100"], pane: true, range: [-100, 100], levels: [-40, 0, 40],
      lineNames: ["SMI", "Signal"] },
    { id: "vwap", name: "VWAP", colors: ["#c2185b"] },
  ],
  series: new Map(),
  pending: new Map(),
  revision: 0,
  busy: false,
  needsReset: false,
  worker: null,
  frame: null,
  failed: false,
  paneFrame: null,
  panels: new Map(),
  collapsedHeight: 36,
  hovering: false,

  active() { return this.settings?.filter(item => item.enabled) || []; },
  isOpen() { return !!this.menu && !this.menu.classList.contains("hidden"); },
  fields(def) {
    if (def.id === "vwap") return [];
    if (def.id === "macd") return ["fastPeriod", "slowPeriod", "signalPeriod"];
    if (def.id === "smi") return ["period", "smooth1", "smooth2", "signalPeriod"];
    return ["period", ...(def.id === "bb" ? ["multiplier"] : [])];
  },
  fieldSpec(field) {
    return {
      label: { period: "Length", multiplier: "StdDev", fastPeriod: "Fast", slowPeriod: "Slow", signalPeriod: "Signal", smooth1: "Smooth 1", smooth2: "Smooth 2" }[field],
      min: field === "multiplier" ? 0.1 : field === "period" ? 2 : 1,
      max: field === "multiplier" ? 10 : 500,
      step: field === "multiplier" ? 0.1 : 1,
    };
  },
  loadSettings(saved) {
    return this.definitions.map(def => {
      const old = Array.isArray(saved) ? saved.find(item => item?.id === def.id) : null;
      const legacyBbColors = def.id === "bb" && old?.colors?.length === 3 &&
        ["#00897b", "#78909c", "#00897b"].every((color, index) => old.colors[index]?.toLowerCase?.() === color);
      const colors = legacyBbColors ? null : old?.colors;
      const params = Object.fromEntries(this.fields(def).map(field => {
        const value = old?.[field], spec = this.fieldSpec(field);
        return [field, Number.isFinite(value) && value >= spec.min && value <= spec.max &&
          (spec.step !== 1 || Number.isInteger(value)) ? value : def[field]];
      }));
      if (def.id === "macd" && params.fastPeriod >= params.slowPeriod) {
        params.fastPeriod = def.fastPeriod;
        params.slowPeriod = def.slowPeriod;
      }
      return {
        id: def.id, enabled: old?.enabled === true,
        ...params,
        colors: def.colors.map((color, index) => /^#[0-9a-f]{6}$/i.test(colors?.[index]) ? colors[index] : color),
        ...(def.pane ? {
          collapsed: old?.collapsed === true,
          expandedHeight: Number.isFinite(old?.expandedHeight) && old.expandedHeight >= 30 ? old.expandedHeight : undefined,
        } : {}),
      };
    });
  },
  save() {
    try { localStorage.setItem(App.config.storageKey("indicators"), JSON.stringify(this.settings)); } catch {}
  },
  init() {
    this.button = document.getElementById("chart-indicators-toggle");
    this.menu = document.getElementById("chart-indicators-menu");
    if (!this.button || !this.menu) return;
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(App.config.storageKey("indicators"))) || []; } catch {}
    this.settings = this.loadSettings(saved);
    this.initPanels();
    this.attachPaneResizePersistence();
    if (window.ResizeObserver) {
      this.paneObserver = new ResizeObserver(() => this.schedulePaneLayout());
      this.paneObserver.observe(App.chart.container);
    }
    App.chart.chart.subscribeCrosshairMove(param => this.updateCrosshairValue(param));
    this.renderMenu();
    this.button.addEventListener("click", event => {
      if (App.measure?.shouldSuppressToolbarClick(event)) return;
      event.stopPropagation();
      if (!this.menu.classList.contains("hidden")) { this.close(); return; }
      App.measure?.setActive(false);
      App.measure?.closePalette();
      App.trendline?.setActive(false);
      App.timeframes?.closeMenu();
      App.sessionPicker?.close();
      this.menu.classList.remove("hidden");
      this.button.setAttribute("aria-expanded", "true");
      App.ui?.refreshMobileViewportLock();
      this.position();
    });
    this.menu.addEventListener("change", event => this.changeSetting(event.target));
    this.menu.addEventListener("click", event => {
      const gear = event.target.closest("[data-settings]");
      if (!gear) return;
      const open = gear.getAttribute("aria-expanded") !== "true";
      this.menu.querySelectorAll("[data-settings]").forEach(button => {
        const expanded = button === gear && open;
        button.setAttribute("aria-expanded", String(expanded));
        document.getElementById(button.getAttribute("aria-controls")).classList.toggle("hidden", !expanded);
      });
      this.position();
    });
    this.menu.addEventListener("dblclick", event => event.preventDefault());
    document.getElementById("chart-indicators-close").addEventListener("click", () => this.close(true));
    document.addEventListener("pointerdown", event => {
      if (!this.menu.contains(event.target) && !this.button.contains(event.target)) this.close();
    }, true);
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !this.menu.classList.contains("hidden")) {
        event.preventDefault();
        this.close(true);
      }
    });
    const resize = () => {
      if (this.isOpen()) this.position();
      this.schedulePaneLayout();
    };
    window.addEventListener("resize", resize);
    window.visualViewport?.addEventListener("resize", resize);
    window.visualViewport?.addEventListener("scroll", resize);
    window.addEventListener("pagehide", () => this.stop());
    window.addEventListener("pageshow", event => {
      if (event.persisted) { this.reset(); this.schedulePaneLayout(); }
    });
    this.syncSeries();
    this.reset();
    App.chart.container.addEventListener("pointerup", () => {
      requestAnimationFrame(() => App.chart.positionNavButtons());
    }, { passive: true });
  },
  initPanels() {
    const template = document.getElementById("chart-indicator-pane-template");
    for (const def of this.definitions.filter(item => item.pane)) {
      const header = template.content.firstElementChild.cloneNode(true);
      const toggle = header.querySelector("button"), title = header.querySelector(".chart-indicator-pane-title");
      const values = (def.lineNames || [def.name]).map(name => {
        const value = document.createElement("output");
        value.textContent = "--";
        value.setAttribute("aria-label", name);
        toggle.append(value);
        return value;
      });
      this.panels.set(def.id, { header, toggle, title, values, latest: [] });
      header.addEventListener("dblclick", event => event.preventDefault());
      header.addEventListener("pointerdown", event => event.stopPropagation());
      this.attachPaneToggle(def.id, toggle);
      document.body.append(header);
    }
  },
  paneFor(id) { return this.series.get(id)?.[0].getPane(); },
  attachPaneResizePersistence() {
    let drag = null;
    App.chart.container.addEventListener("pointerdown", event => {
      const panes = this.settings.filter(item => item.enabled && !item.collapsed && this.panels.has(item.id))
        .map(item => ({ id: item.id, pane: this.paneFor(item.id), height: this.paneFor(item.id)?.getHeight() }));
      drag = { id: event.pointerId, panes, chartHeight: App.chart.container.clientHeight };
    }, { capture: true, passive: true });
    window.addEventListener("pointerup", event => {
      if (event.pointerId !== drag?.id) return;
      const start = drag;
      drag = null;
      // Native pane resizing is laid out asynchronously; persist only a completed user resize.
      requestAnimationFrame(() => {
        if (App.chart.container.clientHeight !== start.chartHeight) return;
        for (const item of start.panes) {
          if (item.pane && this.paneFor(item.id) === item.pane && item.pane.getHeight() !== item.height) this.rememberPaneHeight(item.id);
        }
      });
    }, { capture: true, passive: true });
    window.addEventListener("pointercancel", () => { drag = null; }, { passive: true });
  },
  rememberPaneHeight(id) {
    const setting = this.settings.find(item => item.id === id);
    if (!setting?.enabled || setting.collapsed) return;
    const height = this.paneFor(id)?.getHeight();
    if (!Number.isFinite(height) || height < 30 || height === setting.expandedHeight) return;
    setting.expandedHeight = height;
    this.save();
  },
  attachPaneToggle(id, button) {
    let touch = null, ignoreClickUntil = 0;
    const consume = event => {
      if (event.cancelable) event.preventDefault();
      event.stopPropagation();
    };
    // Cancel compatibility mouse events before resizing moves the button away from the finger.
    button.addEventListener("touchstart", event => {
      consume(event);
      ignoreClickUntil = Date.now() + 750;
      const point = event.touches[0];
      touch = event.touches.length === 1 ? { id: point.identifier, x: point.clientX, y: point.clientY } : null;
    }, { passive: false });
    button.addEventListener("touchmove", event => {
      consume(event);
      const point = event.touches[0];
      if (touch && (event.touches.length !== 1 || point.identifier !== touch.id ||
          Math.hypot(point.clientX - touch.x, point.clientY - touch.y) > 10)) touch = null;
    }, { passive: false });
    button.addEventListener("touchend", event => {
      consume(event);
      ignoreClickUntil = Date.now() + 750;
      const start = touch;
      touch = null;
      const point = Array.from(event.changedTouches).find(item => item.identifier === start?.id);
      if (!start || !point || event.touches.length || Math.hypot(point.clientX - start.x, point.clientY - start.y) > 10) return;
      const rect = button.getBoundingClientRect();
      if (point.clientX >= rect.left && point.clientX <= rect.right && point.clientY >= rect.top && point.clientY <= rect.bottom) this.togglePane(id);
    }, { passive: false });
    button.addEventListener("touchcancel", event => {
      consume(event);
      touch = null;
      ignoreClickUntil = Date.now() + 750;
    }, { passive: false });
    button.addEventListener("click", event => {
      event.stopPropagation();
      if (event.detail !== 0 && Date.now() < ignoreClickUntil) {
        event.preventDefault();
        return;
      }
      this.togglePane(id);
    });
  },
  changeSetting(input) {
    const setting = this.settings.find(item => item.id === input.dataset.id);
    if (!setting) return;
    const colorOnly = input.type === "color";
    if (input.type === "checkbox") setting.enabled = input.checked;
    else if (colorOnly) {
      if (!/^#[0-9a-f]{6}$/i.test(input.value)) return;
      setting.colors[Number(input.dataset.color)] = input.value;
      this.menu.querySelector(`[data-swatch="${setting.id}"]`).style.background = setting.colors[0];
    } else {
      const value = input.valueAsNumber;
      if (!input.checkValidity() || !Number.isFinite(value)) {
        input.value = setting[input.dataset.field];
        return;
      }
      if (setting.id === "macd" && ((input.dataset.field === "fastPeriod" && value >= setting.slowPeriod) ||
          (input.dataset.field === "slowPeriod" && value <= setting.fastPeriod))) {
        input.value = setting[input.dataset.field];
        return;
      }
      setting[input.dataset.field] = value;
    }
    this.save();
    this.syncSeries();
    if (!colorOnly) this.reset();
  },
  renderMenu() {
    const rows = document.getElementById("chart-indicator-options");
    rows.replaceChildren(...this.settings.map(setting => {
      const def = this.definitions.find(item => item.id === setting.id);
      const row = document.createElement("div");
      row.className = "chart-indicator-row";
      const label = document.createElement("label");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.dataset.id = def.id;
      check.checked = setting.enabled;
      const swatch = document.createElement("span");
      swatch.className = "chart-indicator-swatch";
      swatch.dataset.swatch = def.id;
      swatch.style.background = setting.colors[0];
      label.append(check, swatch, document.createTextNode(def.name));
      row.append(label);
      const gear = document.createElement("button");
      gear.type = "button";
      gear.dataset.settings = def.id;
      gear.dataset.tooltip = "Settings";
      gear.setAttribute("aria-label", `${def.name} settings`);
      gear.setAttribute("aria-expanded", "false");
      gear.setAttribute("aria-controls", `chart-indicator-settings-${def.id}`);
      gear.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';
      const fields = document.createElement("div");
      fields.id = gear.getAttribute("aria-controls");
      fields.className = "chart-indicator-settings hidden";
      row.append(gear, fields);
      for (const field of this.fields(def)) {
        const spec = this.fieldSpec(field);
        const fieldLabel = document.createElement("label");
        fieldLabel.className = "chart-indicator-field";
        fieldLabel.textContent = spec.label;
        const input = document.createElement("input");
        input.type = "number";
        input.inputMode = spec.step === 1 ? "numeric" : "decimal";
        input.min = String(spec.min);
        input.max = String(spec.max);
        input.step = String(spec.step);
        input.value = setting[field];
        input.dataset.id = def.id;
        input.dataset.field = field;
        input.setAttribute("aria-label", `${def.name} ${field}`);
        fieldLabel.append(input);
        fields.append(fieldLabel);
      }
      const colors = document.createElement("div");
      colors.className = `chart-indicator-colors${setting.colors.length === 1 ? " single" : ""}`;
      setting.colors.forEach((color, index) => {
        const colorLabel = document.createElement("label");
        colorLabel.className = "chart-indicator-field";
        const name = def.colorNames?.[index] || def.lineNames?.[index] || (def.id === "bb" ? ["Upper", "Basis", "Lower"][index] : "Color");
        colorLabel.textContent = name;
        const input = document.createElement("input");
        input.type = "color";
        input.value = color;
        input.dataset.id = def.id;
        input.dataset.color = index;
        input.setAttribute("aria-label", `${def.name} ${name.toLowerCase()}`);
        colorLabel.append(input);
        colors.append(colorLabel);
      });
      fields.append(colors);
      return row;
    }));
  },
  position() {
    const anchor = this.button.getBoundingClientRect();
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
    this.menu.style.maxHeight = `${Math.max(80, height - 24)}px`;
    const rect = this.menu.getBoundingClientRect();
    this.menu.style.left = `${Math.max(left + 8, Math.min(anchor.right + 8, left + width - rect.width - 8))}px`;
    this.menu.style.top = `${Math.max(top + 8, Math.min(anchor.top, top + height - rect.height - 8))}px`;
  },
  close(focus = false) {
    const wasOpen = this.isOpen();
    this.menu?.classList.add("hidden");
    this.button?.setAttribute("aria-expanded", "false");
    if (wasOpen) App.ui?.refreshMobileViewportLock();
    if (focus) this.button?.focus({ preventScroll: true });
  },
  syncSeries() {
    const chart = App.chart.chart;
    const heights = new Map();
    let layoutChanged = false;
    for (const setting of this.settings) {
      const current = this.series.get(setting.id);
      if (this.panels.has(setting.id) && current) heights.set(setting.id, this.paneFor(setting.id).getHeight());
      if (!setting.enabled && current) {
        current.forEach(series => chart.removeSeries(series));
        this.series.delete(setting.id);
        if (this.panels.has(setting.id)) layoutChanged = true;
      }
    }
    for (const setting of this.settings) {
      if (!setting.enabled) continue;
      const current = this.series.get(setting.id);
      const def = this.definitions.find(item => item.id === setting.id);
      if (!current) {
        const paneIndex = def.pane ? chart.panes().length : 0;
        const count = def.lineNames?.length || def.colors.length;
        const lines = Array.from({ length: count }, (_, index) => {
          const histogram = def.id === "macd" && index === 2;
          return chart.addSeries(histogram ? LightweightCharts.HistogramSeries : LightweightCharts.LineSeries, {
            color: setting.colors[index], priceLineVisible: false, lastValueVisible: true,
            ...(histogram ? { base: 0 } : { lineWidth: 1 }),
            ...(def.range ? { autoscaleInfoProvider: () => ({ priceRange: { minValue: def.range[0], maxValue: def.range[1] } }) } : {}),
          }, paneIndex);
        });
        this.series.set(setting.id, lines);
        if (def.pane) {
          layoutChanged = true;
          heights.set(setting.id, setting.expandedHeight || Math.max(100, Math.round(App.chart.container.clientHeight * 0.25)));
          lines[0].priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: 0.12 } });
          for (const price of def.levels) lines[0].createPriceLine({
            price, color: "#9e9e9e", lineWidth: 1, lineStyle: 2, axisLabelVisible: true,
          });
        }
      }
      this.series.get(setting.id).forEach((series, index) => series.applyOptions({
        title: def.lineNames?.[index] || (setting.id === "vwap" ? "VWAP (UTC)" : setting.id === "bb" ? `BB ${setting.period} ${["Upper", "Basis", "Lower"][index]}` : `${def.name} ${setting.period}`),
        color: (setting.colors || def.colors)[index],
        ...(def.pane ? { visible: !setting.collapsed, lastValueVisible: !setting.collapsed } : {}),
      }));
      if (setting.id === "macd") {
        const panel = this.panels.get("macd"), colors = setting.colors.slice(2).join();
        if (panel && panel.histogramColors !== colors) {
          const histogram = this.series.get("macd")[2];
          if (panel.histogramColors) histogram.setData(this.colorPoints("macd", 2, histogram.data()));
          panel.histogramColors = colors;
        }
      }
      // LWC 5.0.9 requires all right-axis widgets during pane layout. The compact header covers the axis instead.
    }
    if (layoutChanged) this.applyPaneHeights(heights);
    this.button?.classList.toggle("active", this.active().length > 0);
    App.chart.syncManualAlertChipPosition();
    App.measure?.scheduleRender();
    requestAnimationFrame(() => App.chart.positionNavButtons?.());
    this.schedulePaneLayout();
  },
  paneSpace() {
    const chart = App.chart.chart;
    return Math.max(0, App.chart.container.clientHeight - chart.timeScale().height() - chart.panes().length + 1);
  },
  compactPaneHeight() {
    return Math.min(this.collapsedHeight, this.paneSpace() / App.chart.chart.panes().length);
  },
  applyPaneHeights(overrides = new Map()) {
    const chart = App.chart.chart, available = this.paneSpace();
    if (available <= 0) return;
    const lower = this.active().filter(setting => this.panels.has(setting.id) && this.paneFor(setting.id));
    const compact = this.compactPaneHeight();
    const heights = lower.map(setting => setting.collapsed ? compact :
      (overrides.get(setting.id) || this.paneFor(setting.id).getHeight() || 100));
    const fixed = lower.filter(setting => setting.collapsed).length * compact;
    const expanded = heights.reduce((sum, value, index) => sum + (lower[index].collapsed ? 0 : value), 0);
    const room = available - Math.min(100, available / (lower.length + 1)) - fixed;
    const scale = expanded ? Math.min(1, Math.max(0, room) / expanded) : 1;
    let used = 0;
    // Set all stretch factors together: setting heights one by one rescales neighboring panes.
    lower.forEach((setting, index) => {
      const height = Math.max(2, setting.collapsed ? compact : heights[index] * scale);
      this.paneFor(setting.id).setStretchFactor(height);
      used += height;
    });
    chart.panes()[0].setStretchFactor(Math.max(2, available - used));
  },
  togglePane(id) {
    const setting = this.settings.find(item => item.id === id);
    const pane = this.paneFor(id);
    if (!setting?.enabled || !pane) return;
    this.rememberPaneHeight(id);
    setting.collapsed = !setting.collapsed;
    this.save();
    this.applyPaneHeights(new Map([[id, setting.expandedHeight || Math.max(100, Math.round(App.chart.container.clientHeight * 0.25))]]));
    this.syncSeries();
  },
  showPaneValues(id, values = []) {
    const setting = this.settings.find(item => item.id === id);
    this.panels.get(id)?.values.forEach((output, index) => {
      const value = values[index];
      output.textContent = Number.isFinite(value) ? value.toFixed(2) : "--";
      output.style.color = setting.colors[id === "macd" && index === 2 && value < 0 ? 3 : index];
    });
  },
  updateCrosshairValue(param) {
    this.hovering = param?.time != null;
    for (const [id, panel] of this.panels) {
      this.showPaneValues(id, this.hovering ? this.series.get(id)?.map(series => param.seriesData?.get(series)?.value) : panel.latest);
    }
  },
  schedulePaneLayout() {
    if (!this.panels.size || this.paneFrame !== null) return;
    this.paneFrame = requestAnimationFrame(() => {
      this.paneFrame = null;
      this.positionPaneHeaders();
    });
  },
  positionPaneHeaders() {
    let resizeCompact = false;
    const chartRect = App.chart.container.getBoundingClientRect();
    for (const [id, panel] of this.panels) {
      const setting = this.settings.find(item => item.id === id);
      const def = this.definitions.find(item => item.id === id);
      const pane = setting.enabled ? this.paneFor(id) : null;
      const element = pane?.getHTMLElement();
      if (panel.element !== element) {
        if (panel.element) this.paneObserver?.unobserve(panel.element);
        panel.element = element;
        if (element) this.paneObserver?.observe(element);
      }
      panel.header.classList.toggle("hidden", !element);
      if (!element) continue;
      if (setting.collapsed && Math.abs(pane.getHeight() - this.compactPaneHeight()) > 1) resizeCompact = true;
      const rect = element.getBoundingClientRect();
      panel.header.classList.toggle("collapsed", !!setting.collapsed);
      Object.assign(panel.header.style, {
        left: `${chartRect.left}px`, top: `${rect.top}px`,
        width: `${chartRect.width}px`, height: setting.collapsed ? `${rect.height}px` : "",
      });
      panel.toggle.setAttribute("aria-expanded", String(!setting.collapsed));
      panel.toggle.setAttribute("aria-label", `${setting.collapsed ? "Expand" : "Collapse"} ${def.name}`);
      panel.title.textContent = `${def.name} ${this.fields(def).map(field => setting[field]).join(" ")}`;
      if (!this.hovering) this.showPaneValues(id, panel.latest);
    }
    if (resizeCompact) this.applyPaneHeights();
    App.chart.syncManualAlertChipPosition();
    App.measure?.scheduleRender();
    App.chart.positionNavButtons?.();
  },
  stop() {
    this.worker?.terminate();
    this.worker = null;
    this.busy = false;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.pending.clear();
  },
  clearPaneValues() {
    this.hovering = false;
    for (const [id, panel] of this.panels) {
      panel.latest = [];
      this.showPaneValues(id);
    }
  },
  workerBar(bar) { return { time: bar.time, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume }; },
  colorPoints(id, index, points) {
    if (id !== "macd" || index !== 2) return points;
    const colors = this.settings.find(item => item.id === id).colors;
    return points.map(point => Number.isFinite(point.value)
      ? { ...point, color: colors[point.value < 0 ? 3 : 2] } : point);
  },
  reset() {
    this.failed = false;
    this.status();
    this.revision++;
    this.pending.clear();
    this.needsReset = true;
    this.series.forEach(lines => lines.forEach(series => series.setData([])));
    this.clearPaneValues();
    if (!this.active().length) { this.stop(); return; }
    this.schedule();
  },
  update(bar) {
    if (this.failed || !this.active().length || this.needsReset) return;
    this.pending.set(bar.time, this.workerBar(bar));
    this.schedule();
  },
  schedule() {
    if (this.frame !== null) return;
    this.frame = requestAnimationFrame(() => { this.frame = null; this.flush(); });
  },
  fail(message) {
    this.stop();
    this.failed = true;
    this.series.forEach(lines => lines.forEach(series => series.setData([])));
    this.clearPaneValues();
    this.status(message);
  },
  status(message = "") {
    const status = document.getElementById("chart-indicator-status");
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("hidden", !message);
  },
  flush() {
    if (this.failed || this.busy || !this.active().length || (!this.needsReset && !this.pending.size)) return;
    try {
      if (!this.worker) {
        const worker = this.worker = new Worker("/static/chart_indicators_worker.js?v=3");
        worker.onmessage = ({ data }) => {
          if (worker !== this.worker) return;
          this.busy = false;
          if (data.revision === this.revision) {
            if (data.error) { this.fail(`Indicator calculation failed: ${data.error}`); return; }
            for (const item of data.series) {
              const lines = this.series.get(item.id);
              item.lines.forEach((points, index) => {
                const colored = this.colorPoints(item.id, index, points);
                if (data.kind === "reset") lines?.[index].setData(colored);
                else colored.forEach(point => lines?.[index].update(point));
              });
              const panel = this.panels.get(item.id);
              if (panel) {
                panel.latest = item.lines.map(points => points.at(-1)?.value);
                if (!this.hovering) this.showPaneValues(item.id, panel.latest);
              }
            }
            this.status();
          }
          this.schedule();
        };
        worker.onerror = event => {
          event.preventDefault();
          if (worker === this.worker) this.fail("Indicators could not be loaded. Reload the chart to retry.");
        };
      }
      const kind = this.needsReset ? "reset" : "update";
      const bars = this.needsReset
        ? App.collections.ohlcvData.map(bar => this.workerBar(bar))
        : [...this.pending.values()].sort((a, b) => a.time - b.time);
      this.worker.postMessage({ kind, revision: this.revision, configs: this.active(), bars });
      this.pending.clear();
      this.needsReset = false;
      this.busy = true;
    } catch {
      this.fail("Indicators could not be loaded. Reload the chart to retry.");
    }
  },
};
