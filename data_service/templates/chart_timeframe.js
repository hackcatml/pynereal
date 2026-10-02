var App = window.App || (window.App = {});

// Display-only aggregation. The session timeframe and raw history stay intact.
App.timeframes = {
  selected: null,
  switching: false,
  selectionId: 0,
  initialCandleCount: 50,
  groups: new Map(),
  incomplete: new Set(),
  choices: ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"],

  seconds() { return this.selected || App.state.configuredTimeframeSec || 60; },
  isHigher() { return this.selected != null && this.seconds() > App.state.configuredTimeframeSec; },
  options() {
    const base = App.state.configuredTimeframeSec;
    if (!base || !App.state.timeframe) return [];
    return [{ label: App.state.timeframe, seconds: base }, ...this.choices.map(label => ({
      label, seconds: App.data.timeframeToSeconds(label)
    })).filter(option => option.seconds > base && option.seconds % base === 0)];
  },
  label() { return this.options().find(option => option.seconds === this.seconds())?.label || App.state.timeframe; },
  bucketTime(time) {
    if (!this.isHigher()) return time;
    const interval = this.seconds();
    // Weekly candles start on Monday, while intraday/daily candles use UTC.
    const offset = interval === 604800 ? 345600 : 0;
    return Math.floor((time - offset) / interval) * interval + offset;
  },
  reset() {
    this.groups.clear();
    this.incomplete.clear();
  },
  summarize(time) {
    if (this.incomplete.has(time)) return { time };
    const points = this.groups.get(time);
    let first = null, last = null, high = -Infinity, low = Infinity, volume = 0;
    for (const point of points.values()) {
      // Whitespace denotes unread data, not an empty trading interval.
      if (point.close == null) return { time };
      if (!first || point.time < first.time) first = point;
      if (!last || point.time > last.time) last = point;
      high = Math.max(high, point.high);
      low = Math.min(low, point.low);
      volume += Number(point.volume) || 0;
    }
    return first ? { time, open: first.open, high, low, close: last.close, volume } : { time };
  },
  project(bars, windows) {
    this.reset();
    if (!this.isHigher()) return bars;
    const base = App.state.configuredTimeframeSec;
    for (const window of windows) {
      const first = this.bucketTime(window.start), last = this.bucketTime(window.end);
      if (window.hasBefore && window.start > first) this.incomplete.add(first);
      if (window.hasAfter && window.end + base < last + this.seconds()) this.incomplete.add(last);
    }
    for (const point of bars) {
      const time = this.bucketTime(point.time);
      if (!this.groups.has(time)) this.groups.set(time, new Map());
      this.groups.get(time).set(point.time, point);
    }
    return [...this.groups.keys()].map(time => this.summarize(time));
  },
  handleLive(msg) {
    if (!this.isHigher()) return false;
    if (["plot_data", "plotchar", "trade_entry", "trade_close"].includes(msg.type)) return true;
    if (msg.type !== "bar" && msg.type !== "last_bar_open_fix") return false;
    const raw = App.history.renderedWindow;
    const sourceTime = Number(msg.data?.time);
    const point = raw?.bars.get(sourceTime);
    if (!point || (msg.type === "bar" && sourceTime < raw.end)) return true;
    const time = this.bucketTime(sourceTime);
    if (!this.groups.has(time)) this.groups.set(time, new Map());
    this.groups.get(time).set(sourceTime, point);
    const bar = this.summarize(time);
    if (bar.close == null) return true;
    const { chart, state } = App;
    const historical = time < state.lastBarTime;
    chart.candleSeries.update(bar, historical);
    chart.volumeSeries.update({
      time, value: bar.volume, color: bar.close >= bar.open ? "#26a69a" : "#ef5350"
    }, historical);
    App.data.upsertOhlcvCache(bar);
    if (!historical) {
      state.lastBarTime = time;
      state.lastOhlcv = bar;
      state.lastPrice = bar.close;
    }
    state.lastOpenPrice = { ...raw.lastOpenPrice };
    return true;
  },
  fitHigherRange(range) {
    const bars = App.collections.ohlcvData;
    if (!bars.length) return;
    const anchor = this.bucketTime(range?.to ?? bars.at(-1).time);
    let end = bars.findLastIndex(bar => bar.time <= anchor && Number.isFinite(bar.close));
    if (end < 0) end = bars.findIndex(bar => Number.isFinite(bar.close));
    if (end < 0) return;
    let start = end;
    // Fit actual candles around the viewed time, without spanning unread gaps.
    while (start > 0 && end - start + 1 < this.initialCandleCount && Number.isFinite(bars[start - 1].close)) start--;
    while (end + 1 < bars.length && end - start + 1 < this.initialCandleCount && Number.isFinite(bars[end + 1].close)) end++;
    const padding = end === bars.length - 1 ? Math.max(1, (end - start + 1) * 0.08) : 0;
    App.chart.chart.timeScale().setVisibleLogicalRange({ from: start, to: end + padding });
  },
  async ensureHigherHistory(anchor, id, generation) {
    const history = App.history;
    while (id === this.selectionId && generation === App.state.loadGeneration && this.isHigher()) {
      if (history.loading) {
        await history.loadComplete;
        continue;
      }
      const window = history.windows.findLast(item => this.bucketTime(item.start) <= anchor) || history.windows[0];
      if (!window) return;
      const first = this.bucketTime(window.start), last = this.bucketTime(window.end);
      const candles = App.collections.ohlcvData.filter(bar =>
        bar.time >= first && bar.time <= last && Number.isFinite(bar.close));
      const beforeCount = candles.filter(bar => bar.time <= anchor).length;
      let direction;
      // Finish an unread right boundary before treating its candle as usable.
      if (last <= anchor && this.incomplete.has(last) && window.hasAfter) direction = "after";
      else if (beforeCount < this.initialCandleCount && window.hasBefore) direction = "before";
      else if (beforeCount < this.initialCandleCount && candles.length < this.initialCandleCount && window.hasAfter) direction = "after";
      else return;
      const previous = { start: window.start, end: window.end, hasBefore: window.hasBefore, hasAfter: window.hasAfter };
      history.selectWindow(window);
      const query = direction === "before" ? { before: window.start } : { after: window.end };
      if (!await history.requestWindow(query, direction)) return;
      if (id !== this.selectionId || generation !== App.state.loadGeneration) return;
      const next = history.activeWindow;
      if (!next || Object.keys(previous).every(key => next[key] === previous[key])) return;
    }
  },
  async select(seconds) {
    const option = this.options().find(item => item.seconds === seconds);
    if (!option) return;
    const id = ++this.selectionId;
    if (seconds === this.seconds() && !this.switching) return;
    const generation = App.state.loadGeneration;
    while (App.history.loading) {
      await App.history.loadComplete;
      if (id !== this.selectionId || generation !== App.state.loadGeneration) return;
    }
    if (!App.state.initialLoadDone || !App.history.windows.length) return;
    const scale = App.chart.chart.timeScale();
    const range = scale.getVisibleRange();
    const previousSeconds = this.seconds();
    this.switching = true;
    this.selected = seconds === App.state.configuredTimeframeSec ? null : seconds;
    try {
      App.measure?.setActive(false);
      App.measure?.clear();
      App.trendline?.setActive(false);
      App.ui.closeManualAlertMenu?.();
      App.chart.chart.clearCrosshairPosition?.();
      const { bars, gaps } = App.history.retainedData(App.history.windows);
      App.history.renderData(bars, gaps);
      App.ui.setChartInfo(App.chart.formatOhlcvText(App.state.lastOhlcv));
      await App.history.settleChart();
      if (id !== this.selectionId || generation !== App.state.loadGeneration) return;
      if (this.isHigher()) {
        this.fitHigherRange(range);
        const anchor = this.bucketTime(range?.to ?? App.state.lastBarTime);
        await this.ensureHigherHistory(anchor, id, generation);
        if (id !== this.selectionId || generation !== App.state.loadGeneration) return;
        this.fitHigherRange(range);
        App.ui.setChartInfo(App.chart.formatOhlcvText(App.state.lastOhlcv));
      } else if (range) {
        const from = this.bucketTime(range.from);
        const to = this.bucketTime(range.to + previousSeconds - seconds);
        scale.setVisibleRange({ from, to: Math.max(from + seconds, to) });
      }
      App.trendline?.refresh();
      App.ui.applyManualAlertTriggerState?.(App.state.manualAlertTriggers || []);
      await App.history.settleChart();
    } finally {
      if (id === this.selectionId) {
        this.switching = false;
        App.history.lastRange = scale.getVisibleLogicalRange();
      }
    }
  },
  closeMenu(focus = false) {
    if (!this.menu) return;
    this.menu.classList.add("hidden");
    const button = document.getElementById("chart-timeframe-toggle");
    button?.setAttribute("aria-expanded", "false");
    if (focus) button?.focus({ preventScroll: true });
  },
  positionMenu() {
    const button = document.getElementById("chart-timeframe-toggle");
    if (!button || !this.menu) return;
    const rect = button.getBoundingClientRect();
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
    this.menu.style.maxHeight = `${Math.max(60, height - 24)}px`;
    const bounds = this.menu.getBoundingClientRect();
    this.menu.style.left = `${Math.max(left + 12, Math.min(rect.left, left + width - bounds.width - 12))}px`;
    this.menu.style.top = `${Math.max(top + 12, Math.min(rect.bottom + 6, top + height - bounds.height - 12))}px`;
  },
  toggleMenu() {
    if (!this.menu.classList.contains("hidden")) { this.closeMenu(); return; }
    App.sessionPicker?.close();
    App.indicators?.close();
    const options = this.options();
    if (options.length < 2) return;
    this.menu.replaceChildren(...options.map(option => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = option.label;
      button.dataset.seconds = option.seconds;
      button.setAttribute("role", "menuitemradio");
      button.setAttribute("aria-checked", String(option.seconds === this.seconds()));
      return button;
    }));
    this.menu.classList.remove("hidden");
    document.getElementById("chart-timeframe-toggle")?.setAttribute("aria-expanded", "true");
    this.positionMenu();
    this.menu.querySelector('[aria-checked="true"]')?.focus({ preventScroll: true });
  },
  init() {
    this.menu = document.getElementById("chart-timeframe-menu");
    if (!this.menu) return;
    App.ui.elements.chartInfoBase.addEventListener("click", event => {
      if (!event.target.closest("#chart-timeframe-toggle")) return;
      event.stopPropagation();
      this.toggleMenu();
    });
    this.menu.addEventListener("click", event => {
      const button = event.target.closest("button[data-seconds]");
      if (!button) return;
      this.closeMenu(true);
      void this.select(Number(button.dataset.seconds)).catch(error => console.error("Chart timeframe change failed:", error));
    });
    this.menu.addEventListener("dblclick", event => event.preventDefault());
    document.addEventListener("pointerdown", event => {
      if (!this.menu.contains(event.target) && !event.target.closest("#chart-timeframe-toggle")) this.closeMenu();
    }, true);
    document.addEventListener("keydown", event => {
      if (this.menu.classList.contains("hidden")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        this.closeMenu(true);
      } else if (["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const buttons = [...this.menu.querySelectorAll("button")];
        let index = buttons.indexOf(document.activeElement);
        if (event.key === "Home") index = 0;
        else if (event.key === "End") index = buttons.length - 1;
        else index = (index + (event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
        buttons[index]?.focus();
      } else if (event.key === "Tab") this.closeMenu();
    });
    window.addEventListener("resize", () => this.closeMenu());
    window.visualViewport?.addEventListener("resize", () => this.closeMenu());
  }
};
