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
  mobileAimClientX: null,
  mobileAimClientY: null,

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

    [App.chart.container, this.overlay].forEach((target) => {
      target.addEventListener("pointerdown", (e) => this.onPointerDown(e), { capture: true, passive: false });
      target.addEventListener("pointermove", (e) => this.onPointerMove(e), { capture: true, passive: false });
    });
    window.addEventListener("pointermove", (e) => this.onPointerMove(e), { passive: false });
    window.addEventListener("pointerup", (e) => this.onPointerUp(e), { passive: false });
    window.addEventListener("pointercancel", (e) => this.onPointerUp(e), { passive: false });

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
      if (this.isMobileMode()) {
        this.ensureMobileAim();
      }
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
      this.mobileAimClientX = null;
      this.mobileAimClientY = null;
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
    this.mobileAimClientX = null;
    this.mobileAimClientY = null;
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

  isMobileMode() {
    if (App.chart && typeof App.chart.isMobileViewport === "function") {
      return App.chart.isMobileViewport();
    }
    return window.matchMedia &&
      window.matchMedia("(max-width: 640px), (hover: none) and (pointer: coarse)").matches;
  },

  isMobilePointer(e) {
    return this.isMobileMode() && (!e || e.pointerType !== "mouse");
  },

  stopTouchDrawingEvent(e) {
    if (!e) return;
    e.preventDefault();
    e.stopPropagation();
    if (typeof e.stopImmediatePropagation === "function") {
      e.stopImmediatePropagation();
    }
  },

  plotBoundsClient() {
    if (!App.chart || !App.chart.chart || !App.chart.container) return null;
    const rect = this.chartRect();
    let priceScaleWidth = 0;
    try {
      priceScaleWidth = App.chart.chart.priceScale("right").width() || 0;
    } catch {}
    const timeScaleHeight = 28;
    const right = rect.left + Math.max(0, rect.width - priceScaleWidth);
    const bottom = rect.top + Math.max(0, rect.height - timeScaleHeight);
    if (right <= rect.left || bottom <= rect.top) return null;
    return {
      left: rect.left,
      top: rect.top,
      right,
      bottom,
      priceScaleLeft: right,
      width: right - rect.left,
      height: bottom - rect.top
    };
  },

  isClientInPlot(clientX, clientY) {
    const bounds = this.plotBoundsClient();
    if (!bounds) return false;
    return clientX >= bounds.left &&
      clientX < bounds.right &&
      clientY >= bounds.top &&
      clientY < bounds.bottom;
  },

  clampToPlot(clientX, clientY) {
    const bounds = this.plotBoundsClient();
    if (!bounds) return null;
    const right = Math.max(bounds.left, bounds.right - 1);
    const bottom = Math.max(bounds.top, bounds.bottom - 1);
    return {
      clientX: Math.max(bounds.left, Math.min(Number(clientX), right)),
      clientY: Math.max(bounds.top, Math.min(Number(clientY), bottom))
    };
  },

  ensureMobileAim() {
    const bounds = this.plotBoundsClient();
    if (!bounds) return null;
    if (this.mobileAimClientX == null ||
        this.mobileAimClientY == null ||
        !Number.isFinite(Number(this.mobileAimClientX)) ||
        !Number.isFinite(Number(this.mobileAimClientY))) {
      this.mobileAimClientX = bounds.left + bounds.width / 2;
      this.mobileAimClientY = bounds.top + bounds.height / 2;
    }
    const clamped = this.clampToPlot(this.mobileAimClientX, this.mobileAimClientY);
    if (!clamped) return null;
    this.mobileAimClientX = clamped.clientX;
    this.mobileAimClientY = clamped.clientY;
    return clamped;
  },

  setMobileAim(clientX, clientY) {
    const clamped = this.clampToPlot(clientX, clientY);
    if (!clamped) return null;
    this.mobileAimClientX = clamped.clientX;
    this.mobileAimClientY = clamped.clientY;
    return clamped;
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

  pointFromClient(clientX, clientY, sourceEvent = null) {
    const plotPoint = App.chart.plotPointFromClient(clientX, clientY);
    if (!plotPoint) return null;
    const time = this.normalizeTime(plotPoint.time);
    const logical = Number(plotPoint.logical);
    if (time == null && !Number.isFinite(logical)) return null;
    const point = {
      time,
      logical: Number.isFinite(logical) ? logical : null,
      price: plotPoint.price
    };
    return this.shouldSnapToOhlc(sourceEvent) ? this.snapPointToOhlc(point) : point;
  },

  pointFromEvent(e) {
    return this.pointFromClient(e.clientX, e.clientY, e);
  },

  mobileAimClientPoint() {
    return this.ensureMobileAim();
  },

  pointFromMobileAim() {
    const aim = this.mobileAimClientPoint();
    return aim ? this.pointFromClient(aim.clientX, aim.clientY) : null;
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
    if (!App.state.measureToolActive) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (this.isMobilePointer(e)) {
      if (!this.isClientInPlot(e.clientX, e.clientY)) return;
      this.onMobilePointerDown(e);
      return;
    }
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

  onMobilePointerDown(e) {
    this.pointerId = e.pointerId;
    if (App.state.measureResult && !App.state.measureDraft) {
      this.pointerCandidate = {
        pointerId: e.pointerId,
        mode: "mobile-clear",
        clientX: e.clientX,
        clientY: e.clientY,
        moved: false
      };
      this.stopTouchDrawingEvent(e);
      return;
    }

    if (!App.state.measureDraft) {
      this.ensureMobileAim();
      this.pointerCandidate = {
        pointerId: e.pointerId,
        mode: "mobile-start",
        clientX: e.clientX,
        clientY: e.clientY,
        moved: false
      };
      this.stopTouchDrawingEvent(e);
      return;
    }

    this.pointerCandidate = {
      pointerId: e.pointerId,
      mode: "mobile-adjust",
      clientX: e.clientX,
      clientY: e.clientY,
      moved: false
    };
    this.stopTouchDrawingEvent(e);
  },

  onPointerMove(e) {
    if (!App.state.measureToolActive) return;
    if (this.isMobilePointer(e) && this.pointerCandidate && this.pointerCandidate.pointerId === e.pointerId) {
      this.onMobilePointerMove(e);
      return;
    }
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

  onMobilePointerMove(e) {
    const candidate = this.pointerCandidate;
    const distance = Math.hypot(e.clientX - candidate.clientX, e.clientY - candidate.clientY);
    if (distance > 6) {
      candidate.moved = true;
    }
    this.stopTouchDrawingEvent(e);

    if (candidate.mode === "mobile-start") {
      if (!candidate.moved) return;
      this.setMobileAim(e.clientX, e.clientY);
      this.scheduleRender();
      return;
    }

    if (candidate.mode !== "mobile-adjust" || !candidate.moved) return;

    this.setMobileAim(e.clientX, e.clientY);
    const point = this.pointFromMobileAim();
    if (!point) return;
    App.state.measureDraft.end = point;
    this.scheduleRender();
  },

  onPointerUp(e) {
    if (!this.pointerCandidate || this.pointerCandidate.pointerId !== e.pointerId) return;
    if (this.isMobilePointer(e)) {
      this.onMobilePointerUp(e);
      return;
    }
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

  onMobilePointerUp(e) {
    const candidate = this.pointerCandidate;
    const distance = Math.hypot(e.clientX - candidate.clientX, e.clientY - candidate.clientY);
    const isTap = !candidate.moved && distance <= 6;
    this.pointerCandidate = null;
    this.pointerId = null;
    if (e.type === "pointercancel") return;
    this.stopTouchDrawingEvent(e);

    if (candidate.mode === "mobile-start") {
      if (!isTap) {
        this.scheduleRender();
        return;
      }
      const point = this.pointFromMobileAim();
      if (!point) return;
      App.state.measureDraft = { start: point, end: point };
      App.state.measureResult = null;
      this.scheduleRender();
      return;
    }

    if (candidate.mode === "mobile-adjust") {
      if (candidate.moved) {
        this.setMobileAim(e.clientX, e.clientY);
        const point = this.pointFromMobileAim();
        if (point) {
          App.state.measureDraft.end = point;
        }
        this.scheduleRender();
        return;
      }
      if (isTap && App.state.measureDraft) {
        App.state.measureResult = App.state.measureDraft;
        App.state.measureDraft = null;
        this.scheduleRender();
      }
      return;
    }

    if (candidate.mode === "mobile-clear" && isTap) {
      this.clear();
    }
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

  formatReticlePrice(price) {
    const value = Number(price);
    if (!Number.isFinite(value)) return "-";
    const abs = Math.abs(value);
    const decimals = abs >= 100 ? 2 : abs >= 1 ? 4 : 8;
    return value.toLocaleString("en-US", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    });
  },

  formatReticleTime(time) {
    const value = Number(time);
    if (!Number.isFinite(value)) return "";
    const date = new Date(value * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  },

  escapeSvgText(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  },

  labelHtml(stats) {
    const bars = stats.bars == null ? "-" : String(stats.bars);
    return [
      `${this.formatSigned(stats.delta, 2)} (${this.formatSigned(stats.pct, 2)}%)`,
      `${bars} bars, ${this.formatDuration(stats.durationSec)}`
    ].join("<br>");
  },

  mobileHitAreaSvg() {
    if (!this.isMobileMode() || !App.state.measureToolActive) return "";
    const bounds = this.plotBoundsClient();
    if (!bounds) return "";
    return `<rect class="measure-hit-area" x="${bounds.left}" y="${bounds.top}" width="${bounds.width}" height="${bounds.height}" fill="transparent" pointer-events="all"></rect>`;
  },

  reticleAxisLabelsSvg(x, y, width, height) {
    const point = this.pointFromClient(x, y);
    const bounds = this.plotBoundsClient();
    if (!point || !bounds) return "";

    const priceText = this.escapeSvgText(this.formatReticlePrice(point.price));
    const priceWidth = Math.max(62, priceText.length * 8 + 14);
    const priceHeight = 24;
    const priceX = Math.max(4, Math.min(width - priceWidth - 4, bounds.priceScaleLeft + 2));
    const priceY = Math.max(4, Math.min(y - priceHeight / 2, height - priceHeight - 4));

    const timeText = this.escapeSvgText(this.formatReticleTime(point.time));
    const timeWidth = Math.max(74, timeText.length * 7.5 + 16);
    const timeHeight = 24;
    const timeX = Math.max(4, Math.min(x - timeWidth / 2, width - timeWidth - 4));
    const timeY = Math.max(4, Math.min(bounds.bottom + 2, height - timeHeight - 4));

    const priceLabel =
      `<rect x="${priceX}" y="${priceY}" width="${priceWidth}" height="${priceHeight}" rx="4" fill="#2962ff"></rect>` +
      `<text x="${priceX + priceWidth / 2}" y="${priceY + 16}" fill="#ffffff" font-size="12" font-weight="700" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">${priceText}</text>`;
    const timeLabel = timeText
      ? `<rect x="${timeX}" y="${timeY}" width="${timeWidth}" height="${timeHeight}" rx="4" fill="#2962ff"></rect>` +
        `<text x="${timeX + timeWidth / 2}" y="${timeY + 16}" fill="#ffffff" font-size="12" font-weight="700" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">${timeText}</text>`
      : "";

    return priceLabel + timeLabel;
  },

  reticleSvg(x, y, width, height) {
    const cx = Number(x);
    const cy = Number(y);
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) return "";
    return [
      `<line x1="${cx}" y1="0" x2="${cx}" y2="${height}" stroke="#2962ff" stroke-width="1.4" stroke-dasharray="2 6" stroke-linecap="round" opacity="0.9"></line>`,
      `<line x1="0" y1="${cy}" x2="${width}" y2="${cy}" stroke="#2962ff" stroke-width="1.4" stroke-dasharray="2 6" stroke-linecap="round" opacity="0.9"></line>`,
      `<circle cx="${cx}" cy="${cy}" r="5" fill="#2962ff" stroke="#ffffff" stroke-width="1.5"></circle>`,
      this.reticleAxisLabelsSvg(cx, cy, width, height)
    ].join("");
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
      if (App.state.measureToolActive && this.isMobileMode()) {
        const aim = this.mobileAimClientPoint();
        const width = Math.max(1, window.innerWidth);
        const height = Math.max(1, window.innerHeight);
        if (aim) {
          this.overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
          this.overlay.innerHTML = this.mobileHitAreaSvg() +
            this.reticleSvg(aim.clientX, aim.clientY, width, height);
          this.label.classList.add("hidden");
          return;
        }
      }
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
    const reticle = this.isMobileMode() ? this.reticleSvg(x2, y2, width, height) : "";

    this.overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.overlay.innerHTML =
      this.mobileHitAreaSvg() +
      `<rect x="${left}" y="${top}" width="${rectWidth}" height="${rectHeight}" fill="${fill}" stroke="${color}" stroke-opacity="0.36" stroke-width="1"></rect>` +
      `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="1.5"></line>` +
      `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y1}" stroke="${color}" stroke-width="1" stroke-dasharray="4 3" stroke-opacity="0.75"></line>` +
      `<line x1="${x2}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="1" stroke-dasharray="4 3" stroke-opacity="0.75"></line>` +
      `<circle cx="${x1}" cy="${y1}" r="4" fill="#ffffff" stroke="${color}" stroke-width="1.5"></circle>` +
      `<circle cx="${x2}" cy="${y2}" r="4" fill="${color}" stroke="#ffffff" stroke-width="1.5"></circle>` +
      reticle;

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
