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
    return point && Number.isFinite(Number(point.value));
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
      series.setData(data.filter(point => this.hasLineValue(point)));
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

    // Lightweight Charts LineSeries can fail on null/whitespace values; keep gaps only for linebr.
    const drawableData = seriesData.filter(point => this.hasLineValue(point));
    const series = this.addPlotLineSeries(chart, collections, options, drawableData);
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
      if (!this.hasLineValue(linePoint)) {
        return;
      }
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
  normalizeOhlcvBar(bar) {
    if (!bar || bar.time == null || bar.open == null || bar.high == null ||
      bar.low == null || bar.close == null) {
      return null;
    }
    const normalized = {
      time: Number(bar.time),
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
      volume: Number(bar.volume || 0)
    };
    if (!Number.isFinite(normalized.time) || !Number.isFinite(normalized.open) ||
      !Number.isFinite(normalized.high) || !Number.isFinite(normalized.low) ||
      !Number.isFinite(normalized.close) || !Number.isFinite(normalized.volume)) {
      return null;
    }
    return normalized;
  },
  toVolumePoint(bar) {
    return {
      time: bar.time,
      value: bar.volume,
      color: bar.close >= bar.open ? "#26a69a" : "#ef5350"
    };
  },
  refreshLoadedRange() {
    const state = App.state;
    const collections = App.collections;
    const data = collections.ohlcvData;
    if (data.length === 0) {
      state.firstBarTime = null;
      state.oldestLoadedTime = null;
      state.newestLoadedTime = null;
      return;
    }
    state.firstBarTime = data[0].time;
    state.oldestLoadedTime = data[0].time;
    state.newestLoadedTime = data[data.length - 1].time;
    state.lastPrice = data[data.length - 1].close;
    state.lastOhlcv = data[data.length - 1];
  },
  mergeOhlcvData(data) {
    const collections = App.collections;
    let mergedCount = 0;
    if (!Array.isArray(data)) {
      return mergedCount;
    }
    data.forEach(rawBar => {
      const bar = this.normalizeOhlcvBar(rawBar);
      if (!bar) {
        return;
      }
      const existing = collections.ohlcvByTime.get(bar.time);
      if (!existing || JSON.stringify(existing) !== JSON.stringify(bar)) {
        collections.ohlcvByTime.set(bar.time, bar);
        mergedCount += 1;
      }
    });
    collections.ohlcvData = Array.from(collections.ohlcvByTime.values())
      .sort((a, b) => a.time - b.time);
    this.refreshLoadedRange();
    return mergedCount;
  },
  mergeLiveOhlcv(rawBar) {
    const collections = App.collections;
    const bar = this.normalizeOhlcvBar(rawBar);
    if (!bar) {
      return null;
    }
    collections.ohlcvByTime.set(bar.time, bar);
    const existingIndex = collections.ohlcvData.findIndex(item => item.time === bar.time);
    if (existingIndex >= 0) {
      collections.ohlcvData[existingIndex] = bar;
    } else {
      collections.ohlcvData.push(bar);
      collections.ohlcvData.sort((a, b) => a.time - b.time);
    }
    this.refreshLoadedRange();
    return bar;
  },
  applyLastBarOpenFix(fixData) {
    const state = App.state;
    const collections = App.collections;
    if (!fixData || fixData.time == null || fixData.open == null) {
      return null;
    }
    const time = Number(fixData.time);
    const open = Number(fixData.open);
    if (!Number.isFinite(time) || !Number.isFinite(open)) {
      return null;
    }

    let fixedBar = null;
    const storedBar = collections.ohlcvByTime.get(time);
    if (storedBar) {
      fixedBar = { ...storedBar, open };
      collections.ohlcvByTime.set(time, fixedBar);
      const index = collections.ohlcvData.findIndex(item => item.time === time);
      if (index >= 0) {
        collections.ohlcvData[index] = fixedBar;
      }
    }
    if (state.lastOhlcv && state.lastOhlcv.time === time) {
      fixedBar = { ...state.lastOhlcv, open };
      state.lastOhlcv = fixedBar;
      collections.ohlcvByTime.set(time, fixedBar);
      const index = collections.ohlcvData.findIndex(item => item.time === time);
      if (index >= 0) {
        collections.ohlcvData[index] = fixedBar;
      }
    }
    this.refreshLoadedRange();
    return fixedBar;
  },
  restoreLogicalRange(logicalRange, logicalOffset = 0) {
    if (!logicalRange) {
      return false;
    }
    try {
      App.chart.chart.timeScale().setVisibleLogicalRange({
        from: logicalRange.from + logicalOffset,
        to: logicalRange.to + logicalOffset
      });
      return true;
    } catch {
      return false;
    }
  },
  preserveLogicalRange(callback, logicalOffset = 0) {
    const ts = App.chart.chart.timeScale();
    const logicalRange = ts.getVisibleLogicalRange();
    const result = callback();
    this.restoreLogicalRange(logicalRange, logicalOffset);
    return result;
  },
  applyOhlcvData(preserveRange = true, logicalOffset = 0) {
    const collections = App.collections;
    const chart = App.chart;
    const ts = chart.chart.timeScale();
    const logicalRange = preserveRange ? ts.getVisibleLogicalRange() : null;
    const visibleRange = preserveRange ? ts.getVisibleRange() : null;

    chart.candleSeries.setData(collections.ohlcvData);
    chart.volumeSeries.setData(collections.ohlcvData.map(bar => this.toVolumePoint(bar)));

    if (!preserveRange) {
      return;
    }
    try {
      if (this.restoreLogicalRange(logicalRange, logicalOffset)) {
        return;
      }
      if (visibleRange) {
        ts.setVisibleRange(visibleRange);
      }
    } catch {}
  },
  estimateVisibleBars() {
    const chart = App.chart;
    const range = chart.chart.timeScale().getVisibleLogicalRange();
    if (range && Number.isFinite(range.from) && Number.isFinite(range.to)) {
      return Math.max(1, Math.ceil(range.to - range.from + 1));
    }
    const width = Math.max(1, chart.container.clientWidth || window.innerWidth || 1);
    let barSpacing = chart.isMobileViewport() ? 5 : 6;
    try {
      const opts = chart.chart.timeScale().options();
      if (opts && Number.isFinite(Number(opts.barSpacing)) && Number(opts.barSpacing) > 0) {
        barSpacing = Number(opts.barSpacing);
      }
    } catch {}
    return Math.max(140, Math.ceil(width / barSpacing));
  },
  getChunkLimit() {
    return Math.min(5000, Math.max(1000, Math.ceil(this.estimateVisibleBars() * 3)));
  },
  getLoadThreshold() {
    return Math.max(100, Math.ceil(this.estimateVisibleBars() * 0.5));
  },
  buildHistoryUrl(kind, limit, direction) {
    const params = new URLSearchParams();
    const state = App.state;
    params.set("limit", String(limit || this.getChunkLimit()));
    if (direction === "before" && state.oldestLoadedTime != null) {
      params.set("before", String(state.oldestLoadedTime));
    } else if (direction === "after" && state.newestLoadedTime != null) {
      params.set("after", String(state.newestLoadedTime));
    }
    return `${App.config.apiBase}/${kind}?${params.toString()}`;
  },
  clearPlotSeries() {
    const collections = App.collections;
    const chart = App.chart;
    for (const series of collections.plotSeriesList) {
      chart.chart.removeSeries(series);
    }
    collections.plotSeriesList.length = 0;
    collections.plotSeriesMap.clear();
  },
  mergePlotPayload(plots) {
    const collections = App.collections;
    let mergedCount = 0;
    if (!Array.isArray(plots)) {
      return mergedCount;
    }
    plots.forEach(plot => {
      if (!plot || !plot.title) {
        return;
      }
      const meta = {
        title: plot.title,
        color: plot.color,
        linewidth: plot.linewidth,
        style: plot.style
      };
      const styleCode = parseInt(plot.style, 10);
      const keepsGaps = styleCode === this.STYLE_LINEBR;
      collections.plotMetaByTitle.set(plot.title, meta);
      let pointMap = collections.plotDataByTitle.get(plot.title);
      if (!pointMap) {
        pointMap = new Map();
        collections.plotDataByTitle.set(plot.title, pointMap);
      }
      if (Array.isArray(plot.data)) {
        plot.data.forEach(point => {
          const linePoint = this.toLinePoint(point.time, point.value);
          if (linePoint) {
            if (!keepsGaps && !this.hasLineValue(linePoint)) {
              pointMap.delete(linePoint.time);
              return;
            }
            pointMap.set(linePoint.time, linePoint);
            mergedCount += 1;
          }
        });
      }
    });
    return mergedCount;
  },
  renderPlotSeries() {
    const collections = App.collections;
    const chart = App.chart;
    this.preserveLogicalRange(() => {
      this.clearPlotSeries();
      collections.plotMetaByTitle.forEach((plot, title) => {
        const pointMap = collections.plotDataByTitle.get(title);
        const seriesData = pointMap
          ? Array.from(pointMap.values()).sort((a, b) => a.time - b.time)
          : [];
        this.createPlotSeries(chart, collections, plot, seriesData);
      });
    });
  },
  mergeLivePlotPoint(title, time, value) {
    const collections = App.collections;
    const linePoint = this.toLinePoint(time, value);
    if (!title || !linePoint) {
      return;
    }
    let pointMap = collections.plotDataByTitle.get(title);
    if (!pointMap) {
      pointMap = new Map();
      collections.plotDataByTitle.set(title, pointMap);
    }
    pointMap.set(linePoint.time, linePoint);
  },
  normalizeMarkerLineData(data) {
    const byTime = new Map();
    if (!Array.isArray(data)) {
      return [];
    }
    data.forEach(point => {
      if (!point || !Number.isFinite(Number(point.time)) || !Number.isFinite(Number(point.value))) {
        return;
      }
      byTime.set(Number(point.time), {
        time: Number(point.time),
        value: Number(point.value)
      });
    });
    return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
  },
  setMarkerLineData(series, data) {
    const normalizedData = this.normalizeMarkerLineData(data);
    this.preserveLogicalRange(() => {
      series.setData(normalizedData);
    });
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

      const resp = await fetch(`${App.config.apiBase}/trades`);
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

      if (collections.seriesMarkers) {
        collections.seriesMarkers.setMarkers(collections.markers);
      } else if (collections.markers.length > 0) {
        collections.seriesMarkers = LightweightCharts.createSeriesMarkers(chart.candleSeries, collections.markers);
      }

      if (collections.entryMarkerData.length > 0) {
        this.setMarkerLineData(chart.entryMarkerSeries, collections.entryMarkerData);
      }
      if (collections.closeMarkerData.length > 0) {
        this.setMarkerLineData(chart.closeMarkerSeries, collections.closeMarkerData);
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

      const resp = await fetch(`${App.config.apiBase}/plotchar`);
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

      if (collections.plotcharSeriesMarkers) {
        collections.plotcharSeriesMarkers.setMarkers(collections.plotcharMarkers);
      } else if (collections.plotcharMarkers.length > 0) {
        collections.plotcharSeriesMarkers = LightweightCharts.createSeriesMarkers(
          chart.candleSeries,
          collections.plotcharMarkers
        );
      }
    } catch (e) {
      console.error("Failed to load plotchar history:", e);
    }
  },
  async loadPlotData(limit = null, direction = null, retry = false) {
    const state = App.state;
    if (!state.runnerConnected) return false;
    const attempts = retry ? 30 : 1;
    const chunkLimit = limit || this.getChunkLimit();
    for (let i = 0; i < attempts; i++) {
      try {
        const resp = await fetch(this.buildHistoryUrl("plot", chunkLimit, direction));
        const plots = await resp.json();

        if (Array.isArray(plots) && plots.length > 0) {
          this.mergePlotPayload(plots);
          this.renderPlotSeries();
          return true;
        }
        if (!retry && Array.isArray(plots)) {
          return true;
        }
      } catch (e) {
        // Ignore and retry
      }
      if (retry) {
        await App.util.sleep(1000);
      }
    }
    return false;
  },
  async loadChartChunk(direction) {
    const state = App.state;
    const collections = App.collections;
    // The initial window is already the newest data; live bars arrive through WS updates.
    if (direction === "after") {
      state.historyEndReached = true;
      return;
    }
    if (!state.initialLoadDone || collections.ohlcvData.length === 0) {
      return;
    }
    if (state.loadingOlder || state.loadingNewer) {
      return;
    }
    if (direction === "before" && (state.historyStartReached || state.oldestLoadedTime == null)) {
      return;
    }
    if (direction === "after" && (state.historyEndReached || state.newestLoadedTime == null)) {
      return;
    }

    const loadingKey = direction === "before" ? "loadingOlder" : "loadingNewer";
    state[loadingKey] = true;
    const limit = this.getChunkLimit();
    const previousLength = collections.ohlcvData.length;
    try {
      const ohlcvUrl = this.buildHistoryUrl("ohlcv", limit, direction);
      const plotUrl = this.buildHistoryUrl("plot", limit, direction);
      const resp = await fetch(ohlcvUrl);
      const data = await resp.json();
      const cleanData = Array.isArray(data)
        ? data.map(bar => this.normalizeOhlcvBar(bar)).filter(Boolean)
        : [];
      if (cleanData.length === 0) {
        if (direction === "before") {
          state.historyStartReached = true;
        } else {
          state.historyEndReached = true;
        }
        return;
      }

      let plots = [];
      try {
        const plotResp = await fetch(plotUrl);
        plots = await plotResp.json();
      } catch (e) {
        plots = [];
      }

      this.mergeOhlcvData(cleanData);
      const logicalOffset = Math.max(0, collections.ohlcvData.length - previousLength);
      this.applyOhlcvData(true, logicalOffset);
      if (Array.isArray(plots) && plots.length > 0) {
        this.mergePlotPayload(plots);
        this.renderPlotSeries();
      }

      if (collections.ohlcvData.length !== previousLength) {
        await this.loadTradeHistory();
        await this.loadPlotcharHistory();
      } else if (direction === "before") {
        state.historyStartReached = true;
      } else {
        state.historyEndReached = true;
      }

      if (cleanData.length < limit) {
        if (direction === "before") {
          state.historyStartReached = true;
        } else {
          state.historyEndReached = true;
        }
      }
    } catch (e) {
      console.error(`Failed to load ${direction} chart chunk:`, e);
    } finally {
      state[loadingKey] = false;
    }
  },
  attachLazyLoad() {
    const state = App.state;
    const chart = App.chart;
    const collections = App.collections;
    if (state.lazyLoadAttached || !chart.chart) {
      return;
    }
    state.lazyLoadAttached = true;
    chart.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      if (!state.initialLoadDone || collections.ohlcvData.length === 0) {
        return;
      }
      if (state.lazyLoadTimer) {
        clearTimeout(state.lazyLoadTimer);
      }
      state.lazyLoadTimer = setTimeout(() => {
        state.lazyLoadTimer = null;
        const latestRange = chart.chart.timeScale().getVisibleLogicalRange();
        if (!latestRange || !state.initialLoadDone || collections.ohlcvData.length === 0) {
          return;
        }
        const threshold = this.getLoadThreshold();
        if (latestRange.from <= threshold) {
          this.loadChartChunk("before");
        }
      }, 120);
    });
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
        const limit = this.getChunkLimit();
        const resp = await fetch(this.buildHistoryUrl("ohlcv", limit, null));
        const data = await resp.json();
        if (Array.isArray(data) && data.length > 0) {
          const cleanData = data.map(bar => this.normalizeOhlcvBar(bar)).filter(Boolean);
          if (cleanData.length === 0) {
            await App.util.sleep(1000);
            continue;
          }

          collections.ohlcvData.length = 0;
          collections.ohlcvByTime.clear();
          collections.plotMetaByTitle.clear();
          collections.plotDataByTitle.clear();
          this.clearPlotSeries();
          this.mergeOhlcvData(cleanData);
          this.applyOhlcvData(false);
          state.historyStartReached = cleanData.length < limit;
          state.historyEndReached = true;

          const savedRange = sessionStorage.getItem(App.config.storageKey("chartVisibleRange"));
          const savedLogicalRange = sessionStorage.getItem(App.config.storageKey("chartVisibleLogicalRange"));
          const savedScale = sessionStorage.getItem(App.config.storageKey("chartScaleOptions"));
          if (savedScale) {
            try {
              const opts = JSON.parse(savedScale);
              chart.chart.timeScale().applyOptions(opts);
            } catch {}
            sessionStorage.removeItem(App.config.storageKey("chartScaleOptions"));
          }

          let restoredRange = false;
          if (savedRange) {
            try {
              const range = JSON.parse(savedRange);
              if (range && Number(range.from) >= state.oldestLoadedTime &&
                Number(range.to) <= state.newestLoadedTime) {
                chart.chart.timeScale().setVisibleRange(range);
                restoredRange = true;
              }
            } catch {}
            sessionStorage.removeItem(App.config.storageKey("chartVisibleRange"));
          }
          if (!restoredRange && savedLogicalRange) {
            try {
              const range = JSON.parse(savedLogicalRange);
              if (range && Number(range.from) >= 0 && Number(range.to) <= collections.ohlcvData.length - 1) {
                chart.chart.timeScale().setVisibleLogicalRange(range);
                restoredRange = true;
              }
            } catch {}
            sessionStorage.removeItem(App.config.storageKey("chartVisibleLogicalRange"));
          } else if (savedLogicalRange) {
            sessionStorage.removeItem(App.config.storageKey("chartVisibleLogicalRange"));
          }
          if (!restoredRange) {
            chart.applyInitialVisibleRange(collections.ohlcvData.length);
          }

          if (state.configuredTimeframeSec) {
            state.timeframeInterval = state.configuredTimeframeSec;
          } else if (collections.ohlcvData.length >= 2) {
            // Fallback only: on OKX/Binance zero-volume bars are hidden, so the gap
            // between the first two visible bars may not be the timeframe.
            state.timeframeInterval = collections.ohlcvData[1].time - collections.ohlcvData[0].time;
          }

          await this.loadTradeHistory();
          await this.loadPlotcharHistory();
          await this.loadPlotData(limit, null, true);
          state.initialLoadDone = true;
          state.initialLoadInProgress = false;
          this.attachLazyLoad();
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
      const resp = await fetch(`${App.config.apiBase}/info`);
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
      } else if (!state.scriptSourceLoaded) {
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
      const resp = await fetch(`${App.config.apiBase}/script-source`);
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
      App.ui.setChartInfo();
      return true;
    } catch (e) {
      return false;
    }
  },
  async saveScriptSource(source) {
    try {
      const resp = await fetch(`${App.config.apiBase}/script-source`, {
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
      const resp = await fetch(`${App.config.apiBase}/webhook-config`);
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
      App.ui.elements.webhookToggle.checked = App.state.webhookEnabled;
      App.ui.elements.telegramToggle.checked = App.state.telegramEnabled;
      return true;
    } catch (e) {
      return false;
    }
  }
};
