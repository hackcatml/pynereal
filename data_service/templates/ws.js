var App = window.App || (window.App = {});

App.ws = {
  connect() {
    const state = App.state;
    const chart = App.chart;
    const collections = App.collections;

    state.ws = new WebSocket(`ws://${location.host}/ws`);

    state.ws.onopen = () => {
      console.log("ws connected");
      App.data.loadInitialWithRetry();
    };

    state.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "script_modified") {
          chart.resetChartState(false);
          App.data.loadInitialWithRetry();
        } else if (msg.type === "runner_disconnected") {
          state.runnerConnected = false;
          chart.resetChartState(false);
          state.scriptTitle = "No title";
          state.scriptTitleVisible = false;
          App.ui.setChartInfo();
        } else if (msg.type === "runner_connected") {
          state.runnerConnected = true;
          App.data.loadInitialWithRetry();
        } else if (msg.type === "script_info") {
          state.scriptTitle = msg.title || "No title";
          state.scriptTitleVisible = true;
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
          state.lastBarTime = msg.data.time;
          state.lastOhlcv = msg.data;

          if (msg.data && msg.data.close !== undefined) {
            state.lastPrice = msg.data.close;
          }
        } else if (msg.type === "last_bar_open_fix") {
          state.lastOpenPrice.time = msg.data.time;
          state.lastOpenPrice.value = msg.data.open;

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
              collections.entryMarkerData = collections.entryMarkerData.filter(m =>
                Number.isFinite(Number(m.time)) && Number.isFinite(Number(m.value))
              );
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

          const markerKey = `close_${msg.time}_${msg.id}`;
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
            const priceKey = `close_${msg.time}_${msg.id}`;
            if (!collections.closePriceKeys.has(priceKey)) {
              collections.closeMarkerData.push({ time: Number(msg.time), value: Number(msg.price) });
              collections.closePriceKeys.add(priceKey);
              collections.closeMarkerData = collections.closeMarkerData.filter(m =>
                Number.isFinite(Number(m.time)) && Number.isFinite(Number(m.value))
              );
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
          const series = collections.plotSeriesMap.get(title);
          if (series) {
            const plotValue = (value == null || isNaN(value)) ? NaN : value;
            series.update({ time, value: plotValue });
          }
        }
      } catch (e) {
        console.error("ws parse error", e);
      }
    };

    state.ws.onclose = () => {
      chart.resetChartState(false);
      state.ws = null;
      setTimeout(() => this.connect(), 1000);
    };
    state.ws.onerror = () => {
      chart.resetChartState(false);
      try { state.ws.close(); } catch {}
    };
  },
  startKeepalive() {
    setInterval(() => {
      if (App.state.ws && App.state.ws.readyState === WebSocket.OPEN) {
        App.state.ws.send("ping");
      }
    }, 15000);
  }
};
