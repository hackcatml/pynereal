(() => {
  "use strict";

  const jobId = String(window.BACKTEST_JOB_ID || "");
  const STYLE_CIRCLES = 2;
  const STYLE_CROSS = 4;
  const STYLE_LINEBR = 7;
  const state = {
    job: null,
    equity: null,
    levels: [],
    viewStart: 0,
    viewEnd: 1,
    hoverIndex: -1,
    selectedIndex: -1,
    pointer: null,
    priceChart: null,
    candleSeries: null,
    volumeSeries: null,
    entryMarkerSeries: null,
    closeMarkerSeries: null,
    markerApi: null,
    plotDefinitions: [],
    plotSeries: new Map(),
    bgcolorPrimitive: null,
    priceBars: [],
    priceMarkers: [],
    priceRowCount: 0,
    priceStart: -1,
    priceEnd: -1,
    priceRequest: 0,
    priceSelectionRequest: 0,
    pricePaging: false,
    priceWindowReplacing: false,
    maxDrawdownIndex: -1,
    drawdownIndices: [],
    paneResizePointer: null,
  };

  const el = (id) => document.getElementById(id);
  const canvas = el("backtest-equity-canvas");
  const tooltip = el("backtest-equity-tooltip");

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function showError(message) {
    const node = el("backtest-chart-error");
    node.textContent = String(message || "Backtest chart could not be loaded.");
    node.classList.remove("hidden");
  }

  async function json(url) {
    const response = await fetch(url, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function nextAnimationFrame() {
    return new Promise((resolve) => requestAnimationFrame(resolve));
  }

  function formatNumber(value, digits = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "-";
    return new Intl.NumberFormat("en-US", {
      maximumFractionDigits: digits,
      minimumFractionDigits: 0,
    }).format(number);
  }

  function formatAmount(value) {
    const currency = String(state.job && state.job.summary && state.job.summary.currency || "");
    const text = formatNumber(value, 2);
    return currency && text !== "-" ? `${text} ${currency}` : text;
  }

  function formatPercent(value) {
    const text = formatNumber(value, 2);
    return text === "-" ? text : `${text}%`;
  }

  function formatOhlcvMarkup(bar, volumeValue = null) {
    if (!bar) return "";
    const open = Number(bar.open);
    const close = Number(bar.close);
    const tone = close >= open ? "positive" : "negative";
    const volume = volumeValue == null ? bar.volume : volumeValue;
    const change = Number.isFinite(open) && Number.isFinite(close) && open !== 0
      ? ((close - open) / open) * 100
      : null;
    const value = (label, number) => `<span class="backtest-ohlcv-item">${label} <span class="backtest-ohlcv-value ${tone}">${formatNumber(number, 2)}</span></span>`;
    const changeText = Number.isFinite(change)
      ? ` (${change >= 0 ? "+" : ""}${change.toFixed(2)}%)`
      : "";
    return value("O", bar.open)
      + ` ${value("H", bar.high)}`
      + ` ${value("L", bar.low)}`
      + ` ${value("C", bar.close)}`
      + ` <span class="backtest-ohlcv-item">Vol <span class="backtest-ohlcv-value ${tone}">${formatNumber(volume, 2)}${changeText}</span></span>`;
  }

  function formatTimestamp(timestamp) {
    return new Date(Number(timestamp) * 1000).toLocaleString("en-US", {
      timeZone: "UTC",
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }) + " UTC";
  }

  function formatRangeTimestamp(timestamp) {
    const date = new Date(Number(timestamp) * 1000);
    if (!Number.isFinite(date.getTime())) return "-";
    const part = (value) => String(value).padStart(2, "0");
    return `${date.getUTCFullYear()}-${part(date.getUTCMonth() + 1)}-${part(date.getUTCDate())}`
      + ` ${part(date.getUTCHours())}:${part(date.getUTCMinutes())}`;
  }

  function formatListTimestamp(timestamp) {
    const date = new Date(Number(timestamp) * 1000);
    if (!Number.isFinite(date.getTime())) return "-";
    const part = (value) => String(value).padStart(2, "0");
    return `${date.getUTCFullYear()}-${part(date.getUTCMonth() + 1)}-${part(date.getUTCDate())}`
      + ` ${part(date.getUTCHours())}:${part(date.getUTCMinutes())} UTC`;
  }

  function inferredDataInfo(path) {
    const stem = String(path || "").split(/[\\/]/).pop().replace(/\.ohlcv$/i, "");
    const parts = stem.split("_");
    if (parts.length < 5 || parts[0].toLowerCase() !== "ccxt") return {};
    let timeframeIndex = -1;
    for (let index = parts.length - 1; index >= 4; index -= 1) {
      const value = parts[index];
      if (/^\d+[DWM]$/i.test(value) || (/^\d+$/.test(value) && Number(value) <= 1440)) {
        timeframeIndex = index;
        break;
      }
    }
    if (timeframeIndex < 0) return {};
    const market = parts.slice(2, timeframeIndex);
    if (market.length < 2) return {};
    const derivative = market.length >= 3;
    const symbol = derivative
      ? `${market.slice(0, -2).join("_")}/${market.at(-2)}:${market.at(-1)}`
      : `${market.slice(0, -1).join("_")}/${market.at(-1)}`;
    const period = parts[timeframeIndex];
    let timeframe = period;
    if (/^\d+$/.test(period)) {
      const minutes = Number(period);
      timeframe = minutes >= 60 && minutes % 60 === 0 ? `${minutes / 60}h` : `${minutes}m`;
    } else if (/^\d+[DW]$/i.test(period)) {
      timeframe = period.toLowerCase();
    }
    return { exchange: parts[1].toUpperCase(), symbol, timeframe };
  }

  function renderChartIdentity(job) {
    const supplied = job && typeof job.data_info === "object" ? job.data_info : {};
    const inferred = inferredDataInfo(job && job.data_path);
    const info = { ...inferred, ...supplied };
    el("backtest-price-symbol").textContent = info.symbol || "Unknown";
    el("backtest-price-timeframe").textContent = info.timeframe || "Unknown";
    el("backtest-price-exchange").textContent = String(info.exchange || "Unknown").toUpperCase();
    const from = Number(job.actual_time_from || job.time_from);
    const to = Number(job.actual_time_to || job.time_to);
    const validRange = Number.isFinite(from) && Number.isFinite(to);
    const fromText = validRange ? formatRangeTimestamp(from) : "";
    const toText = validRange ? formatRangeTimestamp(to) : "";
    el("backtest-chart-range-inline").textContent = validRange
      ? `${fromText} - ${toText} UTC`
      : "";
    el("backtest-chart-from").textContent = fromText ? `${fromText} UTC` : "";
    el("backtest-chart-to").textContent = toText ? `${toText} UTC` : "";
  }

  function formatAxisNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "-";
    const absolute = Math.abs(number);
    if (absolute >= 1e9) return `${(number / 1e9).toFixed(1)}B`;
    if (absolute >= 1e6) return `${(number / 1e6).toFixed(1)}M`;
    if (absolute >= 1e3) return `${(number / 1e3).toFixed(1)}K`;
    if (absolute >= 10) return number.toFixed(0);
    return number.toFixed(2);
  }

  function createBuilder() {
    let capacity = 65536;
    let length = 0;
    let barIndices = new Float64Array(capacity);
    let timestamps = new Float64Array(capacity);
    let equities = new Float64Array(capacity);
    let drawdowns = new Float64Array(capacity);
    let drawdownPercents = new Float32Array(capacity);

    function grow() {
      capacity *= 2;
      const nextBarIndices = new Float64Array(capacity);
      const nextTimestamps = new Float64Array(capacity);
      const nextEquities = new Float64Array(capacity);
      const nextDrawdowns = new Float64Array(capacity);
      const nextDrawdownPercents = new Float32Array(capacity);
      nextBarIndices.set(barIndices);
      nextTimestamps.set(timestamps);
      nextEquities.set(equities);
      nextDrawdowns.set(drawdowns);
      nextDrawdownPercents.set(drawdownPercents);
      barIndices = nextBarIndices;
      timestamps = nextTimestamps;
      equities = nextEquities;
      drawdowns = nextDrawdowns;
      drawdownPercents = nextDrawdownPercents;
    }

    return {
      append(parts) {
        if (parts.length < 5) return;
        const values = parts.slice(0, 5).map(Number);
        if (!values.every(Number.isFinite)) return;
        if (length >= capacity) grow();
        barIndices[length] = values[0];
        timestamps[length] = values[1];
        equities[length] = values[2];
        drawdowns[length] = values[3];
        drawdownPercents[length] = values[4];
        length += 1;
      },
      finish() {
        return {
          length,
          barIndices: barIndices.slice(0, length),
          timestamps: timestamps.slice(0, length),
          equities: equities.slice(0, length),
          drawdowns: drawdowns.slice(0, length),
          drawdownPercents: drawdownPercents.slice(0, length),
        };
      },
    };
  }

  async function loadEquityCsv() {
    const response = await fetch(`/api/scripting/backtests/${encodeURIComponent(jobId)}/equity.csv`, {
      cache: "no-store",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    const builder = createBuilder();
    const decoder = new TextDecoder();
    let pending = "";
    let headerRead = false;
    const processText = (text, final = false) => {
      pending += text;
      const lines = pending.split(/\r?\n/);
      pending = final ? "" : lines.pop() || "";
      lines.forEach((line) => {
        if (!headerRead) {
          headerRead = true;
          return;
        }
        if (line) builder.append(line.split(","));
      });
      if (final && pending) builder.append(pending.split(","));
    };
    if (response.body && response.body.getReader) {
      const reader = response.body.getReader();
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        processText(decoder.decode(result.value, { stream: true }));
      }
      processText(decoder.decode(), true);
    } else {
      processText(await response.text(), true);
    }
    const equity = builder.finish();
    if (!equity.length) throw new Error("Equity curve is empty.");
    return equity;
  }

  function buildLevels(equity) {
    const levels = [];
    for (let blockSize = 4; blockSize < equity.length; blockSize *= 4) {
      const count = Math.ceil(equity.length / blockSize);
      const minimum = new Int32Array(count);
      const maximum = new Int32Array(count);
      const drawdown = new Int32Array(count);
      for (let bucket = 0; bucket < count; bucket += 1) {
        const start = bucket * blockSize;
        const end = Math.min(equity.length, start + blockSize);
        let minIndex = start;
        let maxIndex = start;
        let drawdownIndex = start;
        for (let index = start + 1; index < end; index += 1) {
          if (equity.equities[index] < equity.equities[minIndex]) minIndex = index;
          if (equity.equities[index] > equity.equities[maxIndex]) maxIndex = index;
          if (equity.drawdowns[index] > equity.drawdowns[drawdownIndex]) drawdownIndex = index;
        }
        minimum[bucket] = minIndex;
        maximum[bucket] = maxIndex;
        drawdown[bucket] = drawdownIndex;
      }
      levels.push({ blockSize, minimum, maximum, drawdown });
      if (count <= 2) break;
    }
    return levels;
  }

  function chartLayout(width, height) {
    return {
      left: 8,
      right: 70,
      top: 12,
      bottom: 24,
      width: Math.max(1, width - 78),
      height: Math.max(1, height - 36),
    };
  }

  function visibleIndices(start, end, width) {
    const equity = state.equity;
    const span = Math.max(1, end - start + 1);
    const pointsPerPixel = span / Math.max(1, width);
    let level = null;
    state.levels.forEach((candidate) => {
      if (candidate.blockSize <= pointsPerPixel * 1.5) level = candidate;
    });
    if (!level) {
      const indices = [];
      for (let index = start; index <= end; index += 1) indices.push(index);
      return indices;
    }
    const indices = [start, end];
    const firstBucket = Math.floor(start / level.blockSize);
    const lastBucket = Math.floor(end / level.blockSize);
    for (let bucket = firstBucket; bucket <= lastBucket; bucket += 1) {
      const candidates = [level.minimum[bucket], level.maximum[bucket], level.drawdown[bucket]];
      candidates.forEach((index) => {
        if (index >= start && index <= end) indices.push(index);
      });
    }
    indices.sort((a, b) => a - b);
    return indices.filter((value, index) => index === 0 || value !== indices[index - 1]);
  }

  function drawEquity() {
    const equity = state.equity;
    if (!equity || !equity.length) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 20 || rect.height < 20) return;
    const width = Math.round(rect.width);
    const height = Math.round(rect.height);
    const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const context = canvas.getContext("2d");
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);
    const layout = chartLayout(width, height);
    const start = Math.max(0, Math.floor(state.viewStart));
    const end = Math.min(equity.length - 1, Math.ceil(state.viewEnd));
    const indices = visibleIndices(start, end, layout.width);
    let minimum = Infinity;
    let maximum = -Infinity;
    let maxDrawdown = 0;
    indices.forEach((index) => {
      minimum = Math.min(minimum, equity.equities[index]);
      maximum = Math.max(maximum, equity.equities[index]);
      maxDrawdown = Math.max(maxDrawdown, equity.drawdowns[index]);
    });
    const padding = Math.max((maximum - minimum) * 0.08, Math.abs(maximum || 1) * 0.002);
    minimum -= padding;
    maximum += padding;
    const valueSpan = Math.max(1e-12, maximum - minimum);
    const xForIndex = (index) => layout.left
      + ((index - state.viewStart) / Math.max(1, state.viewEnd - state.viewStart)) * layout.width;
    const yForValue = (value) => layout.top
      + ((maximum - value) / valueSpan) * layout.height;

    context.strokeStyle = "#e2e8f0";
    context.fillStyle = "#64748b";
    context.lineWidth = 1;
    context.font = "10px system-ui, -apple-system, sans-serif";
    context.textBaseline = "middle";
    context.textAlign = "left";
    for (let line = 0; line <= 4; line += 1) {
      const ratio = line / 4;
      const y = layout.top + ratio * layout.height;
      context.beginPath();
      context.moveTo(layout.left, Math.round(y) + 0.5);
      context.lineTo(layout.left + layout.width, Math.round(y) + 0.5);
      context.stroke();
      context.fillText(formatAxisNumber(maximum - ratio * valueSpan), layout.left + layout.width + 6, y);
    }

    context.beginPath();
    indices.forEach((index, order) => {
      const x = xForIndex(index);
      const y = yForValue(equity.equities[index]);
      if (order === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.strokeStyle = "#2563eb";
    context.lineWidth = 1.5;
    context.stroke();

    if (maxDrawdown > 0) {
      context.beginPath();
      indices.forEach((index, order) => {
        const x = xForIndex(index);
        const y = layout.top + layout.height
          - (equity.drawdowns[index] / maxDrawdown) * Math.min(46, layout.height * 0.22);
        if (order === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.strokeStyle = "#dc2626";
      context.lineWidth = 1;
      context.stroke();
    }

    [start, Math.round((start + end) / 2), end].forEach((index, order) => {
      const x = xForIndex(index);
      const date = new Date(equity.timestamps[index] * 1000);
      const label = `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
      context.fillStyle = "#64748b";
      context.textBaseline = "bottom";
      context.textAlign = order === 0 ? "left" : order === 2 ? "right" : "center";
      context.fillText(label, x, height - 2);
    });

    const selected = state.hoverIndex >= 0 ? state.hoverIndex : state.selectedIndex;
    if (selected >= start && selected <= end) {
      const x = xForIndex(selected);
      const y = yForValue(equity.equities[selected]);
      context.beginPath();
      context.moveTo(Math.round(x) + 0.5, layout.top);
      context.lineTo(Math.round(x) + 0.5, layout.top + layout.height);
      context.strokeStyle = "rgb(71 85 105 / 60%)";
      context.lineWidth = 1;
      context.stroke();
      context.beginPath();
      context.arc(x, y, 3.5, 0, Math.PI * 2);
      context.fillStyle = "#ffffff";
      context.fill();
      context.strokeStyle = "#2563eb";
      context.lineWidth = 1.5;
      context.stroke();
    }
  }

  function equityIndexAt(clientX) {
    const rect = canvas.getBoundingClientRect();
    const layout = chartLayout(rect.width, rect.height);
    const x = Math.max(layout.left, Math.min(layout.left + layout.width, clientX - rect.left));
    const ratio = (x - layout.left) / layout.width;
    return Math.max(0, Math.min(
      state.equity.length - 1,
      Math.round(state.viewStart + ratio * (state.viewEnd - state.viewStart)),
    ));
  }

  function showEquityTooltip(index, clientX, clientY) {
    const equity = state.equity;
    tooltip.innerHTML = `<strong>${escapeHtml(formatTimestamp(equity.timestamps[index]))}</strong>`
      + `<span>Equity ${escapeHtml(formatAmount(equity.equities[index]))}</span>`
      + `<span>Drawdown ${escapeHtml(formatAmount(equity.drawdowns[index]))} · ${escapeHtml(formatNumber(equity.drawdownPercents[index], 2))}%</span>`;
    tooltip.classList.remove("hidden");
    const rect = canvas.getBoundingClientRect();
    const left = Math.max(8, Math.min(rect.width - tooltip.offsetWidth - 8, clientX - rect.left + 12));
    const top = Math.max(8, Math.min(rect.height - tooltip.offsetHeight - 8, clientY - rect.top - 12));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  function fitEquity() {
    if (!state.equity) return;
    state.viewStart = 0;
    state.viewEnd = Math.max(1, state.equity.length - 1);
    drawEquity();
  }

  function zoomEquity(factor, anchorRatio = 0.5) {
    if (!state.equity) return;
    const full = Math.max(1, state.equity.length - 1);
    const span = Math.max(1, state.viewEnd - state.viewStart);
    const nextSpan = Math.max(10, Math.min(full, span * factor));
    const anchor = state.viewStart + span * anchorRatio;
    let start = anchor - nextSpan * anchorRatio;
    start = Math.max(0, Math.min(full - nextSpan, start));
    state.viewStart = start;
    state.viewEnd = start + nextSpan;
    drawEquity();
  }

  function focusEquityIndex(index) {
    if (!state.equity || index < 0 || index >= state.equity.length) return;
    const full = Math.max(1, state.equity.length - 1);
    const span = Math.max(1, Math.min(full, state.viewEnd - state.viewStart));
    if (span < full) {
      const start = Math.max(0, Math.min(full - span, index - span / 2));
      state.viewStart = start;
      state.viewEnd = start + span;
    }
    void loadPriceAt(index);
  }

  function collectDrawdownIndices(equity) {
    const indices = [];
    let deepestIndex = -1;
    for (let index = 0; index < equity.length; index += 1) {
      const drawdown = equity.drawdowns[index];
      if (drawdown > 0) {
        if (deepestIndex < 0 || drawdown > equity.drawdowns[deepestIndex]) {
          deepestIndex = index;
        }
        continue;
      }
      if (deepestIndex >= 0) indices.push(deepestIndex);
      deepestIndex = -1;
    }
    if (deepestIndex >= 0) indices.push(deepestIndex);
    return indices.sort((left, right) => (
      equity.drawdownPercents[right] - equity.drawdownPercents[left]
      || equity.drawdowns[right] - equity.drawdowns[left]
      || equity.timestamps[right] - equity.timestamps[left]
    ));
  }

  function renderDrawdownList() {
    const list = el("backtest-drawdown-list");
    const empty = el("backtest-drawdown-empty");
    const fragment = document.createDocumentFragment();
    state.drawdownIndices.forEach((index) => {
      const button = document.createElement("button");
      button.className = "backtest-drawdown-item";
      button.type = "button";
      button.dataset.equityIndex = String(index);
      const time = document.createElement("span");
      time.textContent = formatListTimestamp(state.equity.timestamps[index]);
      const value = document.createElement("span");
      value.className = "backtest-drawdown-item-value";
      const amount = document.createElement("strong");
      amount.textContent = formatAmount(state.equity.drawdowns[index]);
      const percent = document.createElement("span");
      percent.className = "backtest-drawdown-item-percent";
      percent.textContent = formatPercent(state.equity.drawdownPercents[index]);
      value.append(amount, percent);
      button.append(time, value);
      fragment.append(button);
    });
    list.replaceChildren(fragment);
    empty.classList.toggle("hidden", state.drawdownIndices.length > 0);
  }

  function closeDrawdownList() {
    el("backtest-drawdown-popover").classList.add("hidden");
    el("backtest-drawdown-list-toggle").setAttribute("aria-expanded", "false");
  }

  function positionDrawdownList() {
    const toggle = el("backtest-drawdown-list-toggle");
    const popover = el("backtest-drawdown-popover");
    const list = el("backtest-drawdown-list");
    if (popover.classList.contains("hidden")) return;
    list.style.maxHeight = "280px";
    const toggleRect = toggle.getBoundingClientRect();
    const popoverRect = popover.getBoundingClientRect();
    const margin = 8;
    const gap = 5;
    const roomBelow = window.innerHeight - toggleRect.bottom - gap - margin;
    const roomAbove = toggleRect.top - gap - margin;
    const openAbove = roomBelow < Math.min(140, popoverRect.height) && roomAbove > roomBelow;
    const available = Math.max(56, openAbove ? roomAbove : roomBelow);
    list.style.maxHeight = `${Math.min(280, available - 8)}px`;
    const width = popover.offsetWidth;
    popover.style.left = `${Math.max(margin, Math.min(
      window.innerWidth - width - margin,
      toggleRect.right - width,
    ))}px`;
    popover.style.top = openAbove ? "auto" : `${toggleRect.bottom + gap}px`;
    popover.style.bottom = openAbove ? `${window.innerHeight - toggleRect.top + gap}px` : "auto";
  }

  function toggleDrawdownList() {
    const popover = el("backtest-drawdown-popover");
    const opening = popover.classList.contains("hidden");
    if (!opening) {
      closeDrawdownList();
      return;
    }
    popover.classList.remove("hidden");
    el("backtest-drawdown-list-toggle").setAttribute("aria-expanded", "true");
    positionDrawdownList();
  }

  function initPriceChart() {
    const container = el("backtest-price-chart");
    state.priceChart = LightweightCharts.createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: { background: { color: "#ffffff" }, textColor: "#111827" },
      rightPriceScale: { minimumWidth: 76 },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 10,
        barSpacing: 6,
        minBarSpacing: 0.5,
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
    });
    state.candleSeries = state.priceChart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderUpColor: "#26a69a",
      borderDownColor: "#ef5350",
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    state.volumeSeries = state.priceChart.addSeries(LightweightCharts.HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    state.volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0.02 } });
    state.entryMarkerSeries = state.priceChart.addSeries(LightweightCharts.LineSeries, {
      color: "#0b33e8",
      lineVisible: false,
      pointMarkersVisible: true,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: true,
    });
    state.closeMarkerSeries = state.priceChart.addSeries(LightweightCharts.LineSeries, {
      color: "#9d0bec",
      lineVisible: false,
      pointMarkersVisible: true,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: true,
    });
    state.markerApi = LightweightCharts.createSeriesMarkers(state.candleSeries, []);
    if (window.App && window.App.BgColorPanePrimitive) {
      window.App.collections = window.App.collections || {};
      window.App.collections.ohlcvIndexByTime = new Map();
      state.bgcolorPrimitive = new window.App.BgColorPanePrimitive(
        state.priceChart,
        window.App.collections,
      );
      state.priceChart.panes()[0].attachPrimitive(state.bgcolorPrimitive);
    }
    state.priceChart.subscribeCrosshairMove((param) => {
      if (!param || !param.time) return;
      const bar = param.seriesData.get(state.candleSeries);
      const volume = param.seriesData.get(state.volumeSeries);
      if (!bar) return;
      el("backtest-price-ohlcv").innerHTML = formatOhlcvMarkup(
        bar,
        volume && volume.value,
      );
    });
    state.priceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (
        !range
        || state.pricePaging
        || state.priceWindowReplacing
        || !state.priceBars.length
      ) return;
      const preloadThreshold = 100;
      if (range.from < preloadThreshold && state.priceStart > 0) {
        void extendPriceWindow("before");
      } else if (
        range.to > state.priceBars.length - 1 - preloadThreshold
        && state.priceEnd < state.priceRowCount - 1
      ) {
        void extendPriceWindow("after");
      }
    });
    const observer = new ResizeObserver(() => {
      state.priceChart.resize(container.clientWidth, container.clientHeight);
      drawEquity();
    });
    observer.observe(container);
  }

  function positionPriceNavButtons() {
    if (!state.priceChart) return;
    const startButton = el("backtest-nav-to-start");
    const endButton = el("backtest-nav-to-end");
    if (window.matchMedia("(max-width: 720px)").matches) {
      startButton.classList.add("visible");
      endButton.classList.add("visible");
      endButton.style.right = "";
      return;
    }
    startButton.classList.remove("visible");
    endButton.classList.remove("visible");
    try {
      const width = state.priceChart.priceScale("right").width();
      if (width > 0) endButton.style.right = `${width + 12}px`;
    } catch {}
  }

  function bindPriceNavigation() {
    const pane = document.querySelector(".backtest-price-pane");
    const startButton = el("backtest-nav-to-start");
    const endButton = el("backtest-nav-to-end");
    startButton.addEventListener("click", () => {
      if (state.equity && state.equity.length) void loadPriceAt(0);
    });
    endButton.addEventListener("click", () => {
      if (state.equity && state.equity.length) void loadPriceAt(state.equity.length - 1);
    });
    if (window.matchMedia("(max-width: 720px)").matches) {
      startButton.classList.add("visible");
      endButton.classList.add("visible");
      return;
    }
    pane.addEventListener("pointermove", (event) => {
      const rect = pane.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const nearBottom = y >= rect.height - 110;
      startButton.classList.toggle("visible", nearBottom && x <= 160);
      endButton.classList.toggle("visible", nearBottom && x >= rect.width - 160);
    });
    pane.addEventListener("pointerleave", () => {
      startButton.classList.remove("visible");
      endButton.classList.remove("visible");
    });
    positionPriceNavButtons();
  }

  function renderPriceMarkers() {
    const entryPoints = new Map();
    const closePoints = new Map();
    state.priceMarkers.forEach((marker) => {
      const time = Number(marker.time);
      const price = Number(marker.price);
      if (!Number.isFinite(time) || !Number.isFinite(price)) return;
      if (marker.kind === "entry") entryPoints.set(time, { time, value: price });
      if (marker.kind === "exit") closePoints.set(time, { time, value: price });
    });
    state.entryMarkerSeries.setData([...entryPoints.values()].sort((a, b) => a.time - b.time));
    state.closeMarkerSeries.setData([...closePoints.values()].sort((a, b) => a.time - b.time));
    state.markerApi.setMarkers(state.priceMarkers.map((marker) => ({
      time: marker.time,
      position: marker.position,
      color: marker.color,
      shape: marker.shape,
      text: marker.text,
      size: marker.size,
    })).sort((a, b) => a.time - b.time));
  }

  function normalizedPriceMarkers(markers) {
    const unique = new Map();
    (Array.isArray(markers) ? markers : []).forEach((marker) => {
      const isNormalized = marker && marker.position && marker.shape;
      const isPlotchar = marker && marker.kind === "plotchar";
      const location = String(marker && marker.location || "belowBar");
      const normalized = isNormalized ? { ...marker } : {
        time: Number(marker.time),
        kind: String(marker.kind || ""),
        price: marker.price == null || marker.price === "" ? null : Number(marker.price),
        position: isPlotchar
          ? (location === "aboveBar" ? "aboveBar" : location === "absolute" ? "inBar" : "belowBar")
          : marker.kind === "entry" ? "belowBar" : "aboveBar",
        color: isPlotchar
          ? String(marker.color || "#2962FF")
          : marker.kind === "entry" ? "#0b33e8" : "#9d0bec",
        shape: isPlotchar ? "circle" : marker.kind === "entry" ? "arrowUp" : "arrowDown",
        text: String(marker.signal || ""),
        size: isPlotchar ? Number(marker.size) || 1 : 0.5,
      };
      const key = `${normalized.time}|${normalized.position}|${normalized.text}`;
      if (!unique.has(key)) unique.set(key, normalized);
    });
    return [...unique.values()].sort((a, b) => a.time - b.time);
  }

  function plotSeriesOptions(plot) {
    const style = Number.parseInt(plot.style, 10);
    const pointStyle = style === STYLE_CIRCLES || style === STYLE_CROSS;
    return {
      color: plot.color || "#2962FF",
      lineWidth: Math.max(1, Math.min(4, Number(plot.linewidth) || 1)),
      lineVisible: !pointStyle,
      pointMarkersVisible: pointStyle,
      pointMarkersRadius: 1.5,
      crosshairMarkerVisible: true,
      lastValueVisible: false,
      priceLineVisible: false,
    };
  }

  function removePlotController(controller) {
    if (!controller || !state.priceChart) return;
    if (controller.type === "linebr") {
      controller.series.forEach((series) => state.priceChart.removeSeries(series));
      return;
    }
    if (controller.type === "single" && controller.series) {
      state.priceChart.removeSeries(controller.series);
    }
  }

  function clearPlotSeries() {
    state.plotSeries.forEach(removePlotController);
    state.plotSeries.clear();
  }

  function setLineBreakPlotData(title, options, points) {
    removePlotController(state.plotSeries.get(title));
    const seriesList = [];
    let segment = [];

    const flushSegment = () => {
      if (!segment.length) return;
      const series = state.priceChart.addSeries(LightweightCharts.LineSeries, options);
      series.setData(segment);
      seriesList.push(series);
      segment = [];
    };

    points.forEach((point) => {
      if (Object.prototype.hasOwnProperty.call(point, "value")) {
        segment.push(point);
      } else {
        flushSegment();
      }
    });
    flushSegment();
    state.plotSeries.set(title, { type: "linebr", series: seriesList });
  }

  function updatePlotDefinitions(plots) {
    (Array.isArray(plots) ? plots : []).forEach((plot) => {
      const title = String(plot && plot.title || "");
      if (!title || state.plotDefinitions.some((item) => item.title === title)) return;
      const definition = { ...plot, title };
      state.plotDefinitions.push(definition);
    });
  }

  function applyPlotData() {
    state.plotDefinitions.forEach((plot) => {
      const title = String(plot.title || "");
      const points = state.priceBars.map((bar) => {
        const value = bar.plots && Number(bar.plots[title]);
        return Number.isFinite(value)
          ? { time: Number(bar.time), value }
          : { time: Number(bar.time) };
      });
      if (plot.kind === "bgcolor") {
        if (state.bgcolorPrimitive) state.bgcolorPrimitive.setLayer(plot, points);
        return;
      }
      const options = plotSeriesOptions(plot);
      if (Number.parseInt(plot.style, 10) === STYLE_LINEBR) {
        setLineBreakPlotData(title, options, points);
        return;
      }
      let controller = state.plotSeries.get(title);
      if (!controller || controller.type !== "single") {
        removePlotController(controller);
        controller = {
          type: "single",
          series: state.priceChart.addSeries(LightweightCharts.LineSeries, options),
        };
        state.plotSeries.set(title, controller);
      }
      controller.series.setData(points);
    });
  }

  function applyPriceData(logicalRange = null, prepended = 0) {
    state.candleSeries.setData(state.priceBars.map((bar) => ({
      time: Number(bar.time),
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
    })));
    state.volumeSeries.setData(state.priceBars.map((bar) => ({
      time: Number(bar.time),
      value: Number(bar.volume) || 0,
      color: Number(bar.close) >= Number(bar.open) ? "#26a69a" : "#ef5350",
    })));
    if (window.App && window.App.collections) {
      window.App.collections.ohlcvIndexByTime = new Map(
        state.priceBars.map((bar, index) => [Number(bar.time), index]),
      );
    }
    if (state.bgcolorPrimitive) state.bgcolorPrimitive.clear();
    applyPlotData();
    renderPriceMarkers();
    if (logicalRange) {
      state.priceChart.timeScale().setVisibleLogicalRange({
        from: logicalRange.from + prepended,
        to: logicalRange.to + prepended,
      });
    }
  }

  async function extendPriceWindow(direction) {
    if (state.pricePaging || !state.priceBars.length) return;
    const before = direction === "before";
    if ((before && state.priceStart <= 0) || (!before && state.priceEnd >= state.priceRowCount - 1)) {
      return;
    }
    state.pricePaging = true;
    const request = state.priceRequest;
    const pageSize = 2000;
    const start = before
      ? Math.max(0, state.priceStart - pageSize)
      : state.priceEnd + 1;
    const end = before
      ? state.priceStart - 1
      : Math.min(state.priceRowCount - 1, state.priceEnd + pageSize);
    const logicalRange = state.priceChart.timeScale().getVisibleLogicalRange();
    try {
      const payload = await json(
        `/api/scripting/backtests/${encodeURIComponent(jobId)}/chart?start_index=${start}&end_index=${end}`,
      );
      if (request !== state.priceRequest) return;
      const incomingBars = Array.isArray(payload.bars) ? payload.bars : [];
      const knownBars = new Map(state.priceBars.map((bar) => [Number(bar.bar_index), bar]));
      incomingBars.forEach((bar) => knownBars.set(Number(bar.bar_index), bar));
      const previousStart = state.priceStart;
      state.priceBars = [...knownBars.values()].sort(
        (left, right) => Number(left.bar_index) - Number(right.bar_index),
      );
      state.priceRowCount = Number(payload.row_count) || state.priceRowCount;
      updatePlotDefinitions(payload.plots);
      state.priceStart = Math.min(state.priceStart, Number(payload.start_index));
      state.priceEnd = Math.max(state.priceEnd, Number(payload.end_index));
      state.priceMarkers = normalizedPriceMarkers([
        ...state.priceMarkers,
        ...(Array.isArray(payload.markers) ? payload.markers : []),
      ]);
      const prepended = before
        ? state.priceBars.filter((bar) => Number(bar.bar_index) < previousStart).length
        : 0;
      applyPriceData(logicalRange, prepended);
    } catch (error) {
      showError(error.message || "Earlier price data could not be loaded.");
    } finally {
      state.pricePaging = false;
    }
  }

  function updatePriceSelection(index) {
    const equity = state.equity;
    const selectionRequest = ++state.priceSelectionRequest;
    state.selectedIndex = index;
    const timestamp = equity.timestamps[index];
    el("backtest-equity-selection-time").textContent = formatTimestamp(timestamp);
    el("backtest-equity-selection-value").textContent = formatAmount(equity.equities[index]);
    renderPriceMarkers();
    const bars = state.priceBars.length;
    const localIndex = state.priceBars.findIndex(
      (bar) => Number(bar.bar_index) === Number(equity.barIndices[index]),
    );
    if (bars > 0 && localIndex >= 0 && localIndex < bars) {
      const visible = Math.min(180, bars);
      const from = Math.max(0, Math.floor(localIndex - visible / 2));
      const to = Math.min(bars - 1, Math.ceil(localIndex + visible / 2));
      const candle = state.priceBars[localIndex];
      const visibleRange = {
        from: Number(state.priceBars[from].time),
        to: Number(state.priceBars[to].time),
      };
      const placeSelection = () => {
        state.priceChart.timeScale().setVisibleRange(visibleRange);
        state.priceChart.setCrosshairPosition(
          Number(candle.close),
          Number(candle.time),
          state.candleSeries,
        );
      };
      placeSelection();
      requestAnimationFrame(() => {
        if (selectionRequest !== state.priceSelectionRequest) return;
        placeSelection();
      });
    }
    drawEquity();
  }

  async function loadPriceAt(index) {
    const equity = state.equity;
    const barIndex = Math.round(equity.barIndices[index]);
    const request = ++state.priceRequest;
    const loading = el("backtest-price-loading");
    state.priceWindowReplacing = true;
    const loadedIndex = state.priceBars.findIndex(
      (bar) => Number(bar.bar_index) === barIndex,
    );
    if (loadedIndex >= 0) {
      updatePriceSelection(index);
      await nextAnimationFrame();
      await nextAnimationFrame();
      if (request !== state.priceRequest) return;
      state.priceWindowReplacing = false;
      loading.classList.add("hidden");
      return;
    }
    const start = Math.max(0, barIndex - 1000);
    const end = barIndex + 1000;
    loading.textContent = "Loading price chart...";
    loading.classList.remove("hidden");
    try {
      const payload = await json(
        `/api/scripting/backtests/${encodeURIComponent(jobId)}/chart?start_index=${start}&end_index=${end}`,
      );
      if (request !== state.priceRequest) return;
      const bars = Array.isArray(payload.bars) ? payload.bars : [];
      if (!bars.length) throw new Error("Price chart window is empty.");
      state.priceBars = bars;
      state.priceRowCount = Number(payload.row_count) || bars.length;
      state.plotDefinitions = [];
      clearPlotSeries();
      updatePlotDefinitions(payload.plots);
      state.priceStart = Number(payload.start_index);
      state.priceEnd = Number(payload.end_index);
      state.priceMarkers = normalizedPriceMarkers(payload.markers);
      applyPriceData();
      updatePriceSelection(index);
      await nextAnimationFrame();
      await nextAnimationFrame();
      if (request !== state.priceRequest) return;
      state.priceWindowReplacing = false;
      loading.classList.add("hidden");
    } catch (error) {
      if (request !== state.priceRequest) return;
      state.priceWindowReplacing = false;
      loading.textContent = error.message || "Price chart could not be loaded.";
    }
  }

  function bindEquityInteractions() {
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const layout = chartLayout(rect.width, rect.height);
      const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left - layout.left) / layout.width));
      zoomEquity(event.deltaY < 0 ? 0.78 : 1.28, ratio);
    }, { passive: false });
    canvas.addEventListener("pointerdown", (event) => {
      canvas.setPointerCapture(event.pointerId);
      state.pointer = {
        id: event.pointerId,
        x: event.clientX,
        start: state.viewStart,
        end: state.viewEnd,
        moved: false,
      };
    });
    canvas.addEventListener("pointermove", (event) => {
      if (state.pointer && state.pointer.id === event.pointerId) {
        const rect = canvas.getBoundingClientRect();
        const layout = chartLayout(rect.width, rect.height);
        const delta = event.clientX - state.pointer.x;
        if (Math.abs(delta) > 3) state.pointer.moved = true;
        const span = state.pointer.end - state.pointer.start;
        let start = state.pointer.start - (delta / layout.width) * span;
        start = Math.max(0, Math.min(state.equity.length - 1 - span, start));
        state.viewStart = start;
        state.viewEnd = start + span;
        tooltip.classList.add("hidden");
        drawEquity();
        return;
      }
      state.hoverIndex = equityIndexAt(event.clientX);
      showEquityTooltip(state.hoverIndex, event.clientX, event.clientY);
      drawEquity();
    });
    canvas.addEventListener("pointerup", (event) => {
      const pointer = state.pointer;
      state.pointer = null;
      if (!pointer || pointer.moved) return;
      const index = equityIndexAt(event.clientX);
      void loadPriceAt(index);
    });
    canvas.addEventListener("pointercancel", () => { state.pointer = null; });
    canvas.addEventListener("pointerleave", () => {
      if (state.pointer) return;
      state.hoverIndex = -1;
      tooltip.classList.add("hidden");
      drawEquity();
    });
    el("backtest-equity-zoom-in").addEventListener("click", () => zoomEquity(0.65));
    el("backtest-equity-zoom-out").addEventListener("click", () => zoomEquity(1.55));
    el("backtest-equity-fit").addEventListener("click", fitEquity);
    el("backtest-chart-max-drawdown").addEventListener("click", () => {
      focusEquityIndex(state.maxDrawdownIndex);
    });
    el("backtest-drawdown-list-toggle").addEventListener("click", toggleDrawdownList);
    el("backtest-drawdown-list").addEventListener("click", (event) => {
      const item = event.target.closest("[data-equity-index]");
      if (!item) return;
      const index = Number(item.dataset.equityIndex);
      closeDrawdownList();
      focusEquityIndex(index);
    });
    document.addEventListener("pointerdown", (event) => {
      if (!el("backtest-drawdown-row").contains(event.target)) closeDrawdownList();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeDrawdownList();
    });
  }

  function bindPaneResize() {
    const layout = document.querySelector(".backtest-chart-layout");
    const resizer = el("backtest-pane-resizer");

    const resize = (clientY) => {
      const rect = layout.getBoundingClientRect();
      const compact = window.matchMedia("(max-width: 720px)").matches;
      const minimumPrice = compact ? 150 : 260;
      const minimumEquity = compact ? 160 : 190;
      const maximumEquity = Math.max(
        minimumEquity,
        rect.height - minimumPrice - resizer.offsetHeight,
      );
      const height = Math.max(
        minimumEquity,
        Math.min(maximumEquity, rect.bottom - clientY - resizer.offsetHeight / 2),
      );
      layout.style.setProperty("--equity-pane-height", `${Math.round(height)}px`);
      const percent = Math.round((height / Math.max(1, rect.height)) * 100);
      resizer.setAttribute("aria-valuenow", String(percent));
      resizer.setAttribute("aria-valuetext", `Equity curve ${percent}%`);
    };

    resizer.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      resizer.setPointerCapture(event.pointerId);
      resizer.classList.add("dragging");
      state.paneResizePointer = event.pointerId;
    });
    resizer.addEventListener("pointermove", (event) => {
      if (state.paneResizePointer !== event.pointerId) return;
      event.preventDefault();
      resize(event.clientY);
    });
    const finish = (event) => {
      if (state.paneResizePointer !== event.pointerId) return;
      state.paneResizePointer = null;
      resizer.classList.remove("dragging");
    };
    resizer.addEventListener("pointerup", finish);
    resizer.addEventListener("pointercancel", finish);
    resizer.addEventListener("lostpointercapture", () => {
      state.paneResizePointer = null;
      resizer.classList.remove("dragging");
    });
  }

  async function init() {
    if (!jobId) {
      showError("Backtest job was not specified.");
      return;
    }
    try {
      state.job = await json(`/api/scripting/backtests/${encodeURIComponent(jobId)}`);
      document.title = `${state.job.script_path || "Backtest"} · Equity Curve`;
      el("backtest-chart-script").textContent = state.job.script_path || "Backtest";
      el("backtest-chart-data").textContent = state.job.data_path || "";
      renderChartIdentity(state.job);
      initPriceChart();
      bindPriceNavigation();
      bindEquityInteractions();
      bindPaneResize();
      state.equity = await loadEquityCsv();
      state.levels = buildLevels(state.equity);
      state.viewStart = 0;
      state.viewEnd = Math.max(1, state.equity.length - 1);
      state.drawdownIndices = collectDrawdownIndices(state.equity);
      renderDrawdownList();
      el("backtest-chart-final-equity").textContent = formatAmount(
        state.equity.equities[state.equity.length - 1],
      );
      let maximumDrawdown = 0;
      for (let index = 0; index < state.equity.length; index += 1) {
        if (state.equity.drawdowns[index] > maximumDrawdown) {
          maximumDrawdown = state.equity.drawdowns[index];
          state.maxDrawdownIndex = index;
        }
      }
      const maxDrawdownButton = el("backtest-chart-max-drawdown");
      el("backtest-chart-max-drawdown-value").textContent = formatAmount(maximumDrawdown);
      el("backtest-chart-max-drawdown-percent").textContent = state.maxDrawdownIndex >= 0
        ? formatPercent(state.equity.drawdownPercents[state.maxDrawdownIndex])
        : "-";
      maxDrawdownButton.disabled = state.maxDrawdownIndex < 0;
      if (state.maxDrawdownIndex >= 0) {
        const occurredAt = formatTimestamp(state.equity.timestamps[state.maxDrawdownIndex]);
        el("backtest-chart-max-drawdown-tooltip").textContent = `Occurred ${occurredAt}`;
        maxDrawdownButton.setAttribute(
          "aria-label",
          `Go to maximum drawdown at ${occurredAt}`,
        );
      }
      el("backtest-equity-loading").classList.add("hidden");
      const lastIndex = state.equity.length - 1;
      state.selectedIndex = lastIndex;
      drawEquity();
      await loadPriceAt(lastIndex);
    } catch (error) {
      el("backtest-equity-loading").classList.add("hidden");
      showError(error.message || "Backtest chart could not be loaded.");
    }
  }

  window.addEventListener("resize", () => {
    drawEquity();
    positionDrawdownList();
    positionPriceNavButtons();
  });
  void init();
})();
