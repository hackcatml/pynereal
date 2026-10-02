var App = window.App || (window.App = {});

App.history = {
  pageSize: 5000,
  loading: false,
  hasBefore: false,
  hasAfter: false,
  plots: new Map(),
  trades: new Map(),
  plotchars: new Map(),
  pending: new Map(),
  controller: null,
  replaying: false,
  resyncPending: false,
  lastRange: null,
  windows: [],
  activeWindow: null,
  renderedWindow: null,
  loadComplete: null,
  finishLoading: null,
  requestTimeoutMs: 10000,
  edgeRequestId: 0,
  cancelPending(replay = true) {
    if (!this.loading && !App.state.initialLoadInProgress) return;
    App.state.loadGeneration++;
    App.state.initialLoadInProgress = false;
    if (this.controller) this.controller.abort();
    this.controller = null;
    this.loading = false;
    this.edgeRequestId++;
    this.finishLoading?.();
    this.finishLoading = null;
    if (replay) this.flushLive();
    else this.pending.clear();
  },
  reset() {
    this.cancelPending(false);
    App.timeframes?.reset();
    this.resyncPending = false;
    this.hasBefore = false;
    this.hasAfter = false;
    this.lastRange = null;
    this.windows = [];
    this.activeWindow = null;
    this.renderedWindow = null;
    this.edgeRequestId++;
    this.plots.clear();
    this.trades.clear();
    this.plotchars.clear();
    this.pending.clear();
  },
  suspend() {
    this.resyncPending = true;
    this.cancelPending(false);
    this.pending.clear();
  },
  eventKey(msg) {
    return `${msg.type}:${msg.time ?? msg.data?.time}:${msg.title ?? msg.exit_id ?? msg.id ?? ""}`;
  },
  cacheWindow(payload, mode, remember) {
    const bars = new Map(payload.bars.map(bar => [bar.time, bar]));
    const window = {
      bars, start: payload.bars[0].time, end: payload.bars.at(-1).time,
      hasBefore: payload.has_before, hasAfter: payload.has_after,
      interval: payload.interval, plots: new Map(),
      trades: new Map(payload.trades.map(msg => [this.eventKey(msg), msg])),
      plotchars: new Map(payload.plotchars.map(msg => [this.eventKey(msg), msg])),
      lastOpenPrice: { time: 0, value: 0 }
    };
    for (const plot of payload.plots) {
      window.plots.set(plot.title, {
        definition: plot, points: new Map(plot.data.map(point => [point.time, point])),
        lastTime: plot.data.at(-1)?.time ?? -Infinity
      });
    }
    if (!remember) return window;
    const refresh = mode === "refresh";
    const start = window.start, end = window.end;
    const keepCached = time => !refresh || time < start || time > end;
    const extending = mode === "before" || mode === "after";
    // Only merge overlapping windows, or a page fetched directly next to the
    // active window. Disconnected history must not appear as adjacent candles.
    const remaining = new Set(this.windows);
    let merged;
    do {
      merged = false;
      for (const cached of remaining) {
        if (refresh && !payload.has_after && cached.end < start) cached.hasAfter = true;
        const adjacent = cached.end + window.interval === window.start || window.end + window.interval === cached.start;
        if (!(extending && cached === this.activeWindow) && !adjacent &&
            (cached.end < window.start || cached.start > window.end)) continue;
        if (cached.start <= window.start) {
          window.hasBefore = cached.start === window.start
            ? window.hasBefore && cached.hasBefore : cached.hasBefore;
        }
        if (cached.end >= window.end) {
          window.hasAfter = cached.end === window.end
            ? window.hasAfter && cached.hasAfter : cached.hasAfter;
        }
        window.start = Math.min(window.start, cached.start);
        window.end = Math.max(window.end, cached.end);
        cached.bars.forEach((bar, time) => { if (keepCached(time)) bars.set(time, bar); });
        for (const [title, plot] of cached.plots) {
          const target = window.plots.get(title);
          if (!target) {
            const points = new Map([...plot.points].filter(([time]) => keepCached(time)));
            let lastTime = -Infinity;
            for (const time of points.keys()) lastTime = Math.max(lastTime, time);
            window.plots.set(title, { ...plot, points, lastTime });
          }
          else {
            plot.points.forEach((point, time) => {
              if (keepCached(time)) {
                target.points.set(time, point);
                target.lastTime = Math.max(target.lastTime, time);
              }
            });
          }
        }
        cached.trades.forEach((msg, key) => { if (keepCached(msg.time)) window.trades.set(key, msg); });
        cached.plotchars.forEach((msg, key) => { if (keepCached(msg.time)) window.plotchars.set(key, msg); });
        if (keepCached(cached.lastOpenPrice.time) && cached.lastOpenPrice.time > window.lastOpenPrice.time) {
          window.lastOpenPrice = cached.lastOpenPrice;
        }
        remaining.delete(cached);
        merged = true;
      }
    } while (merged);
    this.windows = [...remaining, window].sort((a, b) => a.start - b.start);
    return window;
  },
  selectWindow(window) {
    this.activeWindow = window;
    this.hasBefore = window.hasBefore;
    this.hasAfter = window.hasAfter;
  },
  retainedData(windows) {
    const first = windows[0], last = windows.at(-1);
    const combined = {
      bars: new Map(), plots: new Map(), trades: new Map(), plotchars: new Map(),
      start: first.start, end: last.end, hasAfter: last.hasAfter,
      lastOpenPrice: last.lastOpenPrice
    };
    const bars = [], gaps = [];
    const interval = App.state.configuredTimeframeSec || first.interval || 60;
    for (let i = 0; i < windows.length; i++) {
      const window = windows[i];
      if (i > 0) {
        const previous = windows[i - 1];
        // Reserve only timestamps, not fabricated OHLCV, for unread history.
        // Filling the gap later replaces these slots without discarding either edge.
        const gapTime = Math.min(previous.end + interval, (previous.end + window.start) / 2);
        gaps.push(gapTime);
        for (let time = gapTime; time < window.start; time += interval) bars.push({ time });
      }
      const sorted = [...window.bars.values()].sort((a, b) => a.time - b.time);
      for (const bar of sorted) bars.push(bar);
      window.bars.forEach((bar, time) => combined.bars.set(time, bar));
      window.trades.forEach((event, key) => combined.trades.set(key, event));
      window.plotchars.forEach((event, key) => combined.plotchars.set(key, event));
      for (const [title, plot] of window.plots) {
        let target = combined.plots.get(title);
        if (!target) {
          target = { definition: plot.definition, points: new Map(), lastTime: -Infinity };
          combined.plots.set(title, target);
        }
        plot.points.forEach((point, time) => target.points.set(time, point));
        target.lastTime = Math.max(target.lastTime, plot.lastTime);
      }
    }
    return { combined, bars, gaps };
  },
  cacheLive(window, msg, time) {
    if (msg.type === "bar") {
      if (time < window.end) return;
      const bar = { ...msg.data };
      if (time === window.lastOpenPrice.time && window.lastOpenPrice.value > 0 &&
          bar.open !== parseFloat(window.lastOpenPrice.value.toFixed(2))) {
        bar.open = window.lastOpenPrice.value;
      }
      window.bars.set(time, bar);
      window.end = time;
    } else if (msg.type === "last_bar_open_fix") {
      if (msg.data.open == null) return;
      window.lastOpenPrice = { time, value: msg.data.open };
      const bar = window.bars.get(time);
      if (bar) window.bars.set(time, { ...bar, open: msg.data.open });
      for (const [key, trade] of window.trades) {
        if (trade.time === time && Math.abs(trade.price - msg.data.open) > 0.01) {
          window.trades.set(key, { ...trade, price: msg.data.open });
        }
      }
    } else if (msg.type === "trade_entry" || msg.type === "trade_close") {
      window.trades.set(this.eventKey(msg), msg);
    } else if (msg.type === "plotchar") {
      window.plotchars.set(this.eventKey(msg), msg);
    } else if (msg.type === "plot_data") {
      const plot = window.plots.get(msg.title);
      if (plot && time >= plot.lastTime) {
        plot.points.set(time, { time, value: msg.value });
        plot.lastTime = time;
      }
    }
  },
  captureLive(msg) {
    if (!["bar", "last_bar_open_fix", "trade_entry", "trade_close", "plotchar", "plot_data"].includes(msg.type)) {
      return false;
    }
    if ((this.loading || this.resyncPending) && !this.replaying) {
      // Keep only the latest update per candle/plot, but retain distinct orders.
      this.pending.set(this.eventKey(msg), msg);
      return true;
    }
    const time = Number(msg.time ?? msg.data?.time);
    const window = this.windows.find(cached => time >= cached.start && (time <= cached.end || !cached.hasAfter));
    if (window) {
      this.cacheLive(window, msg, time);
      if (this.renderedWindow) this.cacheLive(this.renderedWindow, msg, time);
    }
    if (!window && this.windows.length) return true;
    if (App.state.firstBarTime != null && time < App.state.firstBarTime) return true;
    // A snapshot can already contain a newer plot point than this event.
    if (msg.type === "plot_data" && time < (this.plots.get(msg.title)?.lastTime ?? -Infinity)) return true;
    return false;
  },
  flushLive() {
    if (this.resyncPending) return;
    const events = [...this.pending.values()];
    this.pending.clear();
    this.replaying = true;
    try {
      events.forEach(msg => App.ws.handleMessage(msg));
    } finally {
      this.replaying = false;
    }
  },
  logicalAt(time) {
    const scale = App.chart.chart.timeScale();
    const coordinate = scale.timeToCoordinate(App.timeframes?.bucketTime(time) ?? time);
    return coordinate == null ? null : scale.coordinateToLogical(coordinate);
  },
  settleChart() {
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  },
  async applyWindow(payload, mode, overlays = true) {
    const { state, collections, chart } = App;
    const generation = state.loadGeneration;
    const extending = mode === "before" || mode === "after" || mode === "range" || mode === "refresh";
    if (extending) await this.settleChart();
    if (generation !== state.loadGeneration) return;
    const scale = chart.chart.timeScale();
    const previousRange = scale.getVisibleLogicalRange();
    const anchorTime = scale.getVisibleRange()?.from;
    const anchorIndex = anchorTime == null ? null : this.logicalAt(anchorTime);
    const previousLast = state.lastBarTime;
    const previousLastIndex = previousLast ? this.logicalAt(previousLast) : null;
    const followLatest = mode === "refresh" && scale.getVisibleRange()?.to >= previousLast;
    if (!payload.bars.length) {
      if (mode === "before") this.hasBefore = false;
      if (mode === "after") this.hasAfter = false;
      if (this.activeWindow) {
        this.activeWindow.hasBefore = this.hasBefore;
        this.activeWindow.hasAfter = this.hasAfter;
      }
      return;
    }
    const window = this.cacheWindow(payload, mode, overlays);
    const { combined, bars, gaps } = this.retainedData(overlays ? this.windows : [window]);
    this.selectWindow(window);
    this.renderedWindow = combined;
    this.plots = combined.plots;
    this.trades = combined.trades;
    this.plotchars = combined.plotchars;
    this.renderData(bars, gaps, overlays);
    if (extending && previousRange && anchorIndex != null) {
      await this.settleChart();
      if (generation !== state.loadGeneration) return;
      const nextIndex = this.logicalAt(followLatest ? state.lastBarTime : anchorTime);
      const oldIndex = followLatest ? previousLastIndex : anchorIndex;
      const shift = nextIndex == null || oldIndex == null ? 0 : nextIndex - oldIndex;
      scale.setVisibleLogicalRange({ from: previousRange.from + shift, to: previousRange.to + shift });
    }
    // Measurements store logical indexes; keep their endpoints on the same candles.
    const measurement = App.measure?.activeMeasurement();
    if (measurement) {
      for (const point of new Set([measurement.start, measurement.end])) {
        const index = collections.ohlcvIndexByTime.get(point.time);
        if (index == null) { App.measure.clear(); break; }
        point.logical = index;
      }
    }
    App.ui?.applyManualAlertTriggerState?.(state.manualAlertTriggers || []);
    await this.settleChart();
  },
  renderData(sourceBars, gaps, overlays = true) {
    const { state, chart } = App;
    const higher = App.timeframes?.isHigher();
    const bars = App.timeframes?.project(sourceBars, this.windows) ?? sourceBars;
    state.firstBarTime = bars[0].time;
    const last = bars.findLast(bar => bar.close != null);
    state.lastBarTime = bars[bars.length - 1].time;
    state.lastPrice = last?.close || 0;
    state.lastOhlcv = last;
    state.lastOpenPrice = { ...this.renderedWindow.lastOpenPrice };
    state.timeframeInterval = higher ? App.timeframes.seconds() : state.configuredTimeframeSec || this.activeWindow.interval || 60;
    // Release old plots before replacing their shared candle timeline.
    if (overlays || higher) App.data.clearPlotData();
    if (higher) {
      App.data.renderTradeHistory([]);
      App.data.renderPlotcharHistory([]);
    }
    App.data.rebuildOhlcvCache(bars);
    chart.candleSeries.setData(bars);
    chart.volumeSeries.setData(bars.map(bar => bar.close == null ? { time: bar.time } : ({
      time: bar.time, value: bar.volume,
      color: bar.close >= bar.open ? "#26a69a" : "#ef5350"
    })));
    if (overlays && !higher) {
      App.data.renderPlotData([...this.plots.values()].map(plot => ({
        ...plot.definition, historyGaps: gaps,
        data: [...plot.points.values()].sort((a, b) => a.time - b.time)
      })));
      App.data.renderTradeHistory([...this.trades.values()].sort((a, b) => a.time - b.time));
      App.data.renderPlotcharHistory([...this.plotchars.values()].sort((a, b) => a.time - b.time));
    }
  },
  async fetchWindow(query, signal) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    const timeout = setTimeout(() => controller.abort(new Error("Chart history request timed out")), this.requestTimeoutMs);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    let rejectAbort;
    const aborted = new Promise((_, reject) => {
      rejectAbort = () => reject(controller.signal.reason || new Error("Chart history request cancelled"));
      controller.signal.addEventListener("abort", rejectAbort, { once: true });
      if (controller.signal.aborted) rejectAbort();
    });
    try {
      const params = new URLSearchParams({ limit: this.pageSize, ...query });
      return await Promise.race([
        (async () => {
          const response = await fetch(`${App.config.apiBase}/chart-window?${params}`, { signal: controller.signal });
          if (!response.ok) throw new Error(`Chart history HTTP ${response.status}`);
          return response.json();
        })(),
        aborted
      ]);
    } finally {
      clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
      controller.signal.removeEventListener("abort", rejectAbort);
    }
  },
  async requestWindow(query = {}, mode = "replace", initial = false) {
    if (this.loading) return false;
    this.loading = true;
    let finishLoading;
    this.loadComplete = new Promise(resolve => { finishLoading = resolve; });
    this.finishLoading = finishLoading;
    const generation = App.state.loadGeneration;
    const controller = new AbortController();
    this.controller = controller;
    const deadline = Date.now() + 5 * 60 * 1000;
    let failures = 0;
    let painted = false;
    try {
      while (Date.now() < deadline && failures < (initial ? 30 : 3)) {
        if (generation !== App.state.loadGeneration) return false;
        try {
          const payload = await this.fetchWindow(query, controller.signal);
          if (generation !== App.state.loadGeneration) return false;
          if (!payload.bars.length && initial) {
            failures += 1;
          } else if (payload.overlays_ready) {
            await this.applyWindow(payload, mode);
            if (generation !== App.state.loadGeneration) return false;
            if (initial) App.state.initialLoadDone = true;
            if (initial || mode === "refresh") this.resyncPending = false;
            return true;
          } else if (initial && !painted) {
            await this.applyWindow(payload, mode, false);
            if (generation !== App.state.loadGeneration) return false;
            App.chart.applyInitialVisibleRange(App.collections.ohlcvData.length);
            painted = true;
          }
          this.flushLive();
        } catch (error) {
          if (controller.signal.aborted) return false;
          failures += 1;
          if (failures >= (initial ? 30 : 3)) throw error;
        }
        await App.util.sleep(1000);
      }
      throw new Error("Chart history is not ready; retry after warm-up.");
    } catch (error) {
      if (!controller.signal.aborted) console.error("Failed to load chart history:", error);
      return false;
    } finally {
      try {
        if (generation === App.state.loadGeneration) {
          this.flushLive();
          this.loading = false;
          this.controller = null;
          this.finishLoading = null;
          this.lastRange = App.chart.chart.timeScale().getVisibleLogicalRange();
        }
      } finally {
        finishLoading();
      }
    }
  },
  async refreshRecent() {
    const generation = App.state.loadGeneration;
    const initial = !App.state.initialLoadDone;
    if (this.loading) {
      await this.loadComplete;
      if (generation !== App.state.loadGeneration) return false;
      if (initial && App.state.initialLoadDone) return true;
    }
    if (!App.state.initialLoadDone) {
      await this.loadInitial();
      return App.state.initialLoadDone;
    }
    return this.requestWindow({}, "refresh");
  },
  async loadInitial() {
    const state = App.state;
    if (state.initialLoadInProgress || state.initialLoadDone) return;
    state.initialLoadInProgress = true;
    const generation = state.loadGeneration;
    let savedRange = null;
    let savedScale = null;
    try {
      savedRange = JSON.parse(sessionStorage.getItem(App.config.storageKey("chartVisibleRange")));
      savedScale = JSON.parse(sessionStorage.getItem(App.config.storageKey("chartScaleOptions")));
    } catch {}
    const query = savedRange && Number.isFinite(savedRange.from) ? { after: Math.max(0, savedRange.from - 1) } : {};
    try {
      if (!await this.requestWindow(query, "replace", true) || generation !== state.loadGeneration) return;
      const scale = App.chart.chart.timeScale();
      if (savedScale) scale.applyOptions(savedScale);
      App.chart.applyInitialVisibleRange(App.collections.ohlcvData.length);
      if (savedRange) scale.setVisibleRange(savedRange);
      await this.settleChart();
      if (generation !== state.loadGeneration) return;
      this.lastRange = scale.getVisibleLogicalRange();
      for (const key of ["chartVisibleRange", "chartVisibleLogicalRange", "chartScaleOptions"]) {
        sessionStorage.removeItem(App.config.storageKey(key));
      }
    } finally {
      if (generation === state.loadGeneration) state.initialLoadInProgress = false;
    }
  },
  async extend(direction) {
    const window = this.activeWindow;
    if (this.loading || !window || !App.state.initialLoadDone) return;
    if (direction === "before" && this.hasBefore) {
      await this.requestWindow({ before: window.start }, "before");
    } else if (direction === "after" && this.hasAfter) {
      await this.requestWindow({ after: window.end }, "after");
    }
  },
  async goToEdge(edge) {
    if (!App.state.initialLoadDone) return false;
    const generation = App.state.loadGeneration;
    const requestId = ++this.edgeRequestId;
    while (this.loading) {
      await this.loadComplete;
      if (generation !== App.state.loadGeneration || requestId !== this.edgeRequestId) return false;
    }
    const cached = this.windows.find(window => edge === "start" ? !window.hasBefore : !window.hasAfter);
    if (cached) {
      this.selectWindow(cached);
      return true;
    }
    const loaded = await this.requestWindow(edge === "start" ? { after: 0 } : {}, "replace");
    return loaded && generation === App.state.loadGeneration && requestId === this.edgeRequestId;
  },
  async loadVisibleRange(range, direction) {
    const bars = App.collections.ohlcvData;
    const scale = App.chart.chart.timeScale();
    const center = Math.round((range.from + range.to) / 2);
    const time = scale.coordinateToTime(scale.logicalToCoordinate(center))
      ?? (center < 0 ? bars[0]?.time : bars.at(-1)?.time);
    if (time == null) return;
    const window = this.windows.find(window =>
      (App.timeframes?.bucketTime(window.start) ?? window.start) <= time && time <= window.end);
    if (!window) {
      // A wide drag can land inside an unread gap. Fetch that viewport directly.
      const from = scale.getVisibleRange()?.from ?? time;
      await this.requestWindow({ after: Math.max(0, from - 1) }, "range");
      return;
    }
    this.selectWindow(window);
    const firstIndex = this.logicalAt(window.start);
    const lastIndex = this.logicalAt(window.end);
    if (direction < 0 && range.from < firstIndex + 100) await this.extend("before");
    else if (direction > 0 && range.to > lastIndex - 100) await this.extend("after");
  },
  attach() {
    App.chart.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      const previous = this.lastRange;
      this.lastRange = range;
      if (!range || !previous || this.loading || App.timeframes?.switching || App.state.initialLoadInProgress || !App.state.initialLoadDone) return;
      // fitContent() on the initial 5,000 bars must not immediately fetch all history.
      const direction = range.from < previous.from - 0.5 ? -1 : range.to > previous.to + 0.5 ? 1 : 0;
      if (direction) void this.loadVisibleRange(range, direction);
    });
  }
};
