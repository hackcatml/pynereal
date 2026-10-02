var App = window.App || (window.App = {});

// Draw disconnected lines on one price series instead of allocating a series
// for each segment. Scaling and crosshair values still use the chart library.
App.LineBreakPrimitive = class {
  constructor(points, options) {
    this.points = points.slice();
    this.options = options;
    this.views = [{ renderer: () => this }];
  }

  attached({ chart, series, requestUpdate }) {
    this.chart = chart;
    this.series = series;
    this.requestUpdate = requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews() { return this.views; }

  update(point) {
    const last = this.points.at(-1);
    if (last && point.time === last.time) this.points[this.points.length - 1] = point;
    else if (!last || point.time > last.time) this.points.push(point);
    this.requestUpdate?.();
  }

  lowerBound(time) {
    let low = 0, high = this.points.length;
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (this.points[mid].time < time) low = mid + 1;
      else high = mid;
    }
    return low;
  }

  draw(target) {
    if (!this.chart || !this.series || !this.points.length) return;
    const scale = this.chart.timeScale();
    const range = scale.getVisibleRange();
    if (!range) return;
    // Include the neighbouring points so lines crossing a viewport edge remain
    // connected, but never scan the whole history on each frame.
    const from = Math.max(0, this.lowerBound(range.from) - 1);
    const to = Math.min(this.points.length, this.lowerBound(range.to) + 2);
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: rx, verticalPixelRatio: ry }) => {
      ctx.save();
      ctx.strokeStyle = this.options.color;
      ctx.lineWidth = this.options.lineWidth * ry;
      ctx.lineCap = 'butt';
      ctx.lineJoin = 'round';
      let count = 0, singleX = 0, singleY = 0;
      const flush = () => {
        if (count === 1) {
          const half = scale.options().barSpacing * rx / 2;
          ctx.moveTo(singleX - half, singleY);
          ctx.lineTo(singleX + half, singleY);
        }
        if (count) ctx.stroke();
        count = 0;
      };
      for (let i = from; i < to; i++) {
        const point = this.points[i];
        const x = point.value == null ? null : scale.timeToCoordinate(point.time);
        const y = point.value == null ? null : this.series.priceToCoordinate(point.value);
        if (x == null || y == null) { flush(); continue; }
        if (!count) {
          ctx.beginPath();
          ctx.moveTo(x * rx, y * ry);
          singleX = x * rx;
          singleY = y * ry;
        } else ctx.lineTo(x * rx, y * ry);
        count++;
      }
      flush();
      ctx.restore();
    });
  }
};

App.data = {
  STYLE_CIRCLES: 2,
  STYLE_CROSS: 4,
  STYLE_LINEBR: 7,
  rebuildOhlcvCache(data, notifyBgcolor = true) {
    const collections = App.collections;
    collections.ohlcvData = Array.isArray(data)
      ? data
          .filter(d => d && Number.isFinite(Number(d.time)))
          .map(d => d.close == null ? { time: Number(d.time) } : ({
            time: Number(d.time),
            open: Number(d.open),
            high: Number(d.high),
            low: Number(d.low),
            close: Number(d.close),
            volume: Number(d.volume) || 0
          }))
          .sort((a, b) => a.time - b.time)
      : [];
    collections.ohlcvIndexByTime = new Map();
    collections.ohlcvVolumePrefix = [];
    let volumeTotal = 0;
    collections.ohlcvData.forEach((bar, index) => {
      collections.ohlcvIndexByTime.set(bar.time, index);
      volumeTotal += Number(bar.volume) || 0;
      collections.ohlcvVolumePrefix[index] = volumeTotal;
    });
    if (App.measure) {
      App.measure.scheduleRender();
    }
    if (notifyBgcolor && App.chart && App.chart.bgcolorPrimitive) {
      App.chart.bgcolorPrimitive.onOhlcvChanged();
    }
    App.indicators?.reset();
  },
  upsertOhlcvCache(bar) {
    if (!bar || !Number.isFinite(Number(bar.time))) return;
    const collections = App.collections;
    const time = Number(bar.time);
    const cachedBar = {
      time,
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
      volume: Number(bar.volume) || 0
    };
    const existingIndex = collections.ohlcvIndexByTime.get(time);
    const lastIndex = collections.ohlcvData.length - 1;
    if (existingIndex != null) {
      collections.ohlcvData[existingIndex] = cachedBar;
      if (existingIndex === lastIndex) {
        collections.ohlcvVolumePrefix[lastIndex] = (collections.ohlcvVolumePrefix[lastIndex - 1] || 0) + cachedBar.volume;
        if (App.measure) App.measure.scheduleRender();
        App.indicators?.update(cachedBar);
        return;
      }
      this.rebuildOhlcvCache(collections.ohlcvData, false);
      return;
    }
    const last = collections.ohlcvData[collections.ohlcvData.length - 1];
    if (!last || time > last.time) {
      collections.ohlcvData.push(cachedBar);
      collections.ohlcvIndexByTime.set(time, lastIndex + 1);
      collections.ohlcvVolumePrefix.push((collections.ohlcvVolumePrefix[lastIndex] || 0) + cachedBar.volume);
      if (App.measure) App.measure.scheduleRender();
      if (App.chart?.bgcolorPrimitive) App.chart.bgcolorPrimitive.onOhlcvChanged();
      App.indicators?.update(cachedBar);
      return;
    } else {
      collections.ohlcvData.push(cachedBar);
      collections.ohlcvData.sort((a, b) => a.time - b.time);
    }
    this.rebuildOhlcvCache(collections.ohlcvData);
  },
  toLinePoint(time, value) {
    const pointTime = Number(time);
    if (!Number.isFinite(pointTime)) {
      return null;
    }
    if (value == null) {
      return { time: pointTime };
    }
    const pointValue = Number(value);
    if (!Number.isFinite(pointValue)) {
      return { time: pointTime };
    }
    return { time: pointTime, value: pointValue };
  },
  hasLineValue(point) {
    return point && Object.prototype.hasOwnProperty.call(point, "value");
  },
  applyPlotValueLabels(chart, collections) {
    for (const controller of collections.plotSeriesMap.values()) {
      const series = controller.type === "linebr" ? controller.activeSeries : controller.series;
      if (series) {
        series.applyOptions({ lastValueVisible: Boolean(chart.plotValueLabelsVisible) });
      }
    }
  },
  buildPlotSeriesOptions(color, linewidth, style) {
    const styleCode = parseInt(style, 10);
    const isCrossStyle = styleCode === this.STYLE_CROSS || styleCode === this.STYLE_CIRCLES;
    const seriesOptions = {
      color: color || "#2962FF",
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: true
    };

    if (isCrossStyle) {
      seriesOptions.lineVisible = false;
      seriesOptions.pointMarkersVisible = true;
      seriesOptions.pointMarkersRadius = 1;
    } else {
      seriesOptions.lineWidth = linewidth || 2;
    }

    return {
      options: seriesOptions,
      styleCode,
      isLineBreakStyle: styleCode === this.STYLE_LINEBR
    };
  },
  addPlotLineSeries(chart, collections, seriesOptions, data) {
    const series = chart.chart.addSeries(LightweightCharts.LineSeries, seriesOptions);
    collections.plotSeriesList.push(series);
    if (data && data.length > 0) {
      series.setData(data);
    }
    return series;
  },
  createLineBreakPlot(chart, collections, seriesOptions, seriesData) {
    const lastHadValue = Boolean(this.hasLineValue(seriesData.at(-1)));
    const series = this.addPlotLineSeries(chart, collections, {
      ...seriesOptions,
      lineVisible: false,
      lastValueVisible: false
    }, seriesData.filter(point => this.hasLineValue(point)));
    const primitive = new App.LineBreakPrimitive(seriesData, seriesOptions);
    series.attachPrimitive(primitive);
    let activeStart = seriesData.length;
    while (activeStart > 0 && this.hasLineValue(seriesData[activeStart - 1])) activeStart--;
    // The label must follow only the latest segment, never a historical one
    // when the user scrolls back. Keep that small series separate.
    const labelSeries = this.addPlotLineSeries(chart, collections, {
      ...seriesOptions,
      lineVisible: false,
      crosshairMarkerVisible: false,
      lastValueVisible: Boolean(lastHadValue && chart.plotValueLabelsVisible)
    }, seriesData.slice(activeStart));
    const controller = {
      type: "linebr",
      series,
      primitive,
      labelSeries,
      activeSeries: lastHadValue ? labelSeries : null,
      lastHadValue
    };
    return controller;
  },
  createPlotSeries(chart, collections, plot, seriesData) {
    if (plot.kind === "bgcolor") {
      if (chart.bgcolorPrimitive) {
        chart.bgcolorPrimitive.setLayer(plot, seriesData);
      }
      collections.plotSeriesMap.set(plot.title, { type: "bgcolor" });
      return;
    }
    const { title, color, linewidth, style } = plot;
    const { options, isLineBreakStyle } = this.buildPlotSeriesOptions(color, linewidth, style);
    const hasHistoryGaps = plot.historyGaps?.length && options.lineVisible !== false;
    if (isLineBreakStyle || hasHistoryGaps) {
      // Ordinary lines still connect across script nulls, but not unread history.
      const points = !isLineBreakStyle ? seriesData.filter(point => this.hasLineValue(point)) : seriesData.slice();
      for (const time of plot.historyGaps || []) points.push({ time });
      points.sort((a, b) => a.time - b.time);
      const controller = this.createLineBreakPlot(chart, collections, options, points);
      controller.connectNulls = !isLineBreakStyle;
      collections.plotSeriesMap.set(title, controller);
      return;
    }

    const series = this.addPlotLineSeries(chart, collections, {
      ...options,
      lastValueVisible: Boolean(chart.plotValueLabelsVisible)
    }, seriesData);
    collections.plotSeriesMap.set(title, { type: "single", series });
  },
  updatePlotSeries(chart, collections, title, time, value) {
    const controller = collections.plotSeriesMap.get(title);
    if (!controller) {
      return;
    }

    if (controller.type === "bgcolor") {
      if (chart.bgcolorPrimitive) {
        chart.bgcolorPrimitive.updatePoint(title, time, value);
      }
      return;
    }

    const linePoint = this.toLinePoint(time, value);
    if (!linePoint) {
      return;
    }

    if (controller.type !== "linebr") {
      controller.series.update(linePoint);
      return;
    }

    if (controller.connectNulls && !this.hasLineValue(linePoint)) return;
    controller.primitive.update(linePoint);
    if (!this.hasLineValue(linePoint)) {
      if (controller.activeSeries) {
        controller.activeSeries.applyOptions({ lastValueVisible: false });
      }
      controller.lastHadValue = false;
      controller.activeSeries = null;
      return;
    }

    if (!controller.lastHadValue || !controller.activeSeries) {
      controller.activeSeries = controller.labelSeries;
      controller.activeSeries.setData([]);
      controller.activeSeries.applyOptions({
        lastValueVisible: Boolean(chart.plotValueLabelsVisible)
      });
    }
    controller.series.update(linePoint);
    controller.activeSeries.update(linePoint);
    controller.lastHadValue = true;
  },
  normalizePriceMarkerData(data) {
    // A line series requires one price per timestamp, even when several orders
    // execute on the same candle. Order labels are kept separately in markers.
    const points = new Map();
    for (const point of data) {
      if (point.time == null || point.value == null) continue;
      const time = Number(point.time);
      const value = Number(point.value);
      if (Number.isFinite(time) && Number.isFinite(value)) points.set(time, { time, value });
    }
    return [...points.values()].sort((a, b) => a.time - b.time);
  },
  renderTradeHistory(trades) {
    const state = App.state;
    const collections = App.collections;
    const chart = App.chart;
    try {
      if (collections.seriesMarkers) collections.seriesMarkers.detach();
      collections.seriesMarkers = null;
      collections.markers.length = 0;
      collections.markerKeys.clear();
      collections.entryMarkerData.length = 0;
      collections.closeMarkerData.length = 0;
      collections.entryPriceKeys.clear();
      collections.closePriceKeys.clear();

      trades.forEach(msg => {
        if (state.firstBarTime !== null && msg.time < state.firstBarTime) {
          return;
        }

        if (msg.type === "trade_entry") {
          const markerKey = `entry_${msg.time}_${msg.id}`;
          if (!collections.markerKeys.has(markerKey)) {
            collections.markers.push({
              time: msg.time,
              position: "belowBar",
              color: "#0b33e8",
              shape: "arrowUp",
              text: msg.comment || "",
              size: 0.5
            });
            collections.markerKeys.add(markerKey);

            if (msg.price != null) {
              const priceKey = `entry_${msg.time}_${msg.id}`;
              if (!collections.entryPriceKeys.has(priceKey)) {
                collections.entryMarkerData.push({ time: msg.time, value: msg.price });
                collections.entryPriceKeys.add(priceKey);
              }
            }
          }
        } else if (msg.type === "trade_close") {
          const closeOrderId = msg.exit_id || msg.id;
          const markerKey = `close_${msg.time}_${closeOrderId}`;
          if (!collections.markerKeys.has(markerKey)) {
            collections.markers.push({
              time: msg.time,
              position: "aboveBar",
              color: "#9d0bec",
              shape: "arrowDown",
              text: msg.comment || "",
              size: 0.5
            });
            collections.markerKeys.add(markerKey);

            if (msg.price != null) {
              const priceKey = `close_${msg.time}_${closeOrderId}`;
              if (!collections.closePriceKeys.has(priceKey)) {
                collections.closeMarkerData.push({ time: msg.time, value: msg.price });
                collections.closePriceKeys.add(priceKey);
              }
            }
          }
        }
      });

      if (collections.markers.length > 0) {
        collections.seriesMarkers = LightweightCharts.createSeriesMarkers(chart.candleSeries, collections.markers);
      }

      collections.entryMarkerData = this.normalizePriceMarkerData(collections.entryMarkerData);
      collections.closeMarkerData = this.normalizePriceMarkerData(collections.closeMarkerData);
      chart.entryMarkerSeries.setData(collections.entryMarkerData);
      chart.closeMarkerSeries.setData(collections.closeMarkerData);
    } catch (e) {
      console.error("Failed to load trade history:", e);
    }
  },
  renderPlotcharHistory(plotchars) {
    const state = App.state;
    const collections = App.collections;
    const chart = App.chart;
    try {
      if (collections.plotcharSeriesMarkers) collections.plotcharSeriesMarkers.detach();
      collections.plotcharSeriesMarkers = null;
      collections.plotcharMarkers.length = 0;
      collections.plotcharMarkerKeys.clear();

      plotchars.forEach(msg => {
        if (state.firstBarTime !== null && msg.time < state.firstBarTime) {
          return;
        }

        const markerKey = `plotchar_${msg.time}_${msg.title}`;
        if (!collections.plotcharMarkerKeys.has(markerKey)) {
          let position = "belowBar";
          if (msg.location === "aboveBar") {
            position = "aboveBar";
          } else if (msg.location === "absolute") {
            position = "inBar";
          }

          collections.plotcharMarkers.push({
            time: msg.time,
            position: position,
            color: msg.color || "#2962FF",
            shape: "circle",
            text: msg.text || msg.char,
            size: msg.size || 1
          });
          collections.plotcharMarkerKeys.add(markerKey);
        }
      });

      if (collections.plotcharMarkers.length > 0) {
        collections.plotcharSeriesMarkers = LightweightCharts.createSeriesMarkers(
          chart.candleSeries,
          collections.plotcharMarkers
        );
      }
    } catch (e) {
      console.error("Failed to load plotchar history:", e);
    }
  },
  clearPlotData() {
    const collections = App.collections;
    const chart = App.chart;
    for (const series of collections.plotSeriesList) chart.chart.removeSeries(series);
    collections.plotSeriesList.length = 0;
    collections.plotSeriesMap.clear();
    if (chart.bgcolorPrimitive) chart.bgcolorPrimitive.clear();
  },
  renderPlotData(plots) {
    const collections = App.collections;
    const chart = App.chart;
    this.clearPlotData();
    for (const plot of plots) {
      const seriesData = [];
      for (const point of plot.data || []) {
        if (plot.kind === "bgcolor") {
          const pointTime = Number(point.time);
          if (Number.isFinite(pointTime)) seriesData.push({ time: pointTime, value: point.value });
        } else {
          const linePoint = this.toLinePoint(point.time, point.value);
          if (linePoint) seriesData.push(linePoint);
        }
      }
      this.createPlotSeries(chart, collections, plot, seriesData);
    }
  },
  async loadInitialWithRetry() {
    return App.history.loadInitial();
  },
  timeframeToSeconds(tf) {
    const m = /^(\d+)([smhdw])$/i.exec((tf || "").trim());
    if (!m) return null;
    const unit = { s: 1, m: 60, h: 3600, d: 86400, w: 604800 }[m[2].toLowerCase()];
    return parseInt(m[1], 10) * unit;
  },
  async loadChartInfo() {
    const state = App.state;
    try {
      const resp = await fetch(`${App.config.apiBase}/info`);
      const info = await resp.json();
      const exchange = (info.exchange || "Unknown").toUpperCase();
      const symbol = info.symbol || "Unknown";
      const timeframe = info.timeframe || "Unknown";
      state.exchange = exchange;
      state.symbol = symbol;
      state.timeframe = timeframe;
      document.title = `Chart (${symbol})`;
      const tfSeconds = App.data.timeframeToSeconds(info.timeframe);
      if (tfSeconds) {
        state.configuredTimeframeSec = tfSeconds;
        state.timeframeInterval = App.timeframes?.isHigher() ? App.timeframes.seconds() : tfSeconds;
      }
      if (info.script_title) {
        state.scriptTitle = info.script_title || "No title";
        state.scriptTitleVisible = true;
      } else if (!state.scriptSourceLoaded) {
        state.scriptTitleVisible = false;
      }
      if (info.script_source_name != null) {
        state.scriptSourceName = info.script_source_name || "";
      }
      state.runnerConnected = Boolean(info.runner_connected);
      state.runnerPhase = info.runner_phase || (state.runnerConnected ? "running" : "stopped");
      state.nextPrerunAt = Number.isFinite(Number(info.next_prerun_at))
        ? Number(info.next_prerun_at)
        : null;
      state.baseInfoTop = `${symbol} | ${timeframe} | ${exchange}`;
      state.baseInfoText = "";
      App.ui.setChartInfo();
    } catch (e) {
      state.baseInfoTop = "Unknown | Unknown | Unknown";
      state.baseInfoText = "";
      App.ui.setChartInfo();
    }
  },
  async loadScriptSource() {
    try {
      const resp = await fetch(`${App.config.apiBase}/script-source`);
      if (!resp.ok) {
        return false;
      }
      const data = await resp.json();
      App.state.scriptSourceName = data.name || "";
      App.state.scriptSourcePath = data.path || data.name || "";
      App.state.scriptSourceRevision = data.revision || "";
      App.state.scriptSource = data.source || "";
      App.state.scriptSourceLoaded = true;
      App.state.sourceDirty = false;
      App.state.sourceBaseNote = data.note || "";
      App.state.sourceNote = App.state.sourceBaseNote;
      App.state.sourceSaveStatus = "";
      App.state.sourceConflict = false;
      if (data.title) {
        App.state.scriptTitle = data.title;
        App.state.scriptTitleVisible = true;
      }
      App.ui.renderSourcePanel();
      App.ui.setChartInfo();
      return true;
    } catch (e) {
      return false;
    }
  },
  async saveScriptSource(source, note = "") {
    try {
      const resp = await fetch(`${App.config.apiBase}/script-source`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source,
          base_revision: App.state.scriptSourceRevision,
          note
        })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return {
          ok: false,
          error: data.error || `Save failed (${resp.status})`,
          status: resp.status,
          code: data.code || "",
          currentRevision: data.current_revision || ""
        };
      }
      App.state.scriptSourceName = data.name || App.state.scriptSourceName || "";
      App.state.scriptSourcePath = data.path || App.state.scriptSourcePath || data.name || "";
      App.state.scriptSourceRevision = data.revision || App.state.scriptSourceRevision || "";
      App.state.scriptSource = data.source || "";
      App.state.scriptSourceLoaded = true;
      App.state.sourceDirty = false;
      App.state.sourceBaseNote = data.note || "";
      App.state.sourceNote = App.state.sourceBaseNote;
      App.state.sourceSaveStatus = "";
      App.state.sourceConflict = false;
      if (data.title) {
        App.state.scriptTitle = data.title;
        App.state.scriptTitleVisible = true;
      }
      App.ui.setChartInfo();
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: "Save failed" };
    }
  },
  async saveScriptNote(note = "") {
    try {
      const resp = await fetch(`${App.config.apiBase}/script-source`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: App.state.scriptSource,
          base_revision: App.state.scriptSourceRevision,
          note
        })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return {
          ok: false,
          error: data.error || `Save failed (${resp.status})`,
          status: resp.status,
          code: data.code || "",
          currentRevision: data.current_revision || ""
        };
      }
      App.state.scriptSourceRevision = data.revision || App.state.scriptSourceRevision || "";
      App.state.sourceBaseNote = data.note || "";
      App.state.sourceNote = App.state.sourceBaseNote;
      App.state.sourceSaveStatus = "";
      App.state.sourceConflict = false;
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: "Save failed" };
    }
  },
  async loadScriptHistory() {
    const path = App.state.scriptSourcePath;
    if (!path) return { ok: false, error: "Script path is unavailable" };
    try {
      const resp = await fetch(
        `/api/scripting/history?path=${encodeURIComponent(path)}&limit=100`,
        { cache: "no-store" }
      );
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) return { ok: false, error: data.error || `History failed (${resp.status})` };
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: "Version history could not be loaded" };
    }
  },
  async loadScriptDiff(revisionId) {
    const path = App.state.scriptSourcePath;
    if (!path) return { ok: false, error: "Script path is unavailable" };
    try {
      const resp = await fetch(
        `/api/scripting/diff?path=${encodeURIComponent(path)}&revision_id=${encodeURIComponent(revisionId)}`,
        { cache: "no-store" }
      );
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) return { ok: false, error: data.error || `Diff failed (${resp.status})` };
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: "Version diff could not be loaded" };
    }
  },
  async restoreScriptRevision(revisionId) {
    const path = App.state.scriptSourcePath;
    if (!path) return { ok: false, error: "Script path is unavailable" };
    try {
      const resp = await fetch("/api/scripting/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path,
          revision_id: revisionId,
          base_revision: App.state.scriptSourceRevision
        })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return {
          ok: false,
          error: data.error || `Restore failed (${resp.status})`,
          status: resp.status,
          code: data.code || "",
          currentRevision: data.current_revision || ""
        };
      }
      App.state.scriptSourcePath = data.path || path;
      App.state.scriptSourceRevision = data.revision || "";
      App.state.scriptSource = data.content || "";
      App.state.scriptSourceLoaded = true;
      App.state.sourceDirty = false;
      App.state.sourceBaseNote = data.note || "";
      App.state.sourceNote = App.state.sourceBaseNote;
      App.state.sourceConflict = false;
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: "Version could not be restored" };
    }
  },
  async loadWebhookConfig() {
    const state = App.state;
    const ui = App.ui;
    try {
      const resp = await fetch(`${App.config.apiBase}/webhook-config`);
      if (!resp.ok) {
        state.webhookUrl = "";
        return null;
      }
      const cfg = await resp.json();
      state.webhookEnabled = Boolean(cfg.enabled);
      state.telegramEnabled = Boolean(cfg.telegram_notification);
      state.webhookUrl = cfg.url || "";
      ui.elements.webhookToggle.checked = state.webhookEnabled;
      ui.elements.telegramToggle.checked = state.telegramEnabled;
      return cfg;
    } catch (e) {
      state.webhookUrl = "";
      return null;
    }
  },
  async updateWebhookConfig(payload) {
    try {
      const resp = await fetch(`${App.config.apiBase}/webhook-config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!resp.ok) {
        return false;
      }
      const cfg = await resp.json();
      App.state.webhookEnabled = Boolean(cfg.enabled);
      App.state.telegramEnabled = Boolean(cfg.telegram_notification);
      App.state.webhookUrl = cfg.url || "";
      App.ui.elements.webhookToggle.checked = App.state.webhookEnabled;
      App.ui.elements.telegramToggle.checked = App.state.telegramEnabled;
      return true;
    } catch (e) {
      return false;
    }
  },
  normalizeManualAlertTemplates(templates) {
    return Array.isArray(templates)
      ? templates.filter(t => t && typeof t.title === "string" && typeof t.message === "string")
          .map(t => ({
            title: t.title,
            message: t.message,
            ...(typeof t.ai === "string" && t.ai.trim() ? { ai: t.ai } : {})
          }))
      : [];
  },
  async loadManualAlertTemplates() {
    try {
      const resp = await fetch(`${App.config.apiBase}/manual-alert-templates`);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return { ok: false, error: data.error || `Load failed (${resp.status})` };
      }
      const templates = this.normalizeManualAlertTemplates(data.templates);
      App.state.manualAlertTemplates = templates;
      return { ok: true, templates };
    } catch (e) {
      return { ok: false, error: "Load failed" };
    }
  },
  async saveManualAlertTemplates(templates) {
    try {
      const resp = await fetch(`${App.config.apiBase}/manual-alert-templates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ templates })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return { ok: false, error: data.error || `Save failed (${resp.status})` };
      }
      const savedTemplates = this.normalizeManualAlertTemplates(data.templates);
      App.state.manualAlertTemplates = savedTemplates;
      return { ok: true, templates: savedTemplates };
    } catch (e) {
      return { ok: false, error: "Save failed" };
    }
  },
  async sendManualAlert(payload) {
    try {
      const resp = await fetch(`${App.config.apiBase}/manual-alert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return { ok: false, error: data.error || `Send failed (${resp.status})` };
      }
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: "Send failed" };
    }
  },
  normalizeManualAlertTriggers(data) {
    const raw = data && Array.isArray(data.triggers) ? data.triggers : [];
    return raw.filter(t => t && t.enabled && Number.isFinite(Number(t.price)));
  },
  async loadManualAlertTrigger() {
    try {
      const resp = await fetch(`${App.config.apiBase}/manual-alert-trigger`);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return { ok: false, error: data.error || `Load trigger failed (${resp.status})` };
      }
      const triggers = this.normalizeManualAlertTriggers(data);
      App.state.manualAlertTriggers = triggers;
      return { ok: true, triggers };
    } catch (e) {
      return { ok: false, error: "Load trigger failed" };
    }
  },
  async saveManualAlertTriggers(triggers) {
    try {
      const resp = await fetch(`${App.config.apiBase}/manual-alert-trigger`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ triggers: triggers || [] })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return { ok: false, error: data.error || `Save trigger failed (${resp.status})` };
      }
      const savedTriggers = this.normalizeManualAlertTriggers(data);
      App.state.manualAlertTriggers = savedTriggers;
      return { ok: true, triggers: savedTriggers };
    } catch (e) {
      return { ok: false, error: "Save trigger failed" };
    }
  }
};
