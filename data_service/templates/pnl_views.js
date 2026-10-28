(() => {
  "use strict";
  const DAY = 86400000;
  const states = new WeakMap();
  let activePopover = null;
  const dateKey = (value) => new Date(value).toISOString().slice(0, 10);
  const monthKey = (value) => dateKey(value).slice(0, 7);
  const amount = (value) => Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: Math.abs(value) > 0 && Math.abs(value) < 1 ? 6 : 2,
  });
  const signed = (value) => `${value > 0 ? "+" : ""}${amount(value)}`;
  const signClass = (value) => value > 0 ? "account-pnl-positive" : value < 0 ? "account-pnl-negative" : "";
  const node = (tag, cls, text) => {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const svgNode = (tag, attrs, text) => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    if (text !== undefined) element.textContent = text;
    return element;
  };

  function aggregate(rows, exchange, currency) {
    const result = new Map();
    for (const row of rows) {
      if ((exchange && row.exchange !== exchange) || row.currency !== currency) continue;
      let day = result.get(row.date);
      if (!day) {
        day = { date: row.date, value: 0, count: 0, complete: true };
        result.set(row.date, day);
      }
      day.value += Number(row.realized_pnl) || 0;
      day.count += Number(row.closed_positions) || 0;
      day.complete = day.complete && row.complete !== false;
    }
    return result;
  }

  function dailyPoints(days, from, to) {
    const points = [];
    let cumulative = 0;
    for (let time = Date.parse(from); time <= Date.parse(to); time += DAY) {
      const date = dateKey(time);
      const day = days.get(date);
      cumulative += day?.value || 0;
      points.push({ date, value: day?.value || 0, cumulative, complete: day?.complete !== false });
    }
    return points;
  }

  function accountBreakdown(rows, date, exchange, currency) {
    return rows.filter((row) => row.date === date && row.currency === currency
      && (!exchange || row.exchange === exchange) && row.account)
      .sort((a, b) => a.exchange.localeCompare(b.exchange) || a.account.localeCompare(b.account));
  }

  function closeDetails(host) {
    if (!activePopover || (host && activePopover.host !== host)) return;
    activePopover.anchor.setAttribute("aria-expanded", "false");
    activePopover.element.remove();
    activePopover = null;
  }

  function positionDetails() {
    if (!activePopover) return;
    const { element, anchor } = activePopover;
    const rect = anchor.getBoundingClientRect();
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0;
    const top = viewport?.offsetTop || 0;
    const width = viewport?.width || window.innerWidth;
    const height = viewport?.height || window.innerHeight;
    element.style.maxHeight = `${Math.max(100, height - 24)}px`;
    element.style.width = `${Math.min(width <= 720 ? 260 : 320, width - 24)}px`;
    const box = element.getBoundingClientRect();
    element.style.left = `${Math.max(left + 12, Math.min(rect.left + rect.width / 2 - box.width / 2, left + width - box.width - 12))}px`;
    const below = rect.bottom + 8;
    const y = below + box.height <= top + height - 12 ? below : rect.top - box.height - 8;
    element.style.top = `${Math.max(top + 12, Math.min(y, top + height - box.height - 12))}px`;
  }

  function openDetails(host, anchor, date, state) {
    closeDetails();
    const rows = accountBreakdown(state.payload.daily || [], date, state.options.exchange, state.currency);
    const popover = node("section", "pnl-day-popover");
    popover.setAttribute("role", "dialog");
    popover.setAttribute("aria-label", `${date} PnL by account`);
    const heading = node("div", "pnl-day-popover-heading");
    heading.append(node("strong", "", date + " UTC"), node("span", "", state.currency));
    popover.append(heading);
    const columns = node("div", "pnl-day-breakdown-row pnl-day-breakdown-labels");
    ["Account", "PnL"].forEach((label) => columns.append(node("span", "", label)));
    popover.append(columns);
    const list = node("div", "pnl-day-breakdown-list");
    let exchange = null;
    for (const row of rows) {
      if (row.exchange !== exchange) {
        exchange = row.exchange;
        list.append(node("strong", "pnl-day-breakdown-exchange", ({ binance: "Binance", bitget: "Bitget", bybit: "Bybit", okx: "OKX", hyperliquid: "Hyperliquid" })[exchange] || exchange));
      }
      const item = node("div", "pnl-day-breakdown-row");
      item.append(node("span", "pnl-day-breakdown-account", row.account + (row.complete === false ? "*" : "")));
      const pnl = Number(row.realized_pnl);
      const available = row.realized_pnl != null && Number.isFinite(pnl);
      item.append(node("strong", available ? (pnl >= 0 ? "account-pnl-positive" : "account-pnl-negative") : "", available ? signed(pnl) : "—"));
      list.append(item);
    }
    if (!rows.length) list.append(node("div", "pnl-day-breakdown-empty", "Account details unavailable."));
    popover.append(list);
    if (rows.length) {
      const total = rows.reduce((sum, row) => sum + (Number(row.realized_pnl) || 0), 0);
      const footer = node("div", "pnl-day-popover-total");
      footer.append(node("span", "", "Net PnL"), node("strong", total >= 0 ? "account-pnl-positive" : "account-pnl-negative", `${signed(total)} ${state.currency}${rows.some((row) => row.complete === false) ? "*" : ""}`));
      popover.append(footer);
    }
    document.body.append(popover);
    anchor.setAttribute("aria-expanded", "true");
    activePopover = { host, anchor, date, element: popover };
    positionDetails();
  }

  if (typeof document !== "undefined") {
    document.addEventListener("pointerdown", (event) => {
      if (!activePopover || activePopover.element.contains(event.target)) return;
      const day = event.target.closest(".pnl-day");
      if (activePopover.host.contains(event.target) && day && !day.disabled) return;
      closeDetails();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && activePopover) {
        closeDetails();
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    });
    window.addEventListener("resize", positionDetails, { passive: true });
    window.addEventListener("scroll", positionDetails, { passive: true, capture: true });
  }

  function iconButton(label, direction, onClick) {
    const button = node("button", "btn btn-icon pnl-icon");
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.dataset.tooltip = label;
    const icon = svgNode("svg", { class: "icon", viewBox: "0 0 24 24", "aria-hidden": "true" });
    icon.append(svgNode("path", { d: direction === "left" ? "m15 18-6-6 6-6" : "m9 18 6-6-6-6" }));
    button.append(icon);
    button.addEventListener("click", onClick);
    return button;
  }

  function render(host, payload, options) {
    let state = states.get(host);
    if (!state) {
      state = { currency: "", month: "", selected: "", signature: "" };
      states.set(host, state);
      state.observer = new ResizeObserver(() => {
        const width = Math.round(host.clientWidth);
        if (width > 0 && width !== state.width && state.payload) {
          state.signature = "";
          render(host, state.payload, state.options);
        }
      });
      state.observer.observe(host);
    }
    state.width = Math.round(host.clientWidth);
    state.payload = payload;
    state.options = options;
    const rows = Array.isArray(payload.daily) ? payload.daily : [];
    const scoped = rows.filter((row) => !options.exchange || row.exchange === options.exchange);
    const currencies = [...new Set([
      ...scoped.map((row) => row.currency),
      ...(payload.results || []).filter((row) => !options.exchange || row.exchange === options.exchange).map((row) => row.currency),
    ])].sort((a, b) => (a === "USDT" ? -1 : b === "USDT" ? 1 : a.localeCompare(b)));
    if (!currencies.includes(state.currency)) state.currency = currencies[0] || "USDT";
    const today = dateKey(payload.period?.to || payload.collected_at || Date.now());
    const earliest = scoped.map((row) => row.date).sort()[0] || today;
    const from = payload.period?.from ? dateKey(payload.period.from) : earliest;
    const minMonth = monthKey(from);
    const maxMonth = monthKey(today);
    if (!state.month || state.month < minMonth || state.month > maxMonth) state.month = maxMonth;
    const signature = JSON.stringify([options, rows, currencies, from, today, state.currency, state.month, state.selected]);
    if (signature === state.signature) return;
    const detailDate = activePopover?.host === host ? activePopover.date : null;
    closeDetails(host);
    state.signature = signature;
    host.replaceChildren();
    const rerender = () => { state.signature = ""; render(host, payload, options); };
    const header = node("div", "pnl-visual-heading");
    header.append(node("span", "", "Realized PnL · UTC"));
    const currencyControls = node("div", "pnl-currencies");
    for (const currency of currencies) {
      const button = node("button", currency === state.currency ? "active" : "", currency);
      button.type = "button";
      button.setAttribute("aria-pressed", String(currency === state.currency));
      button.addEventListener("click", () => { state.currency = currency; rerender(); });
      currencyControls.append(button);
    }
    header.append(currencyControls);
    host.append(header);
    if (!Array.isArray(payload.daily)) {
      host.append(node("div", "assets-state", "Loading daily PnL..."));
      return;
    }
    const days = aggregate(rows, options.exchange, state.currency);
    if (options.mode === "calendar") {
      renderCalendar(host, days, state, { from, today, minMonth, maxMonth }, rerender);
      const anchor = detailDate && host.querySelector(`[data-pnl-date="${detailDate}"]`);
      if (anchor && !anchor.disabled) openDetails(host, anchor, detailDate, state);
    } else renderGraph(host, days, from, today, state.currency);
  }

  function renderCalendar(host, days, state, bounds, rerender) {
    const first = new Date(`${state.month}-01T00:00:00Z`);
    const toolbar = node("div", "pnl-calendar-heading");
    const previous = iconButton("Previous month", "left", () => {
      state.month = monthKey(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() - 1, 1)); rerender();
    });
    const next = iconButton("Next month", "right", () => {
      state.month = monthKey(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1)); rerender();
    });
    previous.disabled = state.month <= bounds.minMonth;
    next.disabled = state.month >= bounds.maxMonth;
    const title = node("strong", "", first.toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" }));
    toolbar.append(previous, title, next);
    host.append(toolbar);
    const monthDays = [...days.values()].filter((day) => day.date.startsWith(state.month));
    const total = monthDays.reduce((sum, day) => sum + day.value, 0);
    host.append(node("div", `pnl-month-total ${signClass(total)}`, `${signed(total)} ${state.currency}${monthDays.some((day) => !day.complete) ? "*" : ""}`));
    const grid = node("div", "pnl-calendar-grid");
    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach((day) => grid.append(node("span", "pnl-weekday", day)));
    const offset = (first.getUTCDay() + 6) % 7;
    for (let i = 0; i < 42; i++) {
      const time = first.getTime() + (i - offset) * DAY;
      const date = dateKey(time);
      if (!date.startsWith(state.month)) { grid.append(node("span", "pnl-day-blank")); continue; }
      const record = days.get(date);
      const value = record?.value || 0;
      const button = node("button", `pnl-day ${signClass(value)}`);
      button.type = "button";
      button.dataset.pnlDate = date;
      button.disabled = date < bounds.from || date > bounds.today;
      button.classList.toggle("selected", state.selected === date);
      if (date === bounds.today) button.setAttribute("aria-current", "date");
      button.setAttribute("aria-label", `${date}, ${record ? signed(value) + " " + state.currency : "No recorded PnL"}${record?.complete === false ? ", partial data" : ""}`);
      button.append(node("span", "pnl-day-number", String(new Date(time).getUTCDate())));
      button.append(node("span", "pnl-day-value", record ? `${signed(value)}${record.complete ? "" : "*"}` : ""));
      if (record) {
        button.setAttribute("aria-haspopup", "dialog");
        button.setAttribute("aria-expanded", "false");
      }
      button.addEventListener("click", () => {
        const wasOpen = activePopover?.host === host && activePopover.date === date;
        closeDetails();
        state.selected = date;
        rerender();
        if (record && !wasOpen) openDetails(host, host.querySelector(`[data-pnl-date="${date}"]`), date, state);
      });
      grid.append(button);
    }
    host.append(grid);
    const selected = state.selected.startsWith(state.month) ? state.selected : bounds.today.startsWith(state.month) ? bounds.today : `${state.month}-01`;
    const record = days.get(selected);
    const footer = node("div", "pnl-calendar-detail");
    footer.append(node("span", "", `${selected} · ${record?.count || 0} closed`));
    footer.append(node("strong", signClass(record?.value || 0), `${signed(record?.value || 0)} ${state.currency}${record?.complete === false ? "*" : ""}`));
    host.append(footer);
  }

  function renderGraph(host, days, from, to, currency) {
    const points = dailyPoints(days, from, to);
    if (!points.length) { host.append(node("div", "assets-state", "No daily PnL data.")); return; }
    const heading = node("div", "pnl-graph-heading");
    heading.append(node("span", "", "Cumulative realized PnL"));
    const total = points.at(-1).cumulative;
    heading.append(node("strong", signClass(total), `${signed(total)} ${currency}${[...days.values()].some((day) => !day.complete) ? "*" : ""}`));
    host.append(heading);
    const wrap = node("div", "pnl-graph-plot");
    const width = Math.max(240, host.clientWidth - 28);
    const left = 76;
    const right = width - 12;
    const svg = svgNode("svg", { viewBox: `0 0 ${width} 240`, preserveAspectRatio: "none", role: "img", "aria-label": `Cumulative realized PnL in ${currency}` });
    const values = points.map((point) => point.cumulative);
    const low = Math.min(0, ...values);
    const high = Math.max(0, ...values);
    const pad = (high - low) * 0.08 || 1;
    const min = low - pad;
    const max = high + pad;
    const x = (i) => left + i * (right - left) / Math.max(1, points.length - 1);
    const y = (value) => 212 - (value - min) * 198 / (max - min);
    for (let i = 0; i <= 4; i++) {
      const value = min + (max - min) * i / 4;
      svg.append(svgNode("line", { x1: left, x2: right, y1: y(value), y2: y(value), class: "pnl-gridline" }));
      svg.append(svgNode("text", { x: left - 10, y: y(value) + 4, "text-anchor": "end", class: "pnl-axis" }, amount(value)));
    }
    svg.append(svgNode("line", { x1: left, x2: right, y1: y(0), y2: y(0), class: "pnl-zero-line" }));
    svg.append(svgNode("path", { d: points.map((point, i) => `${i ? "L" : "M"}${x(i)},${y(point.cumulative)}`).join(" "), class: "pnl-line" }));
    const crosshair = svgNode("line", { x1: 780, x2: 780, y1: 14, y2: 212, class: "pnl-crosshair" });
    const dot = svgNode("circle", { cx: x(points.length - 1), cy: y(total), r: 3.5, class: "pnl-point" });
    svg.append(crosshair, dot);
    for (const [name, value] of [["High", Math.max(...values)], ["Low", Math.min(...values)]]) {
      const at = values.indexOf(value);
      const label = svgNode("text", {
        x: x(at),
        y: Math.max(12, Math.min(232, y(value) + (name === "High" ? -10 : 18))),
        "text-anchor": "middle",
        class: "pnl-extreme-label", "data-extreme": name.toLowerCase(),
      }, signed(value));
      label.append(svgNode("title", {}, `${points[at].date} UTC · ${signed(value)} ${currency}`));
      svg.append(svgNode("circle", { cx: x(at), cy: y(value), r: 3, class: "pnl-point pnl-extreme-point" }), label);
    }
    wrap.append(svg);
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", "Daily PnL graph");
    const readout = node("div", "pnl-graph-readout");
    const time = node("span");
    const daily = node("span");
    const cumulative = node("strong");
    readout.append(time, daily, cumulative);
    let index = points.length - 1;
    const show = (i) => {
      index = Math.max(0, Math.min(points.length - 1, i));
      const point = points[index];
      crosshair.setAttribute("x1", x(index)); crosshair.setAttribute("x2", x(index));
      dot.setAttribute("cx", x(index)); dot.setAttribute("cy", y(point.cumulative));
      time.textContent = point.date;
      daily.textContent = `Day ${signed(point.value)} ${currency}${point.complete ? "" : "*"}`;
      daily.className = signClass(point.value);
      cumulative.textContent = `${signed(point.cumulative)} ${currency}`;
      cumulative.className = signClass(point.cumulative);
    };
    const showPointer = (event) => {
      const rect = svg.getBoundingClientRect();
      show(Math.round(((event.clientX - rect.left) / rect.width * width - left) / (right - left) * (points.length - 1)));
    };
    let dragPointer = null;
    wrap.addEventListener("pointerdown", (event) => {
      if (!event.isPrimary || event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      dragPointer = event.pointerId;
      wrap.setPointerCapture(event.pointerId);
      showPointer(event);
    });
    wrap.addEventListener("pointermove", (event) => {
      if (dragPointer === event.pointerId) {
        event.preventDefault();
        event.stopPropagation();
        showPointer(event);
      } else if (dragPointer === null && event.pointerType === "mouse") {
        showPointer(event);
      }
    });
    const endDrag = (event) => {
      if (dragPointer !== event.pointerId) return;
      if (event.type === "pointerup") showPointer(event);
      dragPointer = null;
      if (wrap.hasPointerCapture(event.pointerId)) wrap.releasePointerCapture(event.pointerId);
    };
    wrap.addEventListener("pointerup", endDrag);
    wrap.addEventListener("pointercancel", endDrag);
    wrap.addEventListener("lostpointercapture", () => { dragPointer = null; });
    wrap.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      show(event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 : index + (event.key === "ArrowLeft" ? -1 : 1));
    });
    show(index);
    host.append(wrap);
    svg.querySelectorAll(".pnl-extreme-label").forEach((label) => {
      const halfWidth = label.getComputedTextLength() / 2;
      const center = Number(label.getAttribute("x"));
      label.setAttribute("x", Math.max(left + halfWidth, Math.min(right - halfWidth, center)));
    });
    const range = node("div", "pnl-graph-range");
    range.append(node("span", "", from), node("span", "", to));
    host.append(range, readout);
  }

  globalThis.PnlViews = { render, aggregate, dailyPoints, accountBreakdown, closeDetails };
})();
