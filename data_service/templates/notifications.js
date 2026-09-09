(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  const bell = el("notification-bell"), overlay = el("notification-overlay"), panel = el("notification-panel");
  if (!bell || !overlay) return;
  const list = el("notification-list"), status = el("notification-status"), more = el("notification-more");
  const broom = el("notification-read-all"), clearPopover = el("notification-clear-popover");
  const clearConfirm = el("notification-clear-confirm"), clearCancel = el("notification-clear-cancel");
  const items = new Map(), rows = new Map();
  let version = -1, cursor = null, expanded = null, refreshing = false, refreshAgain = false;
  let unread = 0, markingAll = false;
  let clearedThrough = 0, latestId = 0, clearThrough = null;
  const isOpen = () => !overlay.hidden;
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Local";
  const date = (seconds, utc = false) => {
    if (!seconds) return "-";
    return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit",
      minute: "2-digit", second: "2-digit", hourCycle: "h23", ...(utc ? { timeZone: "UTC" } : {}) }).format(new Date(seconds * 1000));
  };
  const delivery = (value) => {
    if (!value) return "";
    const names = { sent: "Sent", failed: "Failed", unknown: "Delivery unknown", disabled: "Disabled",
      configuration_error: "Configuration error", not_requested: "Not requested" };
    return [names[value.status] || value.status, value.http_status ? `HTTP ${value.http_status}` : "",
      value.receiver_status ? `Receiver: ${value.receiver_status}` : "", value.error_type || ""].filter(Boolean).join(" · ");
  };
  function summary(item) {
    const context = item.context || {};
    const kind = item.kind === "verification"
      ? (item.finding?.discrepancy === "missing" ? "Missing signal" : "Primary-only signal")
      : (item.origin === "manual" ? "Manual alert" : "Strategy alert");
    return [context.symbol || context.script_title || item.session_id, kind,
      item.signal?.action, item.webhook ? `Webhook: ${delivery(item.webhook)}` : "",
      item.telegram ? `Telegram: ${delivery(item.telegram)}` : ""].filter(Boolean).join(" · ");
  }
  function message(text = "") { status.textContent = text; status.hidden = !text; }
  function updateBroom() {
    broom.disabled = markingAll || latestId <= clearedThrough;
    const label = unread > 0 ? "Mark all as read" : "Clear notification list";
    broom.setAttribute("aria-label", label);
    broom.dataset.nTooltip = label;
  }
  function hideClear(restoreFocus = false) {
    clearPopover.hidden = true;
    clearThrough = null;
    broom.setAttribute("aria-expanded", "false");
    if (restoreFocus) broom.focus({ preventScroll: true });
  }
  function applyState(data) {
    if (data.version < version) return false;
    version = data.version;
    unread = data.unread;
    latestId = data.latest_id ?? latestId;
    clearedThrough = data.cleared_through_id ?? clearedThrough;
    for (const id of items.keys()) {
      if (id > clearedThrough) continue;
      rows.get(id)?.remove(); rows.delete(id); items.delete(id);
      if (expanded === id) expanded = null;
    }
    if (cursor && cursor <= clearedThrough) { cursor = null; more.hidden = true; }
    bell.classList.toggle("has-unread", unread > 0);
    bell.setAttribute("aria-label", unread > 0 ? "Notifications, unread messages" : "Notifications");
    updateBroom();
    return true;
  }
  async function request(path, body) {
    const response = await fetch(`/api/notifications${path}`, {
      ...(body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
      cache: "no-store",
    });
    if (!response.ok) {
      if (response.status === 409) throw new Error("New results arrived. Mark all as read before clearing the list.");
      throw new Error(`Notifications unavailable (HTTP ${response.status})`);
    }
    return response.json();
  }
  function details(item, target) {
    const dl = document.createElement("dl");
    const field = (label, value, cls = "") => {
      if (value === undefined || value === null || value === "") return;
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = label; dd.textContent = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
      if (cls) dd.className = cls;
      dl.append(dt, dd);
    };
    field("Session", item.session_id);
    field("Strategy", item.context?.script_title);
    field("Market", [item.context?.exchange, item.context?.symbol, item.context?.timeframe].filter(Boolean).join(" · "));
    field("Time", `${date(item.occurred_at)} (${localZone})`);
    if (item.candle_timestamp_ms) field("Candle", `${date(item.candle_timestamp_ms / 1000, true)} UTC`);
    if (item.finding) {
      field("Finding", item.finding.discrepancy === "missing" ? "Verification detected an order signal absent from the primary calculation."
        : "The primary calculation emitted an order signal absent from verification.");
      field("Source", item.finding.authoritative_source);
      field("Primary", item.finding.primary_bar);
      field("Verified", item.finding.finalized_bar);
      field("Difference", item.finding.bar_difference);
    }
    field("Signal", item.signal);
    if (item.webhook) field("Webhook", delivery(item.webhook), item.webhook.status);
    if (item.telegram) field("Telegram", delivery(item.telegram), item.telegram.status);
    target.replaceChildren(dl);
  }
  function render(item) {
    if (item.id <= clearedThrough) return;
    items.set(item.id, item);
    let row = rows.get(item.id);
    if (!row) {
      row = document.createElement("article"); row.className = "notification-row";
      const button = document.createElement("button"); button.type = "button"; button.className = "notification-summary";
      button.innerHTML = '<span></span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg>';
      const detail = document.createElement("div"); detail.className = "notification-detail";
      detail.id = `notification-detail-${item.id}`; detail.append(document.createElement("div"));
      button.setAttribute("aria-controls", detail.id);
      button.addEventListener("click", () => toggle(item.id));
      row.append(button, detail); rows.set(item.id, row);
      const next = [...list.children].find((node) => Number(node.dataset.id) < item.id);
      row.dataset.id = String(item.id); list.insertBefore(row, next || null);
    }
    row.classList.toggle("unread", item.unread);
    row.querySelector("span").textContent = summary(item);
    row.querySelector("button").setAttribute("aria-expanded", String(expanded === item.id));
    row.querySelector(".notification-detail").classList.toggle("open", expanded === item.id);
    if (expanded === item.id) details(item, row.querySelector(".notification-detail > div"));
  }
  async function loadDetail(id, markRead = true) {
    const data = await request(`/${id}`);
    if (!isOpen() || expanded !== id) return;
    if (!applyState(data)) { refreshAgain = true; return; }
    if (id <= clearedThrough) return;
    render(data.item);
    if (markRead && data.item.unread) {
      const read = await request(`/${id}/read`, { revision: data.item.revision });
      if (applyState(read) && items.get(id)?.revision === data.item.revision) {
        render({ ...items.get(id), unread: false });
      }
    }
  }
  async function toggle(id) {
    const previous = expanded;
    expanded = expanded === id ? null : id;
    if (previous && items.has(previous)) render(items.get(previous));
    render(items.get(id));
    if (expanded === id) {
      try { await loadDetail(id); } catch (error) { message(error.message); }
    }
  }
  async function sync() {
    if (refreshing) { refreshAgain = true; return; }
    refreshing = true;
    try {
      do {
        refreshAgain = false;
        const open = isOpen();
        const oldestLoaded = items.size ? Math.min(...items.keys()) : null;
        let before = null;
        do {
          const data = await request(`?limit=${open ? 30 : 1}${before ? `&before=${before}` : ""}`);
          if (!applyState(data)) { refreshAgain = true; break; }
          if (!open || !isOpen()) break;
          for (const item of data.items) render(item);
          before = data.next_cursor;
          cursor = before;
          if (!oldestLoaded || !before || data.items.at(-1)?.id <= oldestLoaded) break;
        } while (isOpen());
        if (open && isOpen()) {
          more.hidden = !cursor;
          message(items.size ? "" : "No notifications");
          if (expanded) await loadDetail(expanded);
        }
      } while (refreshAgain);
    } catch (error) {
      if (isOpen()) message(error.message);
    } finally { refreshing = false; }
  }
  function close(restoreFocus = false) {
    if (!isOpen()) return;
    hideClear();
    overlay.hidden = true; document.body.classList.remove("notifications-open");
    bell.setAttribute("aria-expanded", "false");
    if (restoreFocus) bell.focus({ preventScroll: true });
    else {
      if (panel.contains(document.activeElement)) document.activeElement.blur();
      bell.blur();
    }
  }
  function open() {
    overlay.hidden = false; document.body.classList.add("notifications-open");
    bell.setAttribute("aria-expanded", "true"); panel.focus({ preventScroll: true });
    if (!items.size) message("Loading...");
    sync();
  }
  bell.addEventListener("click", (event) => isOpen() ? close(event.detail === 0) : open());
  overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });
  panel.addEventListener("click", (event) => {
    if (!clearPopover.hidden && !clearPopover.contains(event.target) && !broom.contains(event.target)) hideClear();
  }, true);
  document.addEventListener("keydown", (event) => {
    if (!isOpen()) return;
    if (event.key === "Escape") {
      event.preventDefault(); event.stopImmediatePropagation();
      if (!clearPopover.hidden) hideClear(true); else close(true);
    }
    if (event.key === "Tab") {
      const root = clearPopover.hidden ? panel : clearPopover;
      const buttons = [...root.querySelectorAll("button")].filter((node) => !node.hidden && !node.disabled && node.offsetParent);
      const first = buttons[0], last = buttons.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }, true);
  broom.addEventListener("click", async () => {
    if (markingAll) return;
    if (!clearPopover.hidden) { hideClear(); return; }
    if (unread === 0) {
      if (latestId <= clearedThrough) return;
      clearThrough = latestId;
      clearPopover.hidden = false;
      broom.setAttribute("aria-expanded", "true");
      clearCancel.focus({ preventScroll: true });
      return;
    }
    markingAll = true; updateBroom();
    try {
      const data = await request("/read-all", {});
      if (applyState(data)) for (const item of items.values()) render({ ...item, unread: false });
      message("");
    } catch (error) { message(error.message); }
    finally { markingAll = false; updateBroom(); }
  });
  clearCancel.addEventListener("click", () => hideClear(true));
  clearConfirm.addEventListener("click", async () => {
    if (clearThrough === null || markingAll) return;
    markingAll = true; updateBroom(); clearConfirm.disabled = true;
    try {
      const data = await request("/clear", { through_id: clearThrough });
      applyState(data);
      hideClear();
      await sync();
    } catch (error) {
      hideClear();
      await sync();
      message(error.message);
    } finally {
      markingAll = false; clearConfirm.disabled = false; updateBroom();
      if (isOpen()) broom.focus({ preventScroll: true });
    }
  });
  more.addEventListener("click", async () => {
    if (!cursor || more.disabled) return;
    more.disabled = true;
    try {
      const data = await request(`?limit=30&before=${cursor}`);
      if (applyState(data)) {
        for (const item of data.items) render(item);
        cursor = data.next_cursor; more.hidden = !cursor;
      } else sync();
    } catch (error) { message(error.message); }
    finally { more.disabled = false; }
  });
  window.PyneRealNotifications = { sync, update: (data) => { if (data.version > version) sync(); } };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) sync(); });
  sync();
})();
