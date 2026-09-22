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

export function defaultChartRows(visible) {
  const ids = visible.slice(0, MAX_CHARTS);
  const columns = ids.length === 4 ? 2 : 3;
  const rows = [];
  for (let index = 0; index < ids.length; index += columns) rows.push(ids.slice(index, index + columns));
  return rows;
}

export function moveChartRows(rows, source, target, placement) {
  const ids = rows.flat();
  if (source === target || !ids.includes(source) || !ids.includes(target)
    || !["center", "left", "right", "top", "bottom"].includes(placement)) return null;
  let next;
  if (placement === "center") {
    next = rows.map(row => row.map(id => id === source ? target : id === target ? source : id));
  } else {
    next = rows.map(row => row.filter(id => id !== source)).filter(row => row.length);
    const row = next.findIndex(items => items.includes(target));
    if (placement === "left" || placement === "right") {
      const column = next[row].indexOf(target) + (placement === "right" ? 1 : 0);
      next[row].splice(column, 0, source);
    } else {
      next.splice(row + (placement === "bottom" ? 1 : 0), 0, [source]);
    }
  }
  return JSON.stringify(next) === JSON.stringify(rows) ? null : next;
}

export function chartDropPlacement(rect, x, y) {
  if (!Number.isFinite(x) || !Number.isFinite(y) || !rect.width || !rect.height) return "center";
  const horizontal = Math.min(64, rect.width * 0.22);
  const vertical = Math.min(48, rect.height * 0.22);
  const edges = [
    ["left", (x - rect.left) / horizontal],
    ["right", (rect.left + rect.width - x) / horizontal],
    ["top", (y - rect.top) / vertical],
    ["bottom", (rect.top + rect.height - y) / vertical],
  ].sort((a, b) => a[1] - b[1]);
  return edges[0][1] < 1 ? edges[0][0] : "center";
}

export class ChartSelection {
  constructor(jobs) {
    this.jobs = jobs;
    this.visible = jobs.slice(0, MAX_CHARTS).map(job => job.id);
    this.selected = this.visible[0] || null;
    this.mode = "focus";
    this.gridRows = null;
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
    if (this.gridRows) this.gridRows = moveChartRows(this.gridRows, source, target, "center");
    return true;
  }

  arrange(source, target, placement) {
    if (this.mode !== "grid") return false;
    if (placement === "center") return this.swap(source, target);
    const rows = moveChartRows(this.gridRows || defaultChartRows(this.visible), source, target, placement);
    if (!rows) return false;
    this.gridRows = rows;
    this.visible = rows.flat();
    return true;
  }

  hide(id) {
    const index = this.visible.indexOf(id);
    if (index < 0) return false;
    this.visible.splice(index, 1);
    if (this.gridRows) this.gridRows = this.gridRows.map(row => row.filter(value => value !== id)).filter(row => row.length);
    if (this.selected === id) this.selected = this.visible[Math.min(index, this.visible.length - 1)] || null;
    return true;
  }

  add(id) {
    if (this.visible.length >= MAX_CHARTS || this.visible.includes(id)
      || !this.jobs.some(job => job.id === id)) return false;
    this.visible.push(id);
    if (this.gridRows) {
      if (!this.gridRows.length) this.gridRows.push([]);
      this.gridRows[this.gridRows.length - 1].push(id);
    }
    this.selected = id;
    return true;
  }
}

export function gridChartSizes(rows, previous = {}) {
  const fit = (shares, count) => shares?.length === count ? shares : Array(count).fill(1 / Math.max(1, count));
  return {
    rows: fit(previous.rows, rows.length),
    columns: rows.map((row, index) => fit(previous.columns?.[index], row.length)),
  };
}

function gridTrackOffset(shares, index) {
  const before = shares.slice(0, index).reduce((sum, value) => sum + value, 0);
  return `calc(${before * 100}% + ${index} * var(--chart-gap) - ${(shares.length - 1) * before} * var(--chart-gap))`;
}

export function chartLayout(visible, selected, mode, gridRows = null, gridSizes = {}) {
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
  const rows = gridRows || defaultChartRows(ids);
  const sizes = gridChartSizes(rows, gridSizes);
  // Share one full-width grid cell per row so widths can vary independently,
  // without reparenting iframe elements when a chart changes rows.
  return {
    columns: "minmax(0, 1fr)",
    rows: sizes.rows.map(value => `minmax(0, ${value}fr)`).join(" "),
    cells: rows.flatMap((items, row) => items.map((id, column) => {
      const share = sizes.columns[row][column];
      return {
        id, column: "1", row: String(row + 1),
        width: `calc(${share * 100}% - ${(items.length - 1) * share} * var(--chart-gap))`,
        marginLeft: gridTrackOffset(sizes.columns[row], column),
      };
    })),
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
  const dropPreview = el("workspace-drop-preview");
  const dropMarker = dropPreview.querySelector("div");
  const picker = el("workspace-picker");
  const addButton = el("workspace-add");
  const syncButton = el("workspace-sync");
  const tiles = new Map();
  const scriptPath = new URLSearchParams(location.search).get("script_path") || "";
  let selection = new ChartSelection([]);
  let draggedId = null;
  const splitSizes = { columns: [0.73, 0.27], rows: [] };
  let gridSizes = {};
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

  function placeCell(node, cell) {
    node.style.gridColumn = cell.column;
    node.style.gridRow = cell.row;
    node.style.width = cell.width || "";
    node.style.marginLeft = cell.marginLeft || "";
  }

  function splitShares(axis, row) {
    if (selection.mode !== "grid") return splitSizes[axis];
    return axis === "columns" ? gridSizes.columns[row] : gridSizes.rows;
  }

  function applySplitSizes() {
    if (selection.mode === "single" || selection.visible.length < 2) return;
    const grid = selection.mode === "grid";
    if (grid) {
      const layout = chartLayout(selection.visible, selection.selected, "grid", selection.gridRows, gridSizes);
      container.style.gridTemplateColumns = splitters.style.gridTemplateColumns = layout.columns;
      container.style.gridTemplateRows = splitters.style.gridTemplateRows = layout.rows;
      for (const cell of layout.cells) placeCell(tiles.get(cell.id).node, cell);
    } else {
      for (const axis of ["columns", "rows"]) {
        const property = axis === "columns" ? "gridTemplateColumns" : "gridTemplateRows";
        const tracks = splitSizes[axis].map(value => `minmax(0, ${value}fr)`).join(" ");
        container.style[property] = splitters.style[property] = tracks;
      }
    }
    for (const handle of splitters.children) {
      const shares = splitShares(handle.dataset.axis, Number(handle.dataset.row));
      const index = Number(handle.dataset.index);
      if (grid && handle.dataset.axis === "columns") {
        handle.style.marginLeft = gridTrackOffset(shares, index + 1);
      }
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

  function splitSpace(axis, count) {
    const rect = container.getBoundingClientRect();
    const styles = getComputedStyle(container);
    const gap = parseFloat(axis === "columns" ? styles.columnGap : styles.rowGap) || 0;
    return (axis === "columns" ? rect.width : rect.height) - gap * (count - 1);
  }

  function createSplitter(axis, index, row = null) {
    const vertical = axis === "columns";
    const grid = selection.mode === "grid";
    const handle = splitters.appendChild(document.createElement("div"));
    handle.className = `workspace-splitter ${vertical ? "split-columns" : "split-rows"}${grid && vertical ? " grid-split-columns" : ""}`;
    handle.dataset.axis = axis;
    handle.dataset.index = String(index);
    if (row !== null) handle.dataset.row = String(row);
    handle.tabIndex = 0;
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", vertical ? "vertical" : "horizontal");
    handle.setAttribute("aria-label", grid
      ? vertical ? `Row ${row + 1}: chart ${index + 1} and ${index + 2} width` : `Row ${index + 1} and ${index + 2} height`
      : vertical ? "Main chart width" : `Preview ${index + 1} and ${index + 2} height`);
    handle.setAttribute("aria-valuemin", "0");
    handle.setAttribute("aria-valuemax", "100");
    handle.style.gridColumn = grid || vertical ? "1" : "2";
    handle.style.gridRow = grid && vertical ? String(row + 1)
      : vertical ? `1 / span ${splitSizes.rows.length}` : String(index + 1);
    const minimum = vertical ? 190 : 60;
    const resize = (shares, delta, available) => {
      const next = resizeSplit(shares, index, delta, available, minimum);
      if (grid && vertical) gridSizes.columns[row] = next;
      else (grid ? gridSizes : splitSizes)[axis] = next;
      // Resize existing elements only; preserve chart state and time ranges.
      applySplitSizes();
    };
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0 || draggedId || resizing) return;
      event.preventDefault();
      closePicker();
      handle.focus({ preventScroll: true });
      handle.setPointerCapture(event.pointerId);
      resizing = {
        handle, pointerId: event.pointerId,
        start: vertical ? event.clientX : event.clientY,
        shares: [...splitShares(axis, row)], available: splitSpace(axis, splitShares(axis, row).length),
      };
      container.classList.add("resizing");
      handle.classList.add("active");
    });
    handle.addEventListener("pointermove", event => {
      if (resizing?.handle !== handle || resizing.pointerId !== event.pointerId) return;
      event.preventDefault();
      const delta = (vertical ? event.clientX : event.clientY) - resizing.start;
      resize(resizing.shares, delta, resizing.available);
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
      const shares = splitShares(axis, row);
      resize(shares, direction * 20, splitSpace(axis, shares.length));
    });
  }

  function renderSplitters() {
    const count = Math.max(0, selection.visible.length - 1);
    splitters.replaceChildren();
    splitters.hidden = selection.mode === "single" || !count;
    if (splitters.hidden) return;
    if (selection.mode === "grid") {
      gridSizes.columns.forEach((shares, row) => {
        for (let index = 0; index < shares.length - 1; index++) createSplitter("columns", index, row);
      });
      for (let index = 0; index < gridSizes.rows.length - 1; index++) createSplitter("rows", index);
    } else {
      if (splitSizes.rows.length !== count) splitSizes.rows = Array(count).fill(1 / count);
      createSplitter("columns", 0);
      for (let index = 0; index < count - 1; index++) createSplitter("rows", index);
    }
    applySplitSizes();
  }

  function clearDrag() {
    draggedId = null;
    container.classList.remove("reordering");
    dropPreview.hidden = true;
    for (const item of tiles.values()) {
      item.node.classList.remove("drag-source", "drop-target");
    }
  }

  function dropPlan(event) {
    const node = event.target.closest(".workspace-tile");
    const id = node?.dataset.jobId;
    if (!id || id === draggedId || tiles.get(id)?.node !== node || node.hidden) return null;
    if (selection.mode !== "grid") return { target: id, placement: "center" };
    const placement = chartDropPlacement(node.getBoundingClientRect(), event.clientX, event.clientY);
    const rows = moveChartRows(selection.gridRows || defaultChartRows(selection.visible), draggedId, id, placement);
    return rows ? { target: id, placement, rows } : null;
  }

  function showDropPreview(plan) {
    dropPreview.hidden = !plan?.rows;
    for (const [id, item] of tiles) {
      item.node.classList.toggle("drop-target", !plan?.rows && id === plan?.target);
    }
    if (!plan?.rows) return;
    const layout = chartLayout(selection.visible, selection.selected, "grid", plan.rows, gridSizes);
    const cell = layout.cells.find(value => value.id === draggedId);
    dropPreview.style.gridTemplateColumns = layout.columns;
    dropPreview.style.gridTemplateRows = layout.rows;
    placeCell(dropMarker, cell);
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
    if (selection.mode === "grid") {
      gridSizes = gridChartSizes(selection.gridRows || defaultChartRows(selection.visible), gridSizes);
    }
    const layout = chartLayout(selection.visible, selection.selected, selection.mode, selection.gridRows, gridSizes);
    container.style.gridTemplateColumns = layout.columns;
    container.style.gridTemplateRows = layout.rows;
    for (const [id, item] of tiles) {
      const cell = layout.cells.find(value => value.id === id);
      item.node.hidden = !cell;
      item.title.draggable = selection.mode !== "single" && selection.visible.length > 1;
      if (cell) {
        placeCell(item.node, cell);
      }
      sendLayout(id);
    }
    renderSplitters();
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
    const plan = dropPlan(event);
    event.dataTransfer.dropEffect = plan ? "move" : "none";
    showDropPreview(plan);
  });
  container.addEventListener("dragleave", event => {
    if (container.contains(event.relatedTarget)) return;
    showDropPreview(null);
  });
  container.addEventListener("drop", event => {
    if (!draggedId) return;
    event.preventDefault();
    const plan = dropPlan(event);
    const changed = plan && (selection.mode === "grid"
      ? selection.arrange(draggedId, plan.target, plan.placement)
      : selection.swap(draggedId, plan.target));
    clearDrag();
    if (changed) render();
  });
  window.addEventListener("blur", clearDrag);
  window.addEventListener("resize", clearDrag);
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
