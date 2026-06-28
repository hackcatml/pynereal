var App = window.App || (window.App = {});

App.measure = {
  overlay: document.getElementById("measure-overlay"),
  label: document.getElementById("measure-label"),
  toolsButton: document.getElementById("drawing-tools-toggle"),
  palette: document.getElementById("drawing-tool-palette"),
  toggleButton: document.getElementById("measure-tool-toggle"),
  pointerId: null,
  pointerCandidate: null,
  renderFrame: null,
  lastToolbarTouchAt: 0,

  init() {
    if (!this.overlay || !this.label || !this.toolsButton || !this.palette || !this.toggleButton) return;
    this.installToolbarDoubleTapGuard();

    this.toolsButton.addEventListener("click", (e) => {
      e.stopPropagation();
      this.togglePalette();
    });

    this.toggleButton.addEventListener("click", (e) => {
      e.stopPropagation();
      this.closePalette();
      this.setActive(!App.state.measureToolActive);
    });

    App.chart.container.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    App.chart.container.addEventListener("pointermove", (e) => this.onPointerMove(e));
    window.addEventListener("pointermove", (e) => this.onPointerMove(e));
    window.addEventListener("pointerup", (e) => this.onPointerUp(e));
    window.addEventListener("pointercancel", (e) => this.onPointerUp(e));

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && App.state.measureToolActive) {
        this.setActive(false);
      }
      if (e.key === "Escape") {
        this.closePalette();
      }
    });

    document.addEventListener("pointerdown", (e) => {
      if (!this.palette || this.palette.classList.contains("hidden")) return;
      if (e.target.closest("#drawing-toolbar")) return;
      this.closePalette();
    });

    try {
      App.chart.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.scheduleRender());
    } catch {}

    window.addEventListener("resize", () => this.scheduleRender());
    window.addEventListener("orientationchange", () => this.scheduleRender());
    this.scheduleRender();
  },

  installToolbarDoubleTapGuard() {
    const toolbar = document.getElementById("drawing-toolbar");
    if (!toolbar) return;
    toolbar.addEventListener("touchend", (e) => {
      const now = Date.now();
      const isDoubleTap = now - this.lastToolbarTouchAt < 360;
      this.lastToolbarTouchAt = now;
      if (!isDoubleTap) return;

      const button = e.target.closest("button");
      if (!button || !toolbar.contains(button)) return;
      e.preventDefault();
      button.click();
    }, { passive: false });
  },

  togglePalette(forceOpen = null) {
    const open = forceOpen === null ? this.palette.classList.contains("hidden") : Boolean(forceOpen);
    this.palette.classList.toggle("hidden", !open);
    this.toolsButton.setAttribute("aria-expanded", open ? "true" : "false");
  },

  closePalette() {
    this.togglePalette(false);
  },

  setActive(active) {
    App.state.measureToolActive = Boolean(active);
    document.body.classList.toggle("measure-active", App.state.measureToolActive);
    this.toolsButton.classList.toggle("active", App.state.measureToolActive);
    this.toggleButton.classList.toggle("active", App.state.measureToolActive);
    this.toggleButton.setAttribute("aria-pressed", App.state.measureToolActive ? "true" : "false");
    if (App.state.measureToolActive) {
      if (App.ui) {
        App.ui.closeManualAlertConfirm();
        App.ui.closeManualAlertMenu();
        App.ui.closeAlertTemplateModal();
      }
      if (App.chart) {
        App.chart.setMagnetMode(false);
      }
    } else {
      App.state.measureDraft = null;
      this.pointerId = null;
      this.pointerCandidate = null;
      if (App.chart) {
        App.chart.restoreMagnetMode();
      }
    }
    this.scheduleRender();
  },

  clear() {
    App.state.measureDraft = null;
    App.state.measureResult = null;
    this.pointerId = null;
    this.pointerCandidate = null;
    this.overlay.innerHTML = "";
    this.label.classList.add("hidden");
  },

  chartRect() {
    return App.chart.container.getBoundingClientRect();
  },

  normalizeTime(time) {
    if (typeof time === "number" && Number.isFinite(time)) return Number(time);
    if (time && typeof time.timestamp === "number") return Number(time.timestamp);
    return null;
  },

  shouldSnapToOhlc(e) {
    const isCoarsePointer = window.matchMedia &&
      window.matchMedia("(hover: none) and (pointer: coarse)").matches;
    return Boolean(
      e &&
      e.metaKey &&
      e.pointerType !== "touch" &&
      !isCoarsePointer
    );
  },

  snapPointToOhlc(point) {
    const bars = App.collections && Array.isArray(App.collections.ohlcvData)
      ? App.collections.ohlcvData
      : [];
    if (!point || !bars.length || !Number.isFinite(Number(point.logical))) return point;

    const index = Math.max(0, Math.min(bars.length - 1, Math.round(Number(point.logical))));
    const bar = bars[index];
    if (!bar) return point;

    const candidates = ["open", "high", "low", "close"]
      .map(key => ({ key, price: Number(bar[key]) }))
      .filter(candidate => Number.isFinite(candidate.price));
    if (!candidates.length) return point;

    let nearest = candidates[0];
    let nearestDistance = Math.abs(nearest.price - Number(point.price));
    for (let i = 1; i < candidates.length; i += 1) {
      const distance = Math.abs(candidates[i].price - Number(point.price));
      if (distance < nearestDistance) {
        nearest = candidates[i];
        nearestDistance = distance;
      }
    }

    return {
      time: Number(bar.time),
      logical: index,
      price: nearest.price,
      snap: nearest.key
    };
  },

  pointFromEvent(e) {
    const plotPoint = App.chart.plotPointFromClient(e.clientX, e.clientY);
    if (!plotPoint) return null;
    const time = this.normalizeTime(plotPoint.time);
    const logical = Number(plotPoint.logical);
    if (time == null && !Number.isFinite(logical)) return null;
    const point = {
      time,
      logical: Number.isFinite(logical) ? logical : null,
      price: plotPoint.price
    };
    return this.shouldSnapToOhlc(e) ? this.snapPointToOhlc(point) : point;
  },

  pointToCoordinate(point) {
    if (!point || !App.chart || !App.chart.chart || !App.chart.candleSeries) return null;
    const rect = this.chartRect();
    const timeScale = App.chart.chart.timeScale();
    let x = null;
    if (Number.isFinite(Number(point.logical))) {
      try {
        x = timeScale.logicalToCoordinate(Number(point.logical));
      } catch {}
    }
    if (!Number.isFinite(Number(x)) && point.time != null) {
      x = timeScale.timeToCoordinate(point.time);
    }
    const y = App.chart.candleSeries.priceToCoordinate(point.price);
    if (!Number.isFinite(Number(x)) || !Number.isFinite(Number(y))) return null;
    return {
      x: rect.left + Number(x),
      y: rect.top + Number(y)
    };
  },

  onPointerDown(e) {
    if (!App.state.measureToolActive || e.button !== 0) return;
    const point = this.pointFromEvent(e);
    if (!point) return;
    this.pointerId = e.pointerId;
    this.pointerCandidate = {
      pointerId: e.pointerId,
      point,
      clientX: e.clientX,
      clientY: e.clientY,
      moved: false
    };
  },

  onPointerMove(e) {
    if (!App.state.measureToolActive) return;
    if (this.pointerCandidate && this.pointerCandidate.pointerId === e.pointerId) {
      const distance = Math.hypot(
        e.clientX - this.pointerCandidate.clientX,
        e.clientY - this.pointerCandidate.clientY
      );
      if (distance > 6) {
        this.pointerCandidate.moved = true;
      }
      if (this.pointerCandidate.moved) {
        return;
      }
    }
    if (!App.state.measureDraft) return;
    const point = this.pointFromEvent(e);
    if (!point) return;
    App.state.measureDraft.end = point;
    this.scheduleRender();
  },

  onPointerUp(e) {
    if (!this.pointerCandidate || this.pointerCandidate.pointerId !== e.pointerId) return;
    const candidate = this.pointerCandidate;
    const distance = Math.hypot(e.clientX - candidate.clientX, e.clientY - candidate.clientY);
    const isPointClick = !candidate.moved && distance <= 6;
    this.pointerCandidate = null;
    this.pointerId = null;
    if (!isPointClick) return;

    const point = this.pointFromEvent(e) || candidate.point;
    if (App.state.measureResult && !App.state.measureDraft) {
      this.clear();
      return;
    }
    if (!App.state.measureDraft) {
      App.state.measureDraft = { start: point, end: point };
      App.state.measureResult = null;
    } else {
      App.state.measureDraft.end = point;
      App.state.measureResult = App.state.measureDraft;
      App.state.measureDraft = null;
    }
    this.scheduleRender();
  },

  activeMeasurement() {
    return App.state.measureDraft || App.state.measureResult;
  },

  statsFor(measure) {
    const start = measure.start;
    const end = measure.end;
    const delta = end.price - start.price;
    const pct = start.price !== 0 ? (delta / start.price) * 100 : 0;
    const startIndex = start.time == null ? null : App.collections.ohlcvIndexByTime.get(start.time);
    const endIndex = end.time == null ? null : App.collections.ohlcvIndexByTime.get(end.time);
    const hasIndexes = Number.isInteger(startIndex) && Number.isInteger(endIndex);
    let bars = hasIndexes ? Math.abs(endIndex - startIndex) : null;
    if (bars == null && Number.isFinite(Number(start.logical)) && Number.isFinite(Number(end.logical))) {
      bars = Math.abs(Math.round(Number(end.logical) - Number(start.logical)));
    }
    let durationSec = null;
    if (start.time != null && end.time != null) {
      durationSec = Math.abs(end.time - start.time);
    } else if (bars != null) {
      durationSec = bars * (App.state.timeframeInterval || App.state.configuredTimeframeSec || 60);
    }
    return {
      delta,
      pct,
      bars,
      durationSec
    };
  },

  formatSigned(value, decimals = 2) {
    const sign = value > 0 ? "+" : "";
    return `${sign}${App.ui.formatNumber(value, decimals)}`;
  },

  formatDuration(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds))) return "-";
    const sec = Math.max(0, Math.round(Number(seconds) || 0));
    const days = Math.floor(sec / 86400);
    const hours = Math.floor((sec % 86400) / 3600);
    const minutes = Math.floor((sec % 3600) / 60);
    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    if (minutes > 0) return `${minutes}m`;
    return `${sec}s`;
  },

  labelHtml(stats) {
    const bars = stats.bars == null ? "-" : String(stats.bars);
    return [
      `${this.formatSigned(stats.delta, 2)} (${this.formatSigned(stats.pct, 2)}%)`,
      `${bars} bars, ${this.formatDuration(stats.durationSec)}`
    ].join("<br>");
  },

  scheduleRender() {
    if (this.renderFrame !== null) return;
    this.renderFrame = requestAnimationFrame(() => {
      this.renderFrame = null;
      this.render();
    });
  },

  render() {
    const measure = this.activeMeasurement();
    if (!measure) {
      this.overlay.innerHTML = "";
      this.label.classList.add("hidden");
      return;
    }

    const start = this.pointToCoordinate(measure.start);
    const end = this.pointToCoordinate(measure.end);
    if (!start || !end) {
      this.overlay.innerHTML = "";
      this.label.classList.add("hidden");
      return;
    }

    const width = Math.max(1, window.innerWidth);
    const height = Math.max(1, window.innerHeight);
    const x1 = start.x;
    const y1 = start.y;
    const x2 = end.x;
    const y2 = end.y;
    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    const rectWidth = Math.abs(x2 - x1);
    const rectHeight = Math.abs(y2 - y1);
    const stats = this.statsFor(measure);
    const color = stats.delta > 0 ? "#26a69a" : (stats.delta < 0 ? "#ef5350" : "#666666");
    const fill = stats.delta > 0 ? "rgba(38,166,154,0.14)" : (stats.delta < 0 ? "rgba(239,83,80,0.14)" : "rgba(90,90,90,0.12)");

    this.overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.overlay.innerHTML =
      `<rect x="${left}" y="${top}" width="${rectWidth}" height="${rectHeight}" fill="${fill}" stroke="${color}" stroke-opacity="0.36" stroke-width="1"></rect>` +
      `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="1.5"></line>` +
      `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y1}" stroke="${color}" stroke-width="1" stroke-dasharray="4 3" stroke-opacity="0.75"></line>` +
      `<line x1="${x2}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="1" stroke-dasharray="4 3" stroke-opacity="0.75"></line>` +
      `<circle cx="${x1}" cy="${y1}" r="4" fill="#ffffff" stroke="${color}" stroke-width="1.5"></circle>` +
      `<circle cx="${x2}" cy="${y2}" r="4" fill="${color}" stroke="#ffffff" stroke-width="1.5"></circle>`;

    this.label.classList.toggle("up", stats.delta > 0);
    this.label.classList.toggle("down", stats.delta < 0);
    this.label.classList.toggle("flat", stats.delta === 0);
    this.label.innerHTML = this.labelHtml(stats);
    this.label.classList.remove("hidden");

    const labelRect = this.label.getBoundingClientRect();
    const margin = 8;
    let labelLeft = x2 - labelRect.width / 2;
    let labelTop = y2 + 12;
    if (labelTop + labelRect.height > height - margin) {
      labelTop = y2 - labelRect.height - 12;
    }
    labelLeft = Math.max(margin, Math.min(labelLeft, width - labelRect.width - margin));
    labelTop = Math.max(margin, Math.min(labelTop, height - labelRect.height - margin));
    this.label.style.left = `${Math.round(labelLeft)}px`;
    this.label.style.top = `${Math.round(labelTop)}px`;
  }
};
