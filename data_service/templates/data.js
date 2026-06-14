var App = window.App || (window.App = {});

App.data = {
  STYLE_CIRCLES: 2,
  STYLE_CROSS: 4,
  STYLE_LINEBR: 7,
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
    const controller = {
      type: "linebr",
      options: seriesOptions,
      activeSeries: null,
      lastHadValue: false
    };
    let segment = [];

    const flushSegment = (isActive) => {
      if (segment.length === 0) {
        return;
      }
      const series = this.addPlotLineSeries(chart, collections, seriesOptions, segment);
      if (isActive) {
        controller.activeSeries = series;
      }
      segment = [];
    };

    seriesData.forEach(point => {
      if (this.hasLineValue(point)) {
        segment.push(point);
        controller.lastHadValue = true;
      } else {
        flushSegment(false);
        controller.lastHadValue = false;
        controller.activeSeries = null;
      }
    });
    flushSegment(controller.lastHadValue);

    return controller;
  },
  createPlotSeries(chart, collections, plot, seriesData) {
    const { title, color, linewidth, style } = plot;
    const { options, isLineBreakStyle } = this.buildPlotSeriesOptions(color, linewidth, style);
    if (isLineBreakStyle) {
      const controller = this.createLineBreakPlot(chart, collections, options, seriesData);
      collections.plotSeriesMap.set(title, controller);
      return;
    }

    const series = this.addPlotLineSeries(chart, collections, options, seriesData);
    collections.plotSeriesMap.set(title, { type: "single", series });
  },
  updatePlotSeries(chart, collections, title, time, value) {
    const controller = collections.plotSeriesMap.get(title);
    if (!controller) {
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

    if (!this.hasLineValue(linePoint)) {
      controller.lastHadValue = false;
      controller.activeSeries = null;
      return;
    }

    if (!controller.lastHadValue || !controller.activeSeries) {
      controller.activeSeries = this.addPlotLineSeries(chart, collections, controller.options, []);
    }
    controller.activeSeries.update(linePoint);
    controller.lastHadValue = true;
  },
  async loadTradeHistory() {
    const state = App.state;
    const collections = App.collections;
    const chart = App.chart;
    if (!state.runnerConnected) return;
    try {
      collections.markers.length = 0;
      collections.markerKeys.clear();
      collections.entryMarkerData.length = 0;
      collections.closeMarkerData.length = 0;
      collections.entryPriceKeys.clear();
      collections.closePriceKeys.clear();

      const resp = await fetch("/api/trades");
      const trades = await resp.json();

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
          const markerKey = `close_${msg.time}_${msg.id}`;
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
              const priceKey = `close_${msg.time}_${msg.id}`;
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

      if (collections.entryMarkerData.length > 0) {
        chart.entryMarkerSeries.setData(collections.entryMarkerData);
      }
      if (collections.closeMarkerData.length > 0) {
        chart.closeMarkerSeries.setData(collections.closeMarkerData);
      }
    } catch (e) {
      console.error("Failed to load trade history:", e);
    }
  },
  async loadPlotcharHistory() {
    const state = App.state;
    const collections = App.collections;
    const chart = App.chart;
    if (!state.runnerConnected) return;
    try {
      collections.plotcharMarkers.length = 0;
      collections.plotcharMarkerKeys.clear();

      const resp = await fetch("/api/plotchar");
      const plotchars = await resp.json();

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
  async loadPlotData() {
    const state = App.state;
    const collections = App.collections;
    const chart = App.chart;
    if (!state.runnerConnected) return;
    for (let i = 0; i < 30; i++) {
      try {
        const resp = await fetch("/api/plot?limit=100000");
        const plots = await resp.json();

        if (Array.isArray(plots) && plots.length > 0) {
          const pendingSeriesData = [];
          plots.forEach(plot => {
            const seriesData = [];
            if (plot.data && Array.isArray(plot.data)) {
              plot.data.forEach(point => {
                const linePoint = App.data.toLinePoint(point.time, point.value);
                if (linePoint) {
                  seriesData.push(linePoint);
                }
              });
            }

            pendingSeriesData.push([plot, seriesData]);
          });

          pendingSeriesData.forEach(([plot, data]) => {
            this.createPlotSeries(chart, collections, plot, data);
          });
          return;
        }
      } catch (e) {
        // Ignore and retry
      }
      await App.util.sleep(1000);
    }
  },
  async loadInitialWithRetry() {
    const state = App.state;
    const collections = App.collections;
    const chart = App.chart;
    if (state.initialLoadInProgress || state.initialLoadDone) {
      return;
    }
    state.initialLoadInProgress = true;
    for (let i = 0; i < 30; i++) {
      try {
        const resp = await fetch("/api/ohlcv?limit=100000");
        const data = await resp.json();
        if (Array.isArray(data) && data.length > 0) {
          const cleanData = data.filter(d => d && d.time != null && d.open != null && d.high != null &&
            d.low != null && d.close != null);
          if (cleanData.length === 0) {
            await App.util.sleep(1000);
            continue;
          }
          chart.candleSeries.setData(cleanData);
          chart.volumeSeries.setData(cleanData.map(d => ({
            time: d.time,
            value: d.volume,
            color: d.close >= d.open ? "#26a69a" : "#ef5350"
          })));
          const savedRange = sessionStorage.getItem("chartVisibleRange");
          const savedLogicalRange = sessionStorage.getItem("chartVisibleLogicalRange");
          const savedScale = sessionStorage.getItem("chartScaleOptions");
          if (savedRange || savedLogicalRange) {
            chart.chart.timeScale().fitContent();
          } else {
            chart.applyInitialVisibleRange(cleanData.length);
          }
          if (savedScale) {
            try {
              const opts = JSON.parse(savedScale);
              chart.chart.timeScale().applyOptions(opts);
            } catch {}
            sessionStorage.removeItem("chartScaleOptions");
          }
          if (savedLogicalRange) {
            try {
              const range = JSON.parse(savedLogicalRange);
              chart.chart.timeScale().setVisibleLogicalRange(range);
            } catch {}
            sessionStorage.removeItem("chartVisibleLogicalRange");
          } else if (savedRange) {
            try {
              const range = JSON.parse(savedRange);
              chart.chart.timeScale().setVisibleRange(range);
            } catch {}
            sessionStorage.removeItem("chartVisibleRange");
          }

          state.firstBarTime = data[0].time;
          if (state.configuredTimeframeSec) {
            state.timeframeInterval = state.configuredTimeframeSec;
          } else if (data.length >= 2) {
            // Fallback only: on OKX zero-volume bars are hidden, so the gap
            // between the first two visible bars may not be the timeframe.
            state.timeframeInterval = data[1].time - data[0].time;
          }
          if (data.length > 0) {
            state.lastPrice = data[data.length - 1].close;
            state.lastOhlcv = data[data.length - 1];
          }

          await this.loadTradeHistory();
          await this.loadPlotcharHistory();
          await this.loadPlotData();
          state.initialLoadDone = true;
          state.initialLoadInProgress = false;
          return;
        }
      } catch (e) {
        // ignore
      }
      await App.util.sleep(1000);
    }
    console.error("Initial OHLCV not ready (timeout).");
    state.initialLoadInProgress = false;
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
      const resp = await fetch("/api/info");
      const info = await resp.json();
      const exchange = (info.exchange || "Unknown").toUpperCase();
      const symbol = info.symbol || "Unknown";
      const timeframe = info.timeframe || "Unknown";
      const tfSeconds = App.data.timeframeToSeconds(info.timeframe);
      if (tfSeconds) {
        state.configuredTimeframeSec = tfSeconds;
        state.timeframeInterval = tfSeconds;
      }
      if (info.script_title) {
        state.scriptTitle = info.script_title || "No title";
        state.scriptTitleVisible = true;
      } else {
        state.scriptTitleVisible = false;
      }
      if (info.script_source_name != null) {
        state.scriptSourceName = info.script_source_name || "";
      }
      state.baseInfoTop = `<span class="info-main">${symbol} | ${timeframe} | ${exchange}</span>`;
      state.baseInfoText = state.baseInfoTop;
      App.ui.setChartInfo();
    } catch (e) {
      state.baseInfoTop = "<span class=\"info-main\">Unknown | Unknown | Unknown</span>";
      state.baseInfoText = state.baseInfoTop;
      App.ui.setChartInfo();
    }
  },
  async loadScriptSource() {
    try {
      const resp = await fetch("/api/script-source");
      if (!resp.ok) {
        return false;
      }
      const data = await resp.json();
      App.state.scriptSourceName = data.name || "";
      App.state.scriptSource = data.source || "";
      App.state.scriptSourceLoaded = true;
      App.state.sourceDirty = false;
      App.state.sourceSaveStatus = "";
      if (data.title) {
        App.state.scriptTitle = data.title;
        App.state.scriptTitleVisible = true;
      }
      App.ui.renderSourcePanel();
      return true;
    } catch (e) {
      return false;
    }
  },
  async saveScriptSource(source) {
    try {
      const resp = await fetch("/api/script-source", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return { ok: false, error: data.error || `Save failed (${resp.status})` };
      }
      App.state.scriptSourceName = data.name || App.state.scriptSourceName || "";
      App.state.scriptSource = data.source || "";
      App.state.scriptSourceLoaded = true;
      App.state.sourceDirty = false;
      App.state.sourceSaveStatus = "";
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
  async loadWebhookConfig() {
    const state = App.state;
    const ui = App.ui;
    try {
      const resp = await fetch("/api/webhook-config");
      if (!resp.ok) return;
      const cfg = await resp.json();
      state.webhookEnabled = Boolean(cfg.enabled);
      state.telegramEnabled = Boolean(cfg.telegram_notification);
      ui.elements.webhookToggle.checked = state.webhookEnabled;
      ui.elements.telegramToggle.checked = state.telegramEnabled;
    } catch (e) {
      // ignore
    }
  },
  async updateWebhookConfig(payload) {
    try {
      const resp = await fetch("/api/webhook-config", {
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
      App.ui.elements.webhookToggle.checked = App.state.webhookEnabled;
      App.ui.elements.telegramToggle.checked = App.state.telegramEnabled;
      return true;
    } catch (e) {
      return false;
    }
  }
};
