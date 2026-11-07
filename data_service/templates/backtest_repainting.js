/* Optional post-backtest analysis. No live chart or runner dependencies. */
window.BacktestRepainting = (() => {
  const activeStatuses = new Set(["preparing", "running", "stopping"]);
  function timestamp(value) {
    return new Date(Number(value) * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC";
  }
  function parseStart(value) {
    if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(value)) throw new Error("Use YYYY-MM-DD HH:MM (UTC).");
    const date = new Date(value.replace(" ", "T") + ":00Z");
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 16).replace("T", " ") !== value) {
      throw new Error("Invalid UTC date.");
    }
    return date.getTime() / 1000;
  }
  const sources = {
    historical_only: { name: "Backtest only", short: "BT", color: "#b8750d" },
    sequential_only: { name: "Sequential only", short: "Seq", color: "#2563eb" },
    changed: { name: "Changed fill", short: "Changed", color: "#7c3aed" },
  };
  function quantityOnly(item) {
    const keys = Object.keys(item.differences || {});
    return item.kind === "changed" && keys.length === 1 && keys[0] === "size";
  }
  function filteredFindings(findings, includeQuantity = false) {
    return includeQuantity ? findings : findings.filter(item => !quantityOnly(item));
  }
  function signalName(item) {
    const signal = item.sequential || item.historical;
    return signal.action === "exit"
      ? `Exit · ${signal.comment || signal.exit_id || signal.order_id}`
      : `Entry · ${signal.order_id}`;
  }
  function markerGroups(findings, includeQuantity = false) {
    const groups = new Map();
    for (const item of filteredFindings(findings, includeQuantity)) {
      if (!groups.has(item.time)) groups.set(item.time, []);
      groups.get(item.time).push(item);
    }
    return [...groups.values()];
  }
  function markers(findings, first, last, includeQuantity = false) {
    return markerGroups(findings, includeQuantity).filter(group => group[0].time >= first && group[0].time <= last).map(group => {
      const item = group[0];
      const kinds = new Set(group.map(finding => finding.kind));
      const source = kinds.size === 1 ? sources[item.kind] : sources.changed;
      const actions = new Set(group.map(finding => (finding.sequential || finding.historical).action));
      const action = actions.size === 1 ? [...actions][0] : "fills";
      return { id: `repainting-${item.id}`, time: item.time,
        position: item.kind === "sequential_only" && kinds.size === 1 ? "belowBar" : "aboveBar",
        shape: action === "entry" ? "arrowUp" : action === "exit" ? "arrowDown" : "square",
        color: source.color, size: .8,
        text: `${group.some(finding => finding.first) ? "First · " : ""}${source.short} ${action}${group.length > 1 ? ` (${group.length})` : ""}` };
    });
  }
  function fitLabels(markers, existing, coordinate, width) {
    const occupied = existing.filter(item => item.text).map(item => ({
      time: item.time, position: item.position, x: coordinate(item.time), width: width(item.text),
    }));
    return markers.map(marker => {
      const x = coordinate(marker.time);
      if (x == null) return marker;
      const size = width(marker.text);
      const crowded = occupied.some(item => item.x != null && item.time !== marker.time
        && item.position === marker.position && Math.abs(x - item.x) < (size + item.width) / 2 + 6);
      if (crowded) return { ...marker, text: "" };
      occupied.push({ time: marker.time, position: marker.position, x, width: size });
      return marker;
    });
  }
  function mount({ job, onChange, onNavigate }) {
    const toggle = document.getElementById("backtest-repainting-toggle");
    const panel = document.createElement("section");
    panel.className = "repainting-panel";
    panel.hidden = true;
    panel.setAttribute("aria-label", "Repainting check");
    panel.innerHTML = `<header><strong>Repainting check</strong>
      <button type="button" class="drawing-tool-btn" data-close aria-label="Close">&times;</button></header>
      <div class="repainting-options">
        <label><input type="radio" name="repainting-mode" value="full" checked>Full period</label>
        <label><input type="radio" name="repainting-mode" value="from_time">From time</label>
      </div>
      <div class="repainting-start" hidden><label for="repainting-start">Start (UTC)</label>
        <input id="repainting-start" type="text" placeholder="YYYY-MM-DD HH:MM" autocomplete="off" spellcheck="false"></div>
      <div class="repainting-actions"><button type="button" class="repainting-run" data-run>Verify</button>
        <button type="button" class="repainting-run" data-stop hidden>Cancel</button>
        <label><input type="checkbox" data-markers checked>Markers</label></div>
      <label class="repainting-quantity"><input type="checkbox" data-quantity><span>Quantity-only</span></label>
      <div class="repainting-legend"><span data-kind="historical_only">BT: Backtest only</span>
        <span data-kind="sequential_only">Seq: Sequential only</span><span data-kind="changed">Changed fill</span></div>
      <div class="repainting-status" role="status"></div>
      <small class="repainting-range"></small>
      <div class="repainting-findings"></div><div class="repainting-detail" hidden></div>
      <small>Time basis: Fill candle (UTC), not signal candle.<br>Historical / sequential fills, not live pre_run or intrabar reconstruction.</small>`;
    document.body.append(panel);
    const tooltip = document.createElement("div");
    tooltip.className = "repainting-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    document.body.append(tooltip);
    const node = selector => panel.querySelector(selector);
    const run = node("[data-run]");
    const stop = node("[data-stop]");
    const status = node(".repainting-status");
    const list = node(".repainting-findings");
    const detail = node(".repainting-detail");
    const start = node("#repainting-start");
    start.value = timestamp(job.actual_time_from ?? job.time_from).slice(0, 16);
    const base = `/api/scripting/backtests/${encodeURIComponent(job.id)}/repainting`;
    let state = { status: "ready" };
    let socket = null;
    let reconnect = null;
    let findings = [];
    let visibleFindings = [];
    let groupsById = new Map();
    let shown = 0;
    let selected = null;
    let busy = false;
    let disposed = false;

    async function request(url, options) {
      const response = await fetch(url, { cache: "no-store", ...options });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || "Verification request failed.");
      return value;
    }
    function open(value) {
      panel.hidden = !value;
      toggle.setAttribute("aria-expanded", String(value));
      toggle.classList.toggle("active", value);
    }
    function showError(error) {
      status.textContent = error.message || String(error);
      status.classList.add("error");
    }
    function showDetail(item, navigate = true) {
      if (!item) return;
      selected = item.id;
      open(true);
      detail.hidden = false;
      detail.replaceChildren();
      const heading = document.createElement("h4");
      heading.textContent = `${item.first ? "First · " : ""}${sources[item.kind].name} · ${signalName(item)}\n${timestamp(item.time)}`;
      detail.append(heading);
      appendDetail(item, detail);
      list.querySelectorAll("[data-finding]").forEach(button => {
        button.setAttribute("aria-current", String(Number(button.dataset.finding) === selected));
      });
      if (navigate) Promise.resolve(onNavigate(item.time)).catch(showError);
    }
    function appendDetail(item, target) {
      const dl = document.createElement("dl");
      const number = value => new Intl.NumberFormat("en-US", { maximumSignificantDigits: 14 }).format(value);
      for (const [name, event] of [["Backtest", item.historical], ["Sequential", item.sequential]]) {
        const dt = document.createElement("dt");
        const dd = document.createElement("dd");
        dt.textContent = name;
        dd.textContent = event
          ? `${event.action === "entry" ? "Entry" : "Exit"} · ${event.comment || event.exit_id || event.order_id}\nPosition entry: ${event.order_id}\nFill: ${timestamp(event.time)}\nPrice: ${number(event.price)} | Quantity: ${number(event.size)}`
          : "No fill";
        dl.append(dt, dd);
      }
      target.append(dl);
      const fields = Object.keys(item.differences || {});
      if (fields.length) {
        const changed = document.createElement("small");
        changed.textContent = `Changed: ${fields.map(key => ({ size: "Quantity", price: "Price", entry_time: "Position entry time" })[key] || key).join(", ")}`;
        target.append(changed);
      }
      if (item.differences?.entry_time) {
        const extra = document.createElement("small");
        extra.textContent = `Entry: ${timestamp(item.differences.entry_time.historical)} / ${timestamp(item.differences.entry_time.sequential)}`;
        target.append(extra);
      }
    }
    function appendFindings() {
      const end = Math.min(shown + 50, visibleFindings.length);
      for (; shown < end; shown++) {
        const item = visibleFindings[shown];
        const button = document.createElement("button");
        button.type = "button";
        button.className = "repainting-finding";
        button.dataset.finding = item.id;
        button.dataset.kind = item.kind;
        const title = document.createElement("b");
        title.textContent = `${item.first ? "First · " : ""}${signalName(item)}`;
        button.append(title, document.createTextNode(`${timestamp(item.time)} · ${sources[item.kind].name}`));
        button.addEventListener("click", () => showDetail(item));
        list.append(button);
      }
    }
    function renderFindings() {
      const includeQuantity = node("[data-quantity]").checked;
      visibleFindings = filteredFindings(findings, includeQuantity);
      groupsById = new Map(markerGroups(findings, includeQuantity).map(group => [`repainting-${group[0].id}`, group]));
      list.replaceChildren();
      detail.hidden = true;
      tooltip.hidden = true;
      selected = null;
      shown = 0;
      node(".repainting-quantity span").textContent = `Quantity-only (${findings.filter(quantityOnly).length})`;
      appendFindings();
      onChange();
    }
    function render(value) {
      state = value;
      const active = activeStatuses.has(value.status);
      run.disabled = busy || active || value.status === "unavailable" || job.status !== "completed";
      stop.hidden = !active;
      stop.disabled = busy || value.status === "stopping";
      panel.querySelectorAll('.repainting-options input, .repainting-start input').forEach(input => { input.disabled = busy || active; });
      status.classList.toggle("error", ["failed", "unavailable"].includes(value.status));
      if (active) {
        status.textContent = `${value.phase === "warmup" ? "Warm-up" : value.status === "preparing" ? "Preparing" : "Verifying"}${value.total ? ` ${Math.floor(100 * value.done / value.total)}%` : "..."}`;
      } else if (value.status === "completed") {
        const quantityCount = (value.findings || []).filter(quantityOnly).length;
        const count = (value.findings || []).length - quantityCount;
        status.textContent = `${count} execution differences · ${quantityCount} quantity-only`;
      } else status.textContent = value.error || (value.status === "ready" ? "Ready" : value.status);
      node(".repainting-range").textContent = value.status === "completed"
        ? `${timestamp(value.start)} - ${timestamp(value.end)}` : "";
      if (value.status === "completed" || active || value.status === "failed" || value.status === "cancelled") {
        findings = value.status === "completed" ? value.findings || [] : [];
        renderFindings();
      }
    }
    function watch() {
      clearTimeout(reconnect);
      if (disposed || socket) return;
      const current = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/scripting/backtests/${encodeURIComponent(job.id)}/repainting`);
      socket = current;
      current.onmessage = event => { if (socket === current) render(JSON.parse(event.data)); };
      current.onclose = () => {
        if (socket !== current) return;
        socket = null;
        if (!disposed && activeStatuses.has(state.status)) reconnect = setTimeout(refresh, 1500);
      };
    }
    async function refresh() {
      if (disposed) return;
      try {
        render(await request(base));
        if (activeStatuses.has(state.status)) watch();
      } catch (error) { showError(error); }
    }
    run.addEventListener("click", async () => {
      if (run.disabled) return;
      try {
        const from = node('input[value="from_time"]').checked ? parseStart(start.value.trim()) : null;
        busy = true;
        render(state);
        const value = await request(base, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ start: from }) });
        busy = false;
        render(value);
        watch();
      } catch (error) { busy = false; render(state); showError(error); }
    });
    stop.addEventListener("click", async () => {
      busy = true;
      render(state);
      try { const value = await request(`${base}/stop`, { method: "POST" }); busy = false; render(value); }
      catch (error) { busy = false; render(state); showError(error); }
    });
    toggle.addEventListener("click", () => { open(panel.hidden); if (!panel.hidden) void refresh(); });
    node("[data-close]").addEventListener("click", () => open(false));
    node("[data-markers]").addEventListener("change", () => { tooltip.hidden = true; onChange(); });
    node("[data-quantity]").addEventListener("change", renderFindings);
    panel.querySelectorAll('input[name="repainting-mode"]').forEach(input => input.addEventListener("change", () => {
      node(".repainting-start").hidden = !node('input[value="from_time"]').checked;
    }));
    list.addEventListener("scroll", () => { if (list.scrollHeight - list.scrollTop - list.clientHeight < 60) appendFindings(); });
    panel.addEventListener("dblclick", event => { if (event.target.closest("button")) event.preventDefault(); });
    let downOutside = false;
    document.addEventListener("pointerdown", event => { downOutside = !panel.contains(event.target) && !toggle.contains(event.target); });
    document.addEventListener("click", event => { if (downOutside && !panel.contains(event.target) && !toggle.contains(event.target)) open(false); });
    document.addEventListener("keydown", event => { if (event.key === "Escape") open(false); });
    window.addEventListener("focus", refresh);
    window.addEventListener("pagehide", () => { disposed = true; clearTimeout(reconnect); socket?.close(); });
    if (new URLSearchParams(location.search).has("repainting")) open(true);
    void refresh();
    return {
      markers: (first, last) => node("[data-markers]").checked ? markers(findings, first, last, node("[data-quantity]").checked) : [],
      select: id => {
        const group = groupsById.get(String(id));
        if (group && node("[data-markers]").checked) {
          downOutside = false;
          showDetail(group[0], false);
          for (const item of group.slice(1)) {
            const heading = document.createElement("h4");
            heading.textContent = `${sources[item.kind].name} · ${signalName(item)}`;
            detail.append(heading);
            appendDetail(item, detail);
          }
        }
      },
      hover: (id, point) => {
        const group = groupsById.get(String(id));
        tooltip.hidden = !group || !point || !node("[data-markers]").checked;
        if (tooltip.hidden) return;
        tooltip.textContent = `${timestamp(group[0].time)} · Fill candle\n` + group.map(item =>
          `${item.first ? "First · " : ""}${sources[item.kind].name}: ${signalName(item)}${quantityOnly(item) ? " (Quantity)" : ""}`
        ).join("\n");
        const rect = tooltip.getBoundingClientRect();
        tooltip.style.left = `${Math.max(8, Math.min(point.x + 12, innerWidth - rect.width - 8))}px`;
        tooltip.style.top = `${Math.max(8, Math.min(point.y + 12, innerHeight - rect.height - 8))}px`;
      },
    };
  }
  return { mount, parseStart, markers, quantityOnly, filteredFindings, signalName, fitLabels };
})();
