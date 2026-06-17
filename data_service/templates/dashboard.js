(function () {
  const MAX_SESSIONS = 10;
  let sessions = [];
  let keepaliveTimer = null;
  let removeSessionId = null;
  let scriptsLoading = false;

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

  function render() {
    const body = el("sessions-body");
    body.innerHTML = "";
    el("empty").style.display = sessions.length ? "none" : "block";
    el("session-count").textContent = `${sessions.length} / ${MAX_SESSIONS} sessions`;

    sessions.forEach((s) => {
      const runner = s.runner || "stopped";
      const collector = s.collector || "stopped";
      const wh = s.webhook || {};
      const exchange = (s.exchange || "").toUpperCase();
      const symbolLogo = logoImg(s.symbol_logo_url, s.tv_symbol || s.symbol, "market-logo");
      const exchangeLogo = logoImg(s.exchange_logo_url, exchange, "exchange-logo");
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td data-label="Status"><span class="led led-${runner}" title="${runner}"></span></td>` +
        `<td data-label="Symbol" class="mono"><span class="symbol-cell">${symbolLogo}` +
          `<span class="symbol-text">${esc(s.symbol)}</span></span></td>` +
        `<td data-label="TF">${esc(s.timeframe)}</td>` +
        `<td data-label="Exchange"><span class="exchange-cell">${exchangeLogo}` +
          `<span>${esc(exchange)}</span></span></td>` +
        `<td data-label="Script" class="mono">${esc(s.script_name)}</td>` +
        `<td data-label="Data"><span class="badge badge-${collector}">${collector}</span>` +
          `${s.history_ready ? "" : " <span class='muted'>loading</span>"}</td>` +
        `<td data-label="Last bar" class="muted">${fmtTime(s.last_bar_time)}</td>` +
        `<td data-label="Webhook"><span class="cell-inline">` +
          `<input type="checkbox" data-act="webhook" ${wh.enabled ? "checked" : ""}>` +
          `<button class="btn btn-icon" data-act="webhook-settings" title="Webhook URL">&#9881;</button></span></td>` +
        `<td data-label="Telegram"><span class="cell-inline">` +
          `<input type="checkbox" data-act="telegram" ${wh.telegram_notification ? "checked" : ""}>` +
          `<button class="btn btn-icon" data-act="telegram-settings" title="Telegram bot">&#9881;</button></span></td>` +
        `<td data-label="Runner" class="runner-cell">${runnerButtons(s)}` +
          `<button class="btn" data-act="logs">Logs</button></td>` +
        `<td data-label="Chart"><a class="btn btn-chart" href="/s/${encodeURIComponent(s.id)}" target="_blank">Open</a></td>` +
        `<td data-label="Remove"><button class="btn btn-danger" data-act="delete" title="Delete session">&times;</button></td>`;

      tr.querySelector('[data-act="webhook"]').addEventListener("change", (e) =>
        toggleWebhook(s.id, { enabled: e.target.checked }));
      tr.querySelector('[data-act="telegram"]').addEventListener("change", (e) =>
        toggleWebhook(s.id, { telegram_notification: e.target.checked }));
      tr.querySelector('[data-act="delete"]').addEventListener("click", () => openRemoveConfirm(s.id));
      tr.querySelector('[data-act="logs"]').addEventListener("click", () => openLogs(s.id));
      tr.querySelector('[data-act="webhook-settings"]').addEventListener("click", () => openSettings(s.id, "webhook"));
      tr.querySelector('[data-act="telegram-settings"]').addEventListener("click", () => openSettings(s.id, "telegram"));
      tr.querySelectorAll("[data-runner]").forEach((btn) =>
        btn.addEventListener("click", () => runnerAction(s.id, btn.getAttribute("data-runner"))));

      body.appendChild(tr);
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
      clearFieldErrors();
    } catch (err) {
      el("add-error").textContent = err.message;
    }
  });

  // ---- live log viewer ----------------------------------------------------
  let logTimer = null;
  let logSession = null;
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

  async function fetchLogs() {
    if (!logSession) return;
    try {
      const data = await api(`/api/sessions/${encodeURIComponent(logSession)}/runner/logs?lines=500`);
      const pre = el("log-content");
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
      pre.textContent = data.log && data.log.length ? data.log : "(no log output yet)";
      if (atBottom) pre.scrollTop = pre.scrollHeight;  // follow tail unless user scrolled up
    } catch (e) {
      el("log-content").textContent = `failed to load logs: ${e.message}`;
    }
  }

  function openLogs(id) {
    logSession = id;
    el("log-title").textContent = id;
    el("log-content").textContent = "loading…";
    el("log-modal").classList.remove("hidden");
    lockBodyScroll();
    fetchLogs();
    if (logTimer) clearInterval(logTimer);
    logTimer = setInterval(fetchLogs, 1500);
  }

  function closeLogs() {
    if (el("log-modal").classList.contains("hidden")) return;
    el("log-modal").classList.add("hidden");
    unlockBodyScroll();
    logSession = null;
    if (logTimer) { clearInterval(logTimer); logTimer = null; }
  }

  async function clearLogs() {
    if (!logSession) return;
    try {
      await api(`/api/sessions/${encodeURIComponent(logSession)}/runner/logs`, { method: "DELETE" });
      el("log-content").textContent = "(cleared)";
      fetchLogs();
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
    if (!el("log-modal").classList.contains("hidden")) closeLogs();
    if (!el("settings-modal").classList.contains("hidden")) closeSettings();
    if (!el("remove-modal").classList.contains("hidden")) closeRemoveConfirm();
  });

  // ---- collapsible "Add session" card (collapsed by default) ---------------
  function toggleAddCard() {
    const card = el("add-card");
    const collapsed = card.classList.toggle("collapsed");
    el("add-toggle").setAttribute("aria-expanded", String(!collapsed));
    if (!collapsed) {
      loadScripts();  // refresh the script list each time it opens
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
      const opts = (data.scripts || []).map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
      sel.innerHTML = '<option value="">script_name…</option>' + opts;
      if (cur && (data.scripts || []).includes(cur)) sel.value = cur;
    } catch (e) {
      /* ignore */
    } finally {
      scriptsLoading = false;
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }
  el("script-refresh").addEventListener("click", loadScripts);

  async function refresh() {
    try {
      const data = await api("/api/sessions");
      sessions = data.sessions || [];
      render();
    } catch (e) {
      /* ignore */
    }
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws/hub`);
    ws.onopen = () => {
      el("conn-status").textContent = "live";
      el("conn-status").className = "conn ok";
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "sessions") {
          sessions = msg.sessions || [];
          render();
        }
      } catch {}
    };
    ws.onclose = () => {
      if (keepaliveTimer) { clearInterval(keepaliveTimer); keepaliveTimer = null; }
      el("conn-status").textContent = "reconnecting…";
      el("conn-status").className = "conn";
      setTimeout(connect, 1500);
    };
    ws.onerror = () => { try { ws.close(); } catch {} };
    // Replace any prior keepalive so reconnects don't accumulate timers.
    if (keepaliveTimer) clearInterval(keepaliveTimer);
    keepaliveTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 15000);
  }

  refresh();   // one-time initial load; thereafter the hub pushes via /ws/hub
  connect();
})();
