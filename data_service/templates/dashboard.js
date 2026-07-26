(function () {
  const MAX_SESSIONS = 20;
  let sessions = [];
  let keepaliveTimer = null;
  let removeSessionId = null;
  let scriptsLoading = false;
  let scriptOptions = [];
  let scriptActiveIndex = -1;
  let draggedSessionId = null;
  let pendingSessionOrder = null;
  const priceFormatters = new Map();
  const desktopReorderQuery = window.matchMedia(
    "(min-width: 721px) and (hover: hover) and (pointer: fine)",
  );
  const desktopCardCarouselQuery = window.matchMedia(
    "(max-width: 720px) and (hover: hover) and (pointer: fine)",
  );
  const mobileAiQuery = window.matchMedia("(max-width: 720px)");
  const mobileHubQuery = window.matchMedia("(max-width: 720px)");
  const calendarColors = [
    "#58a6ff", "#3fb950", "#f2cc60", "#f778ba", "#bc8cff",
    "#ff7b72", "#56d4dd", "#d29922", "#a5d6ff", "#7ee787",
  ];
  let calendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  let calendarEvents = [];
  const calendarForecastStates = new Map();
  let calendarSelectedDate = null;
  let calendarRequestSeq = 0;
  let lastCalendarTouchAt = 0;
  let calendarSuppressTapUntil = 0;
  let calendarOpenTimer = null;
  let calendarCloseTimer = null;
  let registerAnimatedPepeFace = () => {};
  const assetColors = [
    "#3b82f6", "#f59e0b", "#10b981", "#ef4444",
    "#a78bfa", "#22d3ee", "#f472b6", "#94a3b8",
  ];
  let assetsRequestSeq = 0;
  let assetsOpenTimer = null;
  let assetsCloseTimer = null;
  let assetsHaveData = false;
  let assetsPayload = null;
  let selectedAssetExchange = null;

  const el = (id) => document.getElementById(id);

  function calendarForecastState(event) {
    const eventId = String(event && event.id || "");
    let state = calendarForecastStates.get(eventId);
    if (!state) {
      state = {
        status: "idle",
        answer: "",
        html: "",
        updatedAt: "",
        error: "",
        open: false,
        viewed: false,
      };
      calendarForecastStates.set(eventId, state);
    }
    return state;
  }

  function reconcileCalendarForecasts(events) {
    for (const event of events) {
      const state = calendarForecastState(event);
      // Protect only THIS client's in-flight regeneration; a "running" state
      // set from a peer's broadcast must still be updated once the peer is done.
      if (state.localStream) continue;
      if (event && event.forecast_running) {
        state.status = "running";
        state.open = false;
        state.viewed = false;
        continue;
      }
      const forecast = event && event.forecast;
      if (!forecast || typeof forecast !== "object") {
        // no server forecast: clear a stale remote "running" back to idle so a
        // failed peer regeneration doesn't leave Pepe spinning forever
        if (state.status === "running") {
          state.status = "idle";
          state.open = false;
        }
        continue;
      }
      const updatedAt = String(forecast.updated_at || "");
      const changed = state.updatedAt !== updatedAt;
      state.status = "ready";
      state.answer = String(forecast.answer || "");
      state.html = String(forecast.html || "");
      state.updatedAt = updatedAt;
      state.error = "";
      state.viewed = Boolean(forecast.viewed_at);
      if (changed) state.open = false;
    }
  }

  function calendarDateKey(value) {
    const d = value instanceof Date ? value : new Date(value);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function calendarDateFromKey(key) {
    const parts = String(key || "").split("-").map(Number);
    if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return null;
    return new Date(parts[0], parts[1] - 1, parts[2]);
  }

  function calendarGridRange() {
    const start = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth(), 1);
    start.setDate(start.getDate() - start.getDay());
    const end = new Date(start);
    end.setDate(end.getDate() + 41);
    return { start, end };
  }

  function calendarSessionColor(sessionId) {
    let hash = 0;
    const value = String(sessionId || "");
    for (let i = 0; i < value.length; i++) hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0;
    return calendarColors[Math.abs(hash) % calendarColors.length];
  }

  function isCalendarOpen() {
    return !el("calendar-modal").classList.contains("hidden");
  }

  function openHubMenu() {
    const menu = el("hub-menu");
    const backdrop = el("hub-menu-backdrop");
    menu.classList.remove("dragging");
    menu.style.transform = "";
    backdrop.classList.remove("dragging");
    backdrop.style.opacity = "";
    menu.classList.add("open");
    backdrop.classList.add("open");
    menu.setAttribute("aria-hidden", "false");
    backdrop.setAttribute("aria-hidden", "false");
    el("hub-menu-button").setAttribute("aria-expanded", "true");
  }

  function closeHubMenu() {
    const menu = el("hub-menu");
    const backdrop = el("hub-menu-backdrop");
    menu.classList.remove("dragging", "open");
    menu.style.transform = "";
    backdrop.classList.remove("dragging", "open");
    backdrop.style.opacity = "";
    menu.setAttribute("aria-hidden", "true");
    backdrop.setAttribute("aria-hidden", "true");
    el("hub-menu-button").setAttribute("aria-expanded", "false");
  }

  async function openCalendar() {
    closeHubMenu();
    closeAiChat();
    if (calendarCloseTimer !== null) {
      clearTimeout(calendarCloseTimer);
      calendarCloseTimer = null;
    }
    if (calendarOpenTimer !== null) {
      clearTimeout(calendarOpenTimer);
      calendarOpenTimer = null;
    }
    calendarSelectedDate = null;
    const modal = el("calendar-modal");
    const box = modal.querySelector(".calendar-modal-box");
    modal.classList.remove(
      "calendar-closing",
      "calendar-dragging",
      "calendar-swipe-closing",
      "calendar-opening",
      "hidden",
    );
    modal.classList.add("calendar-opening");
    calendarOpenTimer = window.setTimeout(finishCalendarOpening, 230);
    modal.style.background = "";
    box.style.transform = "";
    box.style.transition = "";
    el("calendar-details").classList.add("hidden");
    modal.setAttribute("aria-hidden", "false");
    lockBodyScroll();
    renderCalendar();
    await loadCalendarEvents();
  }

  function finishCalendarOpening() {
    if (calendarOpenTimer !== null) {
      clearTimeout(calendarOpenTimer);
      calendarOpenTimer = null;
    }
    el("calendar-modal").classList.remove("calendar-opening");
  }

  function finishCalendarClose() {
    const modal = el("calendar-modal");
    const box = modal.querySelector(".calendar-modal-box");
    modal.classList.remove(
      "calendar-closing",
      "calendar-dragging",
      "calendar-swipe-closing",
      "calendar-opening",
    );
    modal.classList.add("hidden");
    modal.style.background = "";
    box.style.transform = "";
    box.style.transition = "";
    modal.setAttribute("aria-hidden", "true");
    calendarCloseTimer = null;
    calendarOpenTimer = null;
    unlockBodyScroll();
  }

  function closeCalendar(options = {}) {
    if (!isCalendarOpen()) return;
    const modal = el("calendar-modal");
    if (modal.classList.contains("calendar-closing")) return;
    const fromDrag = options && options.fromDrag === true;
    finishCalendarOpening();
    calendarRequestSeq += 1;
    if (!mobileHubQuery.matches) {
      finishCalendarClose();
      return;
    }
    const box = modal.querySelector(".calendar-modal-box");
    modal.classList.remove("calendar-dragging");
    if (fromDrag) {
      modal.classList.add("calendar-swipe-closing");
      box.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
      window.requestAnimationFrame(() => {
        box.style.transform = "translateY(100dvh)";
      });
    } else {
      box.style.transform = "";
      box.style.transition = "";
    }
    modal.classList.add("calendar-closing");
    calendarCloseTimer = window.setTimeout(finishCalendarClose, 220);
  }

  function isAssetsOpen() {
    return !el("assets-modal").classList.contains("hidden");
  }

  function finishAssetsOpening() {
    if (assetsOpenTimer !== null) {
      clearTimeout(assetsOpenTimer);
      assetsOpenTimer = null;
    }
    el("assets-modal").classList.remove("assets-opening");
  }

  function finishAssetsClose() {
    const modal = el("assets-modal");
    const box = modal.querySelector(".assets-modal-box");
    modal.classList.remove(
      "assets-closing",
      "assets-dragging",
      "assets-swipe-closing",
      "assets-opening",
    );
    modal.classList.add("hidden");
    modal.style.background = "";
    box.style.transform = "";
    box.style.transition = "";
    modal.setAttribute("aria-hidden", "true");
    assetsCloseTimer = null;
    assetsOpenTimer = null;
    unlockBodyScroll();
  }

  async function openAssets() {
    closeHubMenu();
    closeAiChat();
    selectedAssetExchange = null;
    el("assets-title").textContent = "Assets";
    el("assets-back").classList.add("hidden");
    if (assetsCloseTimer !== null) {
      clearTimeout(assetsCloseTimer);
      assetsCloseTimer = null;
    }
    if (assetsOpenTimer !== null) {
      clearTimeout(assetsOpenTimer);
      assetsOpenTimer = null;
    }
    const modal = el("assets-modal");
    const box = modal.querySelector(".assets-modal-box");
    modal.classList.remove(
      "assets-closing",
      "assets-dragging",
      "assets-swipe-closing",
      "assets-opening",
      "hidden",
    );
    modal.classList.add("assets-opening");
    assetsOpenTimer = window.setTimeout(finishAssetsOpening, 230);
    modal.style.background = "";
    box.style.transform = "";
    box.style.transition = "";
    modal.setAttribute("aria-hidden", "false");
    lockBodyScroll();
    await loadAssets(false);
  }

  function closeAssets(options = {}) {
    if (!isAssetsOpen()) return;
    const modal = el("assets-modal");
    if (modal.classList.contains("assets-closing")) return;
    const fromDrag = options && options.fromDrag === true;
    finishAssetsOpening();
    assetsRequestSeq += 1;
    if (!mobileHubQuery.matches) {
      finishAssetsClose();
      return;
    }
    const box = modal.querySelector(".assets-modal-box");
    modal.classList.remove("assets-dragging");
    if (fromDrag) {
      modal.classList.add("assets-swipe-closing");
      box.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
      window.requestAnimationFrame(() => {
        box.style.transform = "translateY(100dvh)";
      });
    } else {
      box.style.transform = "";
      box.style.transition = "";
    }
    modal.classList.add("assets-closing");
    assetsCloseTimer = window.setTimeout(finishAssetsClose, 220);
  }

  function formatAssetValue(value, currency, compact = false) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    let maximumFractionDigits = currency === "KRW" ? 0 : 2;
    if (!compact && Math.abs(number) > 0 && Math.abs(number) < 1) {
      maximumFractionDigits = 6;
    }
    return `${number.toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits,
    })} ${currency}`;
  }

  function formatAssetAmount(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return number.toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 8,
    });
  }

  function assetExchangeLabel(exchange) {
    const value = String(exchange || "Exchange");
    const labels = {
      binance: "Binance",
      bitget: "Bitget",
      bybit: "Bybit",
      hyperliquid: "Hyperliquid",
      okx: "OKX",
      upbit: "Upbit",
    };
    return labels[value.toLowerCase()]
      || `${value.charAt(0).toUpperCase()}${value.slice(1)}`;
  }

  function assetExchangeKey(exchange, quoteCurrency) {
    return `${String(exchange || "")}:${String(quoteCurrency || "")}`;
  }

  function groupAssetPortfolios(portfolios) {
    const groups = new Map();
    portfolios.forEach((portfolio) => {
      const exchange = String(portfolio.exchange || "exchange");
      const quoteCurrency = String(portfolio.quote_currency || "");
      const key = assetExchangeKey(exchange, quoteCurrency);
      let group = groups.get(key);
      if (!group) {
        group = {
          key,
          account: assetExchangeLabel(exchange),
          exchange,
          quote_currency: quoteCurrency,
          total_value: 0,
          account_names: [],
        };
        groups.set(key, group);
      }

      const accountName = String(portfolio.account || exchange);
      group.account_names.push(accountName);
      group.total_value += Number(portfolio.total_value || 0);
    });

    return Array.from(groups.values()).map((group) => ({
      ...group,
      account_count: group.account_names.length,
    }));
  }

  function assetSlices(portfolio) {
    const priced = (Array.isArray(portfolio.assets) ? portfolio.assets : [])
      .filter((asset) => Number(asset.value) > 0)
      .sort((left, right) => Number(right.value) - Number(left.value));
    const isBinance = String(portfolio.exchange || "").toLowerCase() === "binance";
    const visible = isBinance
      ? priced.filter((asset) => Number(asset.value) >= 1)
      : priced.slice();
    const remaining = isBinance
      ? priced.filter((asset) => Number(asset.value) < 1)
      : [];
    if (!isBinance && visible.length > 7) {
      remaining.push(...visible.splice(7));
    }
    if (remaining.length) {
      visible.push({
        currency: "Other",
        value: remaining.reduce((sum, asset) => sum + Number(asset.value || 0), 0),
        weight: remaining.reduce((sum, asset) => sum + Number(asset.weight || 0), 0),
        grouped: true,
      });
    }
    return visible;
  }

  function accountTypeSlices(portfolio) {
    const breakdown = Array.isArray(portfolio.account_type_breakdown)
      ? portfolio.account_type_breakdown
      : [];
    return breakdown.map((item) => {
      const accountType = String(item.account_type || "unknown");
      return {
        currency: accountType.charAt(0).toUpperCase() + accountType.slice(1),
        value: Number(item.total_value || 0),
        accountType: true,
      };
    });
  }

  function renderAssetDonut(portfolio) {
    const wrap = document.createElement("div");
    wrap.className = "assets-portfolio-content";
    const donut = document.createElement("button");
    donut.type = "button";
    donut.className = "assets-donut";
    const center = document.createElement("span");
    center.className = "assets-donut-center";
    donut.appendChild(center);

    const legend = document.createElement("div");
    legend.className = "assets-legend";
    let showAccountTypes = false;

    function renderDonutMode() {
      const slices = showAccountTypes
        ? accountTypeSlices(portfolio)
        : assetSlices(portfolio);
      const total = slices.reduce((sum, slice) => sum + Number(slice.value || 0), 0);
      const positiveSlices = slices
        .map((slice, index) => ({ slice, index }))
        .filter(({ slice }) => Number(slice.value) > 0);
      let cursor = 0;
      const stops = [];
      positiveSlices.forEach(({ slice, index }, positiveIndex) => {
        const color = assetColors[index % assetColors.length];
        const ratio = total > 0 ? Number(slice.value || 0) / total * 100 : 0;
        const end = positiveIndex === positiveSlices.length - 1 ? 100 : cursor + ratio;
        stops.push(`${color} ${cursor.toFixed(3)}% ${end.toFixed(3)}%`);
        cursor = end;
      });
      donut.style.background = stops.length
        ? `conic-gradient(${stops.join(", ")})`
        : "#29313c";
      donut.classList.toggle("showing-account-types", showAccountTypes);
      donut.setAttribute("aria-pressed", String(showAccountTypes));
      donut.setAttribute(
        "aria-label",
        showAccountTypes
          ? `Show asset allocation for ${portfolio.account}`
          : `Show account type allocation for ${portfolio.account}`,
      );
      donut.title = showAccountTypes
        ? "Show asset allocation"
        : "Show account type allocation";
      center.textContent = showAccountTypes ? "Account types (%)" : "Assets (%)";

      legend.replaceChildren();
      slices.forEach((slice, index) => {
        const color = assetColors[index % assetColors.length];
        const ratio = total > 0 ? Number(slice.value || 0) / total * 100 : 0;
        const row = document.createElement("div");
        row.className = "assets-legend-row";
        const dot = document.createElement("span");
        dot.className = "assets-legend-dot";
        dot.style.setProperty("--asset-color", color);
        const currency = document.createElement("span");
        currency.className = "assets-legend-currency";
        currency.textContent = slice.currency;
        const weight = document.createElement("span");
        weight.className = "assets-legend-weight";
        weight.textContent = `${ratio.toFixed(ratio < 0.1 ? 2 : 1)}%`;
        const detail = document.createElement("span");
        detail.className = "assets-legend-value";
        if (slice.accountType) {
          detail.textContent =
            formatAssetValue(slice.value, portfolio.quote_currency, true);
        } else {
          detail.textContent = slice.grouped
            ? formatAssetValue(slice.value, portfolio.quote_currency, true)
            : `${formatAssetAmount(slice.amount)} · ${formatAssetValue(
                slice.value,
                portfolio.quote_currency,
                true,
              )}`;
        }
        row.append(dot, currency, weight, detail);
        legend.appendChild(row);
      });
    }
    donut.addEventListener("click", () => {
      showAccountTypes = !showAccountTypes;
      renderDonutMode();
    });
    renderDonutMode();
    wrap.append(donut, legend);
    return wrap;
  }

  function renderAssetPortfolio(portfolio) {
    const section = document.createElement("section");
    section.className = "assets-portfolio";
    const header = document.createElement("div");
    header.className = "assets-portfolio-header";
    const identity = document.createElement("div");
    identity.className = "assets-account-name";
    identity.textContent = portfolio.account || portfolio.exchange || "Account";
    const exchange = document.createElement("span");
    exchange.className = "assets-exchange-name";
    const accountCount = Number(portfolio.account_count || 0);
    exchange.textContent = accountCount
      ? `${accountCount} account${accountCount === 1 ? "" : "s"}`
      : portfolio.exchange || "";
    identity.appendChild(exchange);
    const total = document.createElement("div");
    total.className = "assets-account-total";
    total.textContent = Number(portfolio.total_value || 0).toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: portfolio.quote_currency === "KRW" ? 0 : 2,
    });
    const quote = document.createElement("span");
    quote.textContent = portfolio.quote_currency || "";
    total.appendChild(quote);
    header.append(identity, total);
    section.append(header, renderAssetDonut(portfolio));

    const footer = document.createElement("div");
    footer.className = "assets-portfolio-footer";
    const statuses = Array.isArray(portfolio.account_type_statuses)
      ? portfolio.account_type_statuses
      : [];
    const availableTypes = Array.from(new Set(statuses
      .filter((item) => item.status === "ok")
      .map((item) => item.account_type)));
    const statusText = document.createElement("span");
    statusText.textContent = availableTypes.length
      ? `Included: ${availableTypes.join(", ")}`
      : "No account balance type was available.";
    footer.appendChild(statusText);

    const accountNames = Array.isArray(portfolio.account_names)
      ? portfolio.account_names
      : [];
    if (accountNames.length) {
      const accountsText = document.createElement("span");
      accountsText.className = "assets-account-list";
      accountsText.textContent = `Accounts: ${accountNames.join(", ")}`;
      accountsText.title = accountNames.join(", ");
      footer.appendChild(accountsText);
    }

    const warnings = Array.isArray(portfolio.warnings) ? portfolio.warnings : [];
    warnings.forEach((warning) => {
      const warningText = document.createElement("span");
      warningText.className = "assets-warning";
      warningText.textContent = String(warning);
      footer.appendChild(warningText);
    });
    section.appendChild(footer);
    return section;
  }

  function renderAssetExchangeRow(portfolio) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "assets-exchange-row";
    row.setAttribute("aria-label", `Show ${portfolio.account} asset details`);

    const identity = document.createElement("span");
    identity.className = "assets-exchange-identity";
    const title = document.createElement("span");
    title.className = "assets-exchange-title";
    title.textContent = portfolio.account;
    const accounts = document.createElement("span");
    accounts.className = "assets-exchange-accounts";
    const accountNames = Array.isArray(portfolio.account_names)
      ? portfolio.account_names
      : [];
    accounts.textContent =
      `${Number(portfolio.account_count || 0)} account${portfolio.account_count === 1 ? "" : "s"}`
      + (accountNames.length ? ` · ${accountNames.join(", ")}` : "");
    accounts.title = accountNames.join(", ");
    identity.append(title, accounts);

    const total = document.createElement("span");
    total.className = "assets-exchange-total";
    total.textContent = Number(portfolio.total_value || 0).toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: portfolio.quote_currency === "KRW" ? 0 : 2,
    });
    const quote = document.createElement("span");
    quote.textContent = portfolio.quote_currency || "";
    total.appendChild(quote);

    const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chevron.classList.add("assets-exchange-chevron");
    chevron.setAttribute("viewBox", "0 0 24 24");
    chevron.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "m9 18 6-6-6-6");
    chevron.appendChild(path);

    row.append(identity, total, chevron);
    row.addEventListener("click", () => {
      selectedAssetExchange = portfolio.key;
      renderAssetsView();
      el("assets-body").scrollTop = 0;
    });
    return row;
  }

  function renderAssetsView() {
    if (!assetsPayload) return;
    const portfolios = Array.isArray(assetsPayload.exchangePortfolios)
      ? assetsPayload.exchangePortfolios
      : [];
    let selected = selectedAssetExchange
      ? portfolios.find((portfolio) => portfolio.key === selectedAssetExchange)
      : null;
    if (selectedAssetExchange && !selected) {
      selectedAssetExchange = null;
      selected = null;
    }

    const container = el("assets-portfolios");
    container.replaceChildren();
    container.className = selected
      ? "assets-portfolios assets-detail"
      : "assets-portfolios assets-overview";
    el("assets-back").classList.toggle("hidden", !selected);
    el("assets-title").textContent = selected
      ? `${selected.account} Assets`
      : "Assets";
    el("assets-summary").classList.toggle(
      "hidden",
      Boolean(selected) || portfolios.length === 0,
    );
    el("assets-empty").classList.toggle("hidden", portfolios.length !== 0);

    if (selected) {
      const accountPortfolios = (Array.isArray(assetsPayload.portfolios)
        ? assetsPayload.portfolios
        : []).filter((portfolio) => (
        assetExchangeKey(portfolio.exchange, portfolio.quote_currency) === selected.key
      ));
      accountPortfolios.forEach((portfolio) => {
        container.appendChild(renderAssetPortfolio(portfolio));
      });
      return;
    }
    const list = document.createElement("div");
    list.className = "assets-exchange-list";
    portfolios.forEach((portfolio) => {
      list.appendChild(renderAssetExchangeRow(portfolio));
    });
    container.appendChild(list);
  }

  function renderAssets(payload) {
    const portfolios = Array.isArray(payload.portfolios) ? payload.portfolios : [];
    const exchangePortfolios = groupAssetPortfolios(portfolios);
    assetsPayload = { ...payload, exchangePortfolios };
    const summary = payload.summary || {};
    const totals = Array.isArray(payload.totals_by_quote) ? payload.totals_by_quote : [];

    const totalsElement = el("assets-summary-totals");
    totalsElement.replaceChildren();
    totals.forEach((total) => {
      const item = document.createElement("span");
      item.className = "assets-summary-total-value";
      item.textContent = Number(total.value || 0).toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: total.currency === "KRW" ? 0 : 2,
      });
      const currency = document.createElement("span");
      currency.className = "assets-summary-total-currency";
      currency.textContent = total.currency || "";
      item.appendChild(currency);
      totalsElement.appendChild(item);
    });
    el("assets-summary-counts").textContent =
      `${exchangePortfolios.length} exchanges · ${Number(summary.accounts || 0)} accounts`;
    const collectedAt = Date.parse(payload.collected_at || "");
    el("assets-updated").textContent = Number.isFinite(collectedAt)
      ? `Updated ${new Date(collectedAt).toLocaleString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })}${payload.cached ? " · cached" : ""}`
      : "";
    renderAssetsView();
    assetsHaveData = true;
  }

  function setAssetsLoading(loading, preserveContent = false) {
    el("assets-refresh").disabled = loading;
    el("assets-refresh").classList.toggle("assets-refreshing", loading);
    el("assets-loading").classList.toggle("hidden", !loading || preserveContent);
    if (loading) el("assets-error").classList.add("hidden");
    if (loading && !preserveContent) {
      assetsPayload = null;
      el("assets-summary").classList.add("hidden");
      el("assets-empty").classList.add("hidden");
      el("assets-portfolios").replaceChildren();
    }
  }

  async function loadAssets(force) {
    const seq = ++assetsRequestSeq;
    const preserveContent = Boolean(force && assetsHaveData);
    setAssetsLoading(true, preserveContent);
    try {
      const payload = await api(`/api/assets${force ? "?refresh=true" : ""}`);
      if (seq !== assetsRequestSeq || !isAssetsOpen()) return;
      renderAssets(payload);
    } catch (error) {
      if (seq !== assetsRequestSeq || !isAssetsOpen()) return;
      const errorElement = el("assets-error");
      errorElement.textContent = `${error.message}\nUse refresh to try again.`;
      errorElement.classList.remove("hidden");
      if (!preserveContent) {
        el("assets-summary").classList.add("hidden");
        el("assets-portfolios").replaceChildren();
      }
    } finally {
      if (seq === assetsRequestSeq) setAssetsLoading(false);
    }
  }

  async function loadCalendarEvents() {
    const seq = ++calendarRequestSeq;
    const range = calendarGridRange();
    const query = new URLSearchParams({
      start: calendarDateKey(range.start),
      end: calendarDateKey(range.end),
    });
    try {
      const payload = await api(`/api/calendar/events?${query}`);
      if (seq !== calendarRequestSeq) return;
      calendarEvents = Array.isArray(payload.events) ? payload.events : [];
      reconcileCalendarForecasts(calendarEvents);
      const updated = String(payload.updated_at || "").trim();
      el("calendar-updated").textContent = updated
        ? `Updated ${new Date(updated).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`
        : "";
      renderCalendar();
    } catch (error) {
      if (seq !== calendarRequestSeq) return;
      calendarEvents = [];
      el("calendar-updated").textContent = "Unavailable";
      renderCalendar();
    }
  }

  function renderCalendar() {
    const title = calendarMonth.toLocaleDateString([], { year: "numeric", month: "long" });
    el("calendar-month-title").textContent = title;

    const byDate = new Map();
    calendarEvents.forEach((event) => {
      const key = String(event.date || "");
      if (!byDate.has(key)) byDate.set(key, []);
      byDate.get(key).push(event);
    });

    const range = calendarGridRange();
    const todayKey = calendarDateKey(new Date());
    const cells = [];
    for (let offset = 0; offset < 42; offset++) {
      const day = new Date(range.start);
      day.setDate(day.getDate() + offset);
      const key = calendarDateKey(day);
      const events = byDate.get(key) || [];
      const outside = day.getMonth() !== calendarMonth.getMonth();
      const classes = [
        "calendar-day",
        outside ? "outside" : "",
        key === todayKey ? "today" : "",
        key === calendarSelectedDate ? "selected" : "",
      ].filter(Boolean).join(" ");
      const sessionIds = [...new Set(events.map((event) => String(event.session_id || "")))];
      const dots = sessionIds.slice(0, 4).map((sessionId) => (
        `<span class="calendar-event-dot" style="--event-color:${calendarSessionColor(sessionId)}"></span>`
      )).join("");
      const eventRow = events.length
        ? `<span class="calendar-day-event-row">${dots}<span class="calendar-event-count">${events.length}</span></span>`
        : "";
      cells.push(
        `<button class="${classes}" type="button" data-calendar-date="${key}" ` +
        `aria-label="${esc(day.toLocaleDateString())}, ${events.length} events">` +
        `<span class="calendar-day-number">${day.getDate()}</span>${eventRow}</button>`,
      );
    }
    el("calendar-grid").innerHTML = cells.join("");
    renderCalendarDetails();
  }

  function renderCalendarDetails() {
    const section = el("calendar-details");
    if (!calendarSelectedDate) {
      section.classList.add("hidden");
      return;
    }
    const selected = calendarDateFromKey(calendarSelectedDate);
    el("calendar-detail-date").textContent = selected
      ? selected.toLocaleDateString([], { weekday: "long", year: "numeric", month: "long", day: "numeric" })
      : calendarSelectedDate;
    const events = calendarEvents.filter((event) => event.date === calendarSelectedDate);
    if (!events.length) {
      el("calendar-detail-events").innerHTML = `<div class="calendar-empty-day">No events</div>`;
      section.classList.remove("hidden");
      return;
    }
    el("calendar-detail-events").innerHTML = events.map((event) => {
      const forecastState = calendarForecastState(event);
      const sessionLabel = [
        event.symbol || event.session_id,
        String(event.exchange || "").toUpperCase(),
        event.timeframe || "",
      ].filter(Boolean).join(" · ");
      const time = event.time
        ? `<span class="calendar-event-time">${esc(event.time)}${event.timezone ? ` ${esc(event.timezone)}` : ""}</span>`
        : "";
      const details = event.details
        ? `<div class="calendar-event-details">${esc(event.details)}</div>`
        : "";
      const source = event.source_url
        ? `<a class="calendar-event-source" href="${esc(event.source_url)}" target="_blank" rel="noopener noreferrer">${esc(event.source_name || "Source")}</a>`
        : "";
      const forecastClasses = [
        "calendar-forecast-pepe",
        forecastState.status === "running" ? "forecast-running" : "",
        forecastState.status === "ready" && !forecastState.viewed ? "forecast-unread" : "",
      ].filter(Boolean).join(" ");
      const forecastLabel = forecastState.status === "running"
        ? `${event.title} forecast in progress`
        : forecastState.status === "ready"
          ? `Open ${event.title} forecast`
          : `Forecast ${event.title}`;
      // ready-but-not-open: a tiny "alien speech" bubble signals Pepe has a
      // forecast to say. It shakes with the button while unread and stays put
      // (no shake) once viewed; hidden while the full bubble is open.
      const forecastSay = (forecastState.status === "ready" && !forecastState.open)
        ? `<span class="pepe-say" aria-hidden="true">&#x2827;&#x2837;&#x282e;</span>`
        : "";
      const forecastButton = `<button class="${forecastClasses}" type="button" ` +
        `data-calendar-forecast-event="${esc(event.id)}" aria-label="${esc(forecastLabel)}" ` +
        `title="${esc(forecastLabel)}" ` +
        `${forecastState.status === "running" ? 'aria-busy="true"' : ""} ` +
        `${aiEnabled ? "" : "disabled"}>${forecastSay}</button>`;
      let forecastBubble = "";
      if (forecastState.open && ["ready", "error"].includes(forecastState.status)) {
        const content = forecastState.status === "error"
          ? `<div class="calendar-forecast-error">${esc(forecastState.error)}</div>`
          : `<div class="calendar-forecast-content ai-msg-markdown">` +
            `${forecastState.html || `<p>${esc(forecastState.answer)}</p>`}</div>`;
        forecastBubble = `<aside class="calendar-forecast-bubble" role="status">` +
          `<div class="calendar-forecast-bubble-header"><span>AI Forecast</span>` +
          `<button class="calendar-forecast-refresh" type="button" ` +
          `data-calendar-forecast-refresh="${esc(event.id)}" title="Refresh forecast" ` +
          `aria-label="Refresh ${esc(event.title)} forecast">` +
          `<svg viewBox="0 0 24 24" aria-hidden="true">` +
          `<path d="M3 12a9 9 0 0 1 15.7-6L21 8"></path><path d="M21 3v5h-5"></path>` +
          `<path d="M21 12a9 9 0 0 1-15.7 6L3 16"></path><path d="M3 21v-5h5"></path>` +
          `</svg></button></div>` +
          `${content}</aside>`;
      }
      return `<article class="calendar-event-card has-forecast" style="--event-color:${calendarSessionColor(event.session_id)}">` +
        `${forecastButton}` +
        `<div class="calendar-event-session">${esc(sessionLabel)}</div>` +
        `<div class="calendar-event-title">${esc(event.title || "Schedule")}${time}</div>` +
        `${details}${source}${forecastBubble}</article>`;
    }).join("");
    hydrateCalendarForecastCards();
    section.classList.remove("hidden");
  }

  function hydrateCalendarForecastCards() {
    const template = el("ai-chat-fab").querySelector("svg.pepe");
    if (!template) return;
    for (const button of el("calendar-detail-events").querySelectorAll(".calendar-forecast-pepe")) {
      if (button.querySelector("svg.pepe")) continue;
      const face = template.cloneNode(true);
      face.classList.remove("pepe-blink");
      for (const pupil of face.querySelectorAll(".pepe-pupil")) {
        pupil.removeAttribute("transform");
      }
      button.appendChild(face);
      registerAnimatedPepeFace(face);
    }
    for (const link of el("calendar-detail-events").querySelectorAll(".calendar-forecast-content a")) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    fitCalendarForecastBubbles();
  }

  function fitCalendarForecastBubbles() {
    const bubbles = document.querySelectorAll(".calendar-forecast-bubble");
    for (const bubble of bubbles) {
      const content = bubble.querySelector(".calendar-forecast-content, .calendar-forecast-error");
      if (content) content.style.maxHeight = "";
    }
    if (!mobileHubQuery.matches || !bubbles.length) return;

    const header = el("calendar-modal").querySelector(".calendar-modal-header");
    if (!header) return;
    const safeTop = header.getBoundingClientRect().bottom + 8;
    const viewportHeight = window.visualViewport
      ? window.visualViewport.height
      : window.innerHeight;
    const cssLimit = Math.min(350, viewportHeight * 0.48);

    for (const bubble of bubbles) {
      const content = bubble.querySelector(".calendar-forecast-content, .calendar-forecast-error");
      if (!content) continue;
      const bubbleRect = bubble.getBoundingClientRect();
      const contentRect = content.getBoundingClientRect();
      const bubbleChrome = bubbleRect.height - contentRect.height;
      const available = Math.floor(bubbleRect.bottom - safeTop - bubbleChrome);
      content.style.maxHeight = `${Math.max(60, Math.min(cssLimit, available))}px`;
    }
  }

  function markCalendarForecastViewed(eventId, state) {
    state.viewed = true;
    const updatedAt = state.updatedAt;
    api(`/api/calendar/events/${encodeURIComponent(eventId)}/forecast/viewed`, {
      method: "POST",
    }).catch(() => {
      if (state.updatedAt !== updatedAt) return;
      state.viewed = false;
      if (isCalendarOpen() && calendarSelectedDate) renderCalendarDetails();
    });
  }

  async function requestCalendarForecast(event) {
    const state = calendarForecastState(event);
    if (!aiEnabled || state.status === "running") return;
    state.status = "running";
    state.localStream = true; // this client owns the stream; reconcile must not clobber it
    state.error = "";
    state.open = false;
    state.viewed = false;
    renderCalendarDetails();

    let completed = false;
    let streamError = "";
    try {
      await streamSse(`/api/calendar/events/${encodeURIComponent(event.id)}/forecast`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }, (eventName, data) => {
        if (eventName === "stream_error") {
          streamError = String(data.error || "Calendar forecast failed");
          return;
        }
        if (eventName !== "done") return;
        completed = true;
        state.status = "ready";
        state.answer = String(data.answer || "");
        state.html = String(data.html || "");
        state.updatedAt = String(data.updated_at || "");
        state.error = "";
        state.open = false;
        state.viewed = false;
        renderCalendarDetails();
      });
      if (streamError) throw new Error(streamError);
      if (!completed) throw new Error("AI forecast ended without a response");
    } catch (error) {
      state.status = "error";
      state.error = error && error.message ? error.message : "Calendar forecast failed";
      state.open = true;
      state.viewed = true;
      renderCalendarDetails();
    } finally {
      state.localStream = false;
      if (isCalendarOpen()) loadCalendarEvents();
    }
  }

  // A peer started regenerating this event's forecast: mirror the running
  // (eye-roll) state so every open dashboard shows it, not the stale answer.
  function applyCalendarForecastRunning(eventId) {
    const state = calendarForecastState({ id: eventId });
    if (state.localStream || state.status === "running") return;
    state.status = "running";
    state.open = false;
    if (isCalendarOpen() && calendarSelectedDate) renderCalendarDetails();
  }

  function toggleCalendarForecast(event) {
    const state = calendarForecastState(event);
    if (state.status === "running") return;
    if (state.status === "idle") {
      requestCalendarForecast(event);
      return;
    }
    const shouldOpen = !state.open;
    for (const other of calendarForecastStates.values()) other.open = false;
    state.open = shouldOpen;
    if (shouldOpen && state.status === "ready") {
      markCalendarForecastViewed(event.id, state);
    }
    renderCalendarDetails();
  }

  function closeCalendarForecastBubbles() {
    let changed = false;
    for (const state of calendarForecastStates.values()) {
      if (!state.open) continue;
      state.open = false;
      changed = true;
    }
    if (!changed) return;
    // re-render so the small "say" bubble returns on the now-closed cards; just
    // removing the big-bubble DOM would leave pepe-say missing until the next
    // full render (that mismatch is the toggle-vs-outside-click difference)
    if (isCalendarOpen() && calendarSelectedDate) {
      renderCalendarDetails();
    } else {
      for (const bubble of document.querySelectorAll(".calendar-forecast-bubble")) {
        bubble.remove();
      }
    }
  }

  function moveCalendarMonth(delta, animate = false) {
    calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + delta, 1);
    calendarSelectedDate = null;
    calendarEvents = [];
    renderCalendar();
    if (animate) {
      const grid = el("calendar-grid");
      const className = delta > 0 ? "calendar-month-next" : "calendar-month-prev";
      grid.classList.remove("calendar-month-next", "calendar-month-prev");
      void grid.offsetWidth;
      grid.classList.add(className);
      window.setTimeout(() => grid.classList.remove(className), 190);
    }
    loadCalendarEvents();
  }

  function initMobileHubMenuSwipe() {
    const menu = el("hub-menu");
    const backdrop = el("hub-menu-backdrop");
    let drag = null;
    let suppressClickUntil = 0;

    menu.addEventListener("pointerdown", (event) => {
      if (!mobileHubQuery.matches || !menu.classList.contains("open") || !event.isPrimary) return;
      drag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        dx: 0,
        axis: null,
      };
      try { menu.setPointerCapture(event.pointerId); } catch {}
    });
    menu.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      if (!drag.axis) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        drag.axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      }
      if (drag.axis !== "x") return;
      event.preventDefault();
      drag.dx = Math.min(0, dx);
      if (Math.abs(drag.dx) > 8) suppressClickUntil = Date.now() + 400;
      const width = Math.max(1, menu.getBoundingClientRect().width);
      menu.classList.add("dragging");
      backdrop.classList.add("dragging");
      menu.style.transform = `translateX(${drag.dx}px)`;
      backdrop.style.opacity = String(Math.max(0, 1 - Math.abs(drag.dx) / width));
    }, { passive: false });
    function endMenuDrag(event, cancelled = false) {
      if (!drag || (event && event.pointerId !== drag.id)) return;
      const current = drag;
      drag = null;
      try { menu.releasePointerCapture(current.id); } catch {}
      menu.classList.remove("dragging");
      backdrop.classList.remove("dragging");
      const shouldClose = !cancelled && current.axis === "x" && current.dx < -64;
      if (shouldClose) {
        closeHubMenu();
        return;
      }
      menu.style.transform = current.axis === "x" ? "translateX(0)" : "";
      backdrop.style.opacity = "";
      window.setTimeout(() => {
        if (!menu.classList.contains("open")) return;
        menu.style.transform = "";
      }, 190);
    }
    menu.addEventListener("pointerup", (event) => endMenuDrag(event));
    menu.addEventListener("pointercancel", (event) => endMenuDrag(event, true));
    menu.addEventListener("click", (event) => {
      if (Date.now() >= suppressClickUntil) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
  }

  function initMobileCalendarGestures() {
    const modal = el("calendar-modal");
    const box = modal.querySelector(".calendar-modal-box");
    const header = modal.querySelector(".calendar-modal-header");
    const grid = el("calendar-grid");
    let closeDrag = null;
    let monthDrag = null;

    header.addEventListener("pointerdown", (event) => {
      if (!mobileHubQuery.matches || !event.isPrimary) return;
      if (event.target && event.target.closest && event.target.closest("button")) return;
      closeDrag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        dy: 0,
        active: false,
      };
      try { header.setPointerCapture(event.pointerId); } catch {}
    });
    header.addEventListener("pointermove", (event) => {
      if (!closeDrag || event.pointerId !== closeDrag.id) return;
      const dx = event.clientX - closeDrag.startX;
      const dy = event.clientY - closeDrag.startY;
      if (!closeDrag.active) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        if (dy <= 0 || Math.abs(dy) <= Math.abs(dx)) {
          closeDrag = null;
          return;
        }
        closeDrag.active = true;
        finishCalendarOpening();
        modal.classList.add("calendar-dragging");
      }
      event.preventDefault();
      closeDrag.dy = Math.max(0, dy);
      box.style.transform = `translateY(${closeDrag.dy}px)`;
    }, { passive: false });
    function endCalendarCloseDrag(event, cancelled = false) {
      if (!closeDrag || (event && event.pointerId !== closeDrag.id)) return;
      const current = closeDrag;
      closeDrag = null;
      try { header.releasePointerCapture(current.id); } catch {}
      modal.classList.remove("calendar-dragging");
      if (!cancelled && current.active && current.dy > 100) {
        closeCalendar({ fromDrag: true });
        return;
      }
      if (!current.active) return;
      box.style.transition = "transform 180ms ease";
      box.style.transform = "translateY(0)";
      window.setTimeout(() => {
        if (!isCalendarOpen() || modal.classList.contains("calendar-closing")) return;
        box.style.transition = "";
        box.style.transform = "";
      }, 190);
    }
    header.addEventListener("pointerup", (event) => endCalendarCloseDrag(event));
    header.addEventListener("pointercancel", (event) => endCalendarCloseDrag(event, true));

    grid.addEventListener("pointerdown", (event) => {
      if (!mobileHubQuery.matches || !event.isPrimary) return;
      monthDrag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        dx: 0,
        axis: null,
      };
    });
    window.addEventListener("pointermove", (event) => {
      if (!monthDrag || event.pointerId !== monthDrag.id) return;
      const dx = event.clientX - monthDrag.startX;
      const dy = event.clientY - monthDrag.startY;
      if (!monthDrag.axis) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        monthDrag.axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
        if (monthDrag.axis === "x") {
          try { grid.setPointerCapture(event.pointerId); } catch {}
        }
      }
      if (monthDrag.axis !== "x") return;
      event.preventDefault();
      monthDrag.dx = dx;
      calendarSuppressTapUntil = Date.now() + 500;
    }, { passive: false });
    function endMonthDrag(event) {
      if (!monthDrag || (event && event.pointerId !== monthDrag.id)) return;
      const current = monthDrag;
      monthDrag = null;
      try { grid.releasePointerCapture(current.id); } catch {}
      if (current.axis !== "x" || Math.abs(current.dx) < 48) return;
      moveCalendarMonth(current.dx < 0 ? 1 : -1, true);
    }
    window.addEventListener("pointerup", endMonthDrag);
    window.addEventListener("pointercancel", endMonthDrag);
    grid.addEventListener("click", (event) => {
      if (Date.now() >= calendarSuppressTapUntil) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
  }

  function initMobileAssetsGestures() {
    const modal = el("assets-modal");
    const box = modal.querySelector(".assets-modal-box");
    const header = modal.querySelector(".assets-modal-header");
    let closeDrag = null;

    header.addEventListener("pointerdown", (event) => {
      if (!mobileHubQuery.matches || !event.isPrimary) return;
      if (event.target && event.target.closest && event.target.closest("button")) return;
      closeDrag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        dy: 0,
        active: false,
      };
      try { header.setPointerCapture(event.pointerId); } catch {}
    });
    header.addEventListener("pointermove", (event) => {
      if (!closeDrag || event.pointerId !== closeDrag.id) return;
      const dx = event.clientX - closeDrag.startX;
      const dy = event.clientY - closeDrag.startY;
      if (!closeDrag.active) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        if (dy <= 0 || Math.abs(dy) <= Math.abs(dx)) {
          closeDrag = null;
          return;
        }
        closeDrag.active = true;
        finishAssetsOpening();
        modal.classList.add("assets-dragging");
      }
      event.preventDefault();
      closeDrag.dy = Math.max(0, dy);
      box.style.transform = `translateY(${closeDrag.dy}px)`;
    }, { passive: false });
    function endAssetsCloseDrag(event, cancelled = false) {
      if (!closeDrag || (event && event.pointerId !== closeDrag.id)) return;
      const current = closeDrag;
      closeDrag = null;
      try { header.releasePointerCapture(current.id); } catch {}
      modal.classList.remove("assets-dragging");
      if (!cancelled && current.active && current.dy > 100) {
        closeAssets({ fromDrag: true });
        return;
      }
      if (!current.active) return;
      box.style.transition = "transform 180ms ease";
      box.style.transform = "translateY(0)";
      window.setTimeout(() => {
        if (!isAssetsOpen() || modal.classList.contains("assets-closing")) return;
        box.style.transition = "";
        box.style.transform = "";
      }, 190);
    }
    header.addEventListener("pointerup", (event) => endAssetsCloseDrag(event));
    header.addEventListener("pointercancel", (event) => endAssetsCloseDrag(event, true));
  }

  function initHubMenuCalendar() {
    const calendarModal = el("calendar-modal");
    calendarModal.addEventListener("touchend", (event) => {
      if (Date.now() < calendarSuppressTapUntil) {
        lastCalendarTouchAt = 0;
        event.preventDefault();
        return;
      }
      const button = event.target && event.target.closest
        ? event.target.closest("button")
        : null;
      if (!button || !calendarModal.contains(button)) return;

      const now = Date.now();
      const isDoubleTap = now - lastCalendarTouchAt < 360;
      lastCalendarTouchAt = now;
      if (!isDoubleTap) return;
      event.preventDefault();
      button.click();
    }, { passive: false });

    el("hub-menu-button").addEventListener("click", openHubMenu);
    el("hub-menu-close").addEventListener("click", closeHubMenu);
    el("hub-menu-backdrop").addEventListener("click", closeHubMenu);
    el("hub-calendar-open").addEventListener("click", openCalendar);
    el("hub-assets-open").addEventListener("click", openAssets);
    el("calendar-close").addEventListener("click", closeCalendar);
    calendarModal.addEventListener("click", (event) => {
      if (event.target === calendarModal) closeCalendar();
    });
    el("calendar-prev").addEventListener("click", () => moveCalendarMonth(-1));
    el("calendar-next").addEventListener("click", () => moveCalendarMonth(1));
    el("calendar-today").addEventListener("click", () => {
      const now = new Date();
      calendarMonth = new Date(now.getFullYear(), now.getMonth(), 1);
      calendarSelectedDate = null;
      calendarEvents = [];
      renderCalendar();
      loadCalendarEvents();
    });
    el("calendar-grid").addEventListener("click", (event) => {
      const day = event.target && event.target.closest
        ? event.target.closest("[data-calendar-date]")
        : null;
      if (!day) return;
      calendarSelectedDate = day.dataset.calendarDate || null;
      renderCalendar();
    });
    el("calendar-detail-events").addEventListener("click", (event) => {
      const refresh = event.target && event.target.closest
        ? event.target.closest("[data-calendar-forecast-refresh]")
        : null;
      const forecastButton = event.target && event.target.closest
        ? event.target.closest("[data-calendar-forecast-event]")
        : null;
      const eventId = refresh
        ? refresh.dataset.calendarForecastRefresh
        : forecastButton && forecastButton.dataset.calendarForecastEvent;
      if (!eventId) return;
      const calendarEvent = calendarEvents.find((item) => item.id === eventId);
      if (!calendarEvent) return;
      event.preventDefault();
      event.stopPropagation();
      if (refresh) requestCalendarForecast(calendarEvent);
      else toggleCalendarForecast(calendarEvent);
    });
    document.addEventListener("pointerdown", (event) => {
      if (!isCalendarOpen()) return;
      const target = event.target && event.target.closest ? event.target : null;
      if (target && target.closest(".calendar-forecast-bubble, [data-calendar-forecast-event]")) return;
      closeCalendarForecastBubbles();
    }, { passive: true });
    window.addEventListener("resize", fitCalendarForecastBubbles, { passive: true });
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", fitCalendarForecastBubbles, { passive: true });
    }
    initMobileHubMenuSwipe();
    initMobileCalendarGestures();
    el("assets-close").addEventListener("click", closeAssets);
    el("assets-back").addEventListener("click", () => {
      selectedAssetExchange = null;
      renderAssetsView();
      el("assets-body").scrollTop = 0;
    });
    el("assets-refresh").addEventListener("click", () => loadAssets(true));
    const assetsModal = el("assets-modal");
    assetsModal.addEventListener("click", (event) => {
      if (event.target === assetsModal) closeAssets();
    });
    initMobileAssetsGestures();
  }

  function updateHubClock() {
    const clock = el("hub-clock");
    if (!clock) return;
    const now = new Date();
    const seconds = now.getSeconds();
    const minutes = now.getMinutes() + seconds / 60;
    const hours = (now.getHours() % 12) + minutes / 60;
    clock.style.setProperty("--clock-second", `${seconds * 6}deg`);
    clock.style.setProperty("--clock-minute", `${minutes * 6}deg`);
    clock.style.setProperty("--clock-hour", `${hours * 30}deg`);
    clock.title = now.toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
  }

  function startHubClock() {
    updateHubClock();
    setInterval(updateHubClock, 1000);
  }

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

  // "YYYY-MM-DD HH:MM" in UTC — the editable form the data-since input expects
  function formatUtcInput(epochSec) {
    const d = new Date(epochSec * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
      `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
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

  function isSameUtcDateInput(left, right) {
    const a = String(left || "").trim();
    const b = String(right || "").trim();
    if (a === b) return true;
    const da = parseDateAsUtc(a);
    const db = parseDateAsUtc(b);
    return !!da && !!db && da.getTime() === db.getTime();
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

  function reorderSessionList(items, sessionIds) {
    const byId = new Map((items || []).map((item) => [sessionId(item), item]));
    if (byId.size !== sessionIds.length || sessionIds.some((id) => !byId.has(id))) return null;
    return sessionIds.map((id) => byId.get(id));
  }

  function applySessions(nextSessions) {
    const incoming = nextSessions || [];
    if (pendingSessionOrder) {
      const pending = reorderSessionList(incoming, pendingSessionOrder);
      if (!pending) {
        pendingSessionOrder = null;
        sessions = incoming;
      } else {
        const incomingIds = incoming.map(sessionId);
        const confirmed = incomingIds.every((id, index) => id === pendingSessionOrder[index]);
        sessions = confirmed ? incoming : pending;
        if (confirmed) pendingSessionOrder = null;
      }
    } else {
      sessions = incoming;
    }
    if (!draggedSessionId) render();
  }

  function runnerButtons(s) {
    const r = s.runner || "stopped";
    const dataReady = !!s.history_ready;
    if (r === "running" || r === "starting") {
      return `<button class="btn" data-runner="stop">Stop</button>` +
             `<button class="btn" data-runner="restart"${dataReady ? "" : " disabled"}>Restart</button>`;
    }
    return `<button class="btn btn-primary" data-runner="start"${dataReady ? "" : " disabled"}>Start</button>`;
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
      `<td data-label="Status" class="status-cell">` +
        `<span class="status-cell-content">` +
          `<button type="button" class="session-drag-handle" draggable="true" ` +
          `title="Drag to reorder" aria-label="Drag to reorder session">` +
            `<svg viewBox="0 0 24 24" aria-hidden="true">` +
              `<circle cx="9" cy="5" r="1.5"></circle>` +
              `<circle cx="15" cy="5" r="1.5"></circle>` +
              `<circle cx="9" cy="12" r="1.5"></circle>` +
              `<circle cx="15" cy="12" r="1.5"></circle>` +
              `<circle cx="9" cy="19" r="1.5"></circle>` +
              `<circle cx="15" cy="19" r="1.5"></circle>` +
            `</svg>` +
          `</button>` +
          `<span data-field="runner-led" class="led"></span>` +
        `</span></td>` +
      `<td data-label="Symbol" class="mono"><span data-field="symbol-cell" class="symbol-cell"></span></td>` +
      `<td data-label="TF" data-field="timeframe"></td>` +
      `<td data-label="Exchange"><span data-field="exchange-cell" class="exchange-cell"></span></td>` +
      `<td data-label="Script" class="mono" data-field="script-name"></td>` +
      `<td data-label="Data" class="data-cell"><span class="data-controls">` +
        `<span class="data-badge-wrap">` +
          `<span data-field="collector-badge" class="badge data-badge" ` +
          `data-act="data-since"></span>` +
          `<span data-field="data-since-popover" class="data-since-popover"></span></span>` +
        `<button class="btn btn-icon data-edit-btn" data-act="data-edit" ` +
        `title="Edit data start" aria-label="Edit data start">` +
          `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">` +
            `<path d="M12 20h9"></path>` +
            `<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"></path>` +
          `</svg>` +
        `</button>` +
        `<span data-field="history-loading" class="muted">loading</span>` +
        `</span></td>` +
      `<td data-label="Last bar" class="muted">${lastBarCell({})}</td>` +
      `<td data-label="Webhook"><span class="cell-inline">` +
        `<input type="checkbox" data-act="webhook">` +
        `<button class="btn btn-icon" data-act="webhook-settings" title="Webhook URL">&#9881;</button></span></td>` +
      `<td data-label="Telegram"><span class="cell-inline">` +
        `<input type="checkbox" data-act="telegram">` +
        `<button class="btn btn-icon" data-act="telegram-settings" title="Telegram bot">&#9881;</button></span></td>` +
      `<td data-label="Runner" class="runner-cell" data-field="runner-cell"></td>` +
      `<td data-label="Chart"><a data-field="chart-link" class="btn btn-chart" target="_blank">Open</a></td>` +
      `<td data-label="Remove"><button class="btn btn-danger btn-icon" data-act="delete" title="Delete session">&times;</button></td>`;

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
      } else if (act === "data-edit") openSettings(id, "data-since");
      else if (act === "delete") openRemoveConfirm(id);
      else if (act === "logs") openLogs(id);
      else if (act === "webhook-settings") openSettings(id, "webhook");
      else if (act === "telegram-settings") openSettings(id, "telegram");
    });
    return tr;
  }

  function patchSessionRow(tr, s) {
    const id = sessionId(s);
    const runner = s.runner || "stopped";
    const runnerControlsKey = `${runner}:${s.history_ready ? "ready" : "preparing"}`;
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

    if (tr.dataset.runnerControlsKey !== runnerControlsKey) {
      setHTML(
        tr.querySelector('[data-field="runner-cell"]'),
        `<span class="runner-actions">${runnerButtons(s)}<button class="btn" data-act="logs">Logs</button></span>`,
      );
      tr.dataset.runnerControlsKey = runnerControlsKey;
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

  async function streamSse(path, opts, onEvent) {
    const resp = await fetch(path, opts);
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    if (!resp.body) throw new Error("Streaming response is unavailable");

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    function dispatch(block) {
      let eventName = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event") eventName = value;
        else if (field === "data") dataLines.push(value);
      }
      if (!dataLines.length) return;
      const raw = dataLines.join("\n");
      let data;
      try { data = JSON.parse(raw); }
      catch { data = { text: raw }; }
      onEvent(eventName, data);
    }

    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        buffer = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          dispatch(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
        }
        if (done) break;
      }
      if (buffer.trim()) dispatch(buffer);
    } finally {
      reader.releaseLock();
    }
  }

  function sessionOrderFromRows() {
    return Array.from(el("sessions-body").querySelectorAll("tr[data-session-id]"))
      .map((row) => row.dataset.sessionId)
      .filter(Boolean);
  }

  async function persistSessionOrder(sessionIds) {
    const currentIds = sessions.map(sessionId);
    if (sessionIds.length !== currentIds.length ||
        sessionIds.every((id, index) => id === currentIds[index])) return;

    const ordered = reorderSessionList(sessions, sessionIds);
    if (!ordered) {
      render();
      return;
    }

    pendingSessionOrder = sessionIds.slice();
    sessions = ordered;
    render();
    const body = el("sessions-body");
    body.classList.add("session-order-saving");
    body.setAttribute("aria-busy", "true");
    try {
      const result = await api("/api/sessions/order", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_ids: sessionIds }),
      });
      applySessions(result.sessions || []);
    } catch (e) {
      pendingSessionOrder = null;
      alert(`session reorder failed: ${e.message}`);
      await refresh();
    } finally {
      body.classList.remove("session-order-saving");
      body.removeAttribute("aria-busy");
    }
  }

  function clearSessionDragClasses() {
    const body = el("sessions-body");
    body.querySelectorAll(".session-dragging").forEach((row) => {
      row.classList.remove("session-dragging");
    });
  }

  function dragAfterRow(pointerY) {
    const rows = Array.from(
      el("sessions-body").querySelectorAll("tr[data-session-id]:not(.session-dragging)"),
    );
    return rows.reduce((closest, row) => {
      const box = row.getBoundingClientRect();
      const offset = pointerY - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) return { offset, row };
      return closest;
    }, { offset: Number.NEGATIVE_INFINITY, row: null }).row;
  }

  function initSessionReordering() {
    const body = el("sessions-body");

    body.addEventListener("dragstart", (e) => {
      const handle = e.target && e.target.closest
        ? e.target.closest(".session-drag-handle")
        : null;
      const row = handle && handle.closest("tr[data-session-id]");
      if (!row || !desktopReorderQuery.matches || pendingSessionOrder) {
        e.preventDefault();
        return;
      }
      draggedSessionId = row.dataset.sessionId;
      row.classList.add("session-dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", draggedSessionId);
    });

    body.addEventListener("dragover", (e) => {
      if (!draggedSessionId || !desktopReorderQuery.matches) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const dragging = body.querySelector(".session-dragging");
      if (!dragging) return;
      const after = dragAfterRow(e.clientY);
      if (after) body.insertBefore(dragging, after);
      else body.appendChild(dragging);
    });

    body.addEventListener("drop", (e) => {
      if (!draggedSessionId) return;
      e.preventDefault();
      const order = sessionOrderFromRows();
      draggedSessionId = null;
      clearSessionDragClasses();
      persistSessionOrder(order);
    });

    body.addEventListener("dragend", () => {
      if (!draggedSessionId) return;
      draggedSessionId = null;
      clearSessionDragClasses();
      render();
    });
  }

  function initDesktopCardCarousel() {
    const body = el("sessions-body");
    const dragThreshold = 6;
    let drag = null;
    let suppressClick = false;

    function rowScrollLeft(row) {
      const bodyBox = body.getBoundingClientRect();
      const rowBox = row.getBoundingClientRect();
      return body.scrollLeft + rowBox.left - bodyBox.left;
    }

    function snapToNearestCard() {
      const rows = Array.from(body.querySelectorAll("tr[data-session-id]"));
      if (!rows.length) return;
      const nearest = rows.reduce((best, row) => {
        const distance = Math.abs(rowScrollLeft(row) - body.scrollLeft);
        return !best || distance < best.distance ? { row, distance } : best;
      }, null);
      if (nearest) {
        body.scrollTo({ left: rowScrollLeft(nearest.row), behavior: "smooth" });
      }
    }

    function endDrag(e) {
      if (!drag || (e && e.pointerId !== drag.pointerId)) return;
      const moved = drag.moved;
      drag = null;
      body.classList.remove("session-carousel-dragging");
      if (!moved) return;
      suppressClick = true;
      window.setTimeout(() => { suppressClick = false; }, 0);
      window.requestAnimationFrame(snapToNearestCard);
    }

    body.addEventListener("pointerdown", (e) => {
      if (!desktopCardCarouselQuery.matches || e.pointerType === "touch" || e.button !== 0) {
        return;
      }
      drag = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        scrollLeft: body.scrollLeft,
        moved: false,
      };
    });

    window.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.pointerId) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (!drag.moved) {
        if (Math.abs(dx) < dragThreshold || Math.abs(dx) <= Math.abs(dy)) return;
        drag.moved = true;
        body.classList.add("session-carousel-dragging");
      }
      e.preventDefault();
      body.scrollLeft = drag.scrollLeft - dx;
    }, { passive: false });

    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    window.addEventListener("blur", () => endDrag());
    desktopCardCarouselQuery.addEventListener("change", () => {
      if (!desktopCardCarouselQuery.matches) endDrag();
    });
    body.addEventListener("click", (e) => {
      if (!suppressClick) return;
      suppressClick = false;
      e.preventDefault();
      e.stopImmediatePropagation();
    }, true);
  }

  // ---- AI chat (floating 🐸 button + panel) --------------------------------
  const AI_CHAT_HISTORY_KEY = "aiChatMessages";
  const AI_CHAT_CONVERSATION_KEY = "aiChatConversationId";
  const AI_CHAT_FAB_POS_KEY = "aiChatFabPos";
  const AI_CHAT_MAX_HISTORY = 12;
  const AI_EFFORT_LABELS = {
    none: "None",
    minimal: "Minimal",
    low: "Low",
    medium: "Medium",
    high: "High",
    xhigh: "Extra High",
  };
  let aiEnabled = false;
  let aiMessages = [];
  let aiConversationId = "";
  let aiPending = false;
  let aiRemotePending = false;
  let aiStreamingResponse = false;
  let aiRenderFrame = null;
  let aiChatLockedScroll = false;
  let aiStateSyncPromise = null;
  let aiModelsPromise = null;
  let aiModels = [];
  let aiSelectedModel = "";
  let aiSelectedEffort = "";
  let aiModelMenuAnchor = null;
  let reclampAiFab = null; // set by initAiChat; re-clamps the saved FAB spot

  function applyAiAvailability(enabled) {
    if (typeof enabled !== "boolean") return;
    aiEnabled = enabled;
    el("ai-chat-fab").classList.toggle("hidden", !aiEnabled);
    // the FAB was display:none until now, so any earlier clamp math ran with
    // zero dimensions — redo it against the real size
    if (aiEnabled && reclampAiFab) reclampAiFab();
    if (aiEnabled) loadAiModels();
    if (!aiEnabled && isAiChatOpen()) closeAiChat();
    if (isCalendarOpen() && calendarSelectedDate) renderCalendarDetails();
  }

  function aiEffortLabel(value) {
    return AI_EFFORT_LABELS[value] || value.charAt(0).toUpperCase() + value.slice(1);
  }

  function aiSupportedEfforts() {
    const selected = aiModels.find((item) => item.value === aiSelectedModel);
    return selected && Array.isArray(selected.efforts) ? selected.efforts : [];
  }

  // keep the effort valid for the selected model; an empty supported list means
  // the server does not know the model's efforts, so keep the current pick
  function clampAiEffort() {
    const efforts = aiSupportedEfforts();
    if (!efforts.length || efforts.includes(aiSelectedEffort)) return;
    aiSelectedEffort = efforts.includes("medium") ? "medium" : efforts[efforts.length - 1];
  }

  // the selection lives on the server so every browser shares it; the
  // ai_prefs_updated broadcast brings other clients in line
  function pushAiPrefs() {
    api("/api/ai/chat/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: aiSelectedModel || null,
        effort: aiSelectedEffort || null,
      }),
    }).catch(() => {});
  }

  function applyAiPrefs(model, effort) {
    let changed = false;
    if (
      typeof model === "string" && model && model !== aiSelectedModel
      && aiModels.some((item) => item.value === model)
    ) {
      aiSelectedModel = model;
      changed = true;
    }
    if (typeof effort === "string" && effort && effort !== aiSelectedEffort) {
      aiSelectedEffort = effort;
      changed = true;
    }
    if (!changed) return;
    clampAiEffort();
    renderAiModelMenu();
    updateAiModelControls();
    if (aiModelMenuAnchor) positionAiModelMenu(aiModelMenuAnchor);
  }

  function updateAiModelControls() {
    const selected = aiModels.find((item) => item.value === aiSelectedModel);
    let label = selected ? selected.label : "Default model";
    if (selected && aiSelectedEffort) label += ` · ${aiEffortLabel(aiSelectedEffort)}`;
    el("ai-model-label").textContent = label;
    el("ai-model-selector").disabled = aiModels.length === 0;
    for (const option of el("ai-model-menu").querySelectorAll(".ai-model-option")) {
      const selectedValue = option.dataset.kind === "effort" ? aiSelectedEffort : aiSelectedModel;
      option.setAttribute("aria-selected", String(option.dataset.value === selectedValue));
    }
  }

  function selectAiModel(value) {
    if (!aiModels.some((item) => item.value === value)) return;
    aiSelectedModel = value;
    clampAiEffort();
    pushAiPrefs();
    // the reasoning section follows the model, so rebuild and keep the menu
    // open for the follow-up effort pick
    renderAiModelMenu();
    updateAiModelControls();
    if (aiModelMenuAnchor) positionAiModelMenu(aiModelMenuAnchor);
  }

  function selectAiEffort(value) {
    if (!aiSupportedEfforts().includes(value)) return;
    aiSelectedEffort = value;
    pushAiPrefs();
    updateAiModelControls();
    closeAiModelMenu();
  }

  function renderAiModelMenu() {
    const menu = el("ai-model-menu");
    menu.textContent = "";
    const addHeading = (text) => {
      const heading = document.createElement("div");
      heading.className = "ai-model-menu-heading";
      heading.textContent = text;
      menu.appendChild(heading);
    };
    const addOption = (kind, value, labelText, descriptionText, selected, onSelect) => {
      const option = document.createElement("button");
      option.className = "ai-model-option";
      option.type = "button";
      option.dataset.value = value;
      option.dataset.kind = kind;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(selected));

      const label = document.createElement("span");
      label.className = "ai-model-option-label";
      label.textContent = labelText;
      option.appendChild(label);
      if (descriptionText) {
        const description = document.createElement("span");
        description.className = "ai-model-option-description";
        description.textContent = descriptionText;
        option.appendChild(description);
      }
      option.addEventListener("click", onSelect);
      menu.appendChild(option);
    };
    addHeading("Model");
    for (const item of aiModels) {
      addOption(
        "model",
        item.value,
        item.label,
        item.description,
        item.value === aiSelectedModel,
        () => selectAiModel(item.value),
      );
    }
    const efforts = aiSupportedEfforts();
    if (efforts.length) {
      addHeading("Reasoning");
      for (const value of efforts) {
        addOption(
          "effort",
          value,
          aiEffortLabel(value),
          "",
          value === aiSelectedEffort,
          () => selectAiEffort(value),
        );
      }
    }
  }

  async function loadAiModels() {
    if (aiModelsPromise) return aiModelsPromise;
    aiModelsPromise = (async () => {
      const response = await api("/api/ai/models");
      aiModels = Array.isArray(response.models)
        ? response.models.filter((item) =>
          item && typeof item.value === "string" && typeof item.label === "string")
        : [];
      // the server owns the shared selection; is_default/first are only a
      // safety net if it did not resolve one
      const serverModel = typeof response.selected_model === "string"
        ? response.selected_model : "";
      const selected = aiModels.find((item) => item.value === serverModel)
        || aiModels.find((item) => item.is_default)
        || aiModels[0];
      aiSelectedModel = selected ? selected.value : "";
      aiSelectedEffort = typeof response.selected_effort === "string"
        ? response.selected_effort : "";
      clampAiEffort();
      renderAiModelMenu();
      updateAiModelControls();
    })().catch(() => {
      el("ai-model-label").textContent = "Models unavailable";
      el("ai-model-selector").disabled = true;
    });
    return aiModelsPromise;
  }

  function positionAiModelMenu(anchor) {
    const panel = el("ai-chat-panel");
    const menu = el("ai-model-menu");
    const panelRect = panel.getBoundingClientRect();
    const anchorRect = anchor.getBoundingClientRect();
    const margin = 8;
    let left = mobileAiQuery.matches
      ? 12
      : anchorRect.right - panelRect.left - menu.offsetWidth;
    let top = mobileAiQuery.matches
      ? anchorRect.bottom - panelRect.top + 7
      : anchorRect.top - panelRect.top - menu.offsetHeight - 7;
    left = Math.min(Math.max(left, margin), panel.clientWidth - menu.offsetWidth - margin);
    top = Math.min(Math.max(top, margin), panel.clientHeight - menu.offsetHeight - margin);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  function openAiModelMenu(anchor) {
    if (!aiModels.length) return;
    const menu = el("ai-model-menu");
    if (!menu.classList.contains("hidden") && aiModelMenuAnchor === anchor) {
      closeAiModelMenu();
      return;
    }
    aiModelMenuAnchor = anchor;
    menu.classList.remove("hidden");
    el("ai-model-selector").setAttribute("aria-expanded", String(anchor === el("ai-model-selector")));
    el("ai-chat-title").setAttribute("aria-expanded", String(anchor === el("ai-chat-title")));
    positionAiModelMenu(anchor);
  }

  function closeAiModelMenu() {
    aiModelMenuAnchor = null;
    el("ai-model-menu").classList.add("hidden");
    el("ai-model-selector").setAttribute("aria-expanded", "false");
    el("ai-chat-title").setAttribute("aria-expanded", "false");
  }

  function loadPersistentAiValue(key) {
    try {
      const saved = localStorage.getItem(key);
      if (saved != null) return saved;
      const legacy = sessionStorage.getItem(key);
      if (legacy != null) {
        localStorage.setItem(key, legacy);
        sessionStorage.removeItem(key);
        return legacy;
      }
    } catch {}
    return null;
  }

  function loadAiMessages() {
    try {
      const parsed = JSON.parse(loadPersistentAiValue(AI_CHAT_HISTORY_KEY) || "[]");
      if (Array.isArray(parsed)) {
        aiMessages = parsed.filter((m) =>
          m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string")
          .map((m) => ({
            role: m.role,
            content: m.content,
            ...(m.error ? { error: true } : {}),
          }));
      }
    } catch { aiMessages = []; }
  }

  function saveAiMessages() {
    try { localStorage.setItem(AI_CHAT_HISTORY_KEY, JSON.stringify(aiMessages)); } catch {}
  }

  function loadAiConversationId() {
    try { aiConversationId = loadPersistentAiValue(AI_CHAT_CONVERSATION_KEY) || ""; }
    catch { aiConversationId = ""; }
  }

  function saveAiConversationId() {
    try {
      if (aiConversationId) localStorage.setItem(AI_CHAT_CONVERSATION_KEY, aiConversationId);
      else localStorage.removeItem(AI_CHAT_CONVERSATION_KEY);
    } catch {}
  }

  function applyAiChatState(state) {
    const messages = Array.isArray(state && state.messages) ? state.messages : [];
    aiMessages = messages.filter((m) =>
      m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string");
    aiConversationId = state && typeof state.conversation_id === "string"
      ? state.conversation_id
      : "";
    aiRemotePending = Boolean(state && state.pending);
    // catch up on a model/effort change whose broadcast this client missed
    if (state) applyAiPrefs(state.model, state.effort);
    saveAiMessages();
    saveAiConversationId();
    el("ai-chat-send").disabled = aiPending || aiRemotePending;
    renderAiMessages();
  }

  async function syncAiChatState({ allowImport = true } = {}) {
    if (aiPending) return;
    if (aiStateSyncPromise) return aiStateSyncPromise;
    aiStateSyncPromise = (async () => {
      let state = await api("/api/ai/chat");
      const serverMessages = Array.isArray(state.messages) ? state.messages : [];
      if (allowImport && !serverMessages.length && aiMessages.length) {
        state = await api("/api/ai/chat/state", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            messages: aiMessages,
            conversation_id: aiConversationId || null,
          }),
        });
      }
      if (!aiPending) applyAiChatState(state);
    })().catch(() => {}).finally(() => {
      aiStateSyncPromise = null;
    });
    return aiStateSyncPromise;
  }

  function scheduleAiRender() {
    if (aiRenderFrame != null) return;
    aiRenderFrame = requestAnimationFrame(() => {
      aiRenderFrame = null;
      renderAiMessages();
    });
  }

  function renderAiMessages() {
    const box = el("ai-chat-messages");
    box.textContent = "";
    if (!aiMessages.length && !aiPending && !aiRemotePending) {
      const empty = document.createElement("div");
      empty.className = "ai-chat-empty";
      empty.textContent = "Ask anything about your sessions.";
      box.appendChild(empty);
      return;
    }
    for (const msg of aiMessages) {
      const div = document.createElement("div");
      div.className = `ai-msg ai-msg-${msg.role === "user" ? "user" : "assistant"}`;
      if (msg.error) div.classList.add("ai-msg-error");
      if (msg.role === "assistant" && !msg.error && typeof msg.html === "string") {
        div.classList.add("ai-msg-markdown");
        div.innerHTML = msg.html;
        for (const link of div.querySelectorAll("a")) {
          link.target = "_blank";
          link.rel = "noopener noreferrer";
        }
      } else {
        div.textContent = msg.content;
      }
      if (msg.content || !msg.transient || msg.error) box.appendChild(div);
      if (msg.role === "assistant" && msg.transient && typeof msg.workStatus === "string") {
        const work = document.createElement("div");
        work.className = "ai-msg-work";
        work.textContent = msg.workStatus;
        box.appendChild(work);
      }
    }
    if ((aiPending || aiRemotePending) && !aiStreamingResponse) {
      const div = document.createElement("div");
      div.className = "ai-msg ai-msg-assistant ai-msg-pending";
      div.innerHTML = '<span class="ai-dot">&#9679;</span><span class="ai-dot">&#9679;</span><span class="ai-dot">&#9679;</span>';
      box.appendChild(div);
    }
    box.scrollTop = box.scrollHeight;
  }

  function isAiChatOpen() {
    return !el("ai-chat-panel").classList.contains("hidden");
  }

  // Desktop: dock the panel to the face — right side when it fits, left side
  // otherwise — and clamp so it never leaves the viewport. Mobile keeps the
  // CSS bottom sheet (inline styles cleared so the stylesheet wins).
  function positionAiChatPanel() {
    const panel = el("ai-chat-panel");
    if (!desktopReorderQuery.matches) {
      panel.style.left = "";
      panel.style.top = "";
      panel.style.right = "";
      panel.style.bottom = "";
      panel.style.height = "";
      panel.style.paddingBottom = "";
      // iOS fires window.resize while the keyboard animates; without this
      // re-pin, that resize handler would wipe the keyboard compensation
      updateAiPanelForKeyboard();
      return;
    }
    if (panel.classList.contains("hidden")) return;
    const fab = el("ai-chat-fab");
    const GAP = 10;
    const MARGIN = 10;
    // the face's center is invariant under the hover/drag scale transform, so
    // rebuild its edges from the untransformed layout size
    const rect = fab.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const half = fab.offsetWidth / 2;
    const pw = panel.offsetWidth;
    const ph = panel.offsetHeight;
    let left = cx + half + GAP;
    if (left + pw > window.innerWidth - MARGIN) left = cx - half - GAP - pw;
    left = Math.min(Math.max(left, MARGIN), Math.max(window.innerWidth - pw - MARGIN, MARGIN));
    let top = cy - ph / 2;
    top = Math.min(Math.max(top, MARGIN), Math.max(window.innerHeight - ph - MARGIN, MARGIN));
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    panel.style.paddingBottom = ""; // in case a mobile keyboard pad survived a breakpoint switch
  }

  // Mobile keyboards overlay a fixed bottom sheet, and iOS additionally
  // scrolls fixed elements out of view to reveal the focused input. While the
  // keyboard is up, pin the sheet to the visual viewport so it spans from the
  // top of the *visible* area down to the keyboard edge.
  function updateAiPanelForKeyboard() {
    const panel = el("ai-chat-panel");
    const vv = window.visualViewport;
    if (!vv || desktopReorderQuery.matches || panel.classList.contains("hidden")) return;
    // iOS reveals the focused input by force-scrolling the window based on
    // the PRE-keyboard layout, then leaves that scroll behind even after the
    // layout viewport shrinks to fit (observed: innerHeight 844→389 with
    // scrollY stuck at 310). While the keyboard is up, fixed elements are
    // dragged along by that scroll, shoving the sheet off the top of the
    // screen. The page is scroll-locked whenever the sheet is open, so ANY
    // nonzero scroll here is that forced reveal — always undo it.
    if (window.scrollY || window.pageYOffset) window.scrollTo(0, 0);
    // On iOS versions where the layout viewport resizes with the keyboard,
    // this stays 0 and the plain CSS (top + bottom:0) already fits; the
    // pinning below only kicks in where the keyboard overlays the viewport.
    // Don't subtract vv.offsetTop: it grows to ~the keyboard height while
    // Safari pans, which would read as "keyboard gone" mid-pan. The 50px
    // threshold ignores URL-bar wobble; real keyboards are far taller.
    const keyboard = window.innerHeight - vv.height;
    if (keyboard > 50) {
      const gap = Math.max(10, vv.height * 0.03);
      panel.style.top = `${vv.offsetTop + gap}px`;
      // Stretch the sheet past the visual viewport down to the layout-viewport
      // bottom: Safari's URL/accessory bars above the keyboard are floating
      // and translucent, so anything shorter lets the dashboard (and the FAB)
      // peek through the strip between the composer and the keyboard. The
      // padding keeps the composer itself above that strip, at the bottom of
      // the *visible* area.
      panel.style.height = `${window.innerHeight - vv.offsetTop - gap}px`;
      panel.style.paddingBottom = `${keyboard}px`;
      panel.style.bottom = "auto";
      const box = el("ai-chat-messages");
      box.scrollTop = box.scrollHeight;
    } else {
      panel.style.top = "";
      panel.style.height = "";
      panel.style.bottom = "";
      panel.style.paddingBottom = "";
    }
  }

  function openAiChat() {
    el("ai-chat-panel").classList.remove("hidden");
    el("ai-chat-panel").setAttribute("aria-hidden", "false");
    el("ai-chat-fab").setAttribute("aria-expanded", "true");
    positionAiChatPanel();
    renderAiMessages();
    syncAiChatState();
    if (desktopReorderQuery.matches) {
      // desktop only: on mobile a programmatic focus doesn't raise the
      // keyboard (no user gesture), and iOS may then ignore the tap on the
      // pre-focused input — leave focusing to the user's tap
      el("ai-chat-input").focus();
    } else {
      // modal on mobile: background scroll would fight the sheet-drag gesture
      // and lets iOS shove the sheet off-screen when the keyboard opens
      lockBodyScroll();
      aiChatLockedScroll = true;
    }
  }

  function closeAiChat() {
    if (!isAiChatOpen()) return;
    closeAiModelMenu();
    const panel = el("ai-chat-panel");
    panel.classList.add("hidden");
    panel.setAttribute("aria-hidden", "true");
    // drop any keyboard pin / sheet-drag offset
    panel.style.top = "";
    panel.style.height = "";
    panel.style.bottom = "";
    panel.style.paddingBottom = "";
    panel.style.transform = "";
    el("ai-chat-fab").setAttribute("aria-expanded", "false");
    if (aiChatLockedScroll) {
      unlockBodyScroll();
      aiChatLockedScroll = false;
    }
  }

  function autosizeAiInput() {
    const input = el("ai-chat-input");
    input.style.height = "auto";
    // +2 for the top/bottom border (box-sizing: border-box); CSS max-height clamps
    input.style.height = `${input.scrollHeight + 2}px`;
  }

  async function sendAiChatMessage() {
    if (aiPending || aiRemotePending) return;
    await syncAiChatState();
    if (aiPending || aiRemotePending) return;
    const input = el("ai-chat-input");
    const message = input.value.trim();
    if (!message) return;
    const history = aiMessages
      .filter((m) => !m.error)
      .slice(-AI_CHAT_MAX_HISTORY)
      .map((m) => ({ role: m.role, content: m.content }));
    aiMessages.push({ role: "user", content: message });
    saveAiMessages();
    input.value = "";
    autosizeAiInput();
    aiPending = true;
    aiStreamingResponse = false;
    el("ai-chat-send").disabled = true;
    renderAiMessages();
    let assistantMessage = null;
    let completed = false;
    let finalAnswerStarted = false;
    try {
      await streamSse("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          history,
          conversation_id: aiConversationId || null,
          model: aiSelectedModel || null,
          effort: aiSelectedEffort || null,
        }),
      }, (eventName, data) => {
        if (eventName === "conversation") {
          aiConversationId = data.conversation_id || aiConversationId;
          saveAiConversationId();
          return;
        }
        if (eventName === "status") {
          if (finalAnswerStarted) return;
          if (!assistantMessage) {
            assistantMessage = { role: "assistant", content: "", transient: true };
            aiMessages.push(assistantMessage);
          }
          assistantMessage.content = data.text || "...";
          delete assistantMessage.html;
          assistantMessage.transient = true;
          aiStreamingResponse = true;
          scheduleAiRender();
          return;
        }
        if (eventName === "work_status") {
          if (finalAnswerStarted) return;
          if (!assistantMessage) {
            assistantMessage = { role: "assistant", content: "", transient: true };
            aiMessages.push(assistantMessage);
          }
          assistantMessage.workStatus = data.text || "Working...";
          assistantMessage.transient = true;
          aiStreamingResponse = true;
          scheduleAiRender();
          return;
        }
        if (eventName === "delta") {
          if (!assistantMessage) {
            assistantMessage = { role: "assistant", content: "" };
            aiMessages.push(assistantMessage);
          }
          if (!finalAnswerStarted) {
            assistantMessage.content = "";
            delete assistantMessage.html;
            delete assistantMessage.transient;
            delete assistantMessage.workStatus;
            finalAnswerStarted = true;
          }
          aiStreamingResponse = true;
          assistantMessage.content += data.text || "";
          if (typeof data.html === "string") assistantMessage.html = data.html;
          scheduleAiRender();
          return;
        }
        if (eventName === "done") {
          if (!assistantMessage) {
            assistantMessage = { role: "assistant", content: "" };
            aiMessages.push(assistantMessage);
          }
          delete assistantMessage.transient;
          delete assistantMessage.workStatus;
          assistantMessage.content = data.answer || assistantMessage.content || "(empty response)";
          if (typeof data.html === "string") assistantMessage.html = data.html;
          else delete assistantMessage.html;
          finalAnswerStarted = true;
          completed = true;
          return;
        }
        if (eventName === "stream_error") {
          throw new Error(data.error || "AI stream failed");
        }
      });
      if (!completed) throw new Error("AI stream ended before completion");
    } catch (e) {
      if (assistantMessage && assistantMessage.transient) {
        assistantMessage.content = `AI call failed: ${e.message}`;
        assistantMessage.error = true;
        delete assistantMessage.html;
        delete assistantMessage.transient;
        delete assistantMessage.workStatus;
      } else {
        aiMessages.push({ role: "assistant", content: `AI call failed: ${e.message}`, error: true });
      }
    } finally {
      aiPending = false;
      aiStreamingResponse = false;
      el("ai-chat-send").disabled = aiRemotePending;
      saveAiMessages();
      renderAiMessages();
      await syncAiChatState({ allowImport: false });
    }
  }

  function initAiChat() {
    const fab = el("ai-chat-fab");
    const FAB_MARGIN = 8;
    const DRAG_THRESHOLD = 6; // px of travel before a tap becomes a drag
    let drag = null;
    let suppressClick = false;

    // the FAB is display:none until the AI availability check lands, and a
    // hidden element measures 0 — clamping with that width let a saved spot
    // land almost entirely off-screen (only FAB_MARGIN px stayed visible)
    const fabSize = () => fab.offsetWidth || 52;

    function applyFabPos(left, top) {
      const maxLeft = window.innerWidth - fabSize() - FAB_MARGIN;
      const maxTop = window.innerHeight - fabSize() - FAB_MARGIN;
      fab.style.left = `${Math.min(Math.max(left, FAB_MARGIN), Math.max(maxLeft, FAB_MARGIN))}px`;
      fab.style.top = `${Math.min(Math.max(top, FAB_MARGIN), Math.max(maxTop, FAB_MARGIN))}px`;
      fab.style.right = "auto";
      fab.style.bottom = "auto";
      positionAiChatPanel(); // keep an open panel docked to the face
    }

    function resetFabPos() {
      fab.style.left = "";
      fab.style.top = "";
      fab.style.right = "";
      fab.style.bottom = "";
    }

    function currentFabPos() {
      // prefer the inline style: getBoundingClientRect() is skewed while the
      // hover/drag scale transform is active
      if (fab.style.left) {
        return { left: parseFloat(fab.style.left), top: parseFloat(fab.style.top) };
      }
      const rect = fab.getBoundingClientRect();
      return { left: rect.left, top: rect.top };
    }

    // mobile: the face always rests against the nearest side edge
    function snappedFabLeft(left) {
      const maxLeft = Math.max(window.innerWidth - fabSize() - FAB_MARGIN, FAB_MARGIN);
      return left + fabSize() / 2 < window.innerWidth / 2 ? FAB_MARGIN : maxLeft;
    }

    let snapTimer = null;
    function snapFabToSide() {
      const pos = currentFabPos();
      // inline style jumps straight to the resting spot (so currentFabPos()
      // and persistence see the final value); .snapping animates the visual
      fab.classList.add("snapping");
      applyFabPos(snappedFabLeft(pos.left), pos.top);
      clearTimeout(snapTimer);
      snapTimer = setTimeout(() => fab.classList.remove("snapping"), 250);
    }

    function restoreFabPos() {
      try {
        const pos = JSON.parse(localStorage.getItem(AI_CHAT_FAB_POS_KEY) || "null");
        if (pos && typeof pos.left === "number" && typeof pos.top === "number") {
          // a spot saved on desktop may be mid-screen; mobile pulls it aside
          const left = desktopReorderQuery.matches ? pos.left : snappedFabLeft(pos.left);
          applyFabPos(left, pos.top);
          return;
        }
      } catch {}
      resetFabPos(); // no saved spot: fall back to the per-breakpoint CSS default
    }
    reclampAiFab = restoreFabPos;

    fab.addEventListener("pointerdown", (e) => {
      if (!e.isPrimary) return;
      // a fresh press starts a new tap/drag decision; without this, a drag
      // whose trailing click never fired (touch) would swallow the next tap
      suppressClick = false;
      const pos = currentFabPos();
      drag = {
        id: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        left: pos.left,
        top: pos.top,
        moved: false,
      };
      try { fab.setPointerCapture(e.pointerId); } catch {}
    });
    fab.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      drag.moved = true;
      fab.classList.add("dragging");
      applyFabPos(drag.left + dx, drag.top + dy);
    });
    function endFabDrag(e) {
      if (!drag || e.pointerId !== drag.id) return;
      if (drag.moved) {
        suppressClick = true; // the click right after a drag must not toggle the panel
        if (!desktopReorderQuery.matches) snapFabToSide();
        try {
          localStorage.setItem(AI_CHAT_FAB_POS_KEY, JSON.stringify(currentFabPos()));
        } catch {}
      }
      drag = null;
      fab.classList.remove("dragging");
    }
    fab.addEventListener("pointerup", endFabDrag);
    fab.addEventListener("pointercancel", endFabDrag);

    fab.addEventListener("click", () => {
      if (suppressClick) { suppressClick = false; return; }
      if (isAiChatOpen()) closeAiChat();
      else openAiChat();
    });

    window.addEventListener("resize", () => {
      if (fab.style.left) {
        const left = parseFloat(fab.style.left);
        // orientation change etc.: mobile re-docks to the nearest side
        applyFabPos(
          desktopReorderQuery.matches ? left : snappedFabLeft(left),
          parseFloat(fab.style.top),
        );
      }
      positionAiChatPanel();
    });
    // breakpoint change: re-clamp the saved spot (or fall back to that
    // breakpoint's CSS default), then re-dock or reset the panel
    if (desktopReorderQuery.addEventListener) {
      desktopReorderQuery.addEventListener("change", () => {
        closeAiModelMenu();
        restoreFabPos();
        positionAiChatPanel();
      });
    }
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", updateAiPanelForKeyboard);
      window.visualViewport.addEventListener("scroll", updateAiPanelForKeyboard);
    }
    // Safari's focus-reveal scroll doesn't always emit visualViewport events
    window.addEventListener("scroll", updateAiPanelForKeyboard);
    el("ai-chat-input").addEventListener("focus", () => {
      setTimeout(updateAiPanelForKeyboard, 100);
      setTimeout(updateAiPanelForKeyboard, 400); // after the keyboard animation settles
    });
    // last-resort self-heal: re-pin every 500ms while the sheet is open, so a
    // missed/reordered viewport event can knock the sheet out for at most half
    // a second (no-op on desktop or while closed)
    setInterval(() => {
      if (!desktopReorderQuery.matches && isAiChatOpen()) updateAiPanelForKeyboard();
    }, 500);

    // mobile: dragging the sheet down from the grabber/header closes it
    const panel = el("ai-chat-panel");
    const sheetHeader = panel.querySelector(".ai-chat-header");
    let sheetDrag = null;
    sheetHeader.addEventListener("pointerdown", (e) => {
      if (desktopReorderQuery.matches || !e.isPrimary) return;
      if (e.target && e.target.closest && e.target.closest("button")) return;
      sheetDrag = { id: e.pointerId, startY: e.clientY, dy: 0 };
      try { sheetHeader.setPointerCapture(e.pointerId); } catch {}
    });
    sheetHeader.addEventListener("pointermove", (e) => {
      if (!sheetDrag || e.pointerId !== sheetDrag.id) return;
      sheetDrag.dy = Math.max(0, e.clientY - sheetDrag.startY);
      panel.style.transform = sheetDrag.dy > 0 ? `translateY(${sheetDrag.dy}px)` : "";
    });
    function endSheetDrag(e) {
      if (!sheetDrag || e.pointerId !== sheetDrag.id) return;
      const dy = sheetDrag.dy;
      sheetDrag = null;
      panel.style.transform = "";
      if (dy > 100) closeAiChat();
    }
    sheetHeader.addEventListener("pointerup", endSheetDrag);
    sheetHeader.addEventListener("pointercancel", endSheetDrag);

    el("ai-model-selector").addEventListener("click", () => {
      if (!mobileAiQuery.matches) openAiModelMenu(el("ai-model-selector"));
    });
    el("ai-chat-title").addEventListener("click", () => {
      if (mobileAiQuery.matches) openAiModelMenu(el("ai-chat-title"));
    });
    document.addEventListener("pointerdown", (e) => {
      if (el("ai-model-menu").classList.contains("hidden")) return;
      if (e.target.closest("#ai-model-menu, #ai-model-selector, #ai-chat-title")) return;
      closeAiModelMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeAiModelMenu();
    });

    el("ai-chat-close").addEventListener("click", closeAiChat);
    el("ai-chat-clear").addEventListener("click", () => {
      const conversationId = aiConversationId;
      aiConversationId = "";
      saveAiConversationId();
      aiMessages = [];
      saveAiMessages();
      renderAiMessages();
      api("/api/ai/chat/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId || null }),
      }).catch(() => {});
    });
    el("ai-chat-form").addEventListener("submit", (e) => {
      e.preventDefault();
      sendAiChatMessage();
    });
    const input = el("ai-chat-input");
    let inputComposing = false;
    let sendAfterComposition = false;
    input.addEventListener("input", autosizeAiInput);
    input.addEventListener("compositionstart", () => {
      inputComposing = true;
      sendAfterComposition = false;
    });
    input.addEventListener("compositionend", () => {
      inputComposing = false;
      if (!sendAfterComposition) return;
      sendAfterComposition = false;
      setTimeout(sendAiChatMessage, 0);
    });
    input.addEventListener("keydown", (e) => {
      // Enter sends on desktop (Shift+Enter for a newline); mobile keyboards
      // keep Enter as newline and send via the button.
      if (e.key === "Enter" && !e.shiftKey && desktopReorderQuery.matches) {
        if (e.isComposing || inputComposing || e.keyCode === 229) {
          sendAfterComposition = true;
          return;
        }
        e.preventDefault();
        sendAiChatMessage();
      }
    });

    loadAiMessages();
    loadAiConversationId();
    syncAiChatState();
    restoreFabPos();
  }

  // ---- Pepe faces: random blink + pupils that follow the pointer -----------
  function initPepeFaces() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const faces = new Map();

    function registerFace(svg) {
      if (!svg || faces.has(svg)) return;
      const face = {
        svg,
        pupils: Array.from(svg.querySelectorAll(".pepe-pupil")).map((node) => ({
          node,
          cx: parseFloat(node.getAttribute("cx")),
          cy: parseFloat(node.getAttribute("cy")),
        })),
      };
      faces.set(svg, face);
      scheduleBlink(face);
    }
    registerAnimatedPepeFace = registerFace;

    function blinkOnce(face, done) {
      if (!face.svg.isConnected || face.svg.closest(".forecast-running")) {
        done();
        return;
      }
      face.svg.classList.add("pepe-blink");
      setTimeout(() => {
        face.svg.classList.remove("pepe-blink");
        done();
      }, 130);
    }
    function scheduleBlink(face) {
      setTimeout(() => {
        if (!face.svg.isConnected) {
          faces.delete(face.svg);
          return;
        }
        blinkOnce(face, () => {
          // occasional quick double blink reads more lifelike than a fixed beat
          if (Math.random() < 0.2) {
            setTimeout(() => blinkOnce(face, () => scheduleBlink(face)), 150);
          } else {
            scheduleBlink(face);
          }
        });
      }, 2200 + Math.random() * 4300);
    }
    document.querySelectorAll("svg.pepe").forEach(registerFace);

    const PEPE_VIEWBOX = 64;
    // pupil travel in viewBox units, asymmetric so it stays on the eye white
    // (less headroom up: the half-closed lid sits right above the pupil)
    const RANGE_X = 3;
    const RANGE_Y_UP = 1;
    const RANGE_Y_DOWN = 1.2;
    function lookAt(clientX, clientY) {
      for (const [svg, face] of faces) {
        if (!svg.isConnected) {
          faces.delete(svg);
          continue;
        }
        if (svg.closest(".forecast-running")) continue;
        const rect = face.svg.getBoundingClientRect();
        if (!rect.width) continue; // hidden (chat panel closed)
        const scale = rect.width / PEPE_VIEWBOX;
        for (const pupil of face.pupils) {
          let dx = (clientX - (rect.left + pupil.cx * scale)) / scale;
          let dy = (clientY - (rect.top + pupil.cy * scale)) / scale;
          const ry = dy < 0 ? RANGE_Y_UP : RANGE_Y_DOWN;
          const d = Math.hypot(dx / RANGE_X, dy / ry);
          if (d > 1) {
            dx /= d;
            dy /= d;
          }
          pupil.node.setAttribute("transform", `translate(${dx.toFixed(2)} ${dy.toFixed(2)})`);
        }
      }
    }
    let lookFrame = null;
    let lastX = 0;
    let lastY = 0;
    window.addEventListener(
      "pointermove",
      (e) => {
        lastX = e.clientX;
        lastY = e.clientY;
        if (lookFrame !== null) return;
        lookFrame = requestAnimationFrame(() => {
          lookFrame = null;
          lookAt(lastX, lastY);
        });
      },
      { passive: true }
    );
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
    if (payload.symbol) payload.symbol = payload.symbol.toUpperCase();
    // Block Add when exchange/symbol are confirmed invalid.
    const exOk = await checkExchange();
    const symOk = await checkSymbol();
    if (!exOk || !symOk) {
      el("add-error").textContent = "입력값을 확인하세요 — 잘못된 exchange/symbol 입니다.";
      return;
    }
    if (validatedMarketType) payload.market_type = validatedMarketType;
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
  let logSource = null;
  let logShowingPlaceholder = false;
  let savedScrollY = 0;
  const MAX_LOG_CHARS = 600000;

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

  function closeLogStream() {
    if (logSource) {
      logSource.close();
      logSource = null;
    }
  }

  function logSelectionInside(pre) {
    const selection = window.getSelection && window.getSelection();
    if (!selection || selection.isCollapsed) return false;
    return pre.contains(selection.anchorNode) || pre.contains(selection.focusNode);
  }

  function shouldFollowLogTail(pre) {
    if (logSelectionInside(pre)) return false;
    return pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
  }

  function setLogContent(text) {
    const pre = el("log-content");
    const log = text && text.length ? text : "(no log output yet)";
    logShowingPlaceholder = !text || !text.length;
    pre.textContent = log;
    pre.scrollTop = pre.scrollHeight;
  }

  function appendLogContent(chunk) {
    if (!chunk) return;
    const pre = el("log-content");
    const followTail = shouldFollowLogTail(pre);
    if (logShowingPlaceholder) {
      pre.textContent = "";
      logShowingPlaceholder = false;
    } else if (pre.textContent && !pre.textContent.endsWith("\n") && !chunk.startsWith("\n")) {
      chunk = `\n${chunk}`;
    }
    pre.appendChild(document.createTextNode(chunk));
    if (!logSelectionInside(pre) && pre.textContent.length > MAX_LOG_CHARS) {
      pre.textContent = pre.textContent.slice(-MAX_LOG_CHARS);
    }
    if (followTail) pre.scrollTop = pre.scrollHeight;
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
      const followTail = shouldFollowLogTail(pre);
      logShowingPlaceholder = !(data.log && data.log.length);
      pre.textContent = logShowingPlaceholder ? "(no log output yet)" : data.log;
      if (followTail) pre.scrollTop = pre.scrollHeight;  // follow tail unless user scrolled up
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

  function streamLogs(sessionId, seq) {
    if (!window.EventSource) {
      pollLogs(sessionId, seq);
      return;
    }
    const url = `/api/sessions/${encodeURIComponent(sessionId)}/runner/logs/stream?lines=500`;
    const source = new EventSource(url);
    logSource = source;

    source.addEventListener("snapshot", (ev) => {
      if (seq !== logRequestSeq || logSession !== sessionId || logSource !== source) return;
      try {
        const data = JSON.parse(ev.data || "{}");
        setLogContent(data.log || "");
      } catch {
        setLogContent("");
      }
    });

    source.addEventListener("append", (ev) => {
      if (seq !== logRequestSeq || logSession !== sessionId || logSource !== source) return;
      try {
        const data = JSON.parse(ev.data || "{}");
        appendLogContent(data.chunk || "");
      } catch {}
    });

    source.addEventListener("stream_error", (ev) => {
      if (seq !== logRequestSeq || logSession !== sessionId || logSource !== source) return;
      try {
        const data = JSON.parse(ev.data || "{}");
        appendLogContent(`\n[log stream] ${data.error || "stream error"}\n`);
      } catch {
        appendLogContent("\n[log stream] stream error\n");
      }
    });

    source.onerror = () => {
      // EventSource reconnects automatically. Keep the current log content so
      // text selection and scroll position are not disturbed by transient errors.
    };
  }

  function openLogs(id) {
    clearLogTimer();
    cancelLogFetch();
    closeLogStream();
    logSession = id;
    logRequestSeq += 1;
    const seq = logRequestSeq;
    el("log-title").textContent = id;
    el("log-content").textContent = "loading…";
    logShowingPlaceholder = true;
    el("log-modal").classList.remove("hidden");
    lockBodyScroll();
    streamLogs(id, seq);
  }

  function closeLogs() {
    if (el("log-modal").classList.contains("hidden")) return;
    logRequestSeq += 1;
    clearLogTimer();
    cancelLogFetch();
    closeLogStream();
    el("log-modal").classList.add("hidden");
    unlockBodyScroll();
    logSession = null;
    logShowingPlaceholder = false;
  }

  async function clearLogs() {
    const sessionId = logSession;
    if (!sessionId) return;
    clearLogTimer();
    cancelLogFetch();
    closeLogStream();
    logRequestSeq += 1;
    const seq = logRequestSeq;
    try {
      await api(`/api/sessions/${encodeURIComponent(sessionId)}/runner/logs`, { method: "DELETE" });
      if (seq !== logRequestSeq || logSession !== sessionId) return;
      el("log-content").textContent = "(cleared)";
      logShowingPlaceholder = true;
      logRequestSeq += 1;
      streamLogs(sessionId, logRequestSeq);
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
  let settingsOriginalHistorySince = null;

  async function openSettings(id, mode) {
    settingsSession = id;
    settingsMode = mode;
    settingsOriginalHistorySince = null;
    el("settings-error").textContent = "";
    el("settings-title").textContent =
      (mode === "webhook" ? "Webhook URL — "
        : mode === "telegram" ? "Telegram bot — "
        : "Data since — ") + id;
    el("settings-fields").innerHTML = "loading…";
    el("settings-modal").classList.remove("hidden");
    lockBodyScroll();
    if (mode === "data-since") {
      const s = sessions.find((x) => sessionId(x) === id) || {};
      const shared = sessions
        .filter((x) => x.feed_id && x.feed_id === s.feed_id && sessionId(x) !== id)
        .map((x) => sessionId(x));
      // prefill with the current effective UTC start (from the ohlcv file); fall
      // back to the raw history_since only if it is itself a date
      const since = Number(s.data_since_time);
      let initial = "";
      if (Number.isFinite(since) && since > 0) {
        initial = formatUtcInput(since);
      } else if (parseDateAsUtc(s.history_since)) {
        initial = String(s.history_since);
      }
      settingsOriginalHistorySince = initial;
      el("settings-fields").innerHTML =
        `<label class="settings-label">Data start (UTC)</label>` +
        `<input id="settings-history-since" type="text" ` +
        `placeholder="YYYY-MM-DD or YYYY-MM-DD HH:MM" value="${esc(initial)}">` +
        `<div class="muted">UTC date or datetime, e.g. 2026-05-01 or 2026-06-01 07:30.</div>` +
        `<div class="muted">Saving re-syncs market data and restarts the running strategy.</div>` +
        (shared.length
          ? `<div class="muted">Shares this market's data, so it changes too: ` +
            `<span class="mono">${esc(shared.join(", "))}</span></div>`
          : "") +
        `<div id="settings-loading" class="settings-loading" hidden>` +
          `<span class="settings-spinner" aria-hidden="true"></span>` +
          `<span class="settings-loading-text">Re-syncing data…</span>` +
        `</div>`;
      return;
    }
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
    const saveBtn = el("settings-save");
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "Save"; }
    settingsSession = null;
    settingsMode = null;
    settingsOriginalHistorySince = null;
  }

  function setDataSinceLoading(on) {
    const saveBtn = el("settings-save");
    const inputEl = el("settings-history-since");
    const status = el("settings-loading");
    if (saveBtn) {
      saveBtn.disabled = on;
      saveBtn.textContent = on ? "Applying…" : "Save";
    }
    if (inputEl) inputEl.disabled = on;
    if (status) status.hidden = !on;
  }

  async function saveSettings() {
    if (!settingsSession) return;
    if (settingsMode === "data-since") {
      const inputEl = el("settings-history-since");
      const value = (inputEl ? inputEl.value : "").trim();
      const sid = settingsSession;
      if (isSameUtcDateInput(value, settingsOriginalHistorySince)) {
        closeSettings();
        return;
      }
      el("settings-error").textContent = "";
      setDataSinceLoading(true); // feed + runner restart takes a moment; no double-submit
      try {
        await api(`/api/sessions/${encodeURIComponent(sid)}/history-since`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ history_since: value }),
        });
      } catch (e) {
        el("settings-error").textContent = e.message;
        setDataSinceLoading(false);
        return;
      }
      if (settingsSession === sid && settingsMode === "data-since") {
        setDataSinceLoading(false);
        closeSettings();
      }
      return;
    }
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
    closeHubMenu();
    if (isCalendarOpen()) closeCalendar();
    if (isAssetsOpen()) closeAssets();
    closeScriptDropdown();
    closeDataSinceTooltips();
    if (!el("log-modal").classList.contains("hidden")) closeLogs();
    if (!el("settings-modal").classList.contains("hidden")) closeSettings();
    if (!el("remove-modal").classList.contains("hidden")) closeRemoveConfirm();
    closeAiChat();
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
  let validatedMarketType = "";

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
    validatedMarketType = "";
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
    validatedMarketType = "";
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
      if (data.skipped || data.exists === true) {
        validatedMarketType = data.market_type || "";
        setFieldError("symbol-error", "");
        return true;
      }
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
      applyAiAvailability(data.ai_enabled);
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
      if (!aiPending) syncAiChatState({ allowImport: false });
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
          applyAiAvailability(msg.ai_enabled);
          applySessions(msg.sessions || []);
        } else if (msg.type === "ai_chat_updated" && !aiPending) {
          syncAiChatState({ allowImport: false });
        } else if (msg.type === "ai_prefs_updated") {
          applyAiPrefs(msg.model, msg.effort);
        } else if (msg.type === "calendar_updated") {
          if (isCalendarOpen()) loadCalendarEvents();
        } else if (msg.type === "calendar_forecast_running") {
          if (isCalendarOpen()) applyCalendarForecastRunning(String(msg.event_id || ""));
        } else if (msg.type === "calendar_forecast_updated") {
          if (isCalendarOpen()) loadCalendarEvents();
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

  initHubMenuCalendar();
  initSessionReordering();
  initDesktopCardCarousel();
  initAiChat();
  initPepeFaces();
  startHubClock();
  refresh();   // one-time initial load; thereafter the hub pushes via /ws/hub
  connect();
})();
