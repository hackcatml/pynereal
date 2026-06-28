var App = window.App || (window.App = {});

App.chart = {
  chart: null,
  container: document.getElementById("chart"),
  candleSeries: null,
  volumeSeries: null,
  entryMarkerSeries: null,
  closeMarkerSeries: null,
  currentPriceLine: null,
  manualAlertPriceLine: null,
  resizeObserver: null,
  touchGuardsAttached: false,
  manualAlertLastTap: null,
  isMobileViewport() {
    return window.matchMedia("(max-width: 640px), (hover: none) and (pointer: coarse)").matches;
  },
  isPageZoomed() {
    return Boolean(window.visualViewport && window.visualViewport.scale && window.visualViewport.scale > 1.01);
  },
  isChartGestureTarget(target) {
    if (!(target instanceof Element)) return false;
    if (target.closest("#source-panel")) return false;
    return Boolean(target.closest("#chart, #chart-info, .nav-btn"));
  },
  candleTextColor(bar) {
    if (!bar) return "#d32f2f";
    return Number(bar.close) >= Number(bar.open) ? "#26a69a" : "#ef5350";
  },
  formatCandleChangePercent(bar) {
    if (!bar) return "";
    const open = Number(bar.open);
    const close = Number(bar.close);
    if (!Number.isFinite(open) || !Number.isFinite(close) || open === 0) return "";
    const pct = ((close - open) / open) * 100;
    if (!Number.isFinite(pct)) return "";
    const sign = pct >= 0 ? "+" : "";
    return ` (${sign}${pct.toFixed(2)}%)`;
  },
  formatOhlcvText(bar, volumeValue = null) {
    if (!bar) return "";
    const color = this.candleTextColor(bar);
    const volume = volumeValue == null ? bar.volume : volumeValue;
    const changeText = this.formatCandleChangePercent(bar);
    const value = (label, number) =>
      `${label} <span style="color:${color}">${App.ui.formatNumber(number, 2)}</span>`;
    return value("O", bar.open) +
      ` ${value("H", bar.high)}` +
      ` ${value("L", bar.low)}` +
      ` ${value("C", bar.close)}` +
      ` Vol <span style="color:${color}">${App.ui.formatNumber(volume, 2)}${changeText}</span>`;
  },
  setMagnetMode(enabled) {
    if (!this.chart) return;
    this.chart.applyOptions({
      crosshair: {
        mode: enabled ? LightweightCharts.CrosshairMode.Magnet : LightweightCharts.CrosshairMode.Normal
      }
    });
  },
  restoreMagnetMode() {
    this.setMagnetMode(true);
  },
  removeManualAlertPriceGuide() {
    if (this.manualAlertPriceLine && this.candleSeries && this.candleSeries.removePriceLine) {
      this.candleSeries.removePriceLine(this.manualAlertPriceLine);
    }
    this.manualAlertPriceLine = null;
  },
  updateManualAlertPriceGuide(price) {
    if (!this.candleSeries || !Number.isFinite(Number(price))) return;
    const dottedLineStyle = (LightweightCharts.LineStyle && LightweightCharts.LineStyle.Dotted != null)
      ? LightweightCharts.LineStyle.Dotted
      : 1;
    const options = {
      price: Number(price),
      color: "#111111",
      lineWidth: 1,
      lineStyle: dottedLineStyle,
      axisLabelVisible: true,
      title: "Alert"
    };
    if (this.manualAlertPriceLine) {
      this.manualAlertPriceLine.applyOptions(options);
    } else {
      this.manualAlertPriceLine = this.candleSeries.createPriceLine(options);
    }
  },
  priceFromClientY(clientY) {
    if (!this.candleSeries || !this.container) return null;
    const rect = this.container.getBoundingClientRect();
    const price = this.candleSeries.coordinateToPrice(clientY - rect.top);
    return Number.isFinite(Number(price)) ? Number(price) : null;
  },
  normalizeAlertTime(time) {
    if (typeof time === "number") return time;
    if (time && typeof time.timestamp === "number") return time.timestamp;
    return App.state.lastBarTime || null;
  },
  plotPointFromClient(clientX, clientY) {
    if (!this.chart || !this.candleSeries || !this.container) return null;
    const rect = this.container.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    if (x < 0 || x > rect.width || y < 0 || y > rect.height) return null;

    let priceScaleWidth = 0;
    try {
      priceScaleWidth = this.chart.priceScale("right").width() || 0;
    } catch {}
    // The right price scale and bottom time scale also live inside #chart, but
    // they are not candle area. Keep right-side chart whitespace clickable.
    const timeScaleHeight = 28;
    if (priceScaleWidth > 0 && x >= rect.width - priceScaleWidth) return null;
    if (y >= rect.height - timeScaleHeight) return null;

    const timeScale = this.chart.timeScale();
    const time = timeScale.coordinateToTime(x);
    let logical = null;
    try {
      logical = timeScale.coordinateToLogical(x);
    } catch {}
    const price = this.candleSeries.coordinateToPrice(y);
    if (!Number.isFinite(Number(price))) return null;
    return {
      x,
      y,
      price: Number(price),
      time,
      logical: Number.isFinite(Number(logical)) ? Number(logical) : null
    };
  },
  manualAlertContextFromPointer(pointer) {
    if (!pointer) return null;
    const point = this.plotPointFromClient(pointer.clientX, pointer.clientY);
    if (!point) return null;
    return {
      clientX: pointer.clientX,
      clientY: pointer.clientY,
      price: point.price,
      time: this.normalizeAlertTime(point.time)
    };
  },
  openManualAlertMenuFromPointer(pointer) {
    if (App.state.measureToolActive) return;
    if (App.state.sourcePanelOpen) return;
    if (!App.ui.elements.alertTemplateModal.classList.contains("hidden")) return;
    const context = this.manualAlertContextFromPointer(pointer);
    if (!context) return;
    App.ui.showManualAlertMenu(context);
    this.setMagnetMode(false);
  },
  attachManualAlertGesture() {
    this.container.addEventListener("dblclick", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      App.ui.closeManualAlertMenu();
      this.openManualAlertMenuFromPointer({ clientX: e.clientX, clientY: e.clientY });
    }, { passive: false });

    this.container.addEventListener("pointerup", (e) => {
      if (e.pointerType === "mouse") return;
      const now = performance.now();
      const previous = this.manualAlertLastTap;
      this.manualAlertLastTap = { time: now, clientX: e.clientX, clientY: e.clientY };
      if (!previous) return;
      const dt = now - previous.time;
      const distance = Math.hypot(e.clientX - previous.clientX, e.clientY - previous.clientY);
      if (dt > 360 || distance > 28) return;
      e.preventDefault();
      this.manualAlertLastTap = null;
      App.ui.closeManualAlertMenu();
      this.openManualAlertMenuFromPointer({ clientX: e.clientX, clientY: e.clientY });
    }, { passive: false });
  },
  clearPersistedChartView() {
    try {
      sessionStorage.removeItem(App.config.storageKey("chartVisibleRange"));
      sessionStorage.removeItem(App.config.storageKey("chartVisibleLogicalRange"));
      sessionStorage.removeItem(App.config.storageKey("chartScaleOptions"));
    } catch {}
  },
  getContainerSize() {
    const rect = this.container.getBoundingClientRect();
    return {
      width: Math.max(1, Math.round(rect.width || this.container.clientWidth || window.innerWidth || 1)),
      height: Math.max(1, Math.round(rect.height || this.container.clientHeight || window.innerHeight || 1))
    };
  },
  resizeToContainer() {
    if (!this.chart || !this.container) return;
    const size = this.getContainerSize();
    this.chart.applyOptions(size);
    this.positionNavButtons();
    if (App.measure) {
      App.measure.scheduleRender();
    }
  },
  applyInitialVisibleRange(dataLength) {
    const ts = this.chart.timeScale();
    if (!this.isMobileViewport() || dataLength <= 0) {
      ts.fitContent();
      return;
    }
    const width = Math.max(1, this.container.clientWidth || window.innerWidth || 1);
    const visibleBars = Math.min(dataLength, Math.max(140, Math.round(width / 2)));
    const rightPadding = Math.max(4, Math.round(visibleBars * 0.04));
    ts.setVisibleLogicalRange({
      from: Math.max(0, dataLength - visibleBars),
      to: dataLength - 1 + rightPadding
    });
  },
  init() {
    const size = this.getContainerSize();
    this.chart = LightweightCharts.createChart(this.container, {
      width: size.width,
      height: size.height,
      layout: { background: { color: "#ffffff" }, textColor: "#000000" },
      // grid: { vertLines: { color: "#2b2b43" }, horzLines: { color: "#2b2b43" } },
      rightPriceScale: {
        minimumWidth: 76
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        rightOffset: this.isMobileViewport() ? 4 : 10,
        barSpacing: this.isMobileViewport() ? 5 : 6,
        minBarSpacing: 0.5,
        rightBarStaysOnScroll: true
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Magnet
      }
    });

    this.candleSeries = this.chart.addSeries(
      LightweightCharts.CandlestickSeries,
      {
        upColor: "#26a69a",
        downColor: "#ef5350",
        borderUpColor: "#26a69a",
        borderDownColor: "#ef5350",
        wickUpColor: "#26a69a",
        wickDownColor: "#ef5350",
        lastValueVisible: false,
        priceLineVisible: false,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 }
      }
    );

    this.volumeSeries = this.chart.addSeries(
      LightweightCharts.HistogramSeries,
      {
        color: "#90caf9",
        priceFormat: { type: "volume" },
        lastValueVisible: true,
        priceLineVisible: true,
        priceScaleId: ""
      }
    );
    this.volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.78, bottom: 0.02 }
    });

    this.entryMarkerSeries = this.chart.addSeries(
      LightweightCharts.LineSeries,
      {
        color: "#0b33e8",
        lineVisible: false,
        pointMarkersVisible: true,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: true
      }
    );

    this.closeMarkerSeries = this.chart.addSeries(
      LightweightCharts.LineSeries,
      {
        color: "#9d0bec",
        lineVisible: false,
        pointMarkersVisible: true,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: true
      }
    );

    this.chart.subscribeCrosshairMove((param) => {
      const state = App.state;
      if (!param || !param.time) {
        if (!state.lastOhlcv) {
          App.ui.setChartInfo();
          return;
        }
        App.ui.setChartInfo(this.formatOhlcvText(state.lastOhlcv));
        return;
      }
      const bar = param.seriesData.get(this.candleSeries);
      const volBar = param.seriesData.get(this.volumeSeries);
      if (!bar) {
        App.ui.setChartInfo();
        return;
      }
      const volumeValue = volBar ? volBar.value : (state.lastOhlcv ? state.lastOhlcv.volume : null);
      App.ui.setChartInfo(this.formatOhlcvText(bar, volumeValue));
    });
    this.attachManualAlertGesture();
    this.attachTouchGuards();
  },
  resetChartState(resetCandles = true) {
    const state = App.state;
    const collections = App.collections;
    state.initialLoadDone = false;
    state.initialLoadInProgress = false;
    if (resetCandles) {
      this.candleSeries.setData([]);
      this.volumeSeries.setData([]);
    }
    this.entryMarkerSeries.setData([]);
    this.closeMarkerSeries.setData([]);
    if (collections.seriesMarkers) {
      collections.seriesMarkers.setMarkers([]);
    }
    if (collections.plotcharSeriesMarkers) {
      collections.plotcharSeriesMarkers.setMarkers([]);
    }
    for (const series of collections.plotSeriesList) {
      this.chart.removeSeries(series);
    }
    collections.plotSeriesList.length = 0;
    collections.plotSeriesMap.clear();
    collections.markers.length = 0;
    collections.markerKeys.clear();
    collections.plotcharMarkers.length = 0;
    collections.plotcharMarkerKeys.clear();
    collections.ohlcvData.length = 0;
    collections.ohlcvIndexByTime.clear();
    collections.ohlcvVolumePrefix.length = 0;
    collections.entryMarkerData.length = 0;
    collections.closeMarkerData.length = 0;
    collections.entryPriceKeys.clear();
    collections.closePriceKeys.clear();
    collections.seriesMarkers = null;
    collections.plotcharSeriesMarkers = null;
    state.firstBarTime = null;
    state.timeframeInterval = state.configuredTimeframeSec || 60;
    state.lastBarTime = 0;
    state.lastOpenPrice = { time: 0, value: 0 };
    state.lastPrice = 0;
    if (this.currentPriceLine && this.candleSeries.removePriceLine) {
      this.candleSeries.removePriceLine(this.currentPriceLine);
    }
    this.currentPriceLine = null;
    this.removeManualAlertPriceGuide();
    if (App.measure) {
      App.measure.clear();
    }
  },
  updatePriceLineWithTimer() {
    const state = App.state;
    if (state.timeframeInterval === 0 || state.lastPrice === 0) return;

    const now = Date.now();
    const currentTime = Math.floor(now / 1000);
    const nextCandleTime = Math.ceil(currentTime / state.timeframeInterval) * state.timeframeInterval;
    const remainingSeconds = nextCandleTime - currentTime;
    const minutes = Math.floor(remainingSeconds / 60);
    const seconds = remainingSeconds % 60;
    const timeText = `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;

    if (this.currentPriceLine) {
      this.currentPriceLine.applyOptions({
        price: state.lastPrice,
        title: `${timeText}`
      });
    } else {
      this.currentPriceLine = this.candleSeries.createPriceLine({
        price: state.lastPrice,
        color: "#2196F3",
        lineWidth: 0,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `${state.lastPrice.toFixed(2)} | ${timeText}`
      });
    }
  },
  startPriceLineTimer() {
    this.updatePriceLineWithTimer();
    setInterval(() => this.updatePriceLineWithTimer(), 1000);
  },
  startJankMonitor() {
    const state = App.state;
    const MAX_JANK_RELOADS = 2;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        state.jankFrames = [];
        state.lastFrameTs = null;
      }
    });
    const monitorJank = (ts) => {
      if (document.visibilityState === "hidden") {
        state.lastFrameTs = ts;
        requestAnimationFrame(monitorJank);
        return;
      }
      if (state.lastFrameTs != null) {
        const delta = ts - state.lastFrameTs;
        state.jankFrames.push(delta);
        if (state.jankFrames.length > 120) {
          state.jankFrames.shift();
        }
        if (!state.jankReloaded && state.jankFrames.length === 120) {
          // 초기 로딩(봉 + 마커/플롯 등 플로팅)이 끝나기 전에는 reload 금지.
          // reload가 플로팅 로딩 도중에 발사되어 마커/플롯이 그려지지 않는 문제를 방지한다.
          if (!state.initialLoadDone) {
            state.jankFrames = [];
          } else {
            const avg = state.jankFrames.reduce((a, b) => a + b, 0) / state.jankFrames.length;
            if (avg >= 80) {
              let reloadCount = 0;
              try {
                reloadCount = parseInt(sessionStorage.getItem(App.config.storageKey("chartJankReloadCount")) || "0", 10) || 0;
              } catch {}
              if (reloadCount >= MAX_JANK_RELOADS) {
                // 무한 reload 루프 방지: 상한 도달 시 더 이상 reload 하지 않는다.
                state.jankReloaded = true;
              } else {
                state.jankReloaded = true;
                try {
                  sessionStorage.setItem(App.config.storageKey("chartJankReloadCount"), String(reloadCount + 1));
                  if (this.isPageZoomed()) {
                    this.clearPersistedChartView();
                  } else {
                    const range = this.chart.timeScale().getVisibleRange();
                    if (range) {
                      sessionStorage.setItem(App.config.storageKey("chartVisibleRange"), JSON.stringify(range));
                    }
                    const logicalRange = this.chart.timeScale().getVisibleLogicalRange();
                    if (logicalRange) {
                      sessionStorage.setItem(App.config.storageKey("chartVisibleLogicalRange"), JSON.stringify(logicalRange));
                    }
                    const scaleOptions = this.chart.timeScale().options();
                    sessionStorage.setItem(App.config.storageKey("chartScaleOptions"), JSON.stringify({
                      rightOffset: scaleOptions.rightOffset,
                      barSpacing: scaleOptions.barSpacing,
                      rightBarStaysOnScroll: scaleOptions.rightBarStaysOnScroll
                    }));
                  }
                } catch {}
                location.reload();
                return;
              }
            } else {
              // 정상 상태(jank 없음)가 확인되면 reload 카운트를 리셋해
              // 이후 다시 과부하가 생겨도 복구 reload가 가능하도록 한다.
              try {
                sessionStorage.removeItem(App.config.storageKey("chartJankReloadCount"));
              } catch {}
            }
          }
        }
      }
      state.lastFrameTs = ts;
      requestAnimationFrame(monitorJank);
    };
    requestAnimationFrame(monitorJank);
  },
  attachResizeHandler() {
    let resizeFrame = null;
    const scheduleResize = () => {
      if (resizeFrame !== null) return;
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = null;
        if (this.isPageZoomed()) return;
        this.resizeToContainer();
      });
    };

    window.addEventListener("resize", scheduleResize);
    window.addEventListener("orientationchange", scheduleResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", scheduleResize);
      window.visualViewport.addEventListener("scroll", scheduleResize);
    }
    if (window.ResizeObserver) {
      this.resizeObserver = new ResizeObserver(scheduleResize);
      this.resizeObserver.observe(this.container);
    }
    scheduleResize();
  },
  attachTouchGuards() {
    if (this.touchGuardsAttached) return;
    this.touchGuardsAttached = true;

    const preventBrowserPinch = (event) => {
      if (!this.isChartGestureTarget(event.target)) return;
      if (event.touches && event.touches.length < 2) return;
      event.preventDefault();
    };
    const preventGestureEvent = (event) => {
      if (!this.isChartGestureTarget(event.target)) return;
      event.preventDefault();
    };
    const options = { passive: false, capture: true };

    document.addEventListener("touchmove", preventBrowserPinch, options);
    document.addEventListener("gesturestart", preventGestureEvent, options);
    document.addEventListener("gesturechange", preventGestureEvent, options);
    document.addEventListener("gestureend", preventGestureEvent, options);
  },
  goToStart() {
    const ts = this.chart.timeScale();
    const lr = ts.getVisibleLogicalRange();
    // 현재 줌(보이는 봉 개수)을 유지한 채 첫 봉(logical index 0)을 약간의 여백을 두고 보여준다.
    const span = lr ? Math.max(1, lr.to - lr.from) : 100;
    const margin = Math.max(2, Math.round(span * 0.08));
    ts.setVisibleLogicalRange({ from: -margin, to: span - margin });
  },
  goToEnd() {
    const ts = this.chart.timeScale();
    const lr = ts.getVisibleLogicalRange();
    // 현재 줌을 유지한 채 최신 봉을 오른쪽 여백을 두고 확정 이동한다.
    // scrollToPosition(position, animated=false): position은 오른쪽 끝에서 마지막 봉까지의 여백(봉 수).
    const span = lr ? Math.max(1, lr.to - lr.from) : 100;
    const margin = Math.max(2, Math.round(span * 0.08));
    ts.scrollToPosition(margin, false);
  },
  positionNavButtons() {
    const startBtn = document.getElementById("nav-to-start");
    const endBtn = document.getElementById("nav-to-end");
    if (!startBtn || !endBtn) return;
    if (this.isMobileViewport()) return;
    const container = this.container;

    // 우측 버튼(»): 가격 축 너비만큼 왼쪽으로 이동시켜 축과 겹치지 않게 한다.
    try {
      const priceScaleWidth = this.chart.priceScale("right").width();
      if (priceScaleWidth > 0) {
        endBtn.style.right = (priceScaleWidth + 12) + "px";
      }
    } catch {}

    // 좌측 버튼(«): 트뷰(TradingView) 로고 마크 바로 위에 오도록 bottom을 보정한다.
    // 우측 버튼(»)도 같은 bottom을 적용해 두 버튼의 y축 위치를 맞춘다.
    const logo = container.querySelector('#tv-attr-logo, a[href*="tradingview"]');
    if (logo) {
      const cRect = container.getBoundingClientRect();
      const lRect = logo.getBoundingClientRect();
      // 로고 상단보다 8px 위에 버튼 하단이 오도록 한다.
      const bottom = (cRect.bottom - lRect.top) + 8;
      startBtn.style.bottom = bottom + "px";
      endBtn.style.bottom = bottom + "px";
    }
  },
  attachNavButtons() {
    const startBtn = document.getElementById("nav-to-start");
    const endBtn = document.getElementById("nav-to-end");
    if (!startBtn || !endBtn) return;
    if (this.isMobileViewport()) {
      startBtn.classList.add("visible");
      endBtn.classList.add("visible");
      startBtn.addEventListener("click", () => this.goToStart());
      endBtn.addEventListener("click", () => this.goToEnd());
      return;
    }
    const container = this.container;
    const CORNER_W = 160;
    const CORNER_H = 110;

    const updateVisibility = (e) => {
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const inChart = x >= 0 && x <= rect.width && y >= 0 && y <= rect.height;
      const inBottom = inChart && y >= rect.height - CORNER_H;
      const showStart = inBottom && x <= CORNER_W;
      const showEnd = inBottom && x >= rect.width - CORNER_W;
      // 버튼을 보여주기 직전에 가격 축/로고 위치에 맞춰 좌표를 보정한다.
      if (showStart || showEnd) this.positionNavButtons();
      startBtn.classList.toggle("visible", showStart);
      endBtn.classList.toggle("visible", showEnd);
    };

    document.addEventListener("mousemove", updateVisibility);
    document.addEventListener("mouseleave", () => {
      startBtn.classList.remove("visible");
      endBtn.classList.remove("visible");
    });

    startBtn.addEventListener("click", () => this.goToStart());
    endBtn.addEventListener("click", () => this.goToEnd());
  }
};

App.chart.init();
