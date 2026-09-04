(function () {
  function createBacktestInstance(root, instanceOptions = {}) {
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
  let jobs = [];
  let jobsRefreshTimer = null;
  let inputMetadata = [];
  let inputDataPath = "";
  let inputsExpanded = false;
  let maxConcurrentBacktests = 10;
  let maxInputVariants = 1000;
  let socket = null;
  let reconnectTimer = null;
  let logText = "";
  let logOffset = 0;
  let findQuery = "";
  let findMatches = [];
  let findIndex = -1;
  let requestSequence = 0;
  let inputRequestSequence = 0;
  let actionBusy = false;
  let dataBusy = false;
  let dataDeleteBusy = false;
  let pendingDataDeletePath = "";
  let summaryOpen = false;
  let summaryPagerSyncing = false;
  let summaryPagerSyncTimer = null;
  let summaryPagerSettleTimer = null;
  let closeTimer = null;
  let openTimer = null;
  let sheetDrag = null;
  let desktopDrag = null;
  let desktopResize = null;
  let contextSequence = 0;

  const terminalStatuses = new Set(["completed", "failed", "cancelled", "interrupted"]);
  const desktopGeometryKey = `pynereal.scripting.backtest.geometry.v1.${encodeURIComponent(
    String(instanceOptions.instanceKey || "default"),
  )}`;
  const desktopWindowMargin = 10;
  const desktopWindowOffset = Math.max(0, Number(instanceOptions.desktopOffset) || 0);
  const desktopDefaultWidth = 860;
  const desktopDefaultHeight = 720;
  const dataExchangeLabels = {
    binance: "Binance",
    bitget: "Bitget",
    bybit: "Bybit",
    okx: "OKX",
    hyperliquid: "Hyperliquid",
  };
  const el = (id) => (
    root === document
      ? document.getElementById(id)
      : root.querySelector(`[data-backtest-element="${id}"]`)
  );

  function activate() {
    if (typeof instanceOptions.activate === "function") instanceOptions.activate();
  }

  function isOpen() {
    return !el("scripting-backtest-modal").classList.contains("hidden");
  }

  function isMobile() {
    return Boolean(instanceOptions.mobile);
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

  function isClearConfirmationOpen() {
    return !el("scripting-backtest-clear-popover").classList.contains("hidden");
  }

  function setClearConfirmationOpen(open) {
    const button = el("scripting-backtest-delete");
    const canOpen = Boolean(
      open
      && jobs.length
      && jobs.every((item) => terminalStatuses.has(String(item.status || "")))
      && !actionBusy
      && !dataBusy
    );
    el("scripting-backtest-clear-popover").classList.toggle("hidden", !canOpen);
    button.setAttribute("aria-expanded", canOpen ? "true" : "false");
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
      queued: "Queued",
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
    if (value.status === "queued") {
      return value.queue_position ? `Queue ${value.queue_position}` : "Waiting for a worker";
    }
    if (value.status === "completed") return "";
    if (value.actual_time_from && value.actual_time_to) {
      const start = utcDate(value.actual_time_from).toISOString().replace(".000Z", "Z");
      const end = utcDate(value.actual_time_to).toISOString().replace(".000Z", "Z");
      return `${start} - ${end}`;
    }
    return value.data_path || "";
  }

  function summaryNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function formatSummaryNumber(value, maximumFractionDigits = 2) {
    const number = summaryNumber(value);
    if (number === null) return "-";
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits,
    }).format(number);
  }

  function formatSummaryAmount(value, currency) {
    const formatted = formatSummaryNumber(value, 2);
    return formatted === "-" ? formatted : `${formatted} ${currency}`.trim();
  }

  function formatSummaryPercent(value) {
    const number = summaryNumber(value);
    if (number === null) return "";
    const digits = number !== 0 && Math.abs(number) < 0.01 ? 4 : 2;
    return `${formatSummaryNumber(number, digits)}%`;
  }

  function summaryTone(value) {
    const number = summaryNumber(value);
    if (number === null || number === 0) return "";
    return number > 0 ? "positive" : "negative";
  }

  function summaryMetric(label, value, detail = "", tone = "") {
    return `<div class="scripting-backtest-summary-metric"><dt>${escapeHtml(label)}</dt>`
      + `<dd${tone ? ` class="${tone}"` : ""}>${escapeHtml(value)}</dd>`
      + `${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</div>`;
  }

  function summaryMarkup(value, showInputLabel = false) {
    const summary = value && value.summary;
    if (!summary) return '<div class="scripting-backtest-summary-empty">Summary is unavailable.</div>';
    const currency = String(summary.currency || "");
    const netProfit = summaryNumber(summary.net_profit);
    const drawdown = summaryNumber(summary.max_drawdown);
    const profitFactor = summaryNumber(summary.profit_factor);
    const inputLabel = showInputLabel ? jobLabel(value) : "";
    return '<div class="scripting-backtest-summary-title">Performance'
      + `${inputLabel ? `<span title="${escapeHtml(inputLabel)}">${escapeHtml(inputLabel)}</span>` : ""}</div>`
      + '<dl class="scripting-backtest-summary-grid">'
      + summaryMetric(
        "Net profit",
        formatSummaryAmount(netProfit, currency),
        formatSummaryPercent(summary.net_profit_percent),
        summaryTone(netProfit),
      )
      + summaryMetric(
        "Max drawdown",
        formatSummaryAmount(drawdown, currency),
        formatSummaryPercent(summary.max_drawdown_percent),
        drawdown && drawdown > 0 ? "negative" : "",
      )
      + summaryMetric(
        "Total trades",
        formatSummaryNumber(summary.total_trades, 0),
        summary.open_trades ? `${formatSummaryNumber(summary.open_trades, 0)} open` : "",
      )
      + summaryMetric("Win rate", formatSummaryPercent(summary.win_rate) || "-")
      + summaryMetric(
        "Profit factor",
        formatSummaryNumber(profitFactor, 2),
        "",
        profitFactor === null || profitFactor === 1 ? "" : summaryTone(profitFactor - 1),
      )
      + summaryMetric(
        "Commission",
        formatSummaryAmount(summary.commission, currency),
        "",
        summaryNumber(summary.commission) > 0 ? "negative" : "",
      )
      + summaryMetric(
        "Buy & hold return",
        formatSummaryAmount(summary.buy_hold_return, currency),
        formatSummaryPercent(summary.buy_hold_return_percent),
        summaryTone(summary.buy_hold_return),
      )
      + summaryMetric(
        "Sharpe ratio",
        formatSummaryNumber(summary.sharpe_ratio, 2),
        "",
        summaryTone(summary.sharpe_ratio),
      )
      + '</dl>';
  }

  function summaryJobs() {
    const values = jobs.filter((value) => value && value.summary);
    if (job && job.summary && !values.some((value) => value.id === job.id)) values.unshift(job);
    return values;
  }

  function clearSummaryPagerTimers() {
    if (summaryPagerSyncTimer !== null) clearTimeout(summaryPagerSyncTimer);
    if (summaryPagerSettleTimer !== null) clearTimeout(summaryPagerSettleTimer);
    summaryPagerSyncTimer = null;
    summaryPagerSettleTimer = null;
    summaryPagerSyncing = false;
  }

  function syncSummaryPagerToJob(smooth = false) {
    const panel = el("scripting-backtest-summary-panel");
    if (!isMobile() || !summaryOpen || !job || !panel.classList.contains("summary-pager")) return;
    const page = panel.querySelector(
      `[data-backtest-summary-job-id="${CSS.escape(String(job.id))}"]`,
    );
    if (!page) return;
    const left = page.offsetLeft;
    if (Math.abs(panel.scrollLeft - left) < 2) return;
    summaryPagerSyncing = true;
    panel.scrollTo({ left, behavior: smooth ? "smooth" : "auto" });
    if (summaryPagerSyncTimer !== null) clearTimeout(summaryPagerSyncTimer);
    summaryPagerSyncTimer = window.setTimeout(() => {
      summaryPagerSyncTimer = null;
      summaryPagerSyncing = false;
    }, smooth ? 450 : 60);
  }

  function renderSummary() {
    const panel = el("scripting-backtest-summary-panel");
    const summary = job && job.summary;
    if (!summary) {
      clearSummaryPagerTimers();
      panel.classList.remove("summary-pager");
      delete panel.dataset.summarySignature;
      panel.innerHTML = '<div class="scripting-backtest-summary-empty">Summary is available after a completed backtest.</div>';
      return;
    }
    const values = summaryJobs();
    if (isMobile() && values.length > 1) {
      const signature = JSON.stringify(values.map((value) => [value.id, value.summary]));
      panel.classList.add("summary-pager");
      if (panel.dataset.summarySignature !== signature) {
        clearSummaryPagerTimers();
        panel.dataset.summarySignature = signature;
        panel.innerHTML = values.map((value) => (
          `<section class="scripting-backtest-summary-page" data-backtest-summary-job-id="${escapeHtml(value.id)}">`
          + summaryMarkup(value, true)
          + '</section>'
        )).join("");
      }
      window.requestAnimationFrame(() => syncSummaryPagerToJob(false));
      return;
    }
    clearSummaryPagerTimers();
    panel.classList.remove("summary-pager");
    delete panel.dataset.summarySignature;
    panel.innerHTML = `<div class="scripting-backtest-summary-page">${summaryMarkup(job)}</div>`;
  }

  function setSummaryOpen(open) {
    const available = Boolean(job && job.summary);
    summaryOpen = Boolean(open && available);
    const button = el("scripting-backtest-summary-toggle");
    button.classList.toggle("active", summaryOpen);
    button.setAttribute("aria-pressed", String(summaryOpen));
    button.setAttribute("aria-label", summaryOpen ? "Show log" : "Show summary");
    button.dataset.tooltip = summaryOpen ? "Log" : "Summary";
    el("scripting-backtest-summary-panel").classList.toggle("hidden", !summaryOpen);
    el("scripting-backtest-log").classList.toggle("hidden", summaryOpen);
    if (summaryOpen) setFindOpen(false);
    el("scripting-backtest-find-toggle").disabled = summaryOpen || !logText;
    if (summaryOpen) window.requestAnimationFrame(() => syncSummaryPagerToJob(false));
    window.requestAnimationFrame(updateLogJumpButton);
  }

  function isActiveJob(value = job) {
    return Boolean(value && ["queued", "preparing", "running", "stopping"].includes(value.status));
  }

  function updateActionState() {
    const button = el("scripting-backtest-run");
    const active = isActiveJob();
    const anyActive = jobs.some((item) => isActiveJob(item));
    const runnable = Boolean(
      context
      && context.path
      && context.revision
      && !context.dirty
      && context.validation
      && ["strategy", "indicator"].includes(context.validation.script_kind)
      && context.validation.runnable,
    );
    const inputState = collectInputValues(false);
    button.disabled = actionBusy || dataBusy || !runnable || !selectedDataPath
      || inputDataPath !== selectedDataPath || !validRange(false) || !inputState.ok;
    const stopButton = el("scripting-backtest-stop");
    stopButton.classList.toggle("hidden", !active);
    stopButton.disabled = actionBusy || dataBusy || !active || job.status === "stopping";
    el("scripting-backtest-data-button").disabled = actionBusy || dataBusy || dataRows.length === 0;
    el("scripting-backtest-data-manage").disabled = actionBusy || dataBusy || anyActive;
    ["from-date", "to-date", "from-time", "to-time"].forEach((name) => {
      el(`scripting-backtest-${name}`).disabled = actionBusy || dataBusy || dataRows.length === 0;
    });
    el("scripting-backtest-inputs").querySelectorAll("input, button").forEach((control) => {
      control.disabled = actionBusy || dataBusy;
    });
    el("scripting-backtest-inputs-reset").disabled = actionBusy || dataBusy || !inputMetadata.length;
    el("scripting-backtest-find-toggle").disabled = summaryOpen || !logText;
    const summaryButton = el("scripting-backtest-summary-toggle");
    summaryButton.disabled = !job || !job.summary;
    if (summaryButton.disabled && summaryOpen) setSummaryOpen(false);
    const deleteButton = el("scripting-backtest-delete");
    const canDelete = Boolean(
      jobs.length && jobs.every((item) => terminalStatuses.has(String(item.status || ""))),
    );
    deleteButton.closest(".scripting-backtest-clear-wrap").classList.toggle("hidden", !canDelete);
    deleteButton.disabled = actionBusy || dataBusy || anyActive || !canDelete;
    if (deleteButton.disabled) setClearConfirmationOpen(false);
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

  function inputOptionValues(descriptor) {
    if (String(descriptor.input_type || "").toLowerCase() === "bool") return [false, true];
    return Array.isArray(descriptor.options) && descriptor.options.length
      ? descriptor.options
      : null;
  }

  function inputValueLabel(value) {
    if (value === true) return "On";
    if (value === false) return "Off";
    return String(value ?? "");
  }

  function inputDetail(descriptor) {
    const details = [String(descriptor.id || "")];
    if (descriptor.minval !== null && descriptor.minval !== undefined) {
      details.push(`min ${descriptor.minval}`);
    }
    if (descriptor.maxval !== null && descriptor.maxval !== undefined) {
      details.push(`max ${descriptor.maxval}`);
    }
    if (descriptor.step !== null && descriptor.step !== undefined) {
      details.push(`step ${descriptor.step}`);
    }
    return details.join(" · ");
  }

  function inputPlaceholder(descriptor) {
    const type = String(descriptor.input_type || "").toLowerCase();
    if (type === "int") return "e.g. 10, 20, 30";
    if (type === "float") return "e.g. 0.5, 1.0, 1.5";
    if (type === "source") return "e.g. close, open";
    if (type === "color") return "e.g. #2962ff, #f23645";
    if (type === "string" || type === "enum") return "e.g. fast, slow";
    return "Comma-separated values";
  }

  function setInputsExpanded(expanded) {
    inputsExpanded = Boolean(expanded && inputMetadata.length);
    const section = el("scripting-backtest-inputs-section");
    const container = el("scripting-backtest-inputs");
    section.classList.toggle("inputs-collapsed", !inputsExpanded);
    container.inert = !inputsExpanded;
    container.setAttribute("aria-hidden", String(!inputsExpanded));
    el("scripting-backtest-inputs-toggle").setAttribute("aria-expanded", String(inputsExpanded));
  }

  function renderInputs() {
    const section = el("scripting-backtest-inputs-section");
    const container = el("scripting-backtest-inputs");
    section.classList.toggle("hidden", !inputMetadata.length);
    container.innerHTML = "";
    if (!inputMetadata.length) {
      setInputsExpanded(false);
      updateCombinationCount();
      return;
    }

    let previousGroup = null;
    inputMetadata.forEach((descriptor) => {
      const group = String(descriptor.group || "");
      if (group && group !== previousGroup) {
        const groupTitle = document.createElement("div");
        groupTitle.className = "scripting-backtest-input-group-title";
        groupTitle.textContent = group;
        container.appendChild(groupTitle);
      }
      previousGroup = group;

      const row = document.createElement("div");
      row.className = "scripting-backtest-input-row";
      row.dataset.inputId = String(descriptor.id || "");
      const label = document.createElement("label");
      label.className = "scripting-backtest-input-label";
      label.textContent = String(descriptor.title || descriptor.id || "Input");
      if (descriptor.tooltip) label.title = String(descriptor.tooltip);
      const detail = document.createElement("small");
      detail.textContent = inputDetail(descriptor);
      label.appendChild(detail);
      row.appendChild(label);

      const options = inputOptionValues(descriptor);
      if (options) {
        const optionList = document.createElement("div");
        optionList.className = "scripting-backtest-input-options";
        options.forEach((value) => {
          const button = document.createElement("button");
          button.className = "scripting-backtest-input-option";
          button.type = "button";
          button.dataset.inputValue = JSON.stringify(value);
          button.textContent = inputValueLabel(value);
          button.classList.toggle("selected", value === descriptor.value);
          button.setAttribute("aria-pressed", String(value === descriptor.value));
          optionList.appendChild(button);
        });
        row.appendChild(optionList);
      } else {
        const input = document.createElement("input");
        input.className = "scripting-backtest-input-values";
        input.type = "text";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.value = inputValueLabel(descriptor.value);
        input.placeholder = inputPlaceholder(descriptor);
        input.setAttribute("aria-label", `${descriptor.title || descriptor.id} values`);
        label.htmlFor = `scripting-backtest-input-${descriptor.id}`;
        input.id = `scripting-backtest-input-${descriptor.id}`;
        row.appendChild(input);
      }
      container.appendChild(row);
    });
    applyJobInputsToControls(job && job.inputs);
    setInputsExpanded(inputsExpanded);
    updateCombinationCount();
  }

  function setInputControlValues(values) {
    if (!values || typeof values !== "object") return;
    inputMetadata.forEach((descriptor) => {
      if (!Object.prototype.hasOwnProperty.call(values, descriptor.id)) return;
      const row = el("scripting-backtest-inputs").querySelector(
        `[data-input-id="${CSS.escape(String(descriptor.id))}"]`,
      );
      if (!row) return;
      const selectedValues = Array.isArray(values[descriptor.id])
        ? values[descriptor.id]
        : [values[descriptor.id]];
      const optionButtons = row.querySelectorAll("[data-input-value]");
      if (optionButtons.length) {
        optionButtons.forEach((button) => {
          let value;
          try { value = JSON.parse(button.dataset.inputValue); } catch { return; }
          const selected = selectedValues.some((candidate) => candidate === value);
          button.classList.toggle("selected", selected);
          button.setAttribute("aria-pressed", String(selected));
        });
      } else {
        const input = row.querySelector(".scripting-backtest-input-values");
        if (input) input.value = selectedValues.map(inputValueLabel).join(", ");
      }
    });
    updateCombinationCount();
  }

  function applyJobInputsToControls(values) {
    if (!values || !inputMetadata.length) return;
    setInputControlValues(values);
  }

  function resetInputControls() {
    const values = Object.fromEntries(
      inputMetadata.map((descriptor) => [descriptor.id, descriptor.value]),
    );
    setInputControlValues(values);
  }

  function parseInputToken(descriptor, token) {
    const type = String(descriptor.input_type || "").toLowerCase();
    if (type === "int") {
      if (!/^[+-]?\d+$/.test(token)) throw new Error(`${descriptor.title || descriptor.id} requires integers.`);
      return Number(token);
    }
    if (type === "float") {
      const value = Number(token);
      if (!Number.isFinite(value)) throw new Error(`${descriptor.title || descriptor.id} requires numbers.`);
      return value;
    }
    return token;
  }

  function validateInputValue(descriptor, value) {
    if (typeof value === "number") {
      if (descriptor.minval !== null && descriptor.minval !== undefined && value < Number(descriptor.minval)) {
        throw new Error(`${descriptor.title || descriptor.id} must be at least ${descriptor.minval}.`);
      }
      if (descriptor.maxval !== null && descriptor.maxval !== undefined && value > Number(descriptor.maxval)) {
        throw new Error(`${descriptor.title || descriptor.id} must be at most ${descriptor.maxval}.`);
      }
    }
    return value;
  }

  function collectInputValues(showError = true) {
    const values = {};
    let count = 1;
    let message = "";
    try {
      inputMetadata.forEach((descriptor) => {
        const row = el("scripting-backtest-inputs").querySelector(
          `[data-input-id="${CSS.escape(String(descriptor.id))}"]`,
        );
        if (!row) throw new Error(`Input ${descriptor.id} is unavailable.`);
        const optionButtons = Array.from(row.querySelectorAll("[data-input-value].selected"));
        let selected;
        if (row.querySelector("[data-input-value]")) {
          selected = optionButtons.map((button) => JSON.parse(button.dataset.inputValue));
        } else {
          const raw = String(row.querySelector(".scripting-backtest-input-values")?.value || "");
          const tokens = raw.split(",").map((value) => value.trim()).filter(Boolean);
          selected = tokens.map((token) => parseInputToken(descriptor, token));
        }
        selected = selected.map((value) => validateInputValue(descriptor, value));
        const unique = [];
        const seen = new Set();
        selected.forEach((value) => {
          const marker = JSON.stringify(value);
          if (seen.has(marker)) return;
          seen.add(marker);
          unique.push(value);
        });
        if (!unique.length) throw new Error(`${descriptor.title || descriptor.id} requires a value.`);
        values[descriptor.id] = unique;
        count *= unique.length;
        if (count > maxInputVariants) {
          throw new Error(`Input combinations exceed the ${maxInputVariants} run limit.`);
        }
      });
    } catch (error) {
      message = error.message || "Invalid input values.";
    }
    if (showError && message) setError(message);
    return { ok: !message, values, count: message ? 0 : count, error: message };
  }

  function updateCombinationCount() {
    const state = collectInputValues(false);
    const node = el("scripting-backtest-combinations");
    if (!inputMetadata.length) node.textContent = "";
    else if (!state.ok) node.textContent = "Invalid values";
    else node.textContent = `${state.count} run${state.count === 1 ? "" : "s"} · ${maxConcurrentBacktests} parallel`;
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

  function renderSelectedDataFilename() {
    const row = selectedData();
    const filename = String(row && row.path || "").split(/[\\/]/).pop();
    el("scripting-backtest-data-filename").textContent = filename;
    el("scripting-backtest-data-filename-wrap").classList.toggle("has-value", Boolean(filename));
  }

  function selectDataPath(path, rangeSource = "data") {
    const row = dataRows.find((item) => item.path === path);
    if (!row) return false;
    const changed = selectedDataPath !== row.path;
    selectedDataPath = row.path;
    renderSelectedDataFilename();
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
    if (changed || inputDataPath !== row.path) void loadInputs(row.path, contextSequence);
    else if (rangeSource === "job") applyJobInputsToControls(job && job.inputs);
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
      renderSelectedDataFilename();
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
    const updateButton = el("scripting-backtest-data-update");
    const updating = dataBusy && action === "update";
    updateButton.disabled = dataBusy || !canUpdate;
    updateButton.classList.toggle("syncing", updating);
    const downloadButton = el("scripting-backtest-data-download");
    const downloading = dataBusy && action === "download";
    downloadButton.disabled = dataBusy;
    downloadButton.querySelector(".scripting-backtest-download-icon").classList.toggle("hidden", downloading);
    downloadButton.querySelector(".scripting-backtest-hourglass-icon").classList.toggle("hidden", !downloading);
    el("scripting-backtest-data-exchange-button").disabled = dataBusy;
    ["symbol", "timeframe", "history-since", "file-name"].forEach((name) => {
      el(`scripting-backtest-data-${name}`).disabled = dataBusy;
    });
    updateButton.querySelector("span").textContent = updating ? "Updating..." : "Update to now";
    downloadButton.querySelector("span").textContent = downloading ? "Downloading..." : "Download";
    el("scripting-backtest-data-manage").classList.toggle("syncing", dataBusy);
    updateActionState();
  }

  function openDataManager() {
    if (actionBusy || dataBusy || jobs.some((item) => isActiveJob(item))) return;
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
    const sequence = contextSequence;
    closeDataOptions();
    setDataDeleteError();
    setDataDeleteBusy(true);
    try {
      await api(`/api/scripting/backtest/data?data_path=${encodeURIComponent(path)}`, {
        method: "DELETE",
      });
      if (sequence !== contextSequence || !isOpen()) return;
      const preferred = selectedDataPath === path ? "" : selectedDataPath;
      closeDataDelete(true);
      await loadData(preferred, sequence);
      setError();
    } catch (error) {
      if (sequence !== contextSequence || !isOpen()) return;
      setDataDeleteError(error.message || "OHLCV data could not be deleted.");
      setDataDeleteBusy(false);
    }
  }

  async function syncBacktestData(action) {
    if (dataBusy || actionBusy || isActiveJob()) return;
    const sequence = contextSequence;
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
      if (sequence !== contextSequence || !isOpen()) return;
      setDataBusy(false);
      await loadData(String(result.data_path || ""), sequence);
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
      if (sequence !== contextSequence || !isOpen()) return;
      setDataMessage(error.message || "OHLCV data operation failed.", true);
      if (previousJob) setStatus(previousJob.status, statusSummary(previousJob));
      else setStatus("ready", `${dataRows.length} data source${dataRows.length === 1 ? "" : "s"}`);
      setDataBusy(false);
    }
  }

  function updateLogJumpButton() {
    const container = el("scripting-backtest-log");
    const button = el("scripting-backtest-log-jump");
    const scrollable = !summaryOpen && Boolean(logText)
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

  function jobLabel(value) {
    const entries = Object.entries(value && value.inputs || {});
    if (!entries.length) return "Default";
    const visible = entries.slice(0, 3).map(([name, inputValue]) => `${name}=${inputValueLabel(inputValue)}`);
    if (entries.length > visible.length) visible.push(`+${entries.length - visible.length}`);
    return visible.join(" · ");
  }

  function renderJobs() {
    const container = el("scripting-backtest-jobs");
    container.classList.toggle("hidden", jobs.length < 2);
    container.innerHTML = jobs.map((value) => (
      `<button type="button" class="scripting-backtest-job${job && job.id === value.id ? " selected" : ""}" `
      + `data-backtest-job-id="${escapeHtml(value.id)}" data-status="${escapeHtml(value.status)}" `
      + `role="option" aria-selected="${job && job.id === value.id ? "true" : "false"}" `
      + `title="${escapeHtml(jobLabel(value))}">`
      + '<span class="scripting-backtest-job-status" aria-hidden="true"></span>'
      + `<span class="scripting-backtest-job-label">${escapeHtml(jobLabel(value))}</span></button>`
    )).join("");
  }

  function mergeJob(value) {
    if (!value || !value.id) return;
    const index = jobs.findIndex((item) => item.id === value.id);
    if (index >= 0) jobs[index] = value;
    else jobs.unshift(value);
    jobs.sort((left, right) => Number(right.created_at || 0) - Number(left.created_at || 0));
  }

  function renderJob(value, options = {}) {
    const previousJobId = job && job.id;
    job = value || null;
    if (job) mergeJob(job);
    const status = job ? String(job.status || "ready") : "ready";
    setStatus(status, statusSummary(job));
    setError(job && job.status === "failed" ? job.error || "Backtest failed." : "");
    if (!options.preserveSummaryPager) renderSummary();
    if (summaryOpen && !(job && job.summary)) setSummaryOpen(false);
    if (job && job.data_path) {
      selectDataPath(job.data_path, previousJobId === job.id ? "none" : "job");
    }
    if (job && previousJobId !== job.id) applyJobInputsToControls(job.inputs);
    renderJobs();
    scheduleJobsRefresh();
    updateActionState();
  }

  async function selectJob(jobId, sequence = contextSequence, options = {}) {
    const selected = jobs.find((item) => item.id === jobId);
    if (!selected || (job && job.id === selected.id)) return;
    closeSocket();
    logText = "";
    logOffset = 0;
    findMatches = [];
    findIndex = -1;
    renderLog({ follow: true });
    renderJob(selected, options);
    await connectJob(selected.id, true, sequence);
  }

  function handleSummaryPagerScroll() {
    const panel = el("scripting-backtest-summary-panel");
    if (
      !isMobile()
      || !summaryOpen
      || summaryPagerSyncing
      || !panel.classList.contains("summary-pager")
    ) return;
    if (summaryPagerSettleTimer !== null) clearTimeout(summaryPagerSettleTimer);
    summaryPagerSettleTimer = window.setTimeout(() => {
      summaryPagerSettleTimer = null;
      const pages = Array.from(panel.querySelectorAll("[data-backtest-summary-job-id]"));
      if (!pages.length) return;
      const selected = pages.reduce((closest, page) => (
        Math.abs(page.offsetLeft - panel.scrollLeft) < Math.abs(closest.offsetLeft - panel.scrollLeft)
          ? page
          : closest
      ), pages[0]);
      const jobId = String(selected.dataset.backtestSummaryJobId || "");
      if (!jobId || (job && job.id === jobId)) return;
      void selectJob(jobId, contextSequence, { preserveSummaryPager: true });
    }, 90);
  }

  function clearJobsRefresh() {
    if (jobsRefreshTimer !== null) {
      clearTimeout(jobsRefreshTimer);
      jobsRefreshTimer = null;
    }
  }

  function scheduleJobsRefresh() {
    if (!isOpen() || !jobs.some((item) => isActiveJob(item))) {
      clearJobsRefresh();
      return;
    }
    if (jobsRefreshTimer !== null) return;
    const sequence = contextSequence;
    jobsRefreshTimer = window.setTimeout(async () => {
      jobsRefreshTimer = null;
      await loadJobs(sequence, false);
    }, 1000);
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

  async function fillLog(jobId, reset = false, sequence = contextSequence) {
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
      if (sequence !== contextSequence || !isOpen()) return false;
      if (Number(payload.offset) !== logOffset && logOffset > 0) {
        logText = "";
      }
      logOffset = Number(payload.next_offset || 0);
      appendLog(payload.text);
      if (payload.eof) break;
    }
    return true;
  }

  async function connectJob(jobId, reset = false, sequence = contextSequence) {
    closeSocket();
    try {
      const current = await fillLog(jobId, reset, sequence);
      if (!current) return;
    } catch (error) {
      if (sequence === contextSequence && isOpen()) {
        setError(error.message || "Backtest log could not be loaded.");
      }
    }
    if (
      sequence !== contextSequence
      || !isOpen()
      || !job
      || job.id !== jobId
      || terminalStatuses.has(job.status)
    ) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/ws/scripting/backtests/${encodeURIComponent(jobId)}?offset=${logOffset}`,
    );
    socket = ws;
    ws.onmessage = (event) => {
      if (sequence !== contextSequence || socket !== ws) return;
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
      if (
        sequence !== contextSequence
        || !isOpen()
        || !job
        || job.id !== jobId
        || terminalStatuses.has(job.status)
      ) return;
      reconnectTimer = window.setTimeout(async () => {
        reconnectTimer = null;
        try {
          const status = await api(`/api/scripting/backtests/${encodeURIComponent(jobId)}`, { cache: "no-store" });
          if (sequence !== contextSequence || !isOpen()) return;
          renderJob(status);
          await connectJob(jobId, false, sequence);
        } catch (error) {
          if (sequence === contextSequence && isOpen()) {
            setError(error.message || "Backtest status could not be refreshed.");
          }
        }
      }, 1000);
    };
  }

  async function loadJobs(sequence = contextSequence, connectSelected = true) {
    const scriptPath = context && context.path;
    if (!scriptPath) return;
    try {
      const payload = await api(
        `/api/scripting/backtests?script_path=${encodeURIComponent(scriptPath)}`,
        { cache: "no-store" },
      );
      if (
        sequence !== contextSequence
        || !isOpen()
        || !context
        || context.path !== scriptPath
      ) return;
      const selectedId = job && job.id;
      jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
      const selected = jobs.find((item) => item.id === selectedId) || jobs[0] || null;
      if (selected) {
        const changed = !job || job.id !== selected.id;
        renderJob(selected);
        if (connectSelected && changed) await connectJob(selected.id, true, sequence);
      } else {
        job = null;
        renderJobs();
        updateActionState();
      }
      scheduleJobsRefresh();
    } catch (error) {
      if (sequence === contextSequence && isOpen()) {
        setError(error.message || "Backtest status could not be loaded.");
      }
    }
  }

  async function loadInputs(dataPath = selectedDataPath, sequence = contextSequence) {
    const scriptPath = context && context.path;
    const revision = context && context.revision;
    if (!scriptPath || !revision || !dataPath) return;
    const request = ++inputRequestSequence;
    try {
      const query = new URLSearchParams({
        script_path: scriptPath,
        base_revision: revision,
        data_path: dataPath,
      });
      const payload = await api(
        `/api/scripting/backtest/inputs?${query.toString()}`,
        { cache: "no-store" },
      );
      if (
        sequence !== contextSequence
        || request !== inputRequestSequence
        || !isOpen()
        || !context
        || context.path !== scriptPath
        || selectedDataPath !== dataPath
      ) return;
      inputMetadata = Array.isArray(payload.inputs) ? payload.inputs : [];
      inputDataPath = dataPath;
      maxConcurrentBacktests = Number(payload.max_concurrent) || 10;
      maxInputVariants = Number(payload.max_variants) || 1000;
      renderInputs();
      updateActionState();
    } catch (error) {
      if (sequence !== contextSequence || request !== inputRequestSequence || !isOpen()) return;
      inputMetadata = [];
      inputDataPath = "";
      renderInputs();
      setError(error.message || "Script inputs could not be loaded.");
    }
  }

  async function loadData(preferredPath = "", sequence = contextSequence) {
    const scriptPath = context && context.path;
    if (!scriptPath) return;
    const seq = ++requestSequence;
    setStatus("loading", "Reading OHLCV metadata");
    try {
      const payload = await api(
        `/api/scripting/backtest/data?script_path=${encodeURIComponent(scriptPath)}`,
        { cache: "no-store" },
      );
      if (
        sequence !== contextSequence
        || seq !== requestSequence
        || !isOpen()
        || !context
        || context.path !== scriptPath
      ) return;
      renderData(payload, preferredPath);
      if (!job) setStatus("ready", `${dataRows.length} data source${dataRows.length === 1 ? "" : "s"}`);
      if (!dataRows.length) setError("No OHLCV data with symbol metadata is available.");
    } catch (error) {
      if (sequence !== contextSequence || seq !== requestSequence || !isOpen()) return;
      dataRows = [];
      selectedDataPath = "";
      renderData({ data: [] });
      setStatus("failed", "");
      setError(error.message || "OHLCV data could not be loaded.");
    }
  }

  async function deleteBacktestResults() {
    if (
      !jobs.length
      || jobs.some((item) => !terminalStatuses.has(String(item.status || "")))
      || actionBusy
      || dataBusy
    ) return;
    const sequence = contextSequence;
    setClearConfirmationOpen(false);
    const previousJob = job;
    actionBusy = true;
    closeSocket();
    updateActionState();
    try {
      await api(`/api/scripting/backtests?script_path=${encodeURIComponent(context.path)}`, {
        method: "DELETE",
      });
      if (sequence !== contextSequence || !isOpen()) return;
      job = null;
      jobs = [];
      renderJobs();
      setSummaryOpen(false);
      renderSummary();
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
      if (sequence !== contextSequence || !isOpen()) return;
      job = previousJob;
      setStatus(previousJob.status, statusSummary(previousJob));
      setError(error.message || "Backtest results could not be deleted.");
    } finally {
      if (sequence === contextSequence) {
        actionBusy = false;
        updateActionState();
      }
    }
  }

  async function runBacktests() {
    if (actionBusy || dataBusy) return;
    const sequence = contextSequence;
    setSummaryOpen(false);
    const previousJob = job;
    actionBusy = true;
    updateActionState();
    try {
      if (context.dirty) throw new Error("Save before backtesting.");
      if (!validRange()) return;
      const inputState = collectInputValues();
      if (!inputState.ok) return;
      setError();
      setStatus("preparing", `Starting ${inputState.count} run${inputState.count === 1 ? "" : "s"}`);
      const started = await api("/api/scripting/backtests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script_path: context.path,
          base_revision: context.revision,
          data_path: selectedDataPath,
          time_from: rangeIso("from"),
          time_to: rangeIso("to"),
          input_values: inputState.values,
        }),
      });
      if (sequence !== contextSequence || !isOpen()) return;
      const startedJobs = Array.isArray(started.jobs) ? started.jobs : [];
      if (!startedJobs.length) throw new Error("No backtest runs were started.");
      maxConcurrentBacktests = Number(started.max_concurrent) || maxConcurrentBacktests;
      const startedIds = new Set(startedJobs.map((value) => value.id));
      jobs = [...startedJobs, ...jobs.filter((value) => !startedIds.has(value.id))];
      closeSocket();
      logText = "";
      logOffset = 0;
      renderLog({ follow: true });
      renderJob(startedJobs[0]);
      await connectJob(startedJobs[0].id, true, sequence);
    } catch (error) {
      if (sequence !== contextSequence || !isOpen()) return;
      if (previousJob) setStatus(previousJob.status, statusSummary(previousJob));
      else setStatus("ready", `${dataRows.length} data source${dataRows.length === 1 ? "" : "s"}`);
      if (error && error.code === "revision_conflict") {
        context = { ...context, dirty: true };
      }
      setError(error.message || "Backtest request failed.");
    } finally {
      if (sequence === contextSequence) {
        actionBusy = false;
        updateActionState();
      }
    }
  }

  async function stopSelectedBacktest() {
    if (!isActiveJob() || actionBusy || dataBusy) return;
    const sequence = contextSequence;
    actionBusy = true;
    updateActionState();
    try {
      setStatus("stopping", "Stopping selected worker");
      const stopped = await api(
        `/api/scripting/backtests/${encodeURIComponent(job.id)}/stop`,
        { method: "POST" },
      );
      if (sequence !== contextSequence || !isOpen()) return;
      renderJob(stopped);
      await fillLog(job.id, false, sequence);
      await loadJobs(sequence, false);
    } catch (error) {
      if (sequence === contextSequence && isOpen()) {
        setError(error.message || "Backtest could not be stopped.");
      }
    } finally {
      if (sequence === contextSequence) {
        actionBusy = false;
        updateActionState();
      }
    }
  }

  function desktopGeometry() {
    try {
      const value = JSON.parse(localStorage.getItem(desktopGeometryKey) || "{}");
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  }

  function saveDesktopGeometry() {
    if (isMobile() || !isOpen()) return;
    const box = el("scripting-backtest-modal").querySelector(".scripting-backtest-box");
    const rect = box.getBoundingClientRect();
    try {
      localStorage.setItem(desktopGeometryKey, JSON.stringify({
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      }));
    } catch {}
  }

  function clampDesktopGeometry(left, top) {
    const box = el("scripting-backtest-modal").querySelector(".scripting-backtest-box");
    const rect = box.getBoundingClientRect();
    return {
      left: Math.min(
        Math.max(left, desktopWindowMargin),
        Math.max(desktopWindowMargin, window.innerWidth - rect.width - desktopWindowMargin),
      ),
      top: Math.min(
        Math.max(top, desktopWindowMargin),
        Math.max(desktopWindowMargin, window.innerHeight - rect.height - desktopWindowMargin),
      ),
    };
  }

  function applyDesktopGeometry() {
    const box = el("scripting-backtest-modal").querySelector(".scripting-backtest-box");
    const stored = desktopGeometry();
    const maxWidth = Math.max(1, window.innerWidth - desktopWindowMargin * 2);
    const maxHeight = Math.max(1, window.innerHeight - desktopWindowMargin * 2);
    const minWidth = Math.min(desktopDefaultWidth, maxWidth) / 2;
    const minHeight = Math.min(desktopDefaultHeight, maxHeight) / 2;
    const requestedWidth = Number.isFinite(Number(stored.width))
      ? Number(stored.width)
      : Math.min(desktopDefaultWidth, maxWidth);
    const requestedHeight = Number.isFinite(Number(stored.height))
      ? Number(stored.height)
      : Math.min(desktopDefaultHeight, maxHeight);
    box.style.width = `${Math.min(Math.max(requestedWidth, minWidth), maxWidth)}px`;
    box.style.height = `${Math.min(Math.max(requestedHeight, minHeight), maxHeight)}px`;
    const rect = box.getBoundingClientRect();
    const cascadeOffset = (desktopWindowOffset % 8) * 28;
    const defaultLeft = window.innerWidth - rect.width - 24 - cascadeOffset;
    const defaultTop = window.innerHeight - rect.height - 24 - cascadeOffset;
    const position = clampDesktopGeometry(
      Number.isFinite(Number(stored.left)) ? Number(stored.left) : defaultLeft,
      Number.isFinite(Number(stored.top)) ? Number(stored.top) : defaultTop,
    );
    box.style.right = "auto";
    box.style.bottom = "auto";
    box.style.left = `${position.left}px`;
    box.style.top = `${position.top}px`;
    box.classList.toggle("compact", rect.width < 650);
  }

  function applyBacktestLayout() {
    const box = el("scripting-backtest-modal").querySelector(".scripting-backtest-box");
    if (isMobile()) {
      box.style.removeProperty("left");
      box.style.removeProperty("top");
      box.style.removeProperty("right");
      box.style.removeProperty("bottom");
      box.style.removeProperty("width");
      box.style.removeProperty("height");
      box.classList.remove("compact");
      box.setAttribute("aria-modal", "true");
      return;
    }
    box.setAttribute("aria-modal", "false");
    applyDesktopGeometry();
  }

  function beginDesktopDrag(event) {
    if (
      isMobile()
      || !event.isPrimary
      || event.button !== 0
      || !isOpen()
    ) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target && target.closest("button, input, a")) return;
    activate();
    const box = el("scripting-backtest-modal").querySelector(".scripting-backtest-box");
    const rect = box.getBoundingClientRect();
    desktopDrag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      left: rect.left,
      top: rect.top,
    };
    el("scripting-backtest-modal").classList.add("window-dragging");
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch {}
    event.preventDefault();
  }

  function moveDesktopDrag(event) {
    if (!desktopDrag || event.pointerId !== desktopDrag.pointerId) return;
    event.preventDefault();
    const position = clampDesktopGeometry(
      desktopDrag.left + event.clientX - desktopDrag.startX,
      desktopDrag.top + event.clientY - desktopDrag.startY,
    );
    const box = el("scripting-backtest-modal").querySelector(".scripting-backtest-box");
    box.style.left = `${position.left}px`;
    box.style.top = `${position.top}px`;
  }

  function endDesktopDrag(event) {
    if (!desktopDrag || event.pointerId !== desktopDrag.pointerId) return;
    const pointerId = desktopDrag.pointerId;
    desktopDrag = null;
    el("scripting-backtest-modal").classList.remove("window-dragging");
    try { event.currentTarget.releasePointerCapture(pointerId); } catch {}
    saveDesktopGeometry();
  }

  function beginDesktopResize(event) {
    if (
      isMobile()
      || !event.isPrimary
      || event.button !== 0
      || !isOpen()
    ) return;
    activate();
    const box = el("scripting-backtest-modal").querySelector(".scripting-backtest-box");
    const rect = box.getBoundingClientRect();
    desktopResize = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      width: rect.width,
      height: rect.height,
    };
    el("scripting-backtest-modal").classList.add("window-resizing");
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch {}
    event.preventDefault();
    event.stopPropagation();
  }

  function moveDesktopResize(event) {
    if (!desktopResize || event.pointerId !== desktopResize.pointerId) return;
    event.preventDefault();
    const box = el("scripting-backtest-modal").querySelector(".scripting-backtest-box");
    const rect = box.getBoundingClientRect();
    const maxWidth = Math.max(1, window.innerWidth - rect.left - desktopWindowMargin);
    const maxHeight = Math.max(1, window.innerHeight - rect.top - desktopWindowMargin);
    const viewportWidth = Math.max(1, window.innerWidth - desktopWindowMargin * 2);
    const viewportHeight = Math.max(1, window.innerHeight - desktopWindowMargin * 2);
    const minWidth = Math.min(
      Math.min(desktopDefaultWidth, viewportWidth) / 2,
      maxWidth,
    );
    const minHeight = Math.min(
      Math.min(desktopDefaultHeight, viewportHeight) / 2,
      maxHeight,
    );
    const width = Math.min(
      Math.max(desktopResize.width + event.clientX - desktopResize.startX, minWidth),
      maxWidth,
    );
    const height = Math.min(
      Math.max(desktopResize.height + event.clientY - desktopResize.startY, minHeight),
      maxHeight,
    );
    box.style.width = `${width}px`;
    box.style.height = `${height}px`;
    box.classList.toggle("compact", width < 650);
  }

  function endDesktopResize(event) {
    if (!desktopResize || event.pointerId !== desktopResize.pointerId) return;
    const pointerId = desktopResize.pointerId;
    desktopResize = null;
    el("scripting-backtest-modal").classList.remove("window-resizing");
    try { event.currentTarget.releasePointerCapture(pointerId); } catch {}
    saveDesktopGeometry();
  }

  function open(nextContext) {
    activate();
    const sequence = ++contextSequence;
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
    clearJobsRefresh();
    closeSocket();
    closeCalendars();
    closeDataOptions();
    setClearConfirmationOpen(false);
    job = null;
    jobs = [];
    inputMetadata = [];
    inputDataPath = "";
    inputsExpanded = false;
    inputRequestSequence += 1;
    logText = "";
    logOffset = 0;
    findQuery = "";
    findMatches = [];
    findIndex = -1;
    dataRows = [];
    selectedDataPath = "";
    renderSelectedDataFilename();
    renderInputs();
    renderJobs();
    summaryOpen = false;
    clearSummaryPagerTimers();
    actionBusy = false;
    dataBusy = false;
    closeDataManager(true);
    el("scripting-backtest-data-manage").classList.remove("syncing");
    el("scripting-backtest-path").textContent = context.path;
    el("scripting-backtest-find-input").value = "";
    setFindOpen(false);
    renderLog({ follow: true });
    renderSummary();
    setSummaryOpen(false);
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
    desktopDrag = null;
    desktopResize = null;
    modal.classList.remove(
      "hidden",
      "closing",
      "dragging",
      "window-dragging",
      "window-resizing",
      "swipe-closing",
      "opening",
    );
    modal.classList.add("opening");
    box.style.removeProperty("transform");
    box.style.removeProperty("transition");
    modal.setAttribute("aria-hidden", "false");
    applyBacktestLayout();
    setStatus("loading", "");
    updateActionState();
    openTimer = window.setTimeout(finishOpening, 230);
    void Promise.all([loadData("", sequence), loadJobs(sequence)]);
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
    modal.classList.remove(
      "closing",
      "dragging",
      "window-dragging",
      "window-resizing",
      "swipe-closing",
      "opening",
    );
    box.style.removeProperty("transform");
    box.style.removeProperty("transition");
    modal.setAttribute("aria-hidden", "true");
    closeTimer = null;
    openTimer = null;
    if (typeof instanceOptions.onClosed === "function") instanceOptions.onClosed();
  }

  function close(options = {}) {
    if (!isOpen() || closeTimer !== null || dataBusy) return;
    contextSequence += 1;
    clearJobsRefresh();
    clearSummaryPagerTimers();
    inputRequestSequence += 1;
    finishOpening();
    closeSocket();
    closeCalendars();
    closeDataOptions();
    setClearConfirmationOpen(false);
    closeDataDelete(true);
    closeDataManager(true);
    setFindOpen(false);
    const modal = el("scripting-backtest-modal");
    const box = modal.querySelector(".scripting-backtest-box");
    sheetDrag = null;
    desktopDrag = null;
    desktopResize = null;
    modal.classList.remove("dragging", "window-dragging", "window-resizing");
    if (isMobile()) {
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
    if (!isMobile() || !event.isPrimary || !isOpen()) return;
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
    el("scripting-backtest-modal").addEventListener("pointerdown", activate, { capture: true });
    const sheetHeader = el("scripting-backtest-modal").querySelector(".scripting-backtest-header");
    const resizeHandle = el("scripting-backtest-resize");
    sheetHeader.addEventListener("pointerdown", beginSheetDrag);
    sheetHeader.addEventListener("pointermove", moveSheetDrag, { passive: false });
    sheetHeader.addEventListener("pointerup", endSheetDrag);
    sheetHeader.addEventListener("pointercancel", (event) => endSheetDrag(event, true));
    sheetHeader.addEventListener("pointerdown", beginDesktopDrag);
    sheetHeader.addEventListener("pointermove", moveDesktopDrag, { passive: false });
    sheetHeader.addEventListener("pointerup", endDesktopDrag);
    sheetHeader.addEventListener("pointercancel", endDesktopDrag);
    resizeHandle.addEventListener("pointerdown", beginDesktopResize);
    resizeHandle.addEventListener("pointermove", moveDesktopResize, { passive: false });
    resizeHandle.addEventListener("pointerup", endDesktopResize);
    resizeHandle.addEventListener("pointercancel", endDesktopResize);
    el("scripting-backtest-modal").addEventListener("click", (event) => {
      if (isMobile() && event.target === el("scripting-backtest-modal")) close();
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
    el("scripting-backtest-run").addEventListener("click", () => {
      void runBacktests();
    });
    el("scripting-backtest-stop").addEventListener("click", () => {
      void stopSelectedBacktest();
    });
    el("scripting-backtest-inputs-toggle").addEventListener("click", () => {
      setInputsExpanded(!inputsExpanded);
    });
    el("scripting-backtest-inputs-reset").addEventListener("click", resetInputControls);
    el("scripting-backtest-inputs").addEventListener("input", () => {
      updateCombinationCount();
      updateActionState();
    });
    el("scripting-backtest-inputs").addEventListener("click", (event) => {
      const option = event.target.closest("[data-input-value]");
      if (!option) return;
      const selected = !option.classList.contains("selected");
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-pressed", String(selected));
      updateCombinationCount();
      updateActionState();
    });
    el("scripting-backtest-jobs").addEventListener("click", (event) => {
      const option = event.target.closest("[data-backtest-job-id]");
      if (option) void selectJob(String(option.dataset.backtestJobId || ""));
    });
    [
      "scripting-backtest-run",
      "scripting-backtest-stop",
      "scripting-backtest-inputs-toggle",
      "scripting-backtest-inputs-reset",
    ].forEach((id) => {
      el(id).addEventListener("dblclick", (event) => event.preventDefault());
    });
    el("scripting-backtest-summary-toggle").addEventListener("click", () => {
      setSummaryOpen(!summaryOpen);
    });
    el("scripting-backtest-summary-toggle").addEventListener("dblclick", (event) => {
      event.preventDefault();
    });
    el("scripting-backtest-summary-panel").addEventListener(
      "scroll",
      handleSummaryPagerScroll,
      { passive: true },
    );
    el("scripting-backtest-delete").addEventListener("click", (event) => {
      event.stopPropagation();
      setClearConfirmationOpen(!isClearConfirmationOpen());
    });
    el("scripting-backtest-clear-cancel").addEventListener("click", () => {
      setClearConfirmationOpen(false);
      el("scripting-backtest-delete").focus({ preventScroll: true });
    });
    el("scripting-backtest-clear-confirm").addEventListener("click", () => {
      void deleteBacktestResults();
    });
    el("scripting-backtest-log").addEventListener("scroll", updateLogJumpButton, { passive: true });
    el("scripting-backtest-log-jump").addEventListener("click", jumpBacktestLog);
    el("scripting-backtest-log-jump").addEventListener("dblclick", (event) => {
      event.preventDefault();
    });
    el("scripting-backtest-find-toggle").addEventListener("click", () => {
      setSummaryOpen(false);
      setFindOpen(true);
    });
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
      if (!event.target.closest(".scripting-backtest-clear-wrap")) setClearConfirmationOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (!isOpen()) return;
      if (
        typeof instanceOptions.isActive === "function"
        && !instanceOptions.isActive()
      ) return;
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
      if (isClearConfirmationOpen()) {
        setClearConfirmationOpen(false);
        el("scripting-backtest-delete").focus({ preventScroll: true });
      } else if (!el("scripting-backtest-find").classList.contains("hidden")) setFindOpen(false);
      else if (!el("scripting-backtest-data-options").classList.contains("hidden")) closeDataOptions();
      else close();
    }, true);
    window.addEventListener("resize", () => {
      if (!isOpen()) return;
      applyBacktestLayout();
      renderSummary();
    });
    initialized = true;
  }

  function setLayer(baseLayer) {
    const layer = Math.max(112, Number(baseLayer) || 112);
    el("scripting-backtest-modal").style.zIndex = String(layer);
    el("scripting-backtest-data-modal").style.zIndex = String(layer + 2);
    el("scripting-backtest-data-tooltip").style.zIndex = String(layer + 3);
    el("scripting-backtest-data-delete-modal").style.zIndex = String(layer + 4);
  }

  return { init, open, close, isOpen, setLayer };
  }

  const templateElementIds = [
    "scripting-backtest-modal",
    "scripting-backtest-data-modal",
    "scripting-backtest-data-tooltip",
    "scripting-backtest-data-delete-modal",
  ];
  const desktopInstances = new Map();
  let managerInitialized = false;
  let managerApi = null;
  let managerMobileQuery = null;
  let mobileInstance = null;
  let templateNodes = [];
  let desktopInstanceSequence = 0;
  let activeDesktopInstance = null;
  let closingAll = false;

  function cloneTemplateNode(template, suffix) {
    const clone = template.cloneNode(true);
    const identified = [clone, ...clone.querySelectorAll("[id]")].filter((node) => node.id);
    const idMap = new Map();
    identified.forEach((node) => {
      const originalId = node.id;
      const instanceId = `${originalId}-${suffix}`;
      idMap.set(originalId, instanceId);
      node.dataset.backtestElement = originalId;
      node.id = instanceId;
    });
    const referenceNodes = [
      clone,
      ...clone.querySelectorAll("[aria-controls], [aria-labelledby], [for]"),
    ];
    referenceNodes.forEach((node) => {
      ["aria-controls", "aria-labelledby", "for"].forEach((attribute) => {
        const value = node.getAttribute(attribute);
        if (!value) return;
        node.setAttribute(
          attribute,
          value.split(/\s+/).map((item) => idMap.get(item) || item).join(" "),
        );
      });
    });
    return clone;
  }

  function activateDesktop(instance) {
    activeDesktopInstance = instance;
    instance.setLayer(window.PyneFloatingLayerManager.next());
  }

  function handleDesktopClosed(instance) {
    if (closingAll || activeDesktopInstance !== instance) return;
    activeDesktopInstance = [...desktopInstances.values()]
      .reverse()
      .find((candidate) => candidate !== instance && candidate.isOpen()) || null;
    if (activeDesktopInstance) activateDesktop(activeDesktopInstance);
  }

  function createDesktopInstance(path) {
    const suffix = `desktop-${++desktopInstanceSequence}`;
    const host = document.createElement("div");
    host.className = "scripting-backtest-instance";
    host.dataset.backtestPath = path;
    templateNodes.forEach((template) => host.appendChild(cloneTemplateNode(template, suffix)));
    document.body.appendChild(host);

    let instance = null;
    instance = createBacktestInstance(host, {
      instanceKey: path,
      desktopOffset: desktopInstanceSequence - 1,
      mobile: false,
      activate: () => activateDesktop(instance),
      isActive: () => activeDesktopInstance === instance,
      onClosed: () => handleDesktopClosed(instance),
    });
    instance.init({ api: managerApi, mobileQuery: managerMobileQuery });
    desktopInstances.set(path, instance);
    return instance;
  }

  function init(options = {}) {
    if (managerInitialized) return;
    managerApi = options.api;
    managerMobileQuery = options.mobileQuery;
    if (typeof managerApi !== "function" || !managerMobileQuery) {
      throw new Error("Backtest initialization dependencies are unavailable");
    }
    templateNodes = templateElementIds.map((id) => {
      const node = document.getElementById(id);
      if (!node) throw new Error(`Backtest template is unavailable: ${id}`);
      return node.cloneNode(true);
    });
    mobileInstance = createBacktestInstance(document, {
      instanceKey: "mobile",
      mobile: true,
    });
    mobileInstance.init({ api: managerApi, mobileQuery: managerMobileQuery });
    managerInitialized = true;
  }

  function open(nextContext) {
    if (!managerInitialized) return;
    if (managerMobileQuery.matches) {
      mobileInstance.open(nextContext);
      return;
    }
    const path = String(nextContext && nextContext.path || "");
    const instance = desktopInstances.get(path) || createDesktopInstance(path);
    instance.open(nextContext);
  }

  function close(options = {}) {
    if (!managerInitialized) return;
    closingAll = true;
    mobileInstance.close(options);
    desktopInstances.forEach((instance) => instance.close(options));
    closingAll = false;
    activeDesktopInstance = [...desktopInstances.values()]
      .reverse()
      .find((instance) => instance.isOpen()) || null;
  }

  function isOpen() {
    if (!managerInitialized) return false;
    return mobileInstance.isOpen()
      || [...desktopInstances.values()].some((instance) => instance.isOpen());
  }

  window.PyneScriptingBacktest = { init, open, close, isOpen };
})();
