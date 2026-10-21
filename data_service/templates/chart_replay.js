var App = window.App || (window.App = {});

App.replay = (() => {
  "use strict";
  const params = new URLSearchParams(location.search);
  const mobile = window.matchMedia("(max-width: 640px), (hover: none) and (pointer: coarse)");
  const inset = Boolean(params.has("replay-inset") && window.frameElement && window.parent.App?.replay?.active);
  const active = !inset && params.has("replay");
  if (params.has("replay-inset") && !inset) {
    const url = new URL(location.href);
    url.searchParams.delete("replay-inset");
    history.replaceState(history.state, "", url);
  }
  let socket, heartbeat, timer, playing = false, busy = false, ready = false, ended = false;
  let generation = 0, selecting = false, frames = [], definitions = [], lastTime = 0;
  let toolbar, status, play, step, startButton, date, chooser, chooserAnchor, calendar;
  let selectionChooserWasOpen = false;
  let playbackSpeed = 1, speedMenu, speedToggle;
  let stepStartedAt = 0, livePanel, liveFrame, liveToggle;
  let livePreferred = true, replayCache = [];
  let rewindFrom = null, rewindTo = null, pendingSeek = null;
  let startMarker;
  let crosshairFrame = null;
  const crosshairUpdates = new Map();
  class ReplayStartMarker {
    constructor(chart) {
      this.chart = chart;
      this.time = null;
      this.requestUpdate = null;
      this.views = [{ renderer: () => this, zOrder: () => "top" }];
    }
    attached({ requestUpdate }) { this.requestUpdate = requestUpdate; }
    detached() { this.requestUpdate = null; }
    paneViews() { return this.views; }
    setTime(time) {
      this.time = time;
      this.requestUpdate?.();
    }
    draw(target) {
      if (this.time === null) return;
      const x = this.chart.timeScale().timeToCoordinate(this.time);
      if (x === null) return;
      // A pane primitive follows scrolling, zoom and pane resizing without
      // adding a series, affecting autoscale or intercepting chart gestures.
      target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: rx, verticalPixelRatio: ry, bitmapSize }) => {
        if (x < 0 || x > bitmapSize.width / rx) return;
        const bottom = bitmapSize.height / ry - 3;
        ctx.save();
        ctx.scale(rx, ry);
        ctx.beginPath();
        ctx.moveTo(x, bottom - 13);
        ctx.lineTo(x + 5, bottom - 7);
        ctx.lineTo(x + 2, bottom - 7);
        ctx.lineTo(x + 2, bottom);
        ctx.lineTo(x - 2, bottom);
        ctx.lineTo(x - 2, bottom - 7);
        ctx.lineTo(x - 5, bottom - 7);
        ctx.closePath();
        ctx.fillStyle = "#2563eb";
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1;
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      });
    }
  }
  const icons = {
    replay: '<polygon points="11 19 2 12 11 5 11 19"/><polygon points="22 19 13 12 22 5 22 19"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/>',
    select: '<path d="M12 3v18M3 12h18"/><circle cx="12" cy="12" r="4"/>',
    play: '<path d="m7 4 14 8-14 8Z"/>', pause: '<path d="M8 4v16M16 4v16"/>',
    step: '<path d="m5 4 12 8-12 8ZM20 4v16"/>',
    start: '<path d="M3 19V5m10 1-6 6 6 6m-6-6h14"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    previous: '<path d="m15 6-6 6 6 6"/>', next: '<path d="m9 6 6 6-6 6"/>',
    live: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    grip: '<g fill="currentColor" stroke="none"><circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="19" r="1.5"/><circle cx="15" cy="19" r="1.5"/></g>',
  };
  function icon(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
  }
  function button(parent, name, label, action) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "replay-button";
    node.dataset.tooltip = label;
    node.setAttribute("aria-label", label);
    node.innerHTML = icon(name);
    node.addEventListener("click", action);
    parent.append(node);
    return node;
  }
  function textTime(time) { return new Date(time * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC"; }
  function parseDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}$/.test(value)) return null;
    const normalized = value.replace(" ", "T");
    const parsed = new Date(normalized + ":00Z");
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 16) === normalized ? parsed : null;
  }
  function selectedDateTime() { return parseDate(`${date.value} ${calendar?.timeValue || "00:00"}`); }
  function validateDate() {
    const parsed = selectedDateTime();
    let error = parsed ? "" : "Enter a valid UTC date: YYYY-MM-DD.";
    if (parsed && date.min && parsed < parseDate(date.min)) error = `Earliest available time: ${date.min.replace("T", " ")} UTC.`;
    if (parsed && date.max && parsed > parseDate(date.max)) error = `Latest available time: ${date.max.replace("T", " ")} UTC.`;
    date.setCustomValidity(error);
    date.setAttribute("aria-invalid", String(Boolean(error)));
    return !error;
  }
  function message(text, error = false) {
    status.textContent = text;
    status.classList.toggle("error", error);
  }
  function pause() {
    playing = false;
    clearTimeout(timer);
    if (play) {
      play.innerHTML = icon("play");
      play.dataset.tooltip = "Play";
      play.setAttribute("aria-label", "Play");
    }
  }
  function controls() {
    if (!play) return;
    play.disabled = !ready || ended || selecting;
    step.disabled = !ready || busy || playing || ended || selecting;
    startButton.disabled = !ready || busy || selecting || startMarker.time === null;
  }
  function stop() {
    generation++;
    pause();
    clearInterval(heartbeat);
    if (socket) {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop" }));
      socket.close();
      socket = null;
    }
    ready = false;
    busy = false;
    rewindFrom = rewindTo = pendingSeek = null;
    replayCache = [];
    startMarker?.setTime(null);
    controls();
  }
  function requestStep() {
    if (!ready || busy || ended || !socket || socket.readyState !== WebSocket.OPEN) return;
    busy = true;
    stepStartedAt = performance.now();
    controls();
    socket.send(JSON.stringify({ type: "step" }));
  }
  function schedule() {
    if (playing && !ended) timer = setTimeout(requestStep, Math.max(0, 1000 / playbackSpeed - (performance.now() - stepStartedAt)));
  }
  function goToStart() {
    if (!ready || busy || startMarker.time === null) return;
    const scale = App.chart.chart.timeScale(), range = scale.getVisibleLogicalRange();
    // A cached seek returns a 60-day display window. Keep earlier rendered
    // frames by reference so this navigation never rewinds the replay cursor.
    if (scale.timeToIndex(startMarker.time) === null) {
      let left = 0, right = replayCache.length;
      while (left < right) {
        const mid = (left + right) >>> 1;
        if (replayCache[mid].bar.time <= lastTime) left = mid + 1;
        else right = mid;
      }
      frames = replayCache.slice(0, left);
      renderHistory();
    }
    const index = scale.timeToIndex(startMarker.time);
    if (index === null) return;
    const half = range ? (range.to - range.from) / 2 : 60;
    scale.setVisibleLogicalRange({ from: index - half, to: index + half });
  }
  function seek(time) {
    pause();
    pendingSeek = time;
    if (busy) { controls(); return; }
    pendingSeek = null;
    ready = false;
    busy = true;
    message("Restoring replay...");
    controls();
    socket.send(JSON.stringify({ type: "seek", time }));
    const url = new URL(location.href);
    url.searchParams.set("replay", time);
    history.replaceState(history.state, "", url);
  }
  function queueCrosshair(callback, param) {
    crosshairUpdates.set(callback, param);
    if (crosshairFrame !== null) return;
    crosshairFrame = requestAnimationFrame(() => {
      crosshairFrame = null;
      const pending = [...crosshairUpdates];
      crosshairUpdates.clear();
      for (const [update, value] of pending) update(value);
    });
  }
  function cacheCandleReads() {
    const series = App.chart.candleSeries, read = series.data.bind(series);
    let cached = null, mutating = false;
    // LWC 5.0.9 markers call data() during hit testing. Its readonly snapshot
    // otherwise copies all candles again for every plot/crosshair update.
    series.data = () => mutating ? read() : (cached ??= read());
    for (const name of ["setData", "update"]) {
      const original = series[name].bind(series);
      series[name] = (...args) => {
        cached = null;
        mutating = true;
        try { return original(...args); }
        finally { mutating = false; cached = null; }
      };
    }
  }
  function closeSpeedMenu(focus = false) {
    if (!speedMenu) return;
    speedMenu.hidden = true;
    speedToggle.setAttribute("aria-expanded", "false");
    if (focus) speedToggle.focus({ preventScroll: true });
  }
  function viewportBounds() {
    const view = window.visualViewport;
    const left = view?.offsetLeft ?? 0, top = view?.offsetTop ?? 0;
    return { left, top, right: left + (view?.width ?? innerWidth), bottom: top + (view?.height ?? innerHeight) };
  }
  function placeChooser() {
    if (!chooser || chooser.hidden || !chooserAnchor?.isConnected) return;
    const view = viewportBounds(), anchor = chooserAnchor.getBoundingClientRect();
    if (!anchor.width || !anchor.height) return;
    const rect = chooser.getBoundingClientRect(), gap = 8;
    const clampLeft = value => Math.max(view.left + gap, Math.min(value, view.right - rect.width - gap));
    const clampTop = value => Math.max(view.top + gap, Math.min(value, view.bottom - rect.height - gap));
    let left, top = anchor.top;
    if (anchor.right + gap + rect.width <= view.right - gap) left = anchor.right + gap;
    else if (anchor.left - gap - rect.width >= view.left + gap) left = anchor.left - gap - rect.width;
    else {
      left = anchor.left;
      top = anchor.top - rect.height - gap >= view.top + gap
        ? anchor.top - rect.height - gap : anchor.bottom + gap;
    }
    chooser.style.transform = "none";
    chooser.style.bottom = "auto";
    chooser.style.left = `${clampLeft(left)}px`;
    chooser.style.top = `${clampTop(top)}px`;
  }
  function placeSpeedMenu() {
    if (!speedMenu || speedMenu.hidden) return;
    const view = viewportBounds(), anchor = speedToggle.getBoundingClientRect();
    const above = anchor.top - view.top - 12, below = view.bottom - anchor.bottom - 12;
    speedMenu.style.maxHeight = `${Math.max(0, Math.max(above, below))}px`;
    const rect = speedMenu.getBoundingClientRect();
    const upward = above >= rect.height || above > below;
    speedMenu.style.top = upward ? "auto" : "calc(100% + 8px)";
    speedMenu.style.bottom = upward ? "calc(100% + 8px)" : "auto";
    const left = Math.max(view.left + 4, Math.min(anchor.left, view.right - rect.width - 4));
    speedMenu.style.left = `${left - anchor.left}px`;
  }
  function addToolbarHandle() {
    const handle = button(toolbar, "grip", "Move replay toolbar", event => event.preventDefault());
    handle.classList.add("replay-toolbar-handle");
    toolbar.prepend(handle);
    let drag = null, position = null;
    function place(left, top) {
      const rect = toolbar.getBoundingClientRect(), view = viewportBounds();
      position = {
        left: Math.max(view.left + 4, Math.min(left, view.right - rect.width - 4)),
        top: Math.max(view.top + 4, Math.min(top, view.bottom - rect.height - 4)),
      };
      toolbar.style.transform = "none";
      toolbar.style.bottom = "auto";
      toolbar.style.left = `${position.left}px`;
      toolbar.style.top = `${position.top}px`;
    }
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0 || event.isPrimary === false || drag) return;
      event.preventDefault();
      event.stopPropagation();
      closeSpeedMenu();
      calendar.close();
      chooser.hidden = true;
      App.ui.refreshMobileViewportLock();
      const rect = toolbar.getBoundingClientRect();
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
      toolbar.classList.add("dragging");
      handle.setPointerCapture(event.pointerId);
    });
    window.addEventListener("pointermove", event => {
      if (!drag || event.pointerId !== drag.id) return;
      event.preventDefault();
      event.stopPropagation();
      place(drag.left + event.clientX - drag.x, drag.top + event.clientY - drag.y);
    }, { capture: true, passive: false });
    function endDrag(event) {
      if (!drag || event.pointerId !== drag.id) return;
      const id = drag.id;
      drag = null;
      toolbar.classList.remove("dragging");
      if (handle.hasPointerCapture(id)) handle.releasePointerCapture(id);
    }
    for (const name of ["pointerup", "pointercancel"]) window.addEventListener(name, endDrag, true);
    window.addEventListener("blur", () => { if (drag) endDrag({ pointerId: drag.id }); });
    handle.addEventListener("keydown", event => {
      event.stopPropagation();
      const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
      if (!delta) return;
      event.preventDefault();
      const rect = toolbar.getBoundingClientRect(), distance = event.shiftKey ? 30 : 10;
      place(rect.left + delta[0] * distance, rect.top + delta[1] * distance);
      placeSpeedMenu();
    });
    function constrain() {
      if (position) place(position.left, position.top);
      placeSpeedMenu();
    }
    window.addEventListener("resize", constrain);
    window.visualViewport?.addEventListener("resize", constrain);
    window.visualViewport?.addEventListener("scroll", constrain);
    new ResizeObserver(constrain).observe(toolbar);
  }
  function buildSpeedMenu(parent) {
    const container = document.createElement("div");
    container.className = "replay-speed";
    speedToggle = document.createElement("button");
    speedToggle.type = "button";
    speedToggle.className = "replay-speed-toggle";
    speedToggle.setAttribute("aria-label", "Replay speed: 1x");
    speedToggle.setAttribute("aria-haspopup", "menu");
    speedToggle.setAttribute("aria-expanded", "false");
    speedToggle.setAttribute("aria-controls", "replay-speed-menu");
    speedToggle.innerHTML = '<span>1x</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
    speedMenu = document.createElement("div");
    speedMenu.id = "replay-speed-menu";
    speedMenu.setAttribute("role", "menu");
    speedMenu.setAttribute("aria-label", "Replay speed");
    speedMenu.hidden = true;
    const items = [0.5, 1, 2, 5, 10].map(value => {
      const item = document.createElement("button");
      item.type = "button";
      item.tabIndex = -1;
      item.textContent = `${value}x`;
      item.dataset.speed = String(value);
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", String(value === playbackSpeed));
      item.addEventListener("click", () => {
        playbackSpeed = value;
        speedToggle.querySelector("span").textContent = `${value}x`;
        speedToggle.setAttribute("aria-label", `Replay speed: ${value}x`);
        items.forEach(option => option.setAttribute("aria-checked", String(option === item)));
        closeSpeedMenu(true);
        clearTimeout(timer);
        if (!busy) schedule();
      });
      speedMenu.append(item);
      return item;
    });
    function open() {
      calendar.close();
      chooser.hidden = true;
      App.ui.refreshMobileViewportLock();
      speedMenu.hidden = false;
      placeSpeedMenu();
      speedToggle.setAttribute("aria-expanded", "true");
      items.find(item => item.getAttribute("aria-checked") === "true").focus({ preventScroll: true });
    }
    let pointerOpen = null;
    speedToggle.addEventListener("pointerdown", event => {
      if (event.button === 0 && event.isPrimary !== false) pointerOpen = !speedMenu.hidden;
    });
    speedToggle.addEventListener("pointercancel", () => { pointerOpen = null; });
    speedToggle.addEventListener("click", event => {
      // Focus may leave the menu before click, especially on touch devices.
      const wasOpen = (event.detail > 0 || event.pointerType) && pointerOpen !== null
        ? pointerOpen : !speedMenu.hidden;
      pointerOpen = null;
      if (wasOpen) closeSpeedMenu(); else open();
    });
    container.addEventListener("keydown", event => {
      event.stopPropagation();
      if (event.key === "Escape") { event.preventDefault(); closeSpeedMenu(true); }
      else if (["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        if (speedMenu.hidden) { open(); return; }
        const index = items.indexOf(document.activeElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
          : (index + (["ArrowUp", "ArrowLeft"].includes(event.key) ? -1 : 1) + items.length) % items.length;
        items[next].focus({ preventScroll: true });
      }
    });
    container.addEventListener("focusout", event => {
      if (!container.contains(event.relatedTarget)) closeSpeedMenu();
    });
    document.addEventListener("pointerdown", event => {
      if (!container.contains(event.target)) closeSpeedMenu();
    }, true);
    window.addEventListener("blur", () => closeSpeedMenu());
    container.append(speedToggle, speedMenu);
    parent.append(container);
  }
  function setSelection(value) {
    calendar?.close();
    if (value && !selecting) {
      selectionChooserWasOpen = !chooser.hidden;
      chooser.hidden = true;
    } else if (!value) selectionChooserWasOpen = false;
    selecting = value;
    pause();
    App.measure?.setActive(false);
    App.trendline?.setActive(false);
    App.volumeProfile?.setActive(false);
    App.ui.closeManualAlertMenu?.();
    document.body.classList.toggle("replay-selecting", value);
    App.ui.refreshMobileViewportLock();
    document.querySelectorAll('[aria-label="Select a bar"]').forEach(node => node.setAttribute("aria-pressed", String(value)));
    controls();
  }
  function launch(time) {
    setSelection(false);
    chooser.hidden = true;
    App.ui.refreshMobileViewportLock();
    if (!Number.isFinite(time)) return;
    if (!active) {
      const url = new URL(location.href);
      url.searchParams.set("replay", Math.floor(time));
      location.assign(url);
    } else if (rewindFrom !== null && time >= rewindFrom && time <= rewindTo && socket?.readyState === WebSocket.OPEN) {
      seek(Math.floor(time));
    } else start(Math.floor(time));
  }
  function openChooser(event) {
    if (selecting) setSelection(false);
    closeSpeedMenu();
    calendar.close();
    pause();
    chooser.hidden = !chooser.hidden;
    App.ui.refreshMobileViewportLock();
    if (!chooser.hidden) {
      chooserAnchor = event?.currentTarget || document.getElementById("chart-replay-toggle");
      placeChooser();
      const latestClosed = App.state.lastBarTime > 0
        ? App.state.lastBarTime - (App.state.configuredTimeframeSec || 60) : 0;
      calendar.open(new Date((lastTime || latestClosed || Date.now() / 1000) * 1000));
      validateDate();
      placeChooser();
    }
  }
  function bindDateInput() {
    const mask = "____-__-__";
    const slots = [...mask].flatMap((char, index) => char === "_" ? [index] : []);
    const reverseSlots = [...slots].reverse();
    const nextSlot = position => slots.find(index => index >= position) ?? mask.length;
    const maskedValue = value => [...mask].map((char, index) =>
      char === "_" && /\d/.test(value[index] || "") ? value[index] : char).join("");
    let pendingEdit = null, composition = null;
    const selection = () => ({ value: date.value, start: date.selectionStart ?? 0, end: date.selectionEnd ?? 0 });
    function render(change, notify = false) {
      date.value = change.value;
      date.setSelectionRange(change.caret, change.caret);
      if (notify) date.dispatchEvent(new Event("input", { bubbles: true }));
    }
    function edit(type, text = "", state = selection()) {
      const { start, end } = state;
      const digits = text.replace(/\D/g, "");
      if (type.startsWith("insert") && !digits) return null;
      const chars = [...maskedValue(state.value)];
      let caret = nextSlot(start);
      for (const index of slots) if (index >= start && index < end) chars[index] = "_";
      if (type.startsWith("delete") && start === end) {
        const index = type.endsWith("Backward")
          ? reverseSlots.find(index => index < start) : slots.find(index => index >= start);
        if (index !== undefined) { chars[index] = "_"; caret = index; }
      } else if (type.startsWith("insert")) {
        for (const digit of digits) {
          if (caret >= mask.length) break;
          chars[caret] = digit;
          caret = nextSlot(caret + 1);
        }
      }
      return { value: chars.join(""), caret };
    }
    date.addEventListener("focus", () => {
      const normalized = maskedValue(date.value);
      if (date.value === normalized) return;
      const start = date.selectionStart ?? 0, end = date.selectionEnd ?? start;
      date.value = normalized;
      date.setSelectionRange(nextSlot(start), end > start ? end : nextSlot(start));
    });
    date.addEventListener("beforeinput", event => {
      pendingEdit = null;
      if (composition || event.isComposing || !/^(insert|delete)/.test(event.inputType)) return;
      const state = selection();
      if (event.cancelable && (event.inputType.startsWith("delete") || event.data !== null)) {
        event.preventDefault();
        const change = edit(event.inputType, event.data || "", state);
        if (change) render(change, true);
      } else pendingEdit = { state, type: event.inputType };
    });
    date.addEventListener("input", event => {
      if (composition || event.isComposing) return;
      if (pendingEdit) {
        const { state, type } = pendingEdit;
        pendingEdit = null;
        // Mobile keyboards may omit beforeinput.data or disallow cancellation.
        // Recover the inserted text before applying our fixed-slot overwrite.
        const count = date.value.length - state.value.length + state.end - state.start;
        const text = event.data ?? date.value.slice(state.start, state.start + Math.max(0, count));
        const change = edit(type, text, state);
        if (change) render(change);
        else render({ value: state.value, caret: state.start });
        return;
      }
      const raw = date.value;
      if (/^[\d_]{4}-[\d_]{2}-[\d_]{2}$/.test(raw)) return;
      const count = raw.slice(0, date.selectionStart ?? raw.length).replace(/\D/g, "").length;
      const digits = raw.replace(/\D/g, ""), chars = [...mask];
      slots.forEach((index, offset) => { chars[index] = digits[offset] || "_"; });
      render({ value: chars.join(""), caret: slots[count] ?? mask.length });
    });
    date.addEventListener("compositionstart", () => { composition = selection(); pendingEdit = null; });
    date.addEventListener("compositionend", event => {
      const state = composition;
      composition = null;
      if (!state) return;
      const change = edit("insertText", event.data || "", state);
      render(change || { value: state.value, caret: state.start }, true);
    });
    date.addEventListener("paste", event => {
      event.preventDefault();
      const change = edit("insertFromPaste", event.clipboardData.getData("text"));
      if (change) render(change, true);
    });
    date.addEventListener("keydown", event => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || date.selectionStart !== date.selectionEnd) return;
      const caret = date.selectionStart ?? 0;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const position = event.key === "ArrowLeft" ? reverseSlots.find(index => index < caret) ?? 0 : nextSlot(caret + 1);
        date.setSelectionRange(position, position);
      }
    });
    date.addEventListener("click", () => {
      if (date.value === mask && date.selectionStart === date.selectionEnd) date.setSelectionRange(0, 0);
    });
  }
  function buildCalendar(field) {
    const panel = document.createElement("section");
    panel.id = "replay-calendar";
    panel.className = "replay-calendar";
    panel.setAttribute("role", "group");
    panel.setAttribute("aria-label", "Replay date and time (UTC)");
    field.append(panel);
    const dateLabel = document.createElement("label");
    dateLabel.textContent = "Date (UTC)";
    dateLabel.htmlFor = date.id;
    dateLabel.append(date);
    panel.append(dateLabel);
    const header = document.createElement("header");
    panel.append(header);
    let month;
    const previous = button(header, "previous", "Previous month", () => moveMonth(-1));
    previous.removeAttribute("data-tooltip");
    const caption = document.createElement("strong");
    caption.setAttribute("aria-live", "polite");
    header.append(caption);
    const next = button(header, "next", "Next month", () => moveMonth(1));
    next.removeAttribute("data-tooltip");
    const weekdays = document.createElement("div");
    weekdays.className = "replay-calendar-weekdays";
    weekdays.setAttribute("aria-hidden", "true");
    weekdays.innerHTML = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map(day => `<span>${day}</span>`).join("");
    const grid = document.createElement("div");
    grid.className = "replay-calendar-days";
    grid.setAttribute("role", "group");
    grid.setAttribute("aria-label", "Days");
    const timeLabel = document.createElement("div");
    timeLabel.className = "replay-calendar-time";
    timeLabel.textContent = "Time (UTC)";
    const time = document.createElement("div");
    time.className = "replay-time-fields";
    time.setAttribute("role", "group");
    time.setAttribute("aria-label", "Replay time (UTC)");
    const clockMenus = [];
    function timeSelect(name, count) {
      const container = document.createElement("div");
      container.className = "replay-time-select";
      const trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "replay-time-toggle";
      trigger.setAttribute("aria-label", `Replay ${name} (UTC)`);
      trigger.setAttribute("aria-haspopup", "listbox");
      trigger.setAttribute("aria-expanded", "false");
      trigger.innerHTML = '<span>00</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
      const menu = document.createElement("div");
      menu.id = `replay-${name}-menu`;
      menu.className = "replay-time-menu";
      menu.setAttribute("role", "listbox");
      menu.setAttribute("aria-label", `Replay ${name} options (UTC)`);
      trigger.setAttribute("aria-controls", menu.id);
      menu.hidden = true;
      container.append(trigger);
      time.append(container);
      // Keep the list outside the scrollable calendar so it cannot be clipped.
      field.append(menu);
      let value = "00";
      const options = Array.from({ length: count }, (_, index) => {
        const option = document.createElement("button");
        option.type = "button";
        option.tabIndex = -1;
        option.textContent = String(index).padStart(2, "0");
        option.dataset.value = option.textContent;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(index === 0));
        option.addEventListener("click", () => { setValue(option.dataset.value); updateTime(); hide(true); });
        menu.append(option);
        return option;
      });
      function setValue(nextValue) {
        value = nextValue;
        trigger.querySelector("span").textContent = value;
        options.forEach(option => option.setAttribute("aria-selected", String(option.dataset.value === value)));
      }
      function hide(focus = false) {
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        if (focus) trigger.focus({ preventScroll: true });
      }
      function focusOption(index) {
        const option = options[index];
        option.focus({ preventScroll: true });
        menu.scrollTop = option.offsetTop - (menu.clientHeight - option.offsetHeight) / 2;
      }
      function show() {
        clockMenus.forEach(item => item.close());
        menu.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        const view = viewportBounds(), anchor = trigger.getBoundingClientRect(), origin = field.getBoundingClientRect();
        const above = anchor.top - view.top - 8, below = view.bottom - anchor.bottom - 8;
        menu.style.maxHeight = `${Math.max(0, Math.min(160, Math.max(above, below)))}px`;
        const rect = menu.getBoundingClientRect();
        const top = above >= rect.height || above > below ? anchor.top - rect.height - 4 : anchor.bottom + 4;
        const left = Math.max(view.left + 4, Math.min(anchor.left, view.right - rect.width - 4));
        menu.style.left = `${left - origin.left}px`;
        menu.style.top = `${top - origin.top}px`;
        focusOption(Number(value));
      }
      trigger.addEventListener("click", () => menu.hidden ? show() : hide());
      function keydown(event) {
        if (event.key === "Escape" && menu.hidden) return;
        event.stopPropagation();
        if (event.key === "Escape") { event.preventDefault(); hide(true); }
        else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          if (menu.hidden) { show(); return; }
          const current = options.indexOf(document.activeElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? count - 1
            : (current + (event.key === "ArrowUp" ? -1 : 1) + count) % count;
          focusOption(next);
        }
      }
      container.addEventListener("keydown", keydown);
      menu.addEventListener("keydown", keydown);
      const outside = target => !container.contains(target) && !menu.contains(target);
      document.addEventListener("pointerdown", event => { if (outside(event.target)) hide(); }, true);
      field.addEventListener("focusout", event => { if (event.relatedTarget && outside(event.relatedTarget)) hide(); });
      panel.addEventListener("scroll", () => hide());
      window.addEventListener("resize", () => hide());
      window.visualViewport?.addEventListener("resize", () => hide());
      const control = { get value() { return value; }, setValue, close: hide };
      clockMenus.push(control);
      return control;
    }
    const hour = timeSelect("hour", 24);
    const separator = document.createElement("span");
    separator.textContent = ":";
    separator.setAttribute("aria-hidden", "true");
    time.append(separator);
    const minute = timeSelect("minute", 60);
    timeLabel.append(time);
    panel.append(weekdays, grid, timeLabel);
    function dayKey(value) { return value.toISOString().slice(0, 10); }
    function selected() { return selectedDateTime() || parseDate(date.max) || new Date(); }
    function clockValue() { return `${hour.value}:${minute.value}`; }
    function updateTime() {
      validateDate();
    }
    function render(focusKey) {
      const value = selected(), selectedKey = dayKey(value);
      const year = month.getUTCFullYear(), index = month.getUTCMonth();
      caption.textContent = month.toLocaleDateString("en-US", { timeZone: "UTC", month: "long", year: "numeric" });
      const previousEnd = new Date(month), nextStart = new Date(month);
      previousEnd.setUTCMinutes(-1);
      nextStart.setUTCMonth(index + 1);
      previous.disabled = year <= 1 && index === 0 || Boolean(date.min && previousEnd < parseDate(date.min));
      next.disabled = year >= 9999 && index === 11 || Boolean(date.max && nextStart > parseDate(date.max));
      const firstWeekday = month.getUTCDay();
      grid.replaceChildren();
      for (let i = 0; i < 42; i++) {
        const day = new Date(month);
        day.setUTCDate(1 - firstWeekday + i);
        const key = dayKey(day), cell = document.createElement("button");
        cell.type = "button";
        cell.textContent = String(day.getUTCDate());
        cell.dataset.day = key;
        cell.classList.toggle("outside", day.getUTCMonth() !== index);
        cell.disabled = day.getUTCFullYear() < 1 || day.getUTCFullYear() > 9999 ||
          Boolean(date.min && key < date.min.slice(0, 10) || date.max && key > date.max.slice(0, 10));
        cell.setAttribute("aria-label", key);
        cell.setAttribute("aria-pressed", String(key === selectedKey));
        cell.tabIndex = -1;
        grid.append(cell);
      }
      const focused = [...grid.children].find(cell => !cell.disabled && cell.dataset.day === (focusKey || selectedKey)) ||
        [...grid.children].find(cell => !cell.disabled);
      if (focused) { focused.tabIndex = 0; if (focusKey) focused.focus({ preventScroll: true }); }
    }
    function setMonth(value) { month = new Date(value); month.setUTCDate(1); month.setUTCHours(0, 0, 0, 0); }
    function moveMonth(delta) { month.setUTCMonth(month.getUTCMonth() + delta); render(); }
    function close() {
      clockMenus.forEach(item => item.close());
    }
    function open(value) {
      date.value = dayKey(value);
      setMonth(value);
      hour.setValue("00");
      minute.setValue("00");
      render(dayKey(value));
    }
    grid.addEventListener("click", event => {
      const cell = event.target.closest("[data-day]");
      if (!cell || cell.disabled) return;
      date.value = cell.dataset.day;
      validateDate();
      setMonth(selectedDateTime());
      render(cell.dataset.day);
    });
    grid.addEventListener("keydown", event => {
      const cell = event.target.closest("[data-day]");
      if (!cell) return;
      const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
      if (delta === undefined) return;
      event.preventDefault();
      const target = parseDate(`${cell.dataset.day} 00:00`);
      target.setUTCDate(target.getUTCDate() + delta);
      const key = dayKey(target);
      if (date.min && key < date.min.slice(0, 10) || date.max && key > date.max.slice(0, 10) || target.getUTCFullYear() < 1 || target.getUTCFullYear() > 9999) return;
      setMonth(target);
      render(key);
    });
    date.addEventListener("input", () => {
      validateDate();
      if (selectedDateTime()) {
        setMonth(selected());
        render();
      }
    });
    document.addEventListener("pointerdown", event => { if (!field.contains(event.target)) close(); }, true);
    field.addEventListener("focusout", event => { if (event.relatedTarget && !field.contains(event.relatedTarget)) close(); });
    return { close, open, get timeValue() { return clockValue(); } };
  }
  function buildChooser() {
    chooser = document.createElement("form");
    chooser.className = "replay-chooser";
    chooser.setAttribute("role", "dialog");
    chooser.setAttribute("aria-label", "Replay date and time (UTC)");
    chooser.hidden = true;
    date = document.createElement("input");
    date.id = "replay-date";
    date.type = "text";
    date.placeholder = "YYYY-MM-DD";
    date.autocomplete = "off";
    date.inputMode = "numeric";
    // The mask enforces length after native edits; maxlength blocks some IMEs
    // before they can replace a digit in an already-filled date.
    date.required = true;
    bindDateInput();
    const field = document.createElement("div");
    field.className = "replay-date-field";
    calendar = buildCalendar(field);
    chooser.append(field);
    const actions = document.createElement("div");
    actions.className = "replay-chooser-actions";
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = "Start replay";
    actions.append(submit);
    button(actions, "select", "Select a bar", () => setSelection(!selecting));
    button(actions, "close", "Cancel", () => { chooser.hidden = true; setSelection(false); });
    chooser.append(actions);
    chooser.addEventListener("submit", event => {
      event.preventDefault();
      if (validateDate()) launch(selectedDateTime().getTime() / 1000);
      else date.reportValidity();
    });
    document.body.append(chooser);
    window.addEventListener("resize", placeChooser);
    window.visualViewport?.addEventListener("resize", placeChooser);
    window.visualViewport?.addEventListener("scroll", placeChooser);
    new ResizeObserver(placeChooser).observe(chooser);
    App.chart.chart.subscribeClick(event => {
      if (selecting && typeof event.time === "number") launch(event.time);
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        const restoreChooser = selecting && selectionChooserWasOpen;
        setSelection(false);
        chooser.hidden = !restoreChooser;
        App.ui.refreshMobileViewportLock();
        placeChooser();
        if (restoreChooser) chooser.querySelector('[aria-label="Select a bar"]').focus({ preventScroll: true });
      }
      if (!active || event.defaultPrevented || event.isComposing ||
          event.target.closest?.("input, textarea, select, [contenteditable], .replay-chooser")) return;
      if (event.code === "Space" && !play?.disabled) { event.preventDefault(); play.click(); }
      if (event.key === "ArrowRight" && !step?.disabled) { event.preventDefault(); step.click(); }
    });
  }
  function addLiveChart() {
    const panel = livePanel = document.createElement("section");
    panel.className = "replay-live";
    panel.id = "replay-live";
    panel.hidden = true;
    const label = document.createElement("button");
    label.type = "button";
    label.className = "replay-live-handle";
    label.setAttribute("aria-label", "Move live chart");
    label.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="19" r="1.5"/><circle cx="15" cy="19" r="1.5"/></svg><span>Live</span>';
    panel.append(label);
    const frame = liveFrame = document.createElement("iframe");
    const url = new URL(location.href);
    url.searchParams.delete("replay");
    url.searchParams.set("replay-inset", "1");
    frame.src = url.href;
    frame.title = "Live session chart";
    document.body.append(panel);
    let drag = null, position = null;
    function place(left, top) {
      const rect = panel.getBoundingClientRect();
      position = {
        left: Math.max(4, Math.min(left, innerWidth - rect.width - 4)),
        top: Math.max(4, Math.min(top, innerHeight - rect.height - 4)),
      };
      panel.style.right = "auto";
      panel.style.left = `${position.left}px`;
      panel.style.top = `${position.top}px`;
    }
    label.addEventListener("pointerdown", event => {
      if (event.button !== 0 || event.isPrimary === false || drag) return;
      event.preventDefault();
      event.stopPropagation();
      const rect = panel.getBoundingClientRect();
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
      panel.classList.add("dragging");
      label.setPointerCapture(event.pointerId);
    });
    window.addEventListener("pointermove", event => {
      if (!drag || event.pointerId !== drag.id) return;
      event.preventDefault();
      event.stopPropagation();
      place(drag.left + event.clientX - drag.x, drag.top + event.clientY - drag.y);
    }, { capture: true, passive: false });
    function endDrag(event) {
      if (!drag || event.pointerId !== drag.id) return;
      const id = drag.id;
      drag = null;
      panel.classList.remove("dragging");
      if (label.hasPointerCapture(id)) label.releasePointerCapture(id);
    }
    for (const name of ["pointerup", "pointercancel"]) window.addEventListener(name, endDrag, true);
    window.addEventListener("blur", () => { if (drag) endDrag({ pointerId: drag.id }); });
    label.addEventListener("keydown", event => {
      const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
      if (!delta) return;
      event.preventDefault();
      event.stopPropagation();
      const rect = panel.getBoundingClientRect(), distance = event.shiftKey ? 30 : 10;
      place(rect.left + delta[0] * distance, rect.top + delta[1] * distance);
    });
    function constrain() { if (position && !panel.hidden) place(position.left, position.top); }
    window.addEventListener("resize", constrain);
    new ResizeObserver(constrain).observe(panel);
  }
  function setLiveVisible(visible, persist = true) {
    if (persist) {
      livePreferred = visible;
      try { localStorage.setItem(App.config.storageKey("replay-live-visible"), String(visible)); } catch {}
    }
    liveToggle.hidden = mobile.matches;
    if (!livePanel && !mobile.matches) addLiveChart();
    visible = visible && !mobile.matches;
    // Removing the iframe also stops its websocket, timers and indicator worker.
    if (livePanel) {
      livePanel.hidden = !visible;
      if (visible && !liveFrame.isConnected) livePanel.append(liveFrame);
      else if (!visible) liveFrame.remove();
    }
    liveToggle.setAttribute("aria-pressed", String(visible));
    liveToggle.dataset.tooltip = visible ? "Hide LIVE chart" : "Show LIVE chart";
    liveToggle.setAttribute("aria-label", liveToggle.dataset.tooltip);
  }
  function plotsFromFrames() {
    return definitions.map(definition => ({ ...definition, data: frames.map(frame => ({
      time: frame.bar.time, value: frame.values[definition.title] ?? null,
    })) }));
  }
  function renderHistory() {
    const bars = frames.map(frame => frame.bar);
    if (!bars.length) throw new Error("Replay returned no candles");
    App.data.clearPlotData();
    App.data.rebuildOhlcvCache(bars);
    App.state.firstBarTime = bars[0].time;
    const last = bars.at(-1);
    App.state.lastOhlcv = last;
    App.state.lastBarTime = last.time;
    App.state.lastPrice = last.close;
    App.chart.candleSeries.setData(bars);
    App.chart.volumeSeries.setData(bars.map(bar => ({ time: bar.time, value: bar.volume,
      color: bar.close >= bar.open ? "#26a69a" : "#ef5350" })));
    App.data.renderPlotData(plotsFromFrames());
    const events = frames.flatMap(frame => frame.events);
    App.data.renderTradeHistory(events.filter(event => event.type !== "plotchar"));
    App.data.renderPlotcharHistory(events.filter(event => event.type === "plotchar"));
    const cursor = App.chart.chart.timeScale().timeToIndex(last.time);
    App.chart.chart.timeScale().setVisibleLogicalRange({ from: cursor - 60, to: cursor + 60 });
    App.state.initialLoadDone = true;
    App.ui.setChartInfo(App.chart.formatOhlcvText(last));
  }
  function renderFrame(frame, options) {
    const scale = App.chart.chart.timeScale();
    const range = scale.getVisibleLogicalRange();
    const previousCursor = scale.timeToIndex(App.state.lastBarTime);
    frames.push(frame);
    if (frame.bar.time > (replayCache.at(-1)?.bar.time ?? -Infinity)) replayCache.push(frame);
    const changed = JSON.stringify(definitions) !== JSON.stringify(options);
    definitions = options;
    // Incremental chart updates use the same plot and marker renderer as live charts.
    App.ws.handleMessage({ type: "bar", data: frame.bar });
    if (changed) App.data.renderPlotData(plotsFromFrames());
    else for (const definition of definitions) App.data.updatePlotSeries(
      App.chart, App.collections, definition.title, frame.bar.time, frame.values[definition.title] ?? null);
    for (const event of frame.events) App.ws.handleMessage(event);
    App.ui.setChartInfo(App.chart.formatOhlcvText(frame.bar));
    const cursor = scale.timeToIndex(frame.bar.time);
    if (range && previousCursor !== null && cursor !== null) {
      // Base the shift on the pre-update range, not LWC's already-shifted range.
      const delta = range.to >= previousCursor - 5 ? cursor - previousCursor : 0;
      scale.setVisibleLogicalRange({ from: range.from + delta, to: range.to + delta });
    }
  }
  function start(time) {
    stop();
    const current = generation;
    frames = [];
    definitions = [];
    App.chart.resetChartState(true);
    App.state.lastOhlcv = null;
    ended = false;
    busy = true;
    message("Preparing replay...");
    const url = new URL(location.href);
    url.searchParams.set("replay", time);
    history.replaceState(history.state, "", url);
    const ws = socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/replay/${encodeURIComponent(App.config.runtimeId)}`);
    ws.onopen = () => {
      if (current !== generation) return;
      ws.send(JSON.stringify({ type: "start", time }));
      heartbeat = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}'); }, 30000);
    };
    ws.onmessage = event => {
      if (current !== generation) return;
      try {
        const data = JSON.parse(event.data);
        if (data.type === "error") throw new Error(data.error);
        if (data.type === "preparing") {
          message(`Preparing replay... ${Math.floor(data.done / data.total * 100)}%`);
          if (data.first) date.min = new Date(data.first * 1000).toISOString().slice(0, 16);
          if (data.last) date.max = new Date(data.last * 1000).toISOString().slice(0, 16);
        } else if (data.type === "history_reset") {
          frames = [];
          definitions = [];
          App.chart.resetChartState(true);
          App.state.lastOhlcv = null;
        } else if (data.type === "history") frames.push(...data.frames);
        else if (data.type === "ready") {
          definitions = data.plots;
          App.state.scriptTitle = data.title;
          App.state.scriptTitleVisible = true;
          if (!replayCache.length) replayCache = frames.slice();
          renderHistory();
          lastTime = data.time;
          ready = true;
          busy = false;
          ended = data.done;
          rewindFrom = data.seek_supported === true && Number.isFinite(data.rewind_from) ? data.rewind_from : null;
          rewindTo = data.seek_supported === true && Number.isFinite(data.rewind_to) ? data.rewind_to : null;
          startMarker.setTime(Number.isFinite(data.rewind_from) ? data.rewind_from : (startMarker.time ?? data.time));
          message(textTime(lastTime));
          if (pendingSeek !== null) seek(pendingSeek);
        } else if (data.type === "frame") {
          if (data.frame) { renderFrame(data.frame, data.plots); lastTime = data.frame.bar.time; }
          if (rewindTo !== null) rewindTo = Math.max(rewindTo, lastTime);
          busy = false;
          ended = data.done;
          message(textTime(lastTime) + (ended ? " · End" : ""));
          if (pendingSeek !== null) seek(pendingSeek);
          else if (ended) pause(); else schedule();
        }
        controls();
      } catch (error) { stop(); message(error.message, true); }
    };
    ws.onclose = () => {
      if (current !== generation) return;
      stop();
      message("Replay disconnected. Select a date to restart.", true);
    };
    ws.onerror = () => { if (current === generation) message("Replay connection failed", true); };
  }
  async function init() {
    document.body.classList.toggle("replay-page", active);
    document.body.classList.toggle("replay-inset", inset);
    if (inset) return;
    buildChooser();
    const entry = button(document.getElementById("drawing-toolbar"), "replay", "Bar replay", openChooser);
    entry.id = "chart-replay-toggle";
    entry.className = "drawing-tool-btn";
    entry.querySelector("svg").classList.add("tool-icon");
    if (!active) return;
    cacheCandleReads();
    startMarker = new ReplayStartMarker(App.chart.chart);
    App.chart.candleSeries.attachPrimitive(startMarker);
    toolbar = document.createElement("section");
    toolbar.className = "replay-toolbar";
    toolbar.setAttribute("aria-label", "Bar replay");
    const toolbarControls = document.createElement("div");
    toolbarControls.className = "replay-toolbar-controls";
    toolbar.append(toolbarControls);
    button(toolbarControls, "calendar", "Select date", openChooser);
    button(toolbarControls, "select", "Select a bar", () => setSelection(!selecting));
    startButton = button(toolbarControls, "start", "Go to replay start", goToStart);
    play = button(toolbarControls, "play", "Play", () => {
      if (playing) pause();
      else { playing = true; play.innerHTML = icon("pause"); play.dataset.tooltip = "Pause"; play.setAttribute("aria-label", "Pause"); requestStep(); }
      controls();
    });
    step = button(toolbarControls, "step", "Next bar", requestStep);
    buildSpeedMenu(toolbarControls);
    liveToggle = button(toolbarControls, "live", "Hide LIVE chart", () => setLiveVisible(!livePreferred));
    liveToggle.setAttribute("aria-controls", "replay-live");
    status = document.createElement("output");
    status.setAttribute("aria-live", "polite");
    toolbar.append(status);
    button(toolbar, "close", "Exit replay", () => {
      stop();
      const url = new URL(location.href);
      url.searchParams.delete("replay");
      location.replace(url);
    }).classList.add("replay-toolbar-exit");
    document.body.append(toolbar);
    addToolbarHandle();
    try { livePreferred = localStorage.getItem(App.config.storageKey("replay-live-visible")) !== "false"; } catch {}
    setLiveVisible(livePreferred, false);
    mobile.addEventListener("change", () => {
      setLiveVisible(livePreferred, false);
      App.ui.refreshMobileViewportLock();
    });
    controls();
    await App.data.loadChartInfo();
    App.state.runnerConnected = false;
    App.state.minuteChartAvailable = false;
    // Replay preferences and legend changes must not update the live session settings.
    const requestSettings = App.indicators.requestSettings.bind(App.indicators);
    App.indicators.requestSettings = payload => payload ? Promise.resolve(payload.settings) : requestSettings();
    App.indicators.init();
    App.measure.init();
    App.trendline.init();
    App.chart.attachResizeHandler();
    App.chart.attachNavButtons();
    // Navigation in replay stays within revealed data and never requests live history.
    App.history.goToEdge = async edge => {
      const size = App.collections.ohlcvData.length;
      const count = Math.min(120, size);
      App.chart.chart.timeScale().setVisibleLogicalRange(edge === "start"
        ? { from: -1, to: count } : { from: size - count, to: size + 5 });
    };
    document.addEventListener("visibilitychange", () => { if (document.hidden) { pause(); controls(); } });
    window.addEventListener("pagehide", stop);
    start(Number(params.get("replay")));
  }
  return { active, inset, init, queueCrosshair, get selecting() { return selecting; },
    get dateOpen() { return Boolean(chooser && !chooser.hidden); } };
})();
