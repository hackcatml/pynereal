(function () {
  const MAX_SESSIONS = 10;
  let sessions = [];
  let keepaliveTimer = null;
  let removeSessionId = null;
  let scriptsLoading = false;
  let scriptOptions = [];
  let scriptActiveIndex = -1;
  const priceFormatters = new Map();

  const el = (id) => document.getElementById(id);

  function fmtTime(ts) {
    if (!ts) return "-";
    try {
      return new Date(ts * 1000).toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hourCycle: "h23",
      });
    } catch { return String(ts); }
  }

  function fmtPrice(value) {
    if (value == null || value === "") return "-";
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
    const abs = Math.abs(n);
    const digits = abs >= 100 ? 2 : abs >= 1 ? 4 : 8;
    if (!priceFormatters.has(digits)) {
      priceFormatters.set(digits, new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 0,
        maximumFractionDigits: digits,
      }));
    }
    return priceFormatters.get(digits).format(n);
  }

  function sessionId(s) {
    return String((s && s.id) || "");
  }

  function sessionPrice(s) {
    const raw = s && s.last_price;
    if (raw != null && raw !== "") {
      const direct = Number(raw);
      if (Number.isFinite(direct)) return direct;
    }
    return null;
  }

  function formatUtcDate(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
      `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
  }

  function parseDateAsUtc(raw) {
    const value = String(raw || "").trim();
    if (!value) return null;
    const hasZone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(value);
    const iso = value.includes("T") ? value : value.replace(" ", "T");
    const normalized = hasZone ? iso : (iso.length <= 10 ? `${iso}T00:00:00Z` : `${iso}Z`);
    const d = new Date(normalized);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function historySinceText(s) {
    const actual = Number(s && s.data_since_time);
    if (Number.isFinite(actual) && actual > 0) {
      return `Data since: ${formatUtcDate(new Date(actual * 1000))}`;
    }
    const raw = String((s && s.history_since) || "").trim();
    if (!raw) return "Data since: default window";
    if (raw === "continue") return "Data since: continue";
    if (/^\d+$/.test(raw)) {
      const d = new Date(Date.now() - Number(raw) * 24 * 60 * 60 * 1000);
      d.setUTCSeconds(0, 0);
      return `Data since: ${formatUtcDate(d)} (${raw} days)`;
    }
    const d = parseDateAsUtc(raw);
    return d ? `Data since: ${formatUtcDate(d)}` : `Data since: ${raw}`;
  }

  function lastBarCell(s) {
    return `<span class="last-bar-value">` +
      `<span class="last-bar-time">${fmtTime(s.last_bar_time)}</span>` +
      `<span class="last-bar-price">${fmtPrice(sessionPrice(s))}</span>` +
      `</span>`;
  }

  function applySessions(nextSessions) {
    sessions = nextSessions || [];
    render();
  }

  function runnerButtons(s) {
    const r = s.runner || "stopped";
    if (r === "running" || r === "starting") {
      return `<button class="btn" data-runner="stop">Stop</button>` +
             `<button class="btn" data-runner="restart">Restart</button>`;
    }
    return `<button class="btn btn-primary" data-runner="start">Start</button>`;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function logoImg(url, label, cls) {
    if (!url) return "";
    return `<img class="${cls}" src="${esc(url)}" alt="" title="${esc(label)}" ` +
           `loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">`;
  }

  function setText(node, text) {
    const next = String(text == null ? "" : text);
    if (node && node.textContent !== next) node.textContent = next;
  }

  function setClass(node, cls) {
    if (node && node.className !== cls) node.className = cls;
  }

  function setHTML(node, html) {
    if (node && node.innerHTML !== html) node.innerHTML = html;
  }

  function setChecked(node, checked) {
    if (node && node.checked !== checked) node.checked = checked;
  }

  function isTouchTooltipMode() {
    return window.matchMedia("(hover: none)").matches ||
      window.matchMedia("(pointer: coarse)").matches;
  }

  function closeDataSinceTooltips(exceptWrap) {
    document.querySelectorAll(".data-badge-wrap.show-since").forEach((wrap) => {
      if (wrap !== exceptWrap) wrap.classList.remove("show-since");
    });
  }

  function scriptSelectNodes() {
    return {
      control: el("script-select-control"),
      select: el("script-select"),
      button: el("script-select-button"),
      label: el("script-select-label"),
      options: el("script-select-options"),
    };
  }

  function selectedScriptValue() {
    const sel = el("script-select");
    return sel ? String(sel.value || "") : "";
  }

  function syncScriptSelectLabel() {
    const nodes = scriptSelectNodes();
    const value = selectedScriptValue();
    const text = value || "script_name…";
    if (nodes.label) nodes.label.textContent = text;
    if (nodes.button) nodes.button.title = value || "Select script";
    if (nodes.options) {
      nodes.options.querySelectorAll(".script-select-option").forEach((option, index) => {
        const selected = option.dataset.scriptValue === value;
        option.classList.toggle("selected", selected);
        option.classList.toggle("active", index === scriptActiveIndex);
        option.setAttribute("aria-selected", selected ? "true" : "false");
      });
    }
  }

  function renderScriptOptions() {
    const nodes = scriptSelectNodes();
    if (!nodes.options) return;
    if (!scriptOptions.length) {
      nodes.options.innerHTML = `<div class="script-select-empty">No scripts found</div>`;
      scriptActiveIndex = -1;
      syncScriptSelectLabel();
      return;
    }
    const value = selectedScriptValue();
    nodes.options.innerHTML = scriptOptions.map((script, index) => {
      const selected = script === value;
      const active = index === scriptActiveIndex;
      return `<button type="button" role="option" ` +
        `class="script-select-option${selected ? " selected" : ""}${active ? " active" : ""}" ` +
        `data-script-index="${index}" data-script-value="${esc(script)}" ` +
        `aria-selected="${selected ? "true" : "false"}" title="${esc(script)}">${esc(script)}</button>`;
    }).join("");
  }

  function setScriptActiveIndex(index) {
    if (!scriptOptions.length) {
      scriptActiveIndex = -1;
      renderScriptOptions();
      return;
    }
    const next = Math.max(0, Math.min(Number(index) || 0, scriptOptions.length - 1));
    scriptActiveIndex = next;
    renderScriptOptions();
    const active = el("script-select-options").querySelector(".script-select-option.active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function openScriptDropdown() {
    const nodes = scriptSelectNodes();
    if (!nodes.control || !nodes.button || !nodes.options) return;
    const currentIndex = scriptOptions.indexOf(selectedScriptValue());
    scriptActiveIndex = currentIndex >= 0 ? currentIndex : 0;
    renderScriptOptions();
    nodes.control.classList.add("open");
    nodes.button.setAttribute("aria-expanded", "true");
    nodes.options.classList.remove("hidden");
    nodes.options.setAttribute("tabindex", "-1");
  }

  function closeScriptDropdown() {
    const nodes = scriptSelectNodes();
    if (!nodes.control || !nodes.button || !nodes.options) return;
    nodes.control.classList.remove("open");
    nodes.button.setAttribute("aria-expanded", "false");
    nodes.options.classList.add("hidden");
  }

  function toggleScriptDropdown() {
    const nodes = scriptSelectNodes();
    if (!nodes.options) return;
    if (nodes.options.classList.contains("hidden")) openScriptDropdown();
    else closeScriptDropdown();
  }

  function selectScriptValue(value) {
    const nodes = scriptSelectNodes();
    if (!nodes.select) return;
    nodes.select.value = String(value || "");
    nodes.select.dispatchEvent(new Event("change", { bubbles: true }));
    syncScriptSelectLabel();
  }

  function moveScriptActive(delta) {
    if (!scriptOptions.length) return;
    const current = scriptActiveIndex >= 0 ? scriptActiveIndex : 0;
    setScriptActiveIndex(current + delta);
  }

  function commitScriptActive() {
    if (scriptActiveIndex < 0 || scriptActiveIndex >= scriptOptions.length) return false;
    selectScriptValue(scriptOptions[scriptActiveIndex]);
    closeScriptDropdown();
    const button = el("script-select-button");
    if (button) button.focus();
    return true;
  }

  function toggleDataSinceTooltip(tr) {
    const wrap = tr.querySelector(".data-badge-wrap");
    if (!wrap) return;
    const show = !wrap.classList.contains("show-since");
    closeDataSinceTooltips(wrap);
    wrap.classList.toggle("show-since", show);
  }

  function createSessionRow(id) {
    const tr = document.createElement("tr");
    tr.dataset.sessionId = id;
    tr.innerHTML =
      `<td data-label="Status"><span data-field="runner-led" class="led"></span></td>` +
      `<td data-label="Symbol" class="mono"><span data-field="symbol-cell" class="symbol-cell"></span></td>` +
      `<td data-label="TF" data-field="timeframe"></td>` +
      `<td data-label="Exchange"><span data-field="exchange-cell" class="exchange-cell"></span></td>` +
      `<td data-label="Script" class="mono" data-field="script-name"></td>` +
      `<td data-label="Data" class="data-cell"><span class="data-badge-wrap">` +
        `<span data-field="collector-badge" class="badge data-badge" ` +
        `data-act="data-since"></span>` +
        `<span data-field="data-since-popover" class="data-since-popover"></span></span>` +
        ` <span data-field="history-loading" class="muted">loading</span>` +
        `</td>` +
      `<td data-label="Last bar" class="muted">${lastBarCell({})}</td>` +
      `<td data-label="Webhook"><span class="cell-inline">` +
        `<input type="checkbox" data-act="webhook">` +
        `<button class="btn btn-icon" data-act="webhook-settings" title="Webhook URL">&#9881;</button></span></td>` +
      `<td data-label="Telegram"><span class="cell-inline">` +
        `<input type="checkbox" data-act="telegram">` +
        `<button class="btn btn-icon" data-act="telegram-settings" title="Telegram bot">&#9881;</button></span></td>` +
      `<td data-label="Runner" class="runner-cell" data-field="runner-cell"></td>` +
      `<td data-label="Chart"><a data-field="chart-link" class="btn btn-chart" target="_blank">Open</a></td>` +
      `<td data-label="Remove"><button class="btn btn-danger" data-act="delete" title="Delete session">&times;</button></td>`;

    tr.addEventListener("change", (e) => {
      const target = e.target;
      const id = tr.dataset.sessionId;
      if (!target || !id) return;
      if (target.getAttribute("data-act") === "webhook") {
        toggleWebhook(id, { enabled: target.checked });
      } else if (target.getAttribute("data-act") === "telegram") {
        toggleWebhook(id, { telegram_notification: target.checked });
      }
    });

    tr.addEventListener("click", (e) => {
      const target = e.target && e.target.closest
        ? e.target.closest("[data-runner], [data-act]")
        : null;
      const id = tr.dataset.sessionId;
      if (!target || !tr.contains(target) || !id) return;
      const runnerAct = target.getAttribute("data-runner");
      if (runnerAct) {
        runnerAction(id, runnerAct);
        return;
      }
      const act = target.getAttribute("data-act");
      if (act === "data-since") {
        if (isTouchTooltipMode()) {
          e.preventDefault();
          e.stopPropagation();
          toggleDataSinceTooltip(tr);
        }
      } else if (act === "delete") openRemoveConfirm(id);
      else if (act === "logs") openLogs(id);
      else if (act === "webhook-settings") openSettings(id, "webhook");
      else if (act === "telegram-settings") openSettings(id, "telegram");
    });
    return tr;
  }

  function patchSessionRow(tr, s) {
    const id = sessionId(s);
    const runner = s.runner || "stopped";
    const collector = s.collector || "stopped";
    const wh = s.webhook || {};
    const exchange = (s.exchange || "").toUpperCase();

    const led = tr.querySelector('[data-field="runner-led"]');
    setClass(led, `led led-${runner}`);
    if (led && led.title !== runner) led.title = runner;

    const symbolKey = JSON.stringify([s.symbol || "", s.tv_symbol || "", s.symbol_logo_url || ""]);
    if (tr.dataset.symbolKey !== symbolKey) {
      const symbolLogo = logoImg(s.symbol_logo_url, s.tv_symbol || s.symbol, "market-logo");
      setHTML(
        tr.querySelector('[data-field="symbol-cell"]'),
        `${symbolLogo}<span class="symbol-text">${esc(s.symbol)}</span>`,
      );
      tr.dataset.symbolKey = symbolKey;
    }

    const exchangeKey = JSON.stringify([exchange, s.exchange_logo_url || ""]);
    if (tr.dataset.exchangeKey !== exchangeKey) {
      const exchangeLogo = logoImg(s.exchange_logo_url, exchange, "exchange-logo");
      setHTML(
        tr.querySelector('[data-field="exchange-cell"]'),
        `${exchangeLogo}<span>${esc(exchange)}</span>`,
      );
      tr.dataset.exchangeKey = exchangeKey;
    }

    setText(tr.querySelector('[data-field="timeframe"]'), s.timeframe);
    setText(tr.querySelector('[data-field="script-name"]'), s.script_name);

    const badge = tr.querySelector('[data-field="collector-badge"]');
    setClass(badge, `badge data-badge badge-${collector}`);
    setText(badge, collector);
    const sinceText = historySinceText(s);
    if (badge) {
      badge.setAttribute("aria-label", sinceText);
    }
    setText(tr.querySelector('[data-field="data-since-popover"]'), sinceText);
    const loading = tr.querySelector('[data-field="history-loading"]');
    if (loading) loading.hidden = !!s.history_ready;

    setText(tr.querySelector(".last-bar-time"), fmtTime(s.last_bar_time));
    setText(tr.querySelector(".last-bar-price"), fmtPrice(sessionPrice(s)));

    setChecked(tr.querySelector('[data-act="webhook"]'), !!wh.enabled);
    setChecked(tr.querySelector('[data-act="telegram"]'), !!wh.telegram_notification);

    if (tr.dataset.runnerControlsKey !== runner) {
      setHTML(
        tr.querySelector('[data-field="runner-cell"]'),
        `${runnerButtons(s)}<button class="btn" data-act="logs">Logs</button>`,
      );
      tr.dataset.runnerControlsKey = runner;
    }

    const chart = tr.querySelector('[data-field="chart-link"]');
    const href = `/s/${encodeURIComponent(id)}`;
    if (chart && chart.getAttribute("href") !== href) chart.setAttribute("href", href);
  }

  function render() {
    const body = el("sessions-body");
    el("empty").style.display = sessions.length ? "none" : "block";
    el("session-count").textContent = `${sessions.length} / ${MAX_SESSIONS} sessions`;

    const rows = new Map(Array.from(body.children).map((row) => [row.dataset.sessionId, row]));
    const liveIds = new Set(sessions.map(sessionId).filter((id) => id));
    rows.forEach((row, id) => {
      if (!liveIds.has(id)) {
        row.remove();
        rows.delete(id);
      }
    });
    let cursor = body.firstElementChild;

    sessions.forEach((s) => {
      const id = sessionId(s);
      if (!id) return;
      let row = rows.get(id);
      if (!row) row = createSessionRow(id);
      patchSessionRow(row, s);
      if (row === cursor) {
        cursor = cursor.nextElementSibling;
      } else {
        body.insertBefore(row, cursor);
      }
    });
  }

  async function api(path, opts) {
    const resp = await fetch(path, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    return data;
  }

  async function runnerAction(id, action) {
    try {
      await api(`/api/sessions/${encodeURIComponent(id)}/runner/${action}`, { method: "POST" });
    } catch (e) {
      alert(`runner ${action} failed: ${e.message}`);
    }
  }

  async function toggleWebhook(id, payload) {
    try {
      await api(`/api/${encodeURIComponent(id)}/webhook-config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      alert(`webhook update failed: ${e.message}`);
      refresh();
    }
  }

  function openRemoveConfirm(id) {
    removeSessionId = id;
    el("remove-session-id").textContent = id;
    el("remove-error").textContent = "";
    el("remove-confirm").disabled = false;
    el("remove-modal").classList.remove("hidden");
    lockBodyScroll();
  }

  function closeRemoveConfirm() {
    if (el("remove-modal").classList.contains("hidden")) return;
    el("remove-modal").classList.add("hidden");
    unlockBodyScroll();
    removeSessionId = null;
    el("remove-confirm").disabled = false;
  }

  async function confirmRemoveSession() {
    if (!removeSessionId) return;
    const id = removeSessionId;
    el("remove-error").textContent = "";
    el("remove-confirm").disabled = true;
    try {
      await api(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
      closeRemoveConfirm();
    } catch (e) {
      el("remove-error").textContent = `delete failed: ${e.message}`;
      el("remove-confirm").disabled = false;
    }
  }

  el("add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    el("add-error").textContent = "";
    if (!selectedScriptValue()) {
      el("add-error").textContent = "스크립트를 선택하세요.";
      openScriptDropdown();
      const button = el("script-select-button");
      if (button) button.focus();
      return;
    }
    const fd = new FormData(e.target);
    const payload = {};
    fd.forEach((v, k) => {
      const val = String(v).trim();
      if (val !== "") payload[k] = val;
    });
    // Checkbox -> real boolean (absent from FormData when unchecked).
    const autostartEl = e.target.querySelector('[name="autostart_runner"]');
    payload.autostart_runner = !!(autostartEl && autostartEl.checked);
    if (payload.symbol) payload.symbol = payload.symbol.toUpperCase();
    // Block Add when exchange/symbol are confirmed invalid.
    const exOk = await checkExchange();
    const symOk = await checkSymbol();
    if (!exOk || !symOk) {
      el("add-error").textContent = "입력값을 확인하세요 — 잘못된 exchange/symbol 입니다.";
      return;
    }
    try {
      await api("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      e.target.reset();
      e.target.querySelector('[name="provider"]').value = "ccxt";
      syncScriptSelectLabel();
      closeScriptDropdown();
      clearFieldErrors();
    } catch (err) {
      el("add-error").textContent = err.message;
    }
  });

  // ---- live log viewer ----------------------------------------------------
  let logTimer = null;
  let logSession = null;
  let logRequestSeq = 0;
  let logAbort = null;
  let savedScrollY = 0;

  // Lock the page behind the modal (iOS-safe position:fixed technique) so
  // scrolling inside the log panel doesn't bleed through to the dashboard.
  function lockBodyScroll() {
    savedScrollY = window.scrollY || window.pageYOffset || 0;
    const b = document.body.style;
    b.position = "fixed";
    b.top = `-${savedScrollY}px`;
    b.left = "0";
    b.right = "0";
    b.width = "100%";
  }
  function unlockBodyScroll() {
    const b = document.body.style;
    b.position = "";
    b.top = "";
    b.left = "";
    b.right = "";
    b.width = "";
    window.scrollTo(0, savedScrollY);
  }

  function clearLogTimer() {
    if (logTimer) {
      clearTimeout(logTimer);
      logTimer = null;
    }
  }

  function cancelLogFetch() {
    if (logAbort) {
      logAbort.abort();
      logAbort = null;
    }
  }

  async function fetchLogs(sessionId, seq) {
    if (!sessionId || seq !== logRequestSeq) return;
    const controller = new AbortController();
    logAbort = controller;
    try {
      const data = await api(
        `/api/sessions/${encodeURIComponent(sessionId)}/runner/logs?lines=500`,
        { signal: controller.signal },
      );
      if (logAbort === controller) logAbort = null;
      if (seq !== logRequestSeq || logSession !== sessionId) return;
      const pre = el("log-content");
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
      pre.textContent = data.log && data.log.length ? data.log : "(no log output yet)";
      if (atBottom) pre.scrollTop = pre.scrollHeight;  // follow tail unless user scrolled up
    } catch (e) {
      if (logAbort === controller) logAbort = null;
      if (e.name === "AbortError" || seq !== logRequestSeq || logSession !== sessionId) return;
      el("log-content").textContent = `failed to load logs: ${e.message}`;
    }
  }

  async function pollLogs(sessionId, seq) {
    await fetchLogs(sessionId, seq);
    if (seq !== logRequestSeq || logSession !== sessionId) return;
    logTimer = setTimeout(() => pollLogs(sessionId, seq), 1500);
  }

  function openLogs(id) {
    clearLogTimer();
    cancelLogFetch();
    logSession = id;
    logRequestSeq += 1;
    const seq = logRequestSeq;
    el("log-title").textContent = id;
    el("log-content").textContent = "loading…";
    el("log-modal").classList.remove("hidden");
    lockBodyScroll();
    pollLogs(id, seq);
  }

  function closeLogs() {
    if (el("log-modal").classList.contains("hidden")) return;
    logRequestSeq += 1;
    clearLogTimer();
    cancelLogFetch();
    el("log-modal").classList.add("hidden");
    unlockBodyScroll();
    logSession = null;
  }

  async function clearLogs() {
    const sessionId = logSession;
    if (!sessionId) return;
    clearLogTimer();
    cancelLogFetch();
    logRequestSeq += 1;
    const seq = logRequestSeq;
    try {
      await api(`/api/sessions/${encodeURIComponent(sessionId)}/runner/logs`, { method: "DELETE" });
      if (seq !== logRequestSeq || logSession !== sessionId) return;
      el("log-content").textContent = "(cleared)";
      logRequestSeq += 1;
      pollLogs(sessionId, logRequestSeq);
    } catch (e) {
      alert(`clear failed: ${e.message}`);
    }
  }

  el("log-close").addEventListener("click", closeLogs);
  el("log-clear").addEventListener("click", clearLogs);
  el("log-modal").addEventListener("click", (e) => {
    if (e.target === el("log-modal")) closeLogs();
  });
  // ---- per-session webhook/telegram settings modal ------------------------
  let settingsSession = null;
  let settingsMode = null; // "webhook" | "telegram"

  async function openSettings(id, mode) {
    settingsSession = id;
    settingsMode = mode;
    el("settings-error").textContent = "";
    el("settings-title").textContent =
      (mode === "webhook" ? "Webhook URL — " : "Telegram bot — ") + id;
    el("settings-fields").innerHTML = "loading…";
    el("settings-modal").classList.remove("hidden");
    lockBodyScroll();
    try {
      const cfg = await api(`/api/${encodeURIComponent(id)}/webhook-config`);
      if (mode === "webhook") {
        el("settings-fields").innerHTML =
          `<label class="settings-label">Webhook URL</label>` +
          `<input id="settings-url" type="text" placeholder="http://localhost:8888/webhook" value="${esc(cfg.url || "")}">` +
          `<div class="muted">Webhook server URL for this session. Leave empty to use the script default.</div>`;
      } else {
        el("settings-fields").innerHTML =
          `<label class="settings-label">Bot token</label>` +
          `<input id="settings-token" type="text" placeholder="123456:ABC-DEF..." value="${esc(cfg.telegram_token || "")}">` +
          `<label class="settings-label">Chat ID</label>` +
          `<input id="settings-chatid" type="text" placeholder="-1001234567890" value="${esc(cfg.telegram_chat_id || "")}">` +
          `<div class="muted">Leave empty to use BOT_TOKEN / CHAT_ID from .env.</div>`;
      }
    } catch (e) {
      el("settings-fields").innerHTML = `<div class="error">failed to load: ${esc(e.message)}</div>`;
    }
  }

  function closeSettings() {
    if (el("settings-modal").classList.contains("hidden")) return;
    el("settings-modal").classList.add("hidden");
    unlockBodyScroll();
    settingsSession = null;
    settingsMode = null;
  }

  async function saveSettings() {
    if (!settingsSession) return;
    const payload = {};
    if (settingsMode === "webhook") {
      const urlEl = el("settings-url");
      payload.url = (urlEl ? urlEl.value : "").trim();
    } else {
      const tokEl = el("settings-token");
      const chatEl = el("settings-chatid");
      payload.telegram_token = (tokEl ? tokEl.value : "").trim();
      payload.telegram_chat_id = (chatEl ? chatEl.value : "").trim();
    }
    try {
      await api(`/api/${encodeURIComponent(settingsSession)}/webhook-config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      closeSettings();
    } catch (e) {
      el("settings-error").textContent = e.message;
    }
  }

  el("settings-close").addEventListener("click", closeSettings);
  el("settings-save").addEventListener("click", saveSettings);
  el("settings-modal").addEventListener("click", (e) => {
    if (e.target === el("settings-modal")) closeSettings();
  });
  el("remove-close").addEventListener("click", closeRemoveConfirm);
  el("remove-cancel").addEventListener("click", closeRemoveConfirm);
  el("remove-confirm").addEventListener("click", confirmRemoveSession);
  el("remove-modal").addEventListener("click", (e) => {
    if (e.target === el("remove-modal")) closeRemoveConfirm();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    closeScriptDropdown();
    closeDataSinceTooltips();
    if (!el("log-modal").classList.contains("hidden")) closeLogs();
    if (!el("settings-modal").classList.contains("hidden")) closeSettings();
    if (!el("remove-modal").classList.contains("hidden")) closeRemoveConfirm();
  });
  document.addEventListener("click", (e) => {
    const scriptControl = el("script-select-control");
    if (scriptControl && e.target && !scriptControl.contains(e.target)) {
      closeScriptDropdown();
    }
    if (!isTouchTooltipMode()) return;
    if (!e.target || !e.target.closest || e.target.closest(".data-badge-wrap")) return;
    closeDataSinceTooltips();
  });

  // ---- collapsible "Add session" card (collapsed by default) ---------------
  function toggleAddCard() {
    const card = el("add-card");
    const collapsed = card.classList.toggle("collapsed");
    el("add-toggle").setAttribute("aria-expanded", String(!collapsed));
    if (!collapsed) {
      loadScripts();  // refresh the script list each time it opens
    } else {
      closeScriptDropdown();
    }
  }
  el("add-toggle").addEventListener("click", toggleAddCard);
  el("add-toggle").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleAddCard(); }
  });

  // ---- add-form field validation (exchange / symbol existence) -------------
  const addForm = el("add-form");
  const exchangeInput = addForm.querySelector('[name="exchange"]');
  const symbolInput = addForm.querySelector('[name="symbol"]');
  const providerInput = addForm.querySelector('[name="provider"]');

  function setFieldError(id, msg, kind) {
    const node = el(id);
    if (!node) return;
    node.textContent = msg || "";
    node.className = "field-error" + (kind ? " " + kind : "");
  }

  function clearFieldErrors() {
    setFieldError("exchange-error", "");
    setFieldError("symbol-error", "");
  }

  // Each returns false only when the value is *confirmed* invalid (blocks Add).
  // A network/verify failure shows a soft warning but does not block.
  async function checkExchange() {
    const provider = (providerInput.value || "ccxt").trim();
    const exchange = exchangeInput.value.trim();
    setFieldError("symbol-error", "");  // exchange change invalidates the prior symbol check
    if (!exchange) { setFieldError("exchange-error", ""); return true; }
    setFieldError("exchange-error", "checking…", "checking");
    try {
      const data = await api(`/api/validate/exchange?provider=${encodeURIComponent(provider)}` +
        `&exchange=${encodeURIComponent(exchange)}`);
      if (data.skipped || data.exists) { setFieldError("exchange-error", ""); return true; }
      setFieldError("exchange-error", `exchange '${exchange}' not found`);
      return false;
    } catch (e) {
      setFieldError("exchange-error", "could not verify exchange", "warn");
      return true;
    }
  }

  async function checkSymbol() {
    symbolInput.value = symbolInput.value.toUpperCase();  // canonical-uppercase symbols
    const provider = (providerInput.value || "ccxt").trim();
    const exchange = exchangeInput.value.trim();
    const symbol = symbolInput.value.trim();
    if (!symbol) { setFieldError("symbol-error", ""); return true; }
    if (!exchange) { setFieldError("symbol-error", "enter exchange first"); return false; }
    setFieldError("symbol-error", "checking…", "checking");
    try {
      const q = `provider=${encodeURIComponent(provider)}&exchange=${encodeURIComponent(exchange)}` +
        `&symbol=${encodeURIComponent(symbol)}`;
      const data = await api(`/api/validate/symbol?${q}`);
      if (data.skipped || data.exists === true) { setFieldError("symbol-error", ""); return true; }
      if (data.exists === false) {
        setFieldError("symbol-error", `symbol '${symbol}' not found on ${exchange}`);
        return false;
      }
      setFieldError("symbol-error", data.error || "could not verify symbol", "warn");
      return true;
    } catch (e) {
      setFieldError("symbol-error", "could not verify symbol", "warn");
      return true;
    }
  }

  exchangeInput.addEventListener("blur", checkExchange);
  symbolInput.addEventListener("blur", checkSymbol);

  // ---- script list (populate the script_name <select>) --------------------
  async function loadScripts() {
    if (scriptsLoading) return;
    scriptsLoading = true;
    const refreshBtn = el("script-refresh");
    if (refreshBtn) refreshBtn.disabled = true;
    try {
      const data = await api("/api/scripts", { cache: "no-store" });
      const sel = el("script-select");
      const cur = sel.value;
      scriptOptions = Array.isArray(data.scripts) ? data.scripts : [];
      const opts = scriptOptions.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
      sel.innerHTML = '<option value="">script_name…</option>' + opts;
      sel.value = cur && scriptOptions.includes(cur) ? cur : "";
      renderScriptOptions();
      syncScriptSelectLabel();
    } catch (e) {
      /* ignore */
    } finally {
      scriptsLoading = false;
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }
  el("script-refresh").addEventListener("click", loadScripts);
  el("script-select").addEventListener("change", syncScriptSelectLabel);
  el("script-select-button").addEventListener("click", (e) => {
    e.preventDefault();
    toggleScriptDropdown();
  });
  el("script-select-button").addEventListener("keydown", (e) => {
    const optionsOpen = !el("script-select-options").classList.contains("hidden");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!optionsOpen) openScriptDropdown();
      else moveScriptActive(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (optionsOpen) commitScriptActive();
      else openScriptDropdown();
    } else if (e.key === "Escape") {
      closeScriptDropdown();
    }
  });
  el("script-select-options").addEventListener("click", (e) => {
    const option = e.target && e.target.closest ? e.target.closest(".script-select-option") : null;
    if (!option) return;
    selectScriptValue(option.dataset.scriptValue || "");
    closeScriptDropdown();
    el("script-select-button").focus();
  });
  el("script-select-options").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      moveScriptActive(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Home") {
      e.preventDefault();
      setScriptActiveIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setScriptActiveIndex(scriptOptions.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      commitScriptActive();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeScriptDropdown();
      el("script-select-button").focus();
    }
  });

  async function refresh() {
    try {
      const data = await api("/api/sessions");
      applySessions(data.sessions || []);
      return true;
    } catch (e) {
      /* ignore */
      return false;
    }
  }

  let hubWs = null;
  let reconnectTimer = null;
  let firstMessageTimer = null;
  let fallbackTimer = null;
  let hubGeneration = 0;
  let reconnectAttempt = 0;
  let hubLive = false;

  function clearReconnectTimer() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  }

  function clearFirstMessageTimer() {
    if (firstMessageTimer) { clearTimeout(firstMessageTimer); firstMessageTimer = null; }
  }

  function clearFallbackTimer() {
    if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
  }

  function clearKeepaliveTimer() {
    if (keepaliveTimer) { clearInterval(keepaliveTimer); keepaliveTimer = null; }
  }

  function closeHubSocket(ws) {
    if (!ws) return;
    ws.onopen = null;
    ws.onmessage = null;
    ws.onclose = null;
    ws.onerror = null;
    if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
      try { ws.close(); } catch {}
    }
  }

  function setHubStatus(text, ok = false) {
    el("conn-status").textContent = text;
    el("conn-status").className = ok ? "conn ok" : "conn";
  }

  function scheduleFallbackPoll(delay = 0, generation = hubGeneration) {
    if (fallbackTimer || document.visibilityState === "hidden") return;
    fallbackTimer = setTimeout(async () => {
      fallbackTimer = null;
      if (generation !== hubGeneration || document.visibilityState === "hidden" || hubLive) return;
      const ok = await refresh();
      if (generation !== hubGeneration || document.visibilityState === "hidden" || hubLive) return;
      if (ok) {
        setHubStatus("polling", true);
      } else {
        setHubStatus("reconnecting…");
      }
      scheduleFallbackPoll(ok ? 5000 : 1500, generation);
    }, delay);
  }

  function scheduleReconnect(delay, generation = hubGeneration) {
    if (reconnectTimer) return;            // a reconnect is already pending
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (generation !== hubGeneration) return;
      connect(generation);
    }, delay);
  }

  function retryHub(generation) {
    if (generation !== hubGeneration) return;
    reconnectAttempt += 1;
    hubLive = false;
    clearFirstMessageTimer();
    clearKeepaliveTimer();
    closeHubSocket(hubWs);
    hubWs = null;
    setHubStatus("reconnecting…");
    scheduleFallbackPoll(0, generation);
    scheduleReconnect(reconnectAttempt <= 3 ? 500 : 5000, generation);
  }

  function rebuildHub() {
    if (document.visibilityState === "hidden") return;
    hubGeneration += 1;
    reconnectAttempt = 0;
    hubLive = false;
    clearReconnectTimer();
    clearFirstMessageTimer();
    clearKeepaliveTimer();
    closeHubSocket(hubWs);
    hubWs = null;
    setHubStatus("reconnecting…");
    scheduleFallbackPoll(0, hubGeneration);
    connect(hubGeneration);
  }

  function connect(generation = hubGeneration) {
    if (generation !== hubGeneration) return;
    // Don't stack sockets (an in-flight CONNECTING / live OPEN one is fine).
    if (hubWs && (hubWs.readyState === WebSocket.OPEN || hubWs.readyState === WebSocket.CONNECTING)) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws/hub`);
    hubWs = ws;
    // Mobile resume often leaves the first attempt hung in CONNECTING (radio not
    // ready). CONNECTING never fires onclose, so without this watchdog the retry
    // loop never starts and we sit at "reconnecting…" forever. Force-close it so
    // onclose -> scheduleReconnect kicks in.
    const connectGuard = setTimeout(() => {
      if (ws !== hubWs || generation !== hubGeneration) return;
      if (ws.readyState === WebSocket.CONNECTING) retryHub(generation);
    }, 5000);
    ws.onopen = () => {
      if (ws !== hubWs || generation !== hubGeneration) return;
      clearTimeout(connectGuard);
      setHubStatus("syncing…");
      refresh().then((ok) => {
        if (ws !== hubWs || generation !== hubGeneration || hubLive) return;
        if (ok) {
          setHubStatus("polling", true);
          scheduleFallbackPoll(5000, generation);
        }
      });
      clearFirstMessageTimer();
      firstMessageTimer = setTimeout(() => retryHub(generation), 7000);
    };
    ws.onmessage = (ev) => {
      if (ws !== hubWs || generation !== hubGeneration) return;
      reconnectAttempt = 0;
      hubLive = true;
      clearFirstMessageTimer();
      clearFallbackTimer();
      setHubStatus("live", true);
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "sessions") {
          applySessions(msg.sessions || []);
        }
      } catch {}
    };
    ws.onclose = () => {
      clearTimeout(connectGuard);
      clearFirstMessageTimer();
      clearKeepaliveTimer();
      if (ws !== hubWs || generation !== hubGeneration) return; // superseded by a newer socket
      hubWs = null;
      hubLive = false;
      setHubStatus("reconnecting…");
      scheduleFallbackPoll(0, generation);
      scheduleReconnect(1500, generation);
    };
    ws.onerror = () => { try { ws.close(); } catch {} };
    // Replace any prior keepalive so reconnects don't accumulate timers.
    clearKeepaliveTimer();
    keepaliveTimer = setInterval(() => {
      if (ws !== hubWs || generation !== hubGeneration) return;
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 15000);
  }

  // Close the socket while backgrounded so the 1s hub push can't flood a frozen socket
  // (which iOS then drops, leaving the resumed page unable to re-establish). We reconnect
  // cleanly on return.
  function closeForBackground() {
    hubGeneration += 1;
    hubLive = false;
    clearReconnectTimer();
    clearFirstMessageTimer();
    clearFallbackTimer();
    clearKeepaliveTimer();
    closeHubSocket(hubWs);
    hubWs = null;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") rebuildHub();
    else closeForBackground();
  });
  window.addEventListener("online", rebuildHub);

  refresh();   // one-time initial load; thereafter the hub pushes via /ws/hub
  connect();
})();
