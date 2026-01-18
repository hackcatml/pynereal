var App = window.App || (window.App = {});

App.data = {
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
            if (plot.data && Array.isArray(plot.data)) {
              plot.data.forEach(point => {
                if (point.value == null) {
                  point.value = NaN;
                }
              });
            }

            const { title, color, linewidth, style, data } = plot;
            const STYLE_CIRCLES = 2;
            const STYLE_CROSS = 4;
            const isCrossStyle = (parseInt(style) === STYLE_CROSS || parseInt(style) === STYLE_CIRCLES);
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

            const series = chart.chart.addSeries(LightweightCharts.LineSeries, seriesOptions);
            collections.plotSeriesList.push(series);
            collections.plotSeriesMap.set(title, series);
            pendingSeriesData.push([series, data]);
          });

          pendingSeriesData.forEach(([series, data]) => {
            if (data && data.length > 0) {
              series.setData(data);
            }
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
          chart.chart.timeScale().fitContent();
          const savedRange = sessionStorage.getItem("chartVisibleRange");
          const savedLogicalRange = sessionStorage.getItem("chartVisibleLogicalRange");
          const savedScale = sessionStorage.getItem("chartScaleOptions");
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
          if (data.length >= 2) {
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
  async loadChartInfo() {
    const state = App.state;
    try {
      const resp = await fetch("/api/info");
      const info = await resp.json();
      const exchange = (info.exchange || "Unknown").toUpperCase();
      const symbol = info.symbol || "Unknown";
      const timeframe = info.timeframe || "Unknown";
      if (info.script_title) {
        state.scriptTitle = info.script_title || "No title";
        state.scriptTitleVisible = true;
      } else {
        state.scriptTitleVisible = false;
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
