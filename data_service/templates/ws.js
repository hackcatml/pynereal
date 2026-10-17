var App = window.App || (window.App = {});

App.ws = {
  reconnectTimer: null,
  connectTimer: null,
  probeTimer: null,
  probeId: 0,
  chartRevision: null,
  syncNeeded: false,
  syncTask: null,
  keepaliveTimer: null,
  wasHidden: false,
  lastResumeAt: 0,
  clearProbe() {
    clearTimeout(this.probeTimer);
    this.probeTimer = null;
  },
  disconnect(socket, delay = 1000) {
    if (socket !== App.state.ws) return;
    clearTimeout(this.connectTimer);
    this.clearProbe();
    socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
    App.state.ws = null;
    try { socket.close(); } catch {}
    // Keep the rendered chart; only an explicit data/script reset invalidates it.
    App.history.suspend();
    this.syncTask = null;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (!document.hidden) {
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, delay);
    }
  },
  connect() {
    const state = App.state;
    if (document.hidden || state.ws) return;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${wsProtocol}//${location.host}${App.config.wsPath}`);
    state.ws = socket;
    this.connectTimer = setTimeout(() => this.disconnect(socket), 8000);

    socket.onopen = () => {
      if (state.ws !== socket) return;
      clearTimeout(this.connectTimer);
      App.history.suspend();
      socket.send(JSON.stringify({ type: "client_hello", role: "chart" }));
      this.syncNeeded = true;
      this.probe();
      App.volumeProfile?.onReconnect();
    };

    socket.onmessage = (ev) => {
      if (state.ws !== socket) return;
      try {
        this.handleMessage(JSON.parse(ev.data));
      } catch (e) {
        console.error("ws parse error", e);
      }
    };

    socket.onclose = socket.onerror = () => this.disconnect(socket);
  },
  probe() {
    const socket = App.state.ws;
    if (document.hidden || !socket || socket.readyState !== WebSocket.OPEN || this.probeTimer) return;
    const id = ++this.probeId;
    this.probeTimer = setTimeout(() => this.disconnect(socket, 0), 3000);
    try { socket.send(JSON.stringify({ type: "chart_ping", id })); }
    catch { this.disconnect(socket, 0); }
  },
  resume() {
    if (document.hidden || Date.now() - this.lastResumeAt < 500) return;
    this.lastResumeAt = Date.now();
    App.history.suspend();
    this.syncTask = null;
    this.syncNeeded = true;
    const socket = App.state.ws;
    if (socket && socket.readyState === WebSocket.OPEN) {
      this.clearProbe();
      this.probe();
    } else {
      if (socket) this.disconnect(socket, 0);
      this.connect();
    }
  },
  handleMessage(msg) {
    if (msg.type === "bar" && msg.data) App.volumeProfile?.onBar(msg.data);
    const state = App.state;
    const chart = App.chart;
    const collections = App.collections;
    if (msg.type === "chart_pong" && (msg.id !== this.probeId || !this.probeTimer)) return;
    if (msg.chart_revision) {
      if (this.chartRevision && this.chartRevision !== msg.chart_revision) {
        chart.resetChartState(false);
        this.syncTask = null;
        this.syncNeeded = true;
      }
      this.chartRevision = msg.chart_revision;
    }
    if (msg.type === "chart_pong") {
      this.clearProbe();
      state.runnerConnected = Boolean(msg.runner_connected);
      state.runnerPhase = msg.phase;
      state.nextPrerunAt = msg.next_prerun_at;
      App.ui.updateRunnerStatus();
      if (!this.syncTask && (this.syncNeeded || !state.initialLoadDone)) {
        this.syncNeeded = false;
        const task = App.history.refreshRecent();
        this.syncTask = task;
        void task.then(ok => {
          if (this.syncTask === task && !ok) this.syncNeeded = true;
        }).catch(error => {
          if (this.syncTask === task) this.syncNeeded = true;
          console.error("Chart resync failed:", error);
        }).finally(() => { if (this.syncTask === task) this.syncTask = null; });
      }
      return;
    }
    if (App.timeframes?.isMinute() && ["bar", "last_bar_open_fix", "plot_data", "plotchar", "trade_entry", "trade_close"].includes(msg.type)) return;
    if (App.history.captureLive(msg)) return;
    try {
        if (App.timeframes?.handleLive(msg)) return;
        if (msg.type === "script_modified") {
          state.sourceSaveStatus = "";
          if (state.sourcePanelOpen) {
            App.ui.updateSourceSaveState();
          }
          if (!App.timeframes?.isMinute()) {
            chart.resetChartState(false);
            App.data.loadInitialWithRetry();
          } else App.minuteChart.caches.delete("primary");
        } else if (msg.type === "chart_reset") {
          // data window changed (history_since edit): drop stale series and
          // reload the freshly regenerated ohlcv/plot/markers
          chart.resetChartState(false);
          App.data.loadInitialWithRetry();
        } else if (msg.type === "runner_disconnected") {
          state.runnerConnected = false;
          state.runnerPhase = "stopped";
          state.nextPrerunAt = null;
          if (!App.timeframes?.isMinute()) chart.resetChartState(false);
          else App.minuteChart.caches.delete("primary");
          App.ui.setChartInfo();
        } else if (msg.type === "runner_connected") {
          state.runnerConnected = true;
          if (state.runnerPhase === "stopped") state.runnerPhase = "running";
          App.ui.updateRunnerStatus();
          App.data.loadInitialWithRetry();
        } else if (msg.type === "runner_phase") {
          state.runnerPhase = msg.phase || (state.runnerConnected ? "running" : "stopped");
          state.nextPrerunAt = Number.isFinite(Number(msg.next_prerun_at))
            ? Number(msg.next_prerun_at)
            : null;
          App.ui.updateRunnerStatus();
        } else if (msg.type === "script_info") {
          state.scriptTitle = msg.title || "No title";
          state.scriptTitleVisible = true;
          state.scriptSourceName = msg.source_name || state.scriptSourceName || "";
          if (msg.source != null) {
            state.scriptSource = msg.source || "";
            state.scriptSourceLoaded = true;
          }
          if (state.sourcePanelOpen) {
            App.ui.renderSourcePanel();
          }
          App.ui.setChartInfo();
        } else if (msg.type === "bar") {
          if (msg.data.time < state.lastBarTime) {
            return;
          }

          if (msg.data.time === state.lastOpenPrice.time && state.lastOpenPrice.value > 0 &&
            msg.data.open !== parseFloat(state.lastOpenPrice.value.toFixed(2))) {
            msg.data.open = state.lastOpenPrice.value;
          }

          chart.candleSeries.update(msg.data);
          chart.volumeSeries.update({
            time: msg.data.time,
            value: msg.data.volume,
            color: msg.data.close >= msg.data.open ? "#26a69a" : "#ef5350"
          });
          App.data.upsertOhlcvCache(msg.data);
          state.lastBarTime = msg.data.time;
          state.lastOhlcv = msg.data;

          if (msg.data && msg.data.close !== undefined) {
            state.lastPrice = msg.data.close;
          }
        } else if (msg.type === "manual_alert_trigger") {
          if (App.ui && App.ui.applyManualAlertTriggerState) {
            App.ui.applyManualAlertTriggerState(msg.triggers || []);
          }
        } else if (msg.type === "manual_alert_trigger_fired") {
          if (App.ui && App.ui.applyManualAlertTriggerState) {
            App.ui.applyManualAlertTriggerState(msg.triggers || App.state.manualAlertTriggers || []);
          }
          if (App.ui && App.ui.elements && App.ui.elements.manualAlertStatus) {
            App.ui.elements.manualAlertStatus.textContent = "Triggered";
            App.ui.elements.manualAlertStatus.classList.remove("error");
          }
        } else if (msg.type === "manual_alert_trigger_error") {
          if (App.ui && App.ui.elements && App.ui.elements.manualAlertStatus) {
            App.ui.elements.manualAlertStatus.textContent = msg.error ? `Trigger failed: ${msg.error}` : "Trigger failed";
            App.ui.elements.manualAlertStatus.classList.add("error");
          }
        } else if (msg.type === "last_bar_open_fix") {
          if (!msg.data || msg.data.time == null || msg.data.open == null) {
            return;
          }

          state.lastOpenPrice.time = msg.data.time;
          state.lastOpenPrice.value = msg.data.open;
          // Only the opening price is corrected; keep live high/low/close/volume intact.
          if (state.lastOhlcv && state.lastOhlcv.time === msg.data.time) {
            const fixedBar = { ...state.lastOhlcv, open: msg.data.open };
            chart.candleSeries.update(fixedBar);
            App.data.upsertOhlcvCache(fixedBar);
            state.lastOhlcv = fixedBar;
          }
          state.lastBarTime = Math.max(state.lastBarTime, msg.data.time);

          const entryIndex = collections.entryMarkerData.findIndex(m => m.time === msg.data.time);
          if (entryIndex !== -1) {
            const priceDiff = Math.abs(collections.entryMarkerData[entryIndex].value - msg.data.open);
            if (priceDiff > 0.01) {
              console.log(`Fix entry marker: ${collections.entryMarkerData[entryIndex].value} --> ${msg.data.open}`);
              collections.entryMarkerData[entryIndex].value = msg.data.open;
              chart.entryMarkerSeries.setData(collections.entryMarkerData);
            }
          }

          const closeIndex = collections.closeMarkerData.findIndex(m => m.time === msg.data.time);
          if (closeIndex !== -1) {
            const priceDiff = Math.abs(collections.closeMarkerData[closeIndex].value - msg.data.open);
            if (priceDiff > 0.01) {
              console.log(`Fix close marker: ${collections.closeMarkerData[closeIndex].value} --> ${msg.data.open}`);
              collections.closeMarkerData[closeIndex].value = msg.data.open;
              chart.closeMarkerSeries.setData(collections.closeMarkerData);
            }
          }
        } else if (msg.type === "trade_entry") {
          if (state.firstBarTime !== null && msg.time < state.firstBarTime) {
            return;
          }

          const markerKey = `entry_${msg.time}_${msg.id}`;
          if (collections.markerKeys.has(markerKey)) {
            return;
          }

          collections.markers.push({
            time: msg.time,
            position: "belowBar",
            color: "#0b33e8",
            shape: "arrowUp",
            text: msg.comment || "",
            size: 0.5
          });
          collections.markerKeys.add(markerKey);

          if (msg.price != null && Number.isFinite(Number(msg.price)) && Number.isFinite(Number(msg.time))) {
            const priceKey = `entry_${msg.time}_${msg.id}`;
            if (!collections.entryPriceKeys.has(priceKey)) {
              collections.entryMarkerData.push({ time: Number(msg.time), value: Number(msg.price) });
              collections.entryPriceKeys.add(priceKey);
              collections.entryMarkerData = App.data.normalizePriceMarkerData(collections.entryMarkerData);
              chart.entryMarkerSeries.setData(collections.entryMarkerData);
            }
          }

          if (collections.seriesMarkers) {
            collections.seriesMarkers.setMarkers([]);
          }
          collections.seriesMarkers = LightweightCharts.createSeriesMarkers(chart.candleSeries, collections.markers);
        } else if (msg.type === "trade_close") {
          if (state.firstBarTime !== null && msg.time < state.firstBarTime) {
            return;
          }

          const closeOrderId = msg.exit_id || msg.id;
          const markerKey = `close_${msg.time}_${closeOrderId}`;
          if (collections.markerKeys.has(markerKey)) {
            return;
          }

          collections.markers.push({
            time: msg.time,
            position: "aboveBar",
            color: "#9d0bec",
            shape: "arrowDown",
            text: msg.comment || "",
            size: 0.5
          });
          collections.markerKeys.add(markerKey);

          if (msg.price != null && Number.isFinite(Number(msg.price)) && Number.isFinite(Number(msg.time))) {
            const priceKey = `close_${msg.time}_${closeOrderId}`;
            if (!collections.closePriceKeys.has(priceKey)) {
              collections.closeMarkerData.push({ time: Number(msg.time), value: Number(msg.price) });
              collections.closePriceKeys.add(priceKey);
              collections.closeMarkerData = App.data.normalizePriceMarkerData(collections.closeMarkerData);
              chart.closeMarkerSeries.setData(collections.closeMarkerData);
            }
          }

          if (collections.seriesMarkers) {
            collections.seriesMarkers.setMarkers([]);
          }
          collections.seriesMarkers = LightweightCharts.createSeriesMarkers(chart.candleSeries, collections.markers);
        } else if (msg.type === "plotchar") {
          if (state.firstBarTime !== null && msg.time < state.firstBarTime) {
            return;
          }

          const markerKey = `plotchar_${msg.time}_${msg.title}`;
          if (collections.plotcharMarkerKeys.has(markerKey)) {
            return;
          }

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

          if (collections.plotcharSeriesMarkers) {
            collections.plotcharSeriesMarkers.setMarkers([]);
          }
          collections.plotcharSeriesMarkers = LightweightCharts.createSeriesMarkers(
            chart.candleSeries,
            collections.plotcharMarkers
          );
        } else if (msg.type === "plot_data") {
          const { title, time, value } = msg;
          App.data.updatePlotSeries(chart, collections, title, time, value);
        }
    } catch (error) {
      console.error("ws message error", error);
    }
  },
  startKeepalive() {
    if (this.keepaliveTimer) return;
    this.wasHidden = document.hidden;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        this.wasHidden = true;
        this.clearProbe();
        App.history.suspend();
      } else if (this.wasHidden) {
        this.wasHidden = false;
        this.resume();
      }
    });
    window.addEventListener("pageshow", event => { if (event.persisted) this.resume(); });
    window.addEventListener("online", () => this.resume());
    this.keepaliveTimer = setInterval(() => {
      if (document.hidden) return;
      if (App.state.ws) this.probe();
      else if (!this.reconnectTimer) this.connect();
    }, 15000);
  }
};
