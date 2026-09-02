(function () {
  let initialized = false;
  let api = null;
  let mobileQuery = null;
  let context = null;
  let dataRows = [];
  let selectedDataPath = "";
  let supportedDataExchanges = ["binance", "bitget", "bybit", "okx", "hyperliquid"];
  let dataSymbolCheckSequence = 0;
  let dateValues = { from: "", to: "" };
  let calendarMonths = { from: null, to: null };
  let job = null;
  let socket = null;
  let reconnectTimer = null;
  let logText = "";
  let logOffset = 0;
  let findQuery = "";
  let findMatches = [];
  let findIndex = -1;
  let requestSequence = 0;
  let actionBusy = false;
  let dataBusy = false;
  let dataDeleteBusy = false;
  let pendingDataDeletePath = "";
  let closeTimer = null;
  let openTimer = null;
  let sheetDrag = null;

  const terminalStatuses = new Set(["completed", "failed", "cancelled", "interrupted"]);
  const dataExchangeLabels = {
    binance: "Binance",
    bitget: "Bitget",
    bybit: "Bybit",
    okx: "OKX",
    hyperliquid: "Hyperliquid",
  };
  const el = (id) => document.getElementById(id);

  function isOpen() {
    return !el("scripting-backtest-modal").classList.contains("hidden");
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function utcDate(timestamp) {
    return new Date(Number(timestamp) * 1000);
  }

  function dateKey(date) {
    return new Date(Date.UTC(
      date.getUTCFullYear(),
      date.getUTCMonth(),
      date.getUTCDate(),
    )).toISOString().slice(0, 10);
  }

  function dateFromKey(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
    if (!match) return null;
    const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function dateLabel(value) {
    const date = dateFromKey(value);
    return date
      ? date.toLocaleDateString("en-US", {
        timeZone: "UTC",
        year: "numeric",
        month: "short",
        day: "numeric",
      })
      : "Select date";
  }

  function timeValue(timestamp) {
    const date = utcDate(timestamp);
    return `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
  }

  function normalizeTime(raw) {
    const value = String(raw || "").trim();
    const colon = /^(\d{1,2}):(\d{1,2})$/.exec(value);
    let hours;
    let minutes;
    if (colon) {
      hours = Number(colon[1]);
      minutes = Number(colon[2]);
    } else if (/^\d{1,4}$/.test(value)) {
      hours = value.length <= 2 ? Number(value) : Number(value.slice(0, -2));
      minutes = value.length <= 2 ? 0 : Number(value.slice(-2));
    } else {
      return null;
    }
    if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  }

  function selectedData() {
    return dataRows.find((row) => row.path === selectedDataPath) || null;
  }

  function isDataManagerOpen() {
    return !el("scripting-backtest-data-modal").classList.contains("hidden");
  }

  function isDataDeleteOpen() {
    return !el("scripting-backtest-data-delete-modal").classList.contains("hidden");
  }

  function defaultDataHistorySince() {
    const date = new Date();
    date.setUTCMonth(date.getUTCMonth() - 2);
    return dateKey(date);
  }

  function setError(message = "") {
    const node = el("scripting-backtest-error");
    node.textContent = String(message || "");
    node.classList.toggle("hidden", !message);
  }

  function setStatus(status = "ready", summary = "") {
    const node = el("scripting-backtest-status");
    const labels = {
      preparing: "Preparing",
      running: "Running",
      stopping: "Stopping",
      completed: "Completed",
      failed: "Failed",
      cancelled: "Cancelled",
      interrupted: "Interrupted",
      loading: "Loading",
      ready: "Ready",
    };
    node.textContent = labels[status] || status;
    node.dataset.status = status;
    el("scripting-backtest-summary").textContent = summary;
  }

  function statusSummary(value) {
    if (!value) return "";
    if (value.error) return value.error;
    if (value.status === "completed") return "";
    if (value.actual_time_from && value.actual_time_to) {
      const start = utcDate(value.actual_time_from).toISOString().replace(".000Z", "Z");
      const end = utcDate(value.actual_time_to).toISOString().replace(".000Z", "Z");
      return `${start} - ${end}`;
    }
    return value.data_path || "";
  }

  function isActiveJob(value = job) {
    return Boolean(value && ["preparing", "running", "stopping"].includes(value.status));
  }

  function updateActionState() {
    const button = el("scripting-backtest-run");
    const active = isActiveJob();
    const runnable = Boolean(
      context
      && context.path
      && context.revision
      && !context.dirty
      && context.validation
      && ["strategy", "indicator"].includes(context.validation.script_kind)
      && context.validation.runnable,
    );
    button.disabled = actionBusy || (!active && (!runnable || !selectedDataPath || !validRange(false)));
    button.classList.toggle("btn-primary", !active);
    button.classList.toggle("btn-danger-primary", active);
    const actionLabel = active ? "Stop" : "Run";
    button.dataset.tooltip = actionLabel;
    button.setAttribute("aria-label", actionLabel);
    button.querySelector("svg").innerHTML = active
      ? '<rect x="3" y="3" width="18" height="18" rx="2"></rect>'
      : '<polygon points="6 3 20 12 6 21 6 3"></polygon>';
    el("scripting-backtest-data-button").disabled = actionBusy || dataBusy || active || dataRows.length === 0;
    el("scripting-backtest-data-manage").disabled = actionBusy || dataBusy || active;
    ["from-date", "to-date", "from-time", "to-time"].forEach((name) => {
      el(`scripting-backtest-${name}`).disabled = actionBusy || dataBusy || active || dataRows.length === 0;
    });
    el("scripting-backtest-find-toggle").disabled = !logText;
    const deleteButton = el("scripting-backtest-delete");
    const canDelete = Boolean(job && terminalStatuses.has(String(job.status || "")));
    deleteButton.classList.toggle("hidden", !canDelete);
    deleteButton.disabled = actionBusy || dataBusy || active || !canDelete;
  }

  function setDateValue(which, value) {
    const date = dateFromKey(value);
    if (!date) return;
    dateValues[which] = dateKey(date);
    el(`scripting-backtest-${which}-date`).querySelector("span").textContent = dateLabel(dateValues[which]);
    updateActionState();
  }

  function setRangeFromData(row) {
    if (!row) return;
    const start = utcDate(row.start_timestamp);
    const end = utcDate(row.latest_confirmed_timestamp);
    setDateValue("from", dateKey(start));
    setDateValue("to", dateKey(end));
    el("scripting-backtest-from-time").value = timeValue(row.start_timestamp);
    el("scripting-backtest-to-time").value = timeValue(row.latest_confirmed_timestamp);
  }

  function setRangeFromJob(value, row) {
    const from = Number(value && value.time_from);
    const to = Number(value && value.time_to);
    if (
      !row
      || !Number.isFinite(from)
      || !Number.isFinite(to)
      || from < Number(row.start_timestamp)
      || to > Number(row.latest_confirmed_timestamp)
      || from > to
    ) {
      setRangeFromData(row);
      return;
    }
    setDateValue("from", dateKey(utcDate(from)));
    setDateValue("to", dateKey(utcDate(to)));
    el("scripting-backtest-from-time").value = timeValue(from);
    el("scripting-backtest-to-time").value = timeValue(to);
  }

  function rangeIso(which) {
    const normalized = normalizeTime(el(`scripting-backtest-${which}-time`).value);
    if (!dateValues[which] || !normalized) return null;
    return `${dateValues[which]}T${normalized}:00Z`;
  }

  function validRange(showError = true) {
    const row = selectedData();
    const from = rangeIso("from");
    const to = rangeIso("to");
    let message = "";
    if (!row || !from || !to) {
      message = "Choose valid UTC dates and times.";
    } else {
      const fromSeconds = Math.floor(Date.parse(from) / 1000);
      const toSeconds = Math.floor(Date.parse(to) / 1000);
      if (fromSeconds > toSeconds) message = "Date from must not be after Date to.";
      else if (
        fromSeconds < Number(row.start_timestamp)
        || toSeconds > Number(row.latest_confirmed_timestamp)
      ) message = "The selected range must be inside the confirmed OHLCV data.";
    }
    if (showError) setError(message);
    return !message;
  }

  function closeCalendars(except = "") {
    ["from", "to"].forEach((which) => {
      if (which === except) return;
      el(`scripting-backtest-${which}-calendar`).classList.add("hidden");
      el(`scripting-backtest-${which}-date`).setAttribute("aria-expanded", "false");
    });
  }

  function dataSelectNodes() {
    return {
      control: el("scripting-backtest-data-control"),
      button: el("scripting-backtest-data-button"),
      label: el("scripting-backtest-data-label"),
      options: el("scripting-backtest-data-options"),
    };
  }

  function dataExchangeNodes() {
    return {
      control: el("scripting-backtest-data-exchange-control"),
      input: el("scripting-backtest-data-exchange"),
      button: el("scripting-backtest-data-exchange-button"),
      label: el("scripting-backtest-data-exchange-label"),
      options: el("scripting-backtest-data-exchange-options"),
    };
  }

  function selectedDataExchange() {
    return String(dataExchangeNodes().input.value || "").trim().toLowerCase();
  }

  function dataExchangeLabel(exchange) {
    return dataExchangeLabels[exchange] || String(exchange || "Select exchange");
  }

  function renderDataExchangeOptions() {
    const nodes = dataExchangeNodes();
    const selected = selectedDataExchange();
    nodes.label.textContent = dataExchangeLabel(selected);
    nodes.options.innerHTML = supportedDataExchanges.map((exchange) => (
      `<button type="button" role="option" class="script-select-option${exchange === selected ? " selected" : ""}" `
      + `data-backtest-exchange="${escapeHtml(exchange)}" aria-selected="${exchange === selected ? "true" : "false"}">`
      + `${escapeHtml(dataExchangeLabel(exchange))}</button>`
    )).join("");
  }

  function setDataExchangeOptionsExpanded(expanded) {
    const nodes = dataExchangeNodes();
    nodes.button.setAttribute("aria-expanded", String(expanded));
    if (expanded) {
      nodes.options.classList.remove("hidden");
      nodes.options.style.maxHeight = "0px";
      void nodes.options.offsetHeight;
      nodes.control.classList.add("open");
      nodes.options.style.maxHeight = `${nodes.options.scrollHeight}px`;
      return;
    }
    nodes.options.style.maxHeight = `${nodes.options.getBoundingClientRect().height}px`;
    void nodes.options.offsetHeight;
    nodes.control.classList.remove("open");
    nodes.options.classList.add("hidden");
    nodes.options.style.maxHeight = "0px";
  }

  function closeDataExchangeOptions() {
    setDataExchangeOptionsExpanded(false);
  }

  function setDataSymbolError(message = "", kind = "") {
    const node = el("scripting-backtest-data-symbol-error");
    node.textContent = message;
    node.className = `field-error${kind ? ` ${kind}` : ""}`;
  }

  function setDataExchange(exchange) {
    const normalized = String(exchange || "").trim().toLowerCase();
    const value = supportedDataExchanges.includes(normalized)
      ? normalized
      : supportedDataExchanges[0] || "";
    dataExchangeNodes().input.value = value;
    dataSymbolCheckSequence += 1;
    setDataSymbolError();
    renderDataExchangeOptions();
  }

  async function checkDataSymbol(required = false) {
    const sequence = ++dataSymbolCheckSequence;
    const input = el("scripting-backtest-data-symbol");
    input.value = String(input.value || "").toUpperCase();
    const exchange = selectedDataExchange();
    const symbol = input.value.trim();
    if (!symbol) {
      setDataSymbolError(required ? "enter a symbol such as BTC/USDT:USDT" : "");
      return !required;
    }
    if (!exchange) {
      setDataSymbolError("select exchange first");
      return false;
    }
    setDataSymbolError("checking…", "checking");
    try {
      const query = `provider=ccxt&exchange=${encodeURIComponent(exchange)}`
        + `&symbol=${encodeURIComponent(symbol)}`;
      const result = await api(`/api/validate/symbol?${query}`, { cache: "no-store" });
      if (sequence !== dataSymbolCheckSequence) return true;
      if (result.skipped || result.exists === true) {
        setDataSymbolError();
        return true;
      }
      if (result.exists === false) {
        setDataSymbolError(`symbol '${symbol}' not found on ${exchange}`);
        return false;
      }
      setDataSymbolError(result.error || "could not verify symbol", "warn");
      return true;
    } catch (error) {
      if (sequence === dataSymbolCheckSequence) {
        setDataSymbolError("could not verify symbol", "warn");
      }
      return true;
    }
  }

  function setDataOptionsExpanded(expanded) {
    const nodes = dataSelectNodes();
    nodes.button.setAttribute("aria-expanded", String(expanded));
    if (expanded) {
      nodes.options.classList.remove("hidden");
      nodes.options.style.maxHeight = "0px";
      void nodes.options.offsetHeight;
      nodes.control.classList.add("open");
      const limit = Math.min(320, window.innerHeight * 0.48);
      nodes.options.style.maxHeight = `${Math.min(nodes.options.scrollHeight, limit)}px`;
      return;
    }
    nodes.options.style.maxHeight = `${nodes.options.getBoundingClientRect().height}px`;
    void nodes.options.offsetHeight;
    nodes.control.classList.remove("open");
    nodes.options.classList.add("hidden");
    nodes.options.style.maxHeight = "0px";
  }

  function closeDataOptions() {
    hideDataTooltip();
    setDataOptionsExpanded(false);
  }

  function openDataOptions() {
    if (el("scripting-backtest-data-button").disabled || !dataRows.length) return;
    closeCalendars();
    setDataOptionsExpanded(true);
    const selected = el("scripting-backtest-data-options").querySelector(".script-select-option.selected");
    if (selected) selected.scrollIntoView({ block: "nearest" });
  }

  function toggleDataOptions() {
    const options = el("scripting-backtest-data-options");
    if (options.classList.contains("hidden")) openDataOptions();
    else closeDataOptions();
  }

  function renderCalendar(which) {
    const calendar = el(`scripting-backtest-${which}-calendar`);
    const row = selectedData();
    const month = calendarMonths[which];
    if (!row || !month) return;
    const minimum = dateKey(utcDate(row.start_timestamp));
    const maximum = dateKey(utcDate(row.latest_confirmed_timestamp));
    const year = month.getUTCFullYear();
    const monthIndex = month.getUTCMonth();
    const firstWeekday = new Date(Date.UTC(year, monthIndex, 1)).getUTCDay();
    const days = [];
    for (let index = 0; index < 42; index += 1) {
      const date = new Date(Date.UTC(year, monthIndex, 1 - firstWeekday + index));
      const key = dateKey(date);
      const outside = date.getUTCMonth() !== monthIndex;
      const disabled = key < minimum || key > maximum;
      days.push(
        `<button class="scripting-backtest-calendar-day${outside ? " outside" : ""}${key === dateValues[which] ? " selected" : ""}" `
        + `type="button" data-backtest-date="${key}"${disabled ? " disabled" : ""}>${date.getUTCDate()}</button>`,
      );
    }
    calendar.innerHTML = `
      <div class="scripting-backtest-calendar-toolbar">
        <button type="button" data-backtest-month="-1" aria-label="Previous month">&#8249;</button>
        <strong>${month.toLocaleDateString("en-US", { timeZone: "UTC", year: "numeric", month: "long" })}</strong>
        <button type="button" data-backtest-month="1" aria-label="Next month">&#8250;</button>
      </div>
      <div class="scripting-backtest-calendar-weekdays" aria-hidden="true">
        <span>Su</span><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span>
      </div>
      <div class="scripting-backtest-calendar-days">${days.join("")}</div>`;
  }

  function toggleCalendar(which) {
    const calendar = el(`scripting-backtest-${which}-calendar`);
    const opening = calendar.classList.contains("hidden");
    closeCalendars();
    closeDataOptions();
    if (!opening || !selectedData()) return;
    const selected = dateFromKey(dateValues[which]) || new Date();
    calendarMonths[which] = new Date(Date.UTC(selected.getUTCFullYear(), selected.getUTCMonth(), 1));
    renderCalendar(which);
    calendar.classList.remove("above", "hidden");
    el(`scripting-backtest-${which}-date`).setAttribute("aria-expanded", "true");
    window.requestAnimationFrame(() => {
      if (calendar.getBoundingClientRect().bottom > window.innerHeight - 8) {
        calendar.classList.add("above");
      }
    });
  }

  function handleCalendarClick(which, event) {
    const monthButton = event.target.closest("[data-backtest-month]");
    if (monthButton) {
      const month = calendarMonths[which];
      calendarMonths[which] = new Date(Date.UTC(
        month.getUTCFullYear(),
        month.getUTCMonth() + Number(monthButton.dataset.backtestMonth),
        1,
      ));
      renderCalendar(which);
      return;
    }
    const day = event.target.closest("[data-backtest-date]");
    if (!day || day.disabled) return;
    setDateValue(which, day.dataset.backtestDate);
    closeCalendars();
  }

  function dataOptionLabel(row) {
    const provider = String(row.provider || "").toUpperCase();
    const symbol = String(row.symbol || row.path);
    const timeframe = row.timeframe ? ` · ${row.timeframe}` : "";
    return `${provider} · ${symbol}${timeframe}`;
  }

  function selectDataPath(path, rangeSource = "data") {
    const row = dataRows.find((item) => item.path === path);
    if (!row) return false;
    selectedDataPath = row.path;
    const nodes = dataSelectNodes();
    nodes.label.textContent = dataOptionLabel(row);
    nodes.options.querySelectorAll(".script-select-option").forEach((node) => {
      const selected = node.dataset.backtestDataPath === selectedDataPath;
      node.classList.toggle("selected", selected);
      node.setAttribute("aria-selected", String(selected));
    });
    nodes.options.querySelectorAll(".scripting-backtest-data-option-row").forEach((node) => {
      node.classList.toggle(
        "selected",
        node.querySelector("[data-backtest-data-path]")?.dataset.backtestDataPath === selectedDataPath,
      );
    });
    if (rangeSource === "job") setRangeFromJob(job, row);
    else if (rangeSource === "data") setRangeFromData(row);
    updateActionState();
    return true;
  }

  function hideDataTooltip() {
    el("scripting-backtest-data-tooltip").classList.add("hidden");
  }

  function showDataTooltip(anchor) {
    const tooltip = el("scripting-backtest-data-tooltip");
    const value = String(anchor && anchor.dataset.backtestDataTooltip || "");
    if (!value) {
      hideDataTooltip();
      return;
    }
    tooltip.textContent = value;
    tooltip.classList.remove("hidden");
    const anchorRect = anchor.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const margin = 8;
    const left = Math.min(
      Math.max(margin, anchorRect.left),
      Math.max(margin, window.innerWidth - tooltipRect.width - margin),
    );
    const above = anchorRect.top - tooltipRect.height - 5;
    const top = above >= margin
      ? above
      : Math.min(window.innerHeight - tooltipRect.height - margin, anchorRect.bottom + 5);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(margin, top)}px`;
  }

  function renderData(payload, preferredPath = "") {
    if (Array.isArray(payload && payload.supported_exchanges) && payload.supported_exchanges.length) {
      supportedDataExchanges = payload.supported_exchanges.map((value) => String(value).toLowerCase());
    }
    dataRows = Array.isArray(payload && payload.data) ? payload.data : [];
    const nodes = dataSelectNodes();
    closeDataOptions();
    if (!dataRows.length) {
      nodes.label.textContent = "No OHLCV data available";
      nodes.options.innerHTML = '<div class="script-select-empty">No OHLCV data available</div>';
      selectedDataPath = "";
      updateActionState();
      return;
    }
    const recommendation = String(payload.recommended_data_path || "");
    const preferred = String(preferredPath || "");
    selectedDataPath = dataRows[0].path;
    if (dataRows.some((row) => row.path === recommendation)) selectedDataPath = recommendation;
    if (job && dataRows.some((row) => row.path === job.data_path)) selectedDataPath = job.data_path;
    if (dataRows.some((row) => row.path === preferred)) selectedDataPath = preferred;
    nodes.options.innerHTML = dataRows.map((row) => {
      const selected = row.path === selectedDataPath;
      const blocked = row.delete_blocked === true;
      const deleteLabel = blocked
        ? "Data is used by a registered session"
        : `Delete ${row.path}`;
      return `<div class="scripting-backtest-data-option-row${selected ? " selected" : ""}" `
        + `data-backtest-data-tooltip="${escapeHtml(row.path)}">`
        + `<button type="button" role="option" class="script-select-option${selected ? " selected" : ""}" `
        + `data-backtest-data-path="${escapeHtml(row.path)}" aria-selected="${selected ? "true" : "false"}">`
        + `${escapeHtml(dataOptionLabel(row))}</button>`
        + `<button type="button" class="scripting-backtest-data-delete" `
        + `data-backtest-data-delete="${escapeHtml(row.path)}" `
        + `aria-label="${escapeHtml(deleteLabel)}"${blocked ? " disabled" : ""}>`
        + '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18" />'
        + '<path d="M8 6V4h8v2" /><path d="m19 6-1 14H6L5 6" />'
        + '<path d="M10 11v5M14 11v5" /></svg></button></div>';
    }).join("");
    const restoreLatestRange = !preferred && job && selectedDataPath === job.data_path;
    selectDataPath(selectedDataPath, restoreLatestRange ? "job" : "data");
  }

  function setDataMessage(message = "", error = false) {
    const node = el("scripting-backtest-data-message");
    node.textContent = String(message || "");
    node.classList.toggle("hidden", !message);
    node.classList.toggle("error", Boolean(message) && error);
  }

  function setDataBusy(busy, action = "") {
    dataBusy = Boolean(busy);
    const selected = selectedData();
    const canUpdate = Boolean(selected && selected.download_source);
    el("scripting-backtest-data-close").disabled = dataBusy;
    el("scripting-backtest-data-update").disabled = dataBusy || !canUpdate;
    const downloadButton = el("scripting-backtest-data-download");
    const downloading = dataBusy && action === "download";
    downloadButton.disabled = dataBusy;
    downloadButton.querySelector(".scripting-backtest-download-icon").classList.toggle("hidden", downloading);
    downloadButton.querySelector(".scripting-backtest-hourglass-icon").classList.toggle("hidden", !downloading);
    el("scripting-backtest-data-exchange-button").disabled = dataBusy;
    ["symbol", "timeframe", "history-since", "file-name"].forEach((name) => {
      el(`scripting-backtest-data-${name}`).disabled = dataBusy;
    });
    el("scripting-backtest-data-update").querySelector("span").textContent =
      dataBusy && action === "update" ? "Updating..." : "Update to now";
    downloadButton.querySelector("span").textContent = downloading ? "Downloading..." : "Download";
    el("scripting-backtest-data-manage").classList.toggle("syncing", dataBusy);
    updateActionState();
  }

  function openDataManager() {
    if (actionBusy || dataBusy || isActiveJob()) return;
    closeCalendars();
    closeDataOptions();
    const selected = selectedData();
    const source = selected && selected.download_source || null;
    el("scripting-backtest-data-current-path").textContent = selected ? selected.path : "None";
    setDataExchange(source ? source.exchange : supportedDataExchanges[0]);
    el("scripting-backtest-data-symbol").value = source ? source.symbol : "";
    el("scripting-backtest-data-timeframe").value = source ? source.input_timeframe : "5m";
    el("scripting-backtest-data-history-since").value = defaultDataHistorySince();
    el("scripting-backtest-data-file-name").value = "";
    setDataSymbolError();
    setDataMessage(
      selected && !source
        ? "The selected file has no supported update source. You can still download new data."
        : "",
    );
    setDataBusy(false);
    const modal = el("scripting-backtest-data-modal");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
  }

  function closeDataManager(force = false) {
    if (dataBusy && !force) return;
    closeDataExchangeOptions();
    const modal = el("scripting-backtest-data-modal");
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    setDataMessage();
  }

  function setDataDeleteError(message = "") {
    el("scripting-backtest-data-delete-error").textContent = String(message || "");
  }

  function setDataDeleteBusy(busy) {
    dataDeleteBusy = Boolean(busy);
    el("scripting-backtest-data-delete-close").disabled = dataDeleteBusy;
    el("scripting-backtest-data-delete-cancel").disabled = dataDeleteBusy;
    const button = el("scripting-backtest-data-delete-confirm");
    button.disabled = dataDeleteBusy;
    button.textContent = dataDeleteBusy ? "Deleting..." : "Delete";
  }

  function openDataDelete(path) {
    const row = dataRows.find((item) => item.path === path);
    if (!row || row.delete_blocked === true || actionBusy || dataBusy || isActiveJob()) return;
    hideDataTooltip();
    pendingDataDeletePath = path;
    el("scripting-backtest-data-delete-path").textContent = path;
    setDataDeleteError();
    setDataDeleteBusy(false);
    const modal = el("scripting-backtest-data-delete-modal");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
  }

  function closeDataDelete(force = false) {
    if (dataDeleteBusy && !force) return;
    const modal = el("scripting-backtest-data-delete-modal");
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    pendingDataDeletePath = "";
    setDataDeleteError();
    setDataDeleteBusy(false);
  }

  async function deleteBacktestData() {
    const path = pendingDataDeletePath;
    if (!path || dataDeleteBusy || actionBusy || dataBusy || isActiveJob()) return;
    closeDataOptions();
    setDataDeleteError();
    setDataDeleteBusy(true);
    try {
      await api(`/api/scripting/backtest/data?data_path=${encodeURIComponent(path)}`, {
        method: "DELETE",
      });
      const preferred = selectedDataPath === path ? "" : selectedDataPath;
      closeDataDelete(true);
      await loadData(preferred);
      setError();
    } catch (error) {
      setDataDeleteError(error.message || "OHLCV data could not be deleted.");
      setDataDeleteBusy(false);
    }
  }

  async function syncBacktestData(action) {
    if (dataBusy || actionBusy || isActiveJob()) return;
    if (action === "download") {
      setDataMessage();
      if (!el("scripting-backtest-data-timeframe").value.trim()) {
        setDataMessage("Enter a timeframe such as 5m, 1h, or 1D.", true);
        el("scripting-backtest-data-timeframe").focus({ preventScroll: true });
        return;
      }
      if (!await checkDataSymbol(true)) return;
    }
    const previousJob = job;
    const payload = action === "update"
      ? { action, data_path: selectedDataPath }
      : {
        action,
        exchange: selectedDataExchange(),
        symbol: el("scripting-backtest-data-symbol").value,
        timeframe: el("scripting-backtest-data-timeframe").value,
        history_since: el("scripting-backtest-data-history-since").value,
        file_name: el("scripting-backtest-data-file-name").value,
      };
    setDataMessage();
    setDataBusy(true, action);
    setStatus("loading", action === "update" ? "Updating OHLCV data" : "Downloading OHLCV data");
    try {
      const result = await api("/api/scripting/backtest/data/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setDataBusy(false);
      await loadData(String(result.data_path || ""));
      if (isDataManagerOpen()) {
        const selected = selectedData();
        el("scripting-backtest-data-current-path").textContent = selected ? selected.path : "None";
        setDataBusy(false);
        setDataMessage(String(
          result.message
          || (action === "update" ? "OHLCV data updated." : "OHLCV data downloaded."),
        ));
      }
      if (job) renderJob(job);
      else setStatus("ready", String(result.message || "OHLCV data is ready."));
      setError();
    } catch (error) {
      setDataMessage(error.message || "OHLCV data operation failed.", true);
      if (previousJob) setStatus(previousJob.status, statusSummary(previousJob));
      else setStatus("ready", `${dataRows.length} data source${dataRows.length === 1 ? "" : "s"}`);
      setDataBusy(false);
    }
  }

  function updateLogJumpButton() {
    const container = el("scripting-backtest-log");
    const button = el("scripting-backtest-log-jump");
    const scrollable = Boolean(logText)
      && container.scrollHeight > container.clientHeight + 2;
    button.classList.toggle("hidden", !scrollable);
    if (!scrollable) return;
    const atEnd = container.scrollHeight - container.scrollTop - container.clientHeight <= 8;
    const label = atEnd ? "Jump to top" : "Jump to end";
    button.classList.toggle("at-end", atEnd);
    button.dataset.tooltip = label;
    button.setAttribute("aria-label", label);
  }

  function jumpBacktestLog() {
    const container = el("scripting-backtest-log");
    const toTop = el("scripting-backtest-log-jump").classList.contains("at-end");
    container.scrollTo({
      top: toTop ? 0 : container.scrollHeight,
      behavior: "smooth",
    });
  }

  function renderLog(options = {}) {
    const container = el("scripting-backtest-log");
    const follow = options.follow === true
      || container.scrollHeight - container.scrollTop - container.clientHeight < 36;
    const query = findQuery.toLocaleLowerCase();
    findMatches = [];
    if (!logText) {
      container.innerHTML = '<span class="scripting-backtest-log-empty">Run a backtest to view its log.</span>';
      findIndex = -1;
    } else if (!query) {
      container.textContent = logText;
      findIndex = -1;
    } else {
      const lower = logText.toLocaleLowerCase();
      let position = 0;
      while (position <= lower.length - query.length) {
        const found = lower.indexOf(query, position);
        if (found < 0) break;
        findMatches.push(found);
        position = found + Math.max(query.length, 1);
      }
      if (!findMatches.length) findIndex = -1;
      else if (findIndex < 0 || findIndex >= findMatches.length) findIndex = 0;
      let html = "";
      let cursor = 0;
      findMatches.forEach((start, index) => {
        html += escapeHtml(logText.slice(cursor, start));
        html += `<mark${index === findIndex ? ' class="current"' : ""}>${escapeHtml(logText.slice(start, start + query.length))}</mark>`;
        cursor = start + query.length;
      });
      html += escapeHtml(logText.slice(cursor));
      container.innerHTML = html;
    }
    el("scripting-backtest-find-count").textContent = findMatches.length
      ? `${findIndex + 1}/${findMatches.length}`
      : "0/0";
    el("scripting-backtest-find-previous").disabled = !findMatches.length;
    el("scripting-backtest-find-next").disabled = !findMatches.length;
    if (options.focusMatch && findMatches.length) {
      const current = container.querySelector("mark.current");
      if (current) current.scrollIntoView({ block: "center", inline: "nearest" });
    } else if (follow) {
      container.scrollTop = container.scrollHeight;
    }
    window.requestAnimationFrame(updateLogJumpButton);
    updateActionState();
  }

  function appendLog(chunk) {
    const text = String(chunk || "");
    if (!text) return;
    logText += text;
    renderLog();
  }

  function setFindOpen(open) {
    const panel = el("scripting-backtest-find");
    panel.classList.toggle("hidden", !open);
    if (open) {
      window.requestAnimationFrame(() => {
        el("scripting-backtest-find-input").focus({ preventScroll: true });
        el("scripting-backtest-find-input").select();
      });
    }
  }

  function moveFind(offset) {
    if (!findMatches.length) return;
    findIndex = (findIndex + offset + findMatches.length) % findMatches.length;
    renderLog({ focusMatch: true });
  }

  function renderJob(value) {
    const previousJobId = job && job.id;
    job = value || null;
    const status = job ? String(job.status || "ready") : "ready";
    setStatus(status, statusSummary(job));
    setError(job && job.status === "failed" ? job.error || "Backtest failed." : "");
    if (job && job.data_path) {
      selectDataPath(job.data_path, previousJobId === job.id ? "none" : "job");
    }
    updateActionState();
  }

  function closeSocket() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket) {
      const current = socket;
      socket = null;
      current.onclose = null;
      current.close();
    }
  }

  async function fillLog(jobId, reset = false) {
    if (reset) {
      logText = "";
      logOffset = 0;
      findIndex = -1;
      renderLog({ follow: true });
    }
    for (let pages = 0; pages < 20; pages += 1) {
      const payload = await api(
        `/api/scripting/backtests/${encodeURIComponent(jobId)}/log?offset=${logOffset}&max_bytes=131072`,
        { cache: "no-store" },
      );
      if (Number(payload.offset) !== logOffset && logOffset > 0) {
        logText = "";
      }
      logOffset = Number(payload.next_offset || 0);
      appendLog(payload.text);
      if (payload.eof) break;
    }
  }

  async function connectJob(jobId, reset = false) {
    closeSocket();
    try {
      await fillLog(jobId, reset);
    } catch (error) {
      if (isOpen()) setError(error.message || "Backtest log could not be loaded.");
    }
    if (!isOpen() || !job || job.id !== jobId || terminalStatuses.has(job.status)) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/ws/scripting/backtests/${encodeURIComponent(jobId)}?offset=${logOffset}`,
    );
    socket = ws;
    ws.onmessage = (event) => {
      let payload;
      try { payload = JSON.parse(event.data); } catch { return; }
      if (payload.type !== "backtest_update" || !payload.job) return;
      if (payload.log) {
        logOffset = Number(payload.log.next_offset || logOffset);
        appendLog(payload.log.text);
      }
      renderJob(payload.job);
    };
    ws.onclose = () => {
      if (socket === ws) socket = null;
      if (!isOpen() || !job || job.id !== jobId || terminalStatuses.has(job.status)) return;
      reconnectTimer = window.setTimeout(async () => {
        reconnectTimer = null;
        try {
          const status = await api(`/api/scripting/backtests/${encodeURIComponent(jobId)}`, { cache: "no-store" });
          renderJob(status);
          await connectJob(jobId);
        } catch (error) {
          if (isOpen()) setError(error.message || "Backtest status could not be refreshed.");
        }
      }, 1000);
    };
  }

  async function loadLatest() {
    if (!context || !context.path) return;
    try {
      const payload = await api(
        `/api/scripting/backtests/latest?script_path=${encodeURIComponent(context.path)}`,
        { cache: "no-store" },
      );
      if (!isOpen() || !payload.job) return;
      renderJob(payload.job);
      await connectJob(payload.job.id, true);
    } catch (error) {
      if (isOpen()) setError(error.message || "Previous backtest status could not be loaded.");
    }
  }

  async function loadData(preferredPath = "") {
    if (!context || !context.path) return;
    const seq = ++requestSequence;
    setStatus("loading", "Reading OHLCV metadata");
    try {
      const payload = await api(
        `/api/scripting/backtest/data?script_path=${encodeURIComponent(context.path)}`,
        { cache: "no-store" },
      );
      if (seq !== requestSequence || !isOpen()) return;
      renderData(payload, preferredPath);
      if (!job) setStatus("ready", `${dataRows.length} data source${dataRows.length === 1 ? "" : "s"}`);
      if (!dataRows.length) setError("No OHLCV data with symbol metadata is available.");
    } catch (error) {
      if (seq !== requestSequence || !isOpen()) return;
      dataRows = [];
      selectedDataPath = "";
      renderData({ data: [] });
      setStatus("failed", "");
      setError(error.message || "OHLCV data could not be loaded.");
    }
  }

  async function deleteBacktestResults() {
    if (!job || !terminalStatuses.has(String(job.status || "")) || actionBusy || dataBusy) return;
    const previousJob = job;
    actionBusy = true;
    closeSocket();
    updateActionState();
    try {
      await api(`/api/scripting/backtests?script_path=${encodeURIComponent(context.path)}`, {
        method: "DELETE",
      });
      job = null;
      logText = "";
      logOffset = 0;
      findQuery = "";
      findMatches = [];
      findIndex = -1;
      el("scripting-backtest-find-input").value = "";
      setFindOpen(false);
      renderLog({ follow: true });
      setStatus("ready", `${dataRows.length} data source${dataRows.length === 1 ? "" : "s"}`);
      setError();
    } catch (error) {
      job = previousJob;
      setStatus(previousJob.status, statusSummary(previousJob));
      setError(error.message || "Backtest results could not be deleted.");
    } finally {
      actionBusy = false;
      updateActionState();
    }
  }

  async function runOrStop() {
    if (actionBusy || dataBusy) return;
    const previousJob = job;
    actionBusy = true;
    updateActionState();
    try {
      if (isActiveJob()) {
        setStatus("stopping", "Stopping worker");
        const stopped = await api(
          `/api/scripting/backtests/${encodeURIComponent(job.id)}/stop`,
          { method: "POST" },
        );
        renderJob(stopped);
        await fillLog(job.id);
        return;
      }
      if (context.dirty) throw new Error("Save before backtesting.");
      if (!validRange()) return;
      setError();
      setStatus("preparing", "Starting worker");
      const started = await api("/api/scripting/backtests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script_path: context.path,
          base_revision: context.revision,
          data_path: selectedDataPath,
          time_from: rangeIso("from"),
          time_to: rangeIso("to"),
        }),
      });
      renderJob(started);
      await connectJob(started.id, true);
    } catch (error) {
      if (previousJob) setStatus(previousJob.status, statusSummary(previousJob));
      else setStatus("ready", `${dataRows.length} data source${dataRows.length === 1 ? "" : "s"}`);
      if (error && error.code === "revision_conflict") {
        context = { ...context, dirty: true };
      }
      setError(error.message || "Backtest request failed.");
    } finally {
      actionBusy = false;
      updateActionState();
    }
  }

  function open(nextContext) {
    if (closeTimer !== null) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    if (openTimer !== null) {
      clearTimeout(openTimer);
      openTimer = null;
    }
    context = {
      path: String(nextContext && nextContext.path || ""),
      revision: String(nextContext && nextContext.revision || ""),
      dirty: Boolean(nextContext && nextContext.dirty),
      validation: nextContext && nextContext.validation || null,
      error: String(nextContext && nextContext.error || ""),
    };
    closeSocket();
    closeCalendars();
    closeDataOptions();
    job = null;
    logText = "";
    logOffset = 0;
    findQuery = "";
    findMatches = [];
    findIndex = -1;
    dataRows = [];
    selectedDataPath = "";
    actionBusy = false;
    dataBusy = false;
    closeDataManager(true);
    el("scripting-backtest-data-manage").classList.remove("syncing");
    el("scripting-backtest-path").textContent = context.path;
    el("scripting-backtest-find-input").value = "";
    setFindOpen(false);
    renderLog({ follow: true });
    setError();
    if (context.error) setError(context.error);
    else if (context.dirty) setError("Save before backtesting.");
    else if (
      !context.validation
      || !["strategy", "indicator"].includes(context.validation.script_kind)
      || !context.validation.runnable
    ) setError("Only a runnable strategy or indicator can be backtested.");
    const modal = el("scripting-backtest-modal");
    const box = modal.querySelector(".scripting-backtest-box");
    modal.classList.remove("hidden", "closing", "dragging", "swipe-closing", "opening");
    modal.classList.add("opening");
    box.style.removeProperty("transform");
    box.style.removeProperty("transition");
    modal.setAttribute("aria-hidden", "false");
    setStatus("loading", "");
    updateActionState();
    openTimer = window.setTimeout(finishOpening, 230);
    void Promise.all([loadData(), loadLatest()]);
  }

  function finishOpening() {
    if (openTimer !== null) clearTimeout(openTimer);
    openTimer = null;
    el("scripting-backtest-modal").classList.remove("opening");
  }

  function finishClose() {
    const modal = el("scripting-backtest-modal");
    const box = modal.querySelector(".scripting-backtest-box");
    modal.classList.add("hidden");
    modal.classList.remove("closing", "dragging", "swipe-closing", "opening");
    box.style.removeProperty("transform");
    box.style.removeProperty("transition");
    modal.setAttribute("aria-hidden", "true");
    closeTimer = null;
    openTimer = null;
  }

  function close(options = {}) {
    if (!isOpen() || closeTimer !== null || dataBusy) return;
    finishOpening();
    closeSocket();
    closeCalendars();
    closeDataOptions();
    closeDataDelete(true);
    closeDataManager(true);
    setFindOpen(false);
    const modal = el("scripting-backtest-modal");
    const box = modal.querySelector(".scripting-backtest-box");
    sheetDrag = null;
    modal.classList.remove("dragging");
    if (mobileQuery && mobileQuery.matches) {
      if (options.fromDrag === true) {
        modal.classList.add("swipe-closing");
        box.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
        window.requestAnimationFrame(() => {
          box.style.transform = "translateY(100dvh)";
        });
      } else {
        box.style.removeProperty("transform");
        box.style.removeProperty("transition");
      }
      modal.classList.add("closing");
      closeTimer = window.setTimeout(finishClose, 220);
    } else {
      finishClose();
    }
  }

  function beginSheetDrag(event) {
    if (!mobileQuery.matches || !event.isPrimary || !isOpen()) return;
    if (event.target.closest("button, input, a")) return;
    sheetDrag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      dy: 0,
      active: false,
    };
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch {}
  }

  function moveSheetDrag(event) {
    if (!sheetDrag || event.pointerId !== sheetDrag.pointerId) return;
    const dx = event.clientX - sheetDrag.startX;
    const dy = event.clientY - sheetDrag.startY;
    if (!sheetDrag.active) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
      if (dy <= 0 || Math.abs(dy) <= Math.abs(dx)) {
        sheetDrag = null;
        return;
      }
      sheetDrag.active = true;
      finishOpening();
      el("scripting-backtest-modal").classList.add("dragging");
    }
    event.preventDefault();
    sheetDrag.dy = Math.max(0, dy);
    el("scripting-backtest-modal").querySelector(".scripting-backtest-box").style.transform = `translateY(${sheetDrag.dy}px)`;
  }

  function endSheetDrag(event, cancelled = false) {
    if (!sheetDrag || event.pointerId !== sheetDrag.pointerId) return;
    const drag = sheetDrag;
    sheetDrag = null;
    const modal = el("scripting-backtest-modal");
    const box = modal.querySelector(".scripting-backtest-box");
    try { event.currentTarget.releasePointerCapture(drag.pointerId); } catch {}
    modal.classList.remove("dragging");
    if (!cancelled && drag.active && drag.dy > 100) {
      close({ fromDrag: true });
      return;
    }
    if (!drag.active) return;
    box.style.transition = "transform 180ms ease";
    box.style.transform = "translateY(0)";
    window.setTimeout(() => {
      if (!isOpen() || modal.classList.contains("closing")) return;
      box.style.removeProperty("transition");
      box.style.removeProperty("transform");
    }, 190);
  }

  function init(options = {}) {
    if (initialized) return;
    api = options.api;
    mobileQuery = options.mobileQuery;
    if (typeof api !== "function" || !mobileQuery) {
      throw new Error("Backtest initialization dependencies are unavailable");
    }
    el("scripting-backtest-close").addEventListener("click", close);
    const sheetHeader = el("scripting-backtest-modal").querySelector(".scripting-backtest-header");
    sheetHeader.addEventListener("pointerdown", beginSheetDrag);
    sheetHeader.addEventListener("pointermove", moveSheetDrag, { passive: false });
    sheetHeader.addEventListener("pointerup", endSheetDrag);
    sheetHeader.addEventListener("pointercancel", (event) => endSheetDrag(event, true));
    el("scripting-backtest-modal").addEventListener("click", (event) => {
      if (event.target === el("scripting-backtest-modal")) close();
    });
    el("scripting-backtest-data-manage").addEventListener("click", openDataManager);
    el("scripting-backtest-data-exchange-button").addEventListener("click", () => {
      const nodes = dataExchangeNodes();
      if (nodes.button.disabled) return;
      setDataExchangeOptionsExpanded(nodes.options.classList.contains("hidden"));
    });
    el("scripting-backtest-data-exchange-options").addEventListener("click", (event) => {
      const option = event.target.closest("[data-backtest-exchange]");
      if (!option) return;
      setDataExchange(option.dataset.backtestExchange);
      closeDataExchangeOptions();
      if (el("scripting-backtest-data-symbol").value.trim()) void checkDataSymbol();
    });
    el("scripting-backtest-data-button").addEventListener("click", toggleDataOptions);
    const dataOptions = el("scripting-backtest-data-options");
    dataOptions.addEventListener("pointerover", (event) => {
      if (event.pointerType === "touch") return;
      const row = event.target.closest("[data-backtest-data-tooltip]");
      if (row) showDataTooltip(row);
    });
    dataOptions.addEventListener("pointerout", (event) => {
      const row = event.target.closest("[data-backtest-data-tooltip]");
      if (row && !row.contains(event.relatedTarget)) hideDataTooltip();
    });
    dataOptions.addEventListener("focusin", (event) => {
      const row = event.target.closest("[data-backtest-data-tooltip]");
      if (row) showDataTooltip(row);
    });
    dataOptions.addEventListener("focusout", (event) => {
      const row = event.target.closest("[data-backtest-data-tooltip]");
      if (row && !row.contains(event.relatedTarget)) hideDataTooltip();
    });
    dataOptions.addEventListener("click", (event) => {
      const deleteButton = event.target.closest("[data-backtest-data-delete]");
      if (deleteButton) {
        event.stopPropagation();
        openDataDelete(String(deleteButton.dataset.backtestDataDelete || ""));
        return;
      }
      const option = event.target.closest("[data-backtest-data-path]");
      if (!option) return;
      selectDataPath(String(option.dataset.backtestDataPath || ""));
      closeDataOptions();
      setError();
    });
    ["from", "to"].forEach((which) => {
      el(`scripting-backtest-${which}-date`).addEventListener("click", () => toggleCalendar(which));
      el(`scripting-backtest-${which}-calendar`).addEventListener("click", (event) => {
        handleCalendarClick(which, event);
      });
      el(`scripting-backtest-${which}-calendar`).addEventListener("dblclick", (event) => {
        event.preventDefault();
      });
      el(`scripting-backtest-${which}-time`).addEventListener("blur", (event) => {
        const normalized = normalizeTime(event.target.value);
        if (normalized) event.target.value = normalized;
        updateActionState();
      });
      el(`scripting-backtest-${which}-time`).addEventListener("input", updateActionState);
    });
    el("scripting-backtest-run").addEventListener("click", runOrStop);
    el("scripting-backtest-delete").addEventListener("click", deleteBacktestResults);
    el("scripting-backtest-log").addEventListener("scroll", updateLogJumpButton, { passive: true });
    el("scripting-backtest-log-jump").addEventListener("click", jumpBacktestLog);
    el("scripting-backtest-log-jump").addEventListener("dblclick", (event) => {
      event.preventDefault();
    });
    el("scripting-backtest-find-toggle").addEventListener("click", () => setFindOpen(true));
    el("scripting-backtest-find-toggle").addEventListener("dblclick", (event) => {
      event.preventDefault();
    });
    el("scripting-backtest-find-close").addEventListener("click", () => setFindOpen(false));
    el("scripting-backtest-find-input").addEventListener("input", (event) => {
      findQuery = String(event.target.value || "");
      findIndex = -1;
      renderLog({ focusMatch: Boolean(findQuery) });
    });
    el("scripting-backtest-find-previous").addEventListener("click", () => moveFind(-1));
    el("scripting-backtest-find-next").addEventListener("click", () => moveFind(1));
    el("scripting-backtest-find-input").addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      moveFind(event.shiftKey ? -1 : 1);
    });
    el("scripting-backtest-data-close").addEventListener("click", () => closeDataManager());
    el("scripting-backtest-data-update").addEventListener("click", () => {
      void syncBacktestData("update");
    });
    el("scripting-backtest-data-symbol").addEventListener("blur", () => {
      void checkDataSymbol();
    });
    el("scripting-backtest-data-download-form").addEventListener("submit", (event) => {
      event.preventDefault();
      void syncBacktestData("download");
    });
    el("scripting-backtest-data-modal").addEventListener("click", (event) => {
      if (event.target === el("scripting-backtest-data-modal")) closeDataManager();
    });
    el("scripting-backtest-data-delete-close").addEventListener("click", () => closeDataDelete());
    el("scripting-backtest-data-delete-cancel").addEventListener("click", () => closeDataDelete());
    el("scripting-backtest-data-delete-confirm").addEventListener("click", () => {
      void deleteBacktestData();
    });
    el("scripting-backtest-data-delete-modal").addEventListener("click", (event) => {
      if (event.target === el("scripting-backtest-data-delete-modal")) closeDataDelete();
    });
    document.addEventListener("pointerdown", (event) => {
      if (!isOpen()) return;
      if (isDataDeleteOpen()) return;
      if (!event.target.closest(".scripting-backtest-date-picker")) closeCalendars();
      if (!event.target.closest(".scripting-backtest-data-select")) closeDataOptions();
      if (!event.target.closest(".scripting-backtest-exchange-select")) closeDataExchangeOptions();
    });
    document.addEventListener("keydown", (event) => {
      if (!isOpen()) return;
      if (isDataDeleteOpen()) {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopImmediatePropagation();
          closeDataDelete();
        }
        return;
      }
      if (isDataManagerOpen()) {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopImmediatePropagation();
          if (!el("scripting-backtest-data-exchange-options").classList.contains("hidden")) {
            closeDataExchangeOptions();
          } else {
            closeDataManager();
          }
        }
        return;
      }
      const command = event.metaKey || event.ctrlKey;
      if (command && !event.altKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        event.stopImmediatePropagation();
        setFindOpen(true);
        return;
      }
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!el("scripting-backtest-find").classList.contains("hidden")) setFindOpen(false);
      else if (!el("scripting-backtest-data-options").classList.contains("hidden")) closeDataOptions();
      else close();
    }, true);
    initialized = true;
  }

  window.PyneScriptingBacktest = { init, open, close, isOpen };
})();
