export const MAX_CHARTS = 6;

export function chartJobs(values) {
  const seenKeys = new Set();
  const seenIds = new Set();
  return (Array.isArray(values) ? values : [])
    .filter(value => value && value.id)
    .sort((left, right) => Number(right.created_at || 0) - Number(left.created_at || 0))
    .filter(value => {
      if (seenIds.has(value.id) || (value.run_key && seenKeys.has(value.run_key))) return false;
      seenIds.add(value.id);
      if (value.run_key) seenKeys.add(value.run_key);
      return true;
    })
    .filter(value => value.artifacts && value.artifacts.equity);
}

export class ChartSelection {
  constructor(jobs) {
    this.jobs = jobs;
    this.visible = jobs.slice(0, MAX_CHARTS).map(job => job.id);
    this.selected = this.visible[0] || null;
    this.mode = "focus";
  }

  select(id) {
    if (!this.visible.includes(id)) return false;
    this.selected = id;
    return true;
  }

  setMode(mode) {
    if (!["focus", "grid", "single"].includes(mode)) return false;
    this.mode = mode;
    return true;
  }

  swap(source, target) {
    const from = this.visible.indexOf(source);
    const to = this.visible.indexOf(target);
    if (from < 0 || to < 0 || from === to) return false;
    [this.visible[from], this.visible[to]] = [this.visible[to], this.visible[from]];
    return true;
  }

  hide(id) {
    const index = this.visible.indexOf(id);
    if (index < 0) return false;
    this.visible.splice(index, 1);
    if (this.selected === id) this.selected = this.visible[Math.min(index, this.visible.length - 1)] || null;
    return true;
  }

  add(id) {
    if (this.visible.length >= MAX_CHARTS || this.visible.includes(id)
      || !this.jobs.some(job => job.id === id)) return false;
    this.visible.push(id);
    this.selected = id;
    return true;
  }
}

export function chartLayout(visible, selected, mode) {
  const ids = visible.slice(0, MAX_CHARTS);
  const count = ids.length;
  if (!count) return { columns: "1fr", rows: "1fr", cells: [] };
  if (mode === "single" || count === 1) {
    return { columns: "minmax(0, 1fr)", rows: "minmax(0, 1fr)", cells: [{ id: ids.includes(selected) ? selected : ids[0], column: "1", row: "1" }] };
  }
  if (mode === "focus") {
    const primary = ids[0];
    return {
      columns: "minmax(0, 1fr) minmax(190px, 27%)",
      rows: `repeat(${count - 1}, minmax(0, 1fr))`,
      cells: [
        { id: primary, column: "1", row: `1 / span ${count - 1}` },
        ...ids.filter(id => id !== primary).map((id, index) => ({ id, column: "2", row: String(index + 1), compact: true })),
      ],
    };
  }
  const columns = count === 5 ? 6 : count === 4 ? 2 : Math.min(count, 3);
  return {
    columns: `repeat(${columns}, minmax(0, 1fr))`,
    rows: `repeat(${count > 3 ? 2 : 1}, minmax(0, 1fr))`,
    cells: ids.map((id, index) => count === 5
      ? { id, column: index < 3 ? `${index * 2 + 1} / span 2` : `${(index - 3) * 3 + 1} / span 3`, row: index < 3 ? "1" : "2" }
      : { id, column: String(index % columns + 1), row: String(Math.floor(index / columns) + 1) }),
  };
}

export function resizeSplit(shares, index, delta, available, minimum) {
  const next = [...shares];
  if (index < 0 || index + 1 >= next.length || available <= 0) return next;
  const total = next[index] + next[index + 1];
  const lower = Math.min(minimum / available, total / 4);
  next[index] = Math.max(lower, Math.min(total - lower, next[index] + delta / available));
  next[index + 1] = total - next[index];
  return next;
}

function inputLabel(job) {
  const entries = Object.entries(job.inputs || {});
  return entries.length ? entries.map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`).join(" · ") : "Default";
}

function startWorkspace() {
  const el = id => document.getElementById(id);
  const container = el("workspace-charts");
  const splitters = el("workspace-splitters");
  const picker = el("workspace-picker");
  const addButton = el("workspace-add");
  const syncButton = el("workspace-sync");
  const tiles = new Map();
  const scriptPath = new URLSearchParams(location.search).get("script_path") || "";
  let selection = new ChartSelection([]);
  let draggedId = null;
  const splitSizes = { columns: [0.73, 0.27], rows: [] };
  let resizing = null;
  let syncEnabled = false;
  let lastSyncView = null;

  function syncActive() {
    return syncEnabled && selection.mode !== "single" && selection.visible.length > 1;
  }

  function sendSyncState(id) {
    const tile = tiles.get(id);
    if (!tile) return;
    tile.frame.contentWindow?.postMessage({
      type: "pynereal:backtest-sync-state", enabled: syncActive(), leader: id === selection.selected,
    }, location.origin);
  }

  function updateSyncState() {
    syncButton.disabled = selection.visible.length < 2 || selection.mode === "single";
    syncButton.setAttribute("aria-pressed", String(syncEnabled));
    if (!syncActive()) lastSyncView = null;
    for (const id of tiles.keys()) sendSyncState(id);
  }

  function applyFocusSizes() {
    if (selection.mode !== "focus" || selection.visible.length < 2) return;
    for (const axis of ["columns", "rows"]) {
      const property = axis === "columns" ? "gridTemplateColumns" : "gridTemplateRows";
      const tracks = splitSizes[axis].map(value => `minmax(0, ${value}fr)`).join(" ");
      container.style[property] = tracks;
      splitters.style[property] = tracks;
    }
    for (const handle of splitters.children) {
      const shares = splitSizes[handle.dataset.axis];
      const index = Number(handle.dataset.index);
      handle.setAttribute("aria-valuenow", String(Math.round(shares[index] / (shares[index] + shares[index + 1]) * 100)));
    }
  }

  function finishResize() {
    if (!resizing) return;
    const { handle, pointerId } = resizing;
    resizing = null;
    container.classList.remove("resizing");
    handle.classList.remove("active");
    if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
  }

  function splitSpace(axis) {
    const rect = container.getBoundingClientRect();
    const styles = getComputedStyle(container);
    const gap = parseFloat(axis === "columns" ? styles.columnGap : styles.rowGap) || 0;
    return (axis === "columns" ? rect.width : rect.height) - gap * (splitSizes[axis].length - 1);
  }

  function createSplitter(axis, index) {
    const vertical = axis === "columns";
    const handle = splitters.appendChild(document.createElement("div"));
    handle.className = `workspace-splitter ${vertical ? "split-columns" : "split-rows"}`;
    handle.dataset.axis = axis;
    handle.dataset.index = String(index);
    handle.tabIndex = 0;
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", vertical ? "vertical" : "horizontal");
    handle.setAttribute("aria-label", vertical ? "Main chart width" : `Preview ${index + 1} and ${index + 2} height`);
    handle.setAttribute("aria-valuemin", "0");
    handle.setAttribute("aria-valuemax", "100");
    handle.style.gridColumn = vertical ? "1" : "2";
    handle.style.gridRow = vertical ? `1 / span ${splitSizes.rows.length}` : String(index + 1);
    const minimum = vertical ? 190 : 60;
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0 || draggedId || resizing) return;
      event.preventDefault();
      closePicker();
      handle.focus({ preventScroll: true });
      handle.setPointerCapture(event.pointerId);
      resizing = {
        handle, pointerId: event.pointerId,
        start: vertical ? event.clientX : event.clientY,
        shares: [...splitSizes[axis]], available: splitSpace(axis),
      };
      container.classList.add("resizing");
      handle.classList.add("active");
    });
    handle.addEventListener("pointermove", event => {
      if (resizing?.handle !== handle || resizing.pointerId !== event.pointerId) return;
      event.preventDefault();
      const delta = (vertical ? event.clientX : event.clientY) - resizing.start;
      splitSizes[axis] = resizeSplit(resizing.shares, index, delta, resizing.available, minimum);
      // Only resize grid tracks; retain every chart and its selection/viewport.
      applyFocusSizes();
    });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
      handle.addEventListener(type, event => {
        if (resizing?.handle === handle && resizing.pointerId === event.pointerId) finishResize();
      });
    }
    handle.addEventListener("keydown", event => {
      const direction = event.key === (vertical ? "ArrowLeft" : "ArrowUp") ? -1
        : event.key === (vertical ? "ArrowRight" : "ArrowDown") ? 1 : 0;
      if (!direction || resizing || draggedId) return;
      event.preventDefault();
      splitSizes[axis] = resizeSplit(splitSizes[axis], index, direction * 20, splitSpace(axis), minimum);
      applyFocusSizes();
    });
  }

  function renderSplitters() {
    const count = Math.max(0, selection.visible.length - 1);
    if (splitSizes.rows.length !== count) splitSizes.rows = Array(count).fill(1 / Math.max(1, count));
    splitters.replaceChildren();
    splitters.hidden = selection.mode !== "focus" || !count;
    if (splitters.hidden) return;
    createSplitter("columns", 0);
    for (let index = 0; index < count - 1; index++) createSplitter("rows", index);
    applyFocusSizes();
  }

  function clearDrag() {
    draggedId = null;
    container.classList.remove("reordering");
    for (const item of tiles.values()) {
      item.node.classList.remove("drag-source", "drop-target");
    }
  }

  function dropTarget(event) {
    const node = event.target.closest(".workspace-tile");
    const id = node?.dataset.jobId;
    return id && id !== draggedId && tiles.get(id)?.node === node && !node.hidden ? id : null;
  }

  function status(message, retry = false) {
    el("workspace-message").textContent = message;
    el("workspace-status").hidden = !message;
    el("workspace-retry").hidden = !retry;
  }

  function closePicker() {
    picker.hidden = true;
    addButton.setAttribute("aria-expanded", "false");
  }

  function sendLayout(id) {
    const tile = tiles.get(id);
    if (!tile) return;
    tile.frame.contentWindow?.postMessage({
      type: "pynereal:backtest-layout",
      compact: selection.mode === "focus" && selection.visible[0] !== id,
    }, location.origin);
  }

  function select(id) {
    if (selection.selected === id || !selection.select(id)) return;
    closePicker();
    if (selection.mode === "single") render();
    else { updateSelection(); if (syncEnabled) updateSyncState(); }
  }

  function updateSelection() {
    for (const [id, item] of tiles) {
      item.node.classList.toggle("selected", selection.selected === id);
      item.title.setAttribute("aria-pressed", String(selection.selected === id));
    }
  }

  function createTile(job) {
    const number = selection.jobs.findIndex(value => value.id === job.id) + 1;
    const tile = document.createElement("section");
    tile.className = "workspace-tile";
    tile.dataset.jobId = job.id;
    tile.setAttribute("aria-label", `Result ${number}`);
    const header = tile.appendChild(document.createElement("header"));
    header.className = "tile-header";
    const title = header.appendChild(document.createElement("button"));
    title.className = "tile-title";
    title.type = "button";
    title.setAttribute("aria-label", `Select result ${number}`);
    title.dataset.tooltip = `${inputLabel(job)}\n${job.data_path || ""}`;
    title.appendChild(document.createElement("strong")).textContent = String(number);
    title.appendChild(document.createElement("span")).textContent = inputLabel(job);
    title.addEventListener("click", () => select(job.id));
    title.addEventListener("dragstart", event => {
      if (resizing || selection.mode === "single" || selection.visible.length < 2) {
        event.preventDefault();
        return;
      }
      select(job.id);
      draggedId = job.id;
      container.classList.add("reordering");
      tile.classList.add("drag-source");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", job.id);
    });
    title.addEventListener("dragend", clearDrag);
    const close = header.appendChild(document.createElement("button"));
    close.className = "icon-button tile-close";
    close.type = "button";
    close.dataset.tooltip = "Hide chart";
    close.setAttribute("aria-label", `Hide result ${number}`);
    close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
    close.addEventListener("click", () => {
      selection.hide(job.id);
      closePicker();
      render();
      if (!selection.visible.length) addButton.focus();
    });
    const body = tile.appendChild(document.createElement("div"));
    body.className = "tile-body";
    const frame = body.appendChild(document.createElement("iframe"));
    frame.title = `Result ${number}: ${inputLabel(job)}`;
    frame.addEventListener("load", () => sendLayout(job.id));
    const compact = selection.mode === "focus" && selection.visible[0] !== job.id;
    frame.src = `/backtests/${encodeURIComponent(job.id)}?embedded=1&compact=${compact ? 1 : 0}`;
    const item = { node: tile, frame, title };
    tiles.set(job.id, item);
    container.append(tile);
    return item;
  }

  function render() {
    finishResize();
    clearDrag();
    for (const [id, item] of tiles) {
      if (selection.visible.includes(id)) continue;
      // Remove the browsing context, not the underlying backtest result.
      item.frame.src = "about:blank";
      item.node.remove();
      tiles.delete(id);
    }
    for (const id of selection.visible) {
      if (!tiles.has(id)) createTile(selection.jobs.find(job => job.id === id));
    }
    const layout = chartLayout(selection.visible, selection.selected, selection.mode);
    container.style.gridTemplateColumns = layout.columns;
    container.style.gridTemplateRows = layout.rows;
    renderSplitters();
    for (const [id, item] of tiles) {
      const cell = layout.cells.find(value => value.id === id);
      item.node.hidden = !cell;
      item.title.draggable = selection.mode !== "single" && selection.visible.length > 1;
      if (cell) {
        item.node.style.gridColumn = cell.column;
        item.node.style.gridRow = cell.row;
      }
      sendLayout(id);
    }
    updateSelection();
    updateSyncState();
    document.querySelectorAll("[data-layout]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.layout === selection.mode));
      button.disabled = !selection.visible.length;
    });
    el("workspace-count").textContent = `${selection.visible.length} / ${MAX_CHARTS}`;
    addButton.disabled = selection.visible.length >= MAX_CHARTS
      || selection.visible.length === selection.jobs.length;
    addButton.dataset.tooltip = selection.visible.length >= MAX_CHARTS ? "Hide a chart to add another" : "Add chart";
    status(selection.visible.length ? "" : selection.jobs.length ? "No charts selected" : "No equity curves available");
  }

  function openPicker() {
    if (addButton.disabled) return;
    if (!picker.hidden) { closePicker(); return; }
    picker.replaceChildren();
    selection.jobs.forEach((job, index) => {
      if (selection.visible.includes(job.id)) return;
      const button = picker.appendChild(document.createElement("button"));
      button.type = "button";
      button.appendChild(document.createElement("strong")).textContent = String(index + 1);
      button.appendChild(document.createElement("span")).textContent = inputLabel(job);
      button.addEventListener("click", () => {
        if (selection.add(job.id)) { closePicker(); render(); }
      });
    });
    picker.hidden = false;
    addButton.setAttribute("aria-expanded", "true");
    picker.querySelector("button")?.focus();
  }

  async function load() {
    status("Loading results...");
    if (!scriptPath) { status("A script was not specified."); return; }
    try {
      const response = await fetch(`/api/scripting/backtests?${new URLSearchParams({ script_path: scriptPath })}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      selection = new ChartSelection(chartJobs(payload.jobs));
      el("workspace-title").textContent = scriptPath;
      document.title = `${scriptPath} · Backtest charts`;
      render();
    } catch (error) {
      status(error.message || "Backtest results could not be loaded.", true);
    }
  }

  document.querySelectorAll("[data-layout]").forEach(button => {
    button.addEventListener("click", () => {
      selection.setMode(button.dataset.layout);
      closePicker();
      render();
    });
  });
  container.addEventListener("dragover", event => {
    if (!draggedId) return;
    event.preventDefault();
    const target = dropTarget(event);
    event.dataTransfer.dropEffect = target ? "move" : "none";
    for (const [id, item] of tiles) item.node.classList.toggle("drop-target", id === target);
  });
  container.addEventListener("dragleave", event => {
    if (container.contains(event.relatedTarget)) return;
    for (const item of tiles.values()) item.node.classList.remove("drop-target");
  });
  container.addEventListener("drop", event => {
    if (!draggedId) return;
    event.preventDefault();
    const changed = selection.swap(draggedId, dropTarget(event));
    clearDrag();
    if (changed) render();
  });
  window.addEventListener("blur", clearDrag);
  window.addEventListener("blur", finishResize);
  window.addEventListener("resize", finishResize);
  addButton.addEventListener("click", openPicker);
  syncButton.addEventListener("click", () => {
    if (syncButton.disabled) return;
    syncEnabled = !syncEnabled;
    lastSyncView = null;
    updateSyncState();
  });
  el("workspace-retry").addEventListener("click", load);
  document.addEventListener("pointerdown", event => {
    if (!event.target.closest(".workspace-add")) closePicker();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") { clearDrag(); finishResize(); }
    if (event.key === "Escape" && !picker.hidden) { closePicker(); addButton.focus(); }
  });
  window.addEventListener("message", event => {
    if (event.origin !== location.origin) return;
    const entry = [...tiles.entries()].find(([, item]) => item.frame.contentWindow === event.source);
    if (!entry || entry[1].node.hidden) return;
    const [id] = entry;
    const type = event.data?.type;
    if (type === "pynereal:backtest-ready") {
      sendSyncState(id);
      if (syncActive() && lastSyncView && id !== selection.selected) {
        event.source.postMessage({ type: "pynereal:backtest-sync-view", view: lastSyncView }, location.origin);
      }
      return;
    }
    if (draggedId || resizing) return;
    if (type === "pynereal:backtest-select") { select(id); return; }
    if (type !== "pynereal:backtest-view" || !syncActive() || id !== selection.selected) return;
    const view = event.data.view;
    if (!view || !Number.isFinite(view.from) || !Number.isFinite(view.to) || view.from >= view.to
      || !Number.isFinite(view.time)) return;
    lastSyncView = { from: view.from, to: view.to, time: view.time };
    for (const [otherId, tile] of tiles) {
      if (otherId !== id && !tile.node.hidden) {
        tile.frame.contentWindow?.postMessage({ type: "pynereal:backtest-sync-view", view: lastSyncView }, location.origin);
      }
    }
  });
  void load();
}

if (typeof document !== "undefined") startWorkspace();
