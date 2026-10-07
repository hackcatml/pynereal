var App = window.App || (window.App = {});

// Minute history has its own source/cache. Primary bars and strategy events
// must never enter it, even when an HTTP request or a reconnect is in flight.
App.minuteChart = {
  socket: null,
  timer: null,
  watchdog: null,
  pending: null,
  caches: new Map(),
  close() {
    clearTimeout(this.timer);
    clearTimeout(this.watchdog);
    this.timer = this.watchdog = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
      socket.close();
    }
    this.pending = null;
  },
  reset() {
    this.close();
    this.caches.clear();
  },
  acceptBar(old, bar) {
    if (!old) return true;
    const authoritative = value => value.source && !["live", "trades"].includes(value.source);
    if (authoritative(old) && !authoritative(bar)) return false;
    if (!authoritative(old) && authoritative(bar)) return true;
    if (bar.source === "live" || old.source === "live") {
      if (bar.volume !== old.volume) return bar.volume > old.volume;
    }
    return !(old.updated_at > bar.updated_at);
  },
  connect() {
    if (!App.timeframes.isMinute() || document.hidden || this.socket) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}${App.config.wsPath}/minute-chart`);
    this.socket = socket;
    const retry = () => {
      if (this.socket !== socket) return;
      this.close();
      this.timer = setTimeout(() => this.connect(), 1500);
    };
    const alive = () => {
      clearTimeout(this.watchdog);
      this.watchdog = setTimeout(retry, 15000);
    };
    alive();
    socket.onclose = socket.onerror = retry;
    socket.onmessage = event => {
      if (this.socket !== socket || !App.timeframes.isMinute()) return;
      alive();
      try {
        const payload = JSON.parse(event.data);
        if (payload.type !== "minute_window") return;
        this.pending = payload;
        this.flush();
      } catch (error) { console.error("Minute chart update failed:", error); }
    };
  },
  flush() {
    const history = App.history;
    if (!this.pending || !App.timeframes.isMinute() || !App.state.initialLoadDone || history.loading) return;
    const payload = this.pending;
    this.pending = null;
    const latest = history.windows.findLast(window => !window.hasAfter);
    if (!latest || !history.renderedWindow) return;
    if (payload.bars.length && payload.bars[0].time > latest.end + 60) {
      // A background tab can miss more than the small live window.
      void history.refreshRecent();
      return;
    }
    const { state, chart } = App;
    if (payload.bars.some(bar => bar.time >= latest.start && bar.time < state.lastBarTime &&
        !latest.bars.has(bar.time))) {
      // A REST repair inserted a previously absent candle. Rebuild the shared
      // timeline once, preserving its viewport, rather than updating a missing point.
      void history.requestWindow({ after: payload.bars[0].time - 1 }, "refresh");
      return;
    }
    const changed = [];
    const previousLast = state.lastBarTime;
    for (const bar of payload.bars) {
      if (bar.time < latest.start) continue;
      const old = latest.bars.get(bar.time);
      if (!this.acceptBar(old, bar)) continue;
      if (old && ["open", "high", "low", "close", "volume"].every(key => old[key] === bar[key])) {
        latest.bars.set(bar.time, bar);
        history.renderedWindow.bars.set(bar.time, bar);
        continue;
      }
      latest.bars.set(bar.time, bar);
      latest.end = Math.max(latest.end, bar.time);
      history.renderedWindow.bars.set(bar.time, bar);
      history.renderedWindow.end = Math.max(history.renderedWindow.end, bar.time);
      const historical = bar.time < state.lastBarTime;
      chart.candleSeries.update(bar, historical);
      chart.volumeSeries.update({ time: bar.time, value: bar.volume,
        color: bar.close >= bar.open ? "#26a69a" : "#ef5350" }, historical);
      changed.push(bar);
      if (!historical) {
        state.lastBarTime = bar.time;
        state.lastOhlcv = bar;
        state.lastPrice = bar.close;
      }
    }
    if (changed.some(bar => bar.time < previousLast)) {
      // Historical corrections require fresh indicator seeds. Apply the whole
      // batch first and retain the displayed result until the worker replaces it.
      const { ohlcvData, ohlcvIndexByTime } = App.collections;
      for (const bar of changed) {
        const index = ohlcvIndexByTime.get(bar.time);
        if (index != null) ohlcvData[index] = bar;
        else ohlcvData.push(bar);
      }
      App.data.rebuildOhlcvCache(ohlcvData, false, { preserveIndicators: true });
    } else {
      changed.forEach(bar => App.data.upsertOhlcvCache(bar));
    }
  },
  async select(seconds, id) {
    const { state, history, timeframes, chart } = App;
    const scale = chart.chart.timeScale();
    const range = scale.getVisibleRange();
    const previousSeconds = timeframes.seconds();
    const oldSource = timeframes.isMinute() ? "minute" : "primary";
    const source = seconds === 60 ? "minute" : "primary";
    const caches = new Map(this.caches);
    if (history.windows.length) caches.set(oldSource, {
      windows: history.windows, active: history.activeWindow
    });
    timeframes.switching = true;
    timeframes.selected = seconds === state.configuredTimeframeSec ? null : seconds;
    App.measure?.setActive(false);
    App.trendline?.setActive(false);
    App.ui.closeManualAlertMenu?.();
    chart.chart.clearCrosshairPosition?.();
    chart.resetChartState();
    this.caches = caches;
    const generation = state.loadGeneration;
    const current = () => id === timeframes.selectionId && generation === state.loadGeneration;
    App.ui.setChartInfo();
    try {
      const cached = caches.get(source);
      if (cached) {
        history.windows = cached.windows;
        history.selectWindow(cached.active || cached.windows.at(-1));
        const { combined, bars, gaps } = history.retainedData(history.windows);
        history.renderedWindow = combined;
        history.plots = combined.plots;
        history.trades = combined.trades;
        history.plotchars = combined.plotchars;
        history.renderData(bars, gaps);
        state.initialLoadDone = true;
      }
      // Refresh both sources on return; cached past pages remain available.
      const query = !cached && range ? { before: Math.ceil(range.to + previousSeconds) } : {};
      if (!await history.requestWindow(query, cached ? "refresh" : "replace", !cached) || !current()) return;
      state.initialLoadDone = true;
      if (timeframes.isHigher()) {
        const anchor = timeframes.bucketTime(range?.to ?? state.lastBarTime);
        await timeframes.ensureHigherHistory(anchor, id, generation);
        if (!current()) return;
        timeframes.fitHigherRange(range);
      } else if (range) {
        const from = Math.max(state.firstBarTime, range.from);
        const to = Math.min(state.lastBarTime + seconds * 3, range.to + previousSeconds - seconds);
        if (to > from) scale.setVisibleRange({ from, to });
        else chart.applyInitialVisibleRange(App.collections.ohlcvData.length);
      } else chart.applyInitialVisibleRange(App.collections.ohlcvData.length);
      App.ui.setChartInfo(chart.formatOhlcvText(state.lastOhlcv));
      App.trendline?.refresh();
      App.ui.applyManualAlertTriggerState?.(state.manualAlertTriggers || []);
      await history.settleChart();
    } finally {
      if (id === timeframes.selectionId) {
        timeframes.switching = false;
        history.lastRange = scale.getVisibleLogicalRange();
      }
      if (current()) { this.connect(); this.flush(); }
    }
  },
  init() {
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.close();
      else this.connect();
    });
    window.addEventListener("pagehide", () => this.close());
    window.addEventListener("pageshow", () => this.connect());
  }
};
