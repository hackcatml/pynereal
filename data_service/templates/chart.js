var App = window.App || (window.App = {});

App.chart = {
  chart: null,
  container: document.getElementById("chart"),
  candleSeries: null,
  volumeSeries: null,
  entryMarkerSeries: null,
  closeMarkerSeries: null,
  currentPriceLine: null,
  init() {
    this.chart = LightweightCharts.createChart(this.container, {
      layout: { background: { color: "#ffffff" }, textColor: "#000000" },
      // grid: { vertLines: { color: "#2b2b43" }, horzLines: { color: "#2b2b43" } },
      timeScale: { timeVisible: true, secondsVisible: true },
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
        priceLineVisible: false
      }
    );

    this.volumeSeries = this.chart.addSeries(
      LightweightCharts.HistogramSeries,
      {
        color: "#90caf9",
        priceFormat: { type: "volume" },
        priceScaleId: ""
      }
    );
    this.volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 }
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
        const ohlcvText = `O <span style="color:#d32f2f">${App.ui.formatNumber(state.lastOhlcv.open, 1)}</span>` +
          ` H <span style="color:#d32f2f">${App.ui.formatNumber(state.lastOhlcv.high, 1)}</span>` +
          ` L <span style="color:#d32f2f">${App.ui.formatNumber(state.lastOhlcv.low, 1)}</span>` +
          ` C <span style="color:#d32f2f">${App.ui.formatNumber(state.lastOhlcv.close, 1)}</span>` +
          ` Vol <span style="color:#d32f2f">${App.ui.formatNumber(state.lastOhlcv.volume, 2)}</span>`;
        App.ui.setChartInfo(ohlcvText);
        return;
      }
      const bar = param.seriesData.get(this.candleSeries);
      const volBar = param.seriesData.get(this.volumeSeries);
      if (!bar) {
        App.ui.setChartInfo();
        return;
      }
      const volumeValue = volBar ? volBar.value : (state.lastOhlcv ? state.lastOhlcv.volume : null);
      const ohlcvText = `O <span style="color:#d32f2f">${App.ui.formatNumber(bar.open, 1)}</span>` +
        ` H <span style="color:#d32f2f">${App.ui.formatNumber(bar.high, 1)}</span>` +
        ` L <span style="color:#d32f2f">${App.ui.formatNumber(bar.low, 1)}</span>` +
        ` C <span style="color:#d32f2f">${App.ui.formatNumber(bar.close, 1)}</span>` +
        ` Vol <span style="color:#d32f2f">${App.ui.formatNumber(volumeValue, 2)}</span>`;
      App.ui.setChartInfo(ohlcvText);
    });
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
    collections.entryMarkerData.length = 0;
    collections.closeMarkerData.length = 0;
    collections.entryPriceKeys.clear();
    collections.closePriceKeys.clear();
    collections.seriesMarkers = null;
    collections.plotcharSeriesMarkers = null;
    state.firstBarTime = null;
    state.timeframeInterval = 60;
    state.lastBarTime = 0;
    state.lastOpenPrice = { time: 0, value: 0 };
    state.lastPrice = 0;
    if (this.currentPriceLine && this.candleSeries.removePriceLine) {
      this.candleSeries.removePriceLine(this.currentPriceLine);
    }
    this.currentPriceLine = null;
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
        if (state.jankFrames.length > 60) {
          state.jankFrames.shift();
        }
        if (!state.jankReloaded && state.jankFrames.length === 60) {
          const avg = state.jankFrames.reduce((a, b) => a + b, 0) / state.jankFrames.length;
          if (avg >= 60) {
            state.jankReloaded = true;
            try {
              const range = this.chart.timeScale().getVisibleRange();
              if (range) {
                sessionStorage.setItem("chartVisibleRange", JSON.stringify(range));
              }
              const logicalRange = this.chart.timeScale().getVisibleLogicalRange();
              if (logicalRange) {
                sessionStorage.setItem("chartVisibleLogicalRange", JSON.stringify(logicalRange));
              }
              const scaleOptions = this.chart.timeScale().options();
              sessionStorage.setItem("chartScaleOptions", JSON.stringify({
                rightOffset: scaleOptions.rightOffset,
                barSpacing: scaleOptions.barSpacing,
                rightBarStaysOnScroll: scaleOptions.rightBarStaysOnScroll
              }));
            } catch {}
            location.reload();
            return;
          }
        }
      }
      state.lastFrameTs = ts;
      requestAnimationFrame(monitorJank);
    };
    requestAnimationFrame(monitorJank);
  },
  attachResizeHandler() {
    window.addEventListener("resize", () => {
      this.chart.applyOptions({
        width: this.container.clientWidth,
        height: this.container.clientHeight
      });
    });
  }
};

App.chart.init();
