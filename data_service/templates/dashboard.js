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
  const calendarExpandedSessionEvents = new Set();
  let calendarSelectedDate = null;
  let calendarRequestSeq = 0;
  let lastCalendarTouchAt = 0;
  let calendarSuppressTapUntil = 0;
  let calendarOpenTimer = null;
  let calendarCloseTimer = null;
  let calendarAddText = "";
  let calendarAddPending = false;
  let calendarAddOpen = false;
  let calendarAddStatus = "";
  let calendarAddError = false;
  let calendarAddSessionMenuOpen = false;
  let calendarAddComposing = false;
  const calendarAddSessionIds = new Set();
  let watchlistRows = [];
  let watchlistErrors = [];
  let watchlistCollectedAt = "";
  let watchlistHasSnapshot = false;
  let watchlistExchange = "all";
  let watchlistMarketFilter = "all";
  let watchlistSearch = "";
  let watchlistSort = "turnover_24h";
  let watchlistSortDescending = true;
  let watchlistWs = null;
  let watchlistGeneration = 0;
  let watchlistReconnectTimer = null;
  let watchlistKeepaliveTimer = null;
  let watchlistOpenTimer = null;
  let watchlistCloseTimer = null;
  let watchlistUiError = "";
  let watchlistFilteredRowsCache = [];
  let watchlistWindowRenderFrame = null;
  let watchlistAddRow = null;
  let watchlistAddTimeframe = "5m";
  let watchlistAddHistoryDate = "";
  let watchlistAddCalendarMonth = new Date(Date.UTC(1970, 0, 1));
  let watchlistAddCalendarSuppressTapUntil = 0;
  let lastWatchlistAddCalendarTouchAt = 0;
  let watchlistAddPending = false;
  let scriptChangeSessionId = null;
  let scriptChangeSelected = "";
  let scriptChangePending = false;
  const watchlistFavorites = new Set();
  const watchlistOverscanRows = 8;
  let registerAnimatedPepeFace = () => {};
  const assetColors = [
    "#3b82f6", "#f59e0b", "#10b981", "#ef4444",
    "#a78bfa", "#22d3ee", "#f472b6", "#94a3b8",
  ];
  const assetTransferSources = {
    binance: new Set(["spot", "swap", "margin", "funding", "earn"]),
    bitget: new Set(["spot", "swap", "margin", "funding", "earn"]),
    bybit: new Set(["spot", "swap", "margin", "funding"]),
    okx: new Set(["spot", "funding"]),
  };
  let assetsRequestSeq = 0;
  const assetsRefreshIntervalMs = 10000;
  let assetsRefreshTimer = null;
  let assetsOpenTimer = null;
  let assetsCloseTimer = null;
  let assetsHaveData = false;
  let assetsPayload = null;
  let selectedAssetExchange = null;
  const assetAccountTypeDonuts = new Set();
  let accountView = "assets";
  let accountPagerScrollToView = null;
  let positionsRequestSeq = 0;
  let positionsHaveData = false;
  let positionsPayload = null;
  let positionsObservedAt = 0;
  let pnlRequestSeq = 0;
  let pnlHaveData = false;
  let pnlPayload = null;
  let selectedPnlExchange = null;
  let pnlPeriodDays = 90;
  const pnlRefreshIntervalMs = 10000;
  let pnlRefreshTimer = null;
  let pnlListSizeFrame = null;
  const historyCustomSelects = {};
  let historyImportPreview = null;
  let historyImportSelections = {};
  let historyImportBusy = false;
  let historyImportJobTimer = null;
  let historyImportLogLoaded = false;
  let historyImportLogPayload = { results: [], total: 0 };
  let historyImportStatusText = "";
  let positionHistoryRequestSeq = 0;
  let positionHistoryHaveData = false;
  let positionHistoryRows = [];
  let positionHistoryCursor = null;
  let positionHistoryTotal = 0;
  let positionHistoryGroupsHaveData = false;
  const positionHistoryNavigation = {
    level: "exchanges",
    exchange: "",
    symbol: "",
  };
  let orderHistoryRequestSeq = 0;
  let orderHistoryHaveData = false;
  let orderHistoryRows = [];
  let orderHistoryCursor = null;
  let orderHistoryTotal = 0;
  let orderHistoryGroupsHaveData = false;
  const orderHistoryNavigation = {
    level: "exchanges",
    exchange: "",
    symbol: "",
  };
  const accountHistoryPageSize = 50;
  let accountPositionsWs = null;
  let accountPositionsReconnectTimer = null;
  let accountPositionsKeepaliveTimer = null;
  let accountPositionsGeneration = 0;
  let assetTransferRequestSeq = 0;
  let assetTransferContext = null;
  let assetTransferReview = null;
  let assetTransferMode = "options";
  let assetTransferSubmitting = false;
  let assetTransferHistoryRequestSeq = 0;
  let assetTransferHistoryPortfolio = null;
  let assetTransferHistoryRows = [];
  let assetTransferHistoryCursor = null;
  let assetTransferHistoryLoading = false;
  let updateConfirmationToken = "";
  let updatePollTimer = null;
  let updateMessageTimer = null;
  let updateCompleteTimer = null;
  let updateRestartPending = false;
  let updateRequiresRestart = true;

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
      state.cancelPending = false;
      state.cancelRequested = false;
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

  function calendarEventColor(index) {
    if (index < calendarColors.length) return calendarColors[index];
    const hue = Math.round((index * 137.508) % 360);
    return `hsl(${hue} 68% 65%)`;
  }

  function calendarAffectedSessions(event) {
    if (Array.isArray(event && event.sessions) && event.sessions.length) {
      return event.sessions.filter((session) => session && session.session_id);
    }
    const sessionId = String(event && event.session_id || "");
    return sessionId ? [{
      session_id: sessionId,
      symbol: event.symbol,
      exchange: event.exchange,
      timeframe: event.timeframe,
    }] : [];
  }

  function calendarSessionLabel(session) {
    return [
      session.symbol || session.session_id,
      String(session.exchange || "").toUpperCase(),
      session.timeframe || "",
    ].filter(Boolean).join(" · ");
  }

  function closeCalendarAddSessionMenu() {
    calendarAddSessionMenuOpen = false;
    const select = el("calendar-add-session-select");
    const options = el("calendar-add-session-options");
    const button = el("calendar-add-session-button");
    if (select) select.classList.remove("open");
    if (options) options.classList.add("hidden");
    if (button) button.setAttribute("aria-expanded", "false");
  }

  function renderCalendarAddControls() {
    if (!calendarSelectedDate) return;
    el("calendar-add-form").classList.toggle("hidden", !calendarAddOpen);
    el("calendar-add-toggle").setAttribute(
      "aria-expanded",
      calendarAddOpen ? "true" : "false",
    );
    el("calendar-add-toggle").disabled = calendarAddPending;
    const liveSessionIds = new Set(sessions.map(sessionId));
    for (const sessionIdValue of calendarAddSessionIds) {
      if (!liveSessionIds.has(sessionIdValue)) calendarAddSessionIds.delete(sessionIdValue);
    }

    const input = el("calendar-add-input");
    if (document.activeElement !== input) input.value = calendarAddText;
    input.disabled = calendarAddPending;

    let selectionLabel = "";
    if (calendarAddSessionIds.size) {
      if (calendarAddSessionIds.size === 1) {
        const selected = sessions.find((session) => (
          calendarAddSessionIds.has(sessionId(session))
        ));
        selectionLabel = selected ? calendarSessionLabel(selected) : "1 session";
      } else {
        selectionLabel = `${calendarAddSessionIds.size} sessions`;
      }
    } else {
      selectionLabel = aiEnabled ? "AI matches sessions" : "Select sessions";
    }
    el("calendar-add-session-label").textContent = selectionLabel;

    const options = [];
    if (aiEnabled) {
      options.push(
        `<button class="calendar-add-session-option${calendarAddSessionIds.size ? "" : " selected"}" ` +
        `type="button" data-calendar-add-session="__auto__" role="option" ` +
        `aria-selected="${calendarAddSessionIds.size ? "false" : "true"}">AI matches sessions</button>`,
      );
    }
    for (const session of sessions) {
      const id = sessionId(session);
      const selected = calendarAddSessionIds.has(id);
      options.push(
        `<button class="calendar-add-session-option${selected ? " selected" : ""}" ` +
        `type="button" data-calendar-add-session="${esc(id)}" role="option" ` +
        `aria-selected="${selected ? "true" : "false"}">${esc(calendarSessionLabel(session))}</button>`,
      );
    }
    if (!options.length) {
      options.push(`<div class="script-select-empty">No active sessions</div>`);
    }
    el("calendar-add-session-options").innerHTML = options.join("");
    el("calendar-add-session-select").classList.toggle("open", calendarAddSessionMenuOpen);
    el("calendar-add-session-options").classList.toggle("hidden", !calendarAddSessionMenuOpen);
    el("calendar-add-session-button").setAttribute(
      "aria-expanded",
      calendarAddSessionMenuOpen ? "true" : "false",
    );
    el("calendar-add-session-button").disabled = calendarAddPending || !sessions.length;
    el("calendar-add-submit").disabled = (
      calendarAddPending || !calendarAddText.trim() || !sessions.length
    );

    const status = el("calendar-add-status");
    status.textContent = calendarAddStatus;
    status.classList.toggle("error", calendarAddError);
    status.classList.toggle("pending", calendarAddPending);
  }

  async function submitCalendarEvent() {
    const text = calendarAddText.trim();
    if (!calendarSelectedDate || !text || calendarAddPending) return;
    if (!sessions.length) {
      calendarAddStatus = "Add a session before creating an event.";
      calendarAddError = true;
      renderCalendarAddControls();
      return;
    }
    if (!aiEnabled && !calendarAddSessionIds.size) {
      calendarAddStatus = "Select at least one session.";
      calendarAddError = true;
      renderCalendarAddControls();
      return;
    }

    const submittedDate = calendarSelectedDate;
    calendarAddPending = true;
    calendarAddError = false;
    calendarAddStatus = aiEnabled ? "Researching event" : "Adding event";
    closeCalendarAddSessionMenu();
    renderCalendarAddControls();

    let completed = false;
    try {
      await streamSse("/api/calendar/events/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          date: submittedDate,
          text,
          session_ids: Array.from(calendarAddSessionIds),
        }),
      }, (eventName, data) => {
        if (eventName === "status" || eventName === "work_status") {
          const statusText = String(data.text || "").trim();
          if (statusText) {
            calendarAddStatus = statusText;
            calendarAddError = false;
            renderCalendarAddControls();
          }
          return;
        }
        if (eventName === "stream_error") {
          throw new Error(data.error || "Calendar event could not be added");
        }
        if (eventName === "done") {
          completed = true;
          calendarAddStatus = String(data.answer || "Event added").trim() || "Event added";
          calendarAddError = false;
        }
      });
      if (!completed) throw new Error("Calendar event request ended before completion");
      calendarAddText = "";
      calendarAddSessionIds.clear();
      calendarAddOpen = false;
      await loadCalendarEvents();
    } catch (error) {
      calendarAddStatus = error && error.message
        ? error.message
        : "Calendar event could not be added";
      calendarAddError = true;
    } finally {
      calendarAddPending = false;
      if (isCalendarOpen() && calendarSelectedDate) renderCalendarDetails();
    }
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
    loadUpdateStatus();
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

  const watchlistExchangeNames = {
    binance: "Binance",
    bitget: "Bitget",
    bybit: "Bybit",
    okx: "OKX",
    hyperliquid: "Hyperliquid",
  };

  function isWatchlistOpen() {
    return !el("watchlist-modal").classList.contains("hidden");
  }

  function watchlistFavoriteKey(exchange, symbol) {
    return `${String(exchange || "").toLowerCase()}|${String(symbol || "").toUpperCase()}`;
  }

  function applyWatchlistFavorites(payload) {
    watchlistFavorites.clear();
    const favorites = payload && Array.isArray(payload.favorites) ? payload.favorites : [];
    for (const item of favorites) {
      if (!item || typeof item !== "object") continue;
      watchlistFavorites.add(watchlistFavoriteKey(item.exchange, item.symbol));
    }
    if (isWatchlistOpen()) renderWatchlist();
  }

  function watchlistNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function formatWatchlistPrice(value) {
    const number = watchlistNumber(value);
    if (number === null) return "—";
    const absolute = Math.abs(number);
    let digits = 8;
    if (absolute >= 1000) digits = 2;
    else if (absolute >= 1) digits = 4;
    else if (absolute >= 0.01) digits = 6;
    return new Intl.NumberFormat("en-US", {
      maximumFractionDigits: digits,
      minimumFractionDigits: 0,
    }).format(number);
  }

  function formatWatchlistTurnover(value) {
    const number = watchlistNumber(value);
    if (number === null) return "Turnover unavailable";
    return `${new Intl.NumberFormat("en-US", {
      notation: "compact",
      maximumFractionDigits: 2,
    }).format(number)} 24h turnover`;
  }

  function formatWatchlistChange(value) {
    const number = watchlistNumber(value);
    if (number === null) return "—";
    return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
  }

  function formatWatchlistUpdated(value) {
    const date = new Date(value || "");
    if (!Number.isFinite(date.getTime())) return "Waiting for market data";
    return `Updated ${date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    })}`;
  }

  function setWatchlistStatus(text, state = "") {
    const node = el("watchlist-live-status");
    node.textContent = text;
    node.className = `watchlist-live-status${state ? ` ${state}` : ""}`;
  }

  function filteredWatchlistRows() {
    const search = watchlistSearch.trim().toUpperCase();
    const rows = watchlistRows.filter((row) => {
      if (!row || typeof row !== "object") return false;
      const exchange = String(row.exchange || "").toLowerCase();
      if (watchlistExchange !== "all" && exchange !== watchlistExchange) return false;
      const key = watchlistFavoriteKey(exchange, row.symbol);
      if (watchlistMarketFilter === "favorites" && !watchlistFavorites.has(key)) return false;
      if (
        ["stocks", "etfs", "commodities"].includes(watchlistMarketFilter)
        && String(row.category || "crypto").toLowerCase() !== watchlistMarketFilter
      ) return false;
      if (
        (watchlistMarketFilter === "USDT" || watchlistMarketFilter === "USDC")
        && String(row.quote || "").toUpperCase() !== watchlistMarketFilter
      ) return false;
      if (!search) return true;
      return [row.symbol, row.market_id, row.base, row.quote, row.exchange]
        .some((value) => String(value || "").toUpperCase().includes(search));
    });
    const direction = watchlistSortDescending ? -1 : 1;
    rows.sort((left, right) => {
      const leftValue = watchlistNumber(left[watchlistSort]);
      const rightValue = watchlistNumber(right[watchlistSort]);
      const leftValid = leftValue !== null;
      const rightValid = rightValue !== null;
      if (leftValid && rightValid && leftValue !== rightValue) {
        return (leftValue - rightValue) * direction;
      }
      if (leftValid !== rightValid) return leftValid ? -1 : 1;
      return String(left.symbol || "").localeCompare(String(right.symbol || ""));
    });
    return rows;
  }

  function watchlistRowHeight() {
    const value = Number.parseFloat(
      getComputedStyle(el("watchlist-list")).getPropertyValue("--watchlist-row-height"),
    );
    return Number.isFinite(value) && value > 0 ? value : 57;
  }

  function renderWatchlistWindow() {
    watchlistWindowRenderFrame = null;
    const list = el("watchlist-list");
    const rows = watchlistFilteredRowsCache;
    if (list.classList.contains("hidden") || rows.length === 0) {
      list.replaceChildren();
      return;
    }

    const rowHeight = watchlistRowHeight();
    const viewportRows = Math.max(1, Math.ceil(list.clientHeight / rowHeight));
    const requestedStart = Math.max(
      0,
      Math.floor(list.scrollTop / rowHeight) - watchlistOverscanRows,
    );
    const start = Math.min(requestedStart, Math.max(0, rows.length - viewportRows));
    const end = Math.min(rows.length, start + viewportRows + watchlistOverscanRows * 2);
    const existing = new Map(
      Array.from(list.querySelectorAll(".watchlist-row"))
        .map((item) => [item.dataset.watchlistKey, item]),
    );
    const fragment = document.createDocumentFragment();
    const topSpacer = document.createElement("div");
    topSpacer.className = "watchlist-spacer";
    topSpacer.style.height = `${start * rowHeight}px`;
    fragment.appendChild(topSpacer);

    for (let index = start; index < end; index += 1) {
      const row = rows[index];
      const key = watchlistFavoriteKey(row.exchange, row.symbol);
      const item = existing.get(key) || createWatchlistRow();
      patchWatchlistRow(item, row);
      fragment.appendChild(item);
    }

    const bottomSpacer = document.createElement("div");
    bottomSpacer.className = "watchlist-spacer";
    bottomSpacer.style.height = `${(rows.length - end) * rowHeight}px`;
    fragment.appendChild(bottomSpacer);
    list.replaceChildren(fragment);
  }

  function scheduleWatchlistWindowRender() {
    if (watchlistWindowRenderFrame !== null) return;
    watchlistWindowRenderFrame = window.requestAnimationFrame(renderWatchlistWindow);
  }

  function createWatchlistRow() {
    const item = document.createElement("div");
    item.className = "watchlist-row";
    item.setAttribute("role", "row");

    const favorite = document.createElement("button");
    favorite.type = "button";
    favorite.className = "watchlist-favorite";
    favorite.textContent = "★";
    item.appendChild(favorite);

    const market = document.createElement("div");
    market.className = "watchlist-market";
    const logo = document.createElement("img");
    logo.className = "watchlist-exchange-logo";
    logo.alt = "";
    logo.decoding = "async";
    logo.loading = "lazy";
    logo.fetchPriority = "low";
    logo.addEventListener("error", () => {
      const current = logo.getAttribute("src") || "";
      const fallback = logo.dataset.fallbackSrc || "";
      if (fallback && current !== fallback) {
        logo.dataset.failedSrc = current;
        logo.src = fallback;
        logo.hidden = false;
        return;
      }
      logo.hidden = true;
    });
    market.appendChild(logo);
    const text = document.createElement("div");
    text.className = "watchlist-market-text";
    const name = document.createElement("div");
    name.className = "watchlist-market-name";
    const symbol = document.createElement("button");
    symbol.type = "button";
    symbol.className = "watchlist-symbol-button";
    symbol.dataset.watchlistAddSymbol = "";
    const exchange = document.createElement("span");
    name.append(symbol, exchange);
    const meta = document.createElement("span");
    meta.className = "watchlist-market-meta";
    text.append(name, meta);
    market.appendChild(text);
    item.appendChild(market);

    const last = document.createElement("span");
    last.className = "watchlist-number";
    item.appendChild(last);

    const change = document.createElement("span");
    change.className = "watchlist-number watchlist-change";
    item.appendChild(change);
    item._watchlistNodes = { favorite, logo, symbol, exchange, meta, last, change };
    return item;
  }

  function patchWatchlistRow(item, row) {
    const nodes = item._watchlistNodes;
    if (!nodes) return;
    const key = watchlistFavoriteKey(row.exchange, row.symbol);
    const isFavorite = watchlistFavorites.has(key);
    item.dataset.watchlistKey = key;
    item._watchlistRow = row;
    nodes.favorite.classList.toggle("active", isFavorite);
    nodes.favorite.dataset.watchlistFavoriteExchange = String(row.exchange || "");
    nodes.favorite.dataset.watchlistFavoriteSymbol = String(row.symbol || "");
    nodes.favorite.setAttribute("aria-pressed", String(isFavorite));
    nodes.favorite.setAttribute("aria-label", `${isFavorite ? "Remove" : "Add"} ${row.symbol} favorite`);
    nodes.favorite.title = isFavorite ? "Remove favorite" : "Add favorite";
    const symbolLogoUrl = String(row.symbol_logo_url || "");
    const fallbackLogoUrl = String(row.exchange_logo_url || "");
    nodes.logo.dataset.fallbackSrc = fallbackLogoUrl;
    const logoUrl = (
      symbolLogoUrl && nodes.logo.dataset.failedSrc !== symbolLogoUrl
        ? symbolLogoUrl
        : fallbackLogoUrl
    );
    if (logoUrl) {
      if (nodes.logo.getAttribute("src") !== logoUrl) nodes.logo.src = logoUrl;
      nodes.logo.hidden = false;
    } else {
      nodes.logo.hidden = true;
      nodes.logo.removeAttribute("src");
    }
    nodes.symbol.textContent = `${String(row.base || "")} / ${String(row.quote || "")}`;
    nodes.symbol.dataset.watchlistAddSymbol = String(row.symbol || "");
    nodes.symbol.title = `Add ${String(row.symbol || "")} session`;
    nodes.symbol.setAttribute("aria-label", `Add ${String(row.symbol || "")} session`);
    const showExchange = watchlistExchange === "all";
    nodes.exchange.textContent = showExchange
      ? watchlistExchangeNames[row.exchange] || String(row.exchange || "")
      : "";
    nodes.exchange.hidden = !showExchange;
    nodes.meta.textContent = formatWatchlistTurnover(row.turnover_24h);
    nodes.last.textContent = formatWatchlistPrice(row.last);
    const changeValue = watchlistNumber(row.change_24h);
    nodes.change.classList.toggle("positive", changeValue !== null && changeValue > 0);
    nodes.change.classList.toggle("negative", changeValue !== null && changeValue < 0);
    nodes.change.textContent = formatWatchlistChange(row.change_24h);
  }

  function renderWatchlist(resetScroll = false) {
    const rows = filteredWatchlistRows();
    const list = el("watchlist-list");
    const loading = el("watchlist-loading");
    const error = el("watchlist-error");
    const empty = el("watchlist-empty");
    const noDataError = Boolean(watchlistUiError && !watchlistHasSnapshot);
    loading.classList.toggle("hidden", watchlistHasSnapshot || noDataError);
    error.classList.toggle("hidden", !noDataError);
    error.textContent = noDataError ? watchlistUiError : "";
    empty.classList.toggle("hidden", !watchlistHasSnapshot || rows.length > 0);
    list.classList.toggle("hidden", !watchlistHasSnapshot || rows.length === 0);
    watchlistFilteredRowsCache = rows;
    if (resetScroll) list.scrollTop = 0;
    scheduleWatchlistWindowRender();
    el("watchlist-count").textContent = `${rows.length.toLocaleString()} of ${watchlistRows.length.toLocaleString()} markets`;
    el("watchlist-updated").textContent = formatWatchlistUpdated(watchlistCollectedAt);

    document.querySelectorAll("[data-watchlist-exchange]").forEach((button) => {
      button.classList.toggle("active", button.dataset.watchlistExchange === watchlistExchange);
    });
    document.querySelectorAll("[data-watchlist-market]").forEach((button) => {
      button.classList.toggle("active", button.dataset.watchlistMarket === watchlistMarketFilter);
    });
    document.querySelectorAll("[data-watchlist-sort]").forEach((button) => {
      const active = button.dataset.watchlistSort === watchlistSort;
      button.classList.toggle("active", active);
      button.classList.toggle("desc", active && watchlistSortDescending);
    });

    const exchangeErrors = watchlistErrors.map((item) => {
      const exchangeName = watchlistExchangeNames[item && item.exchange] || String(item && item.exchange || "Exchange");
      return `${exchangeName} unavailable`;
    });
    if (watchlistUiError && watchlistHasSnapshot) exchangeErrors.unshift(watchlistUiError);
    const errorSummary = el("watchlist-exchange-errors");
    errorSummary.textContent = exchangeErrors.join(" · ");
    errorSummary.title = errorSummary.textContent;
    errorSummary.classList.toggle("hidden", exchangeErrors.length === 0);
  }

  function clearWatchlistTimers() {
    if (watchlistReconnectTimer !== null) {
      clearTimeout(watchlistReconnectTimer);
      watchlistReconnectTimer = null;
    }
    if (watchlistKeepaliveTimer !== null) {
      clearInterval(watchlistKeepaliveTimer);
      watchlistKeepaliveTimer = null;
    }
  }

  function closeWatchlistSocket() {
    clearWatchlistTimers();
    const ws = watchlistWs;
    watchlistWs = null;
    if (!ws) return;
    ws.onopen = null;
    ws.onmessage = null;
    ws.onclose = null;
    ws.onerror = null;
    if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
      try { ws.close(); } catch {}
    }
  }

  function scheduleWatchlistReconnect(generation) {
    if (watchlistReconnectTimer !== null || !isWatchlistOpen()) return;
    watchlistReconnectTimer = window.setTimeout(() => {
      watchlistReconnectTimer = null;
      connectWatchlist(generation);
    }, 2000);
  }

  function connectWatchlist(generation = watchlistGeneration) {
    if (
      generation !== watchlistGeneration
      || !isWatchlistOpen()
      || document.visibilityState !== "visible"
    ) return;
    if (
      watchlistWs
      && (watchlistWs.readyState === WebSocket.OPEN || watchlistWs.readyState === WebSocket.CONNECTING)
    ) return;
    setWatchlistStatus("Connecting");
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws/watchlist`);
    watchlistWs = ws;
    ws.onopen = () => {
      if (watchlistWs !== ws || generation !== watchlistGeneration) return;
      setWatchlistStatus(watchlistHasSnapshot ? "Live" : "Syncing", watchlistHasSnapshot ? "live" : "");
      watchlistKeepaliveTimer = window.setInterval(() => {
        if (watchlistWs === ws && ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 15000);
    };
    ws.onmessage = (event) => {
      if (watchlistWs !== ws || generation !== watchlistGeneration) return;
      try {
        const message = JSON.parse(event.data);
        if (message.type === "watchlist.snapshot") {
          const payload = message.payload && typeof message.payload === "object" ? message.payload : {};
          watchlistRows = Array.isArray(payload.results) ? payload.results : [];
          watchlistErrors = Array.isArray(payload.errors) ? payload.errors : [];
          watchlistCollectedAt = String(payload.collected_at || "");
          watchlistHasSnapshot = true;
          watchlistUiError = "";
          setWatchlistStatus("Live", "live");
          renderWatchlist();
        } else if (message.type === "watchlist.favorites") {
          applyWatchlistFavorites(message.payload);
        }
      } catch {
        watchlistUiError = "Invalid watchlist response";
        renderWatchlist();
      }
    };
    ws.onclose = () => {
      if (watchlistWs !== ws || generation !== watchlistGeneration) return;
      watchlistWs = null;
      clearWatchlistTimers();
      setWatchlistStatus("Reconnecting", "error");
      scheduleWatchlistReconnect(generation);
    };
    ws.onerror = () => {
      if (watchlistWs !== ws || generation !== watchlistGeneration) return;
      setWatchlistStatus("Reconnecting", "error");
    };
  }

  function finishWatchlistOpening() {
    if (watchlistOpenTimer !== null) clearTimeout(watchlistOpenTimer);
    watchlistOpenTimer = null;
    el("watchlist-modal").classList.remove("watchlist-opening");
  }

  function finishWatchlistClose() {
    const modal = el("watchlist-modal");
    const box = modal.querySelector(".watchlist-modal-box");
    modal.classList.remove(
      "watchlist-closing",
      "watchlist-dragging",
      "watchlist-swipe-closing",
      "watchlist-opening",
    );
    modal.classList.add("hidden");
    modal.style.background = "";
    box.style.transform = "";
    box.style.transition = "";
    modal.setAttribute("aria-hidden", "true");
    watchlistCloseTimer = null;
    watchlistOpenTimer = null;
    unlockBodyScroll();
  }

  function openWatchlist() {
    closeHubMenu();
    closeAiChat();
    if (watchlistCloseTimer !== null) {
      clearTimeout(watchlistCloseTimer);
      watchlistCloseTimer = null;
    }
    if (watchlistOpenTimer !== null) {
      clearTimeout(watchlistOpenTimer);
      watchlistOpenTimer = null;
    }
    const modal = el("watchlist-modal");
    const box = modal.querySelector(".watchlist-modal-box");
    modal.classList.remove(
      "watchlist-closing",
      "watchlist-dragging",
      "watchlist-swipe-closing",
      "watchlist-opening",
      "hidden",
    );
    modal.classList.add("watchlist-opening");
    modal.style.background = "";
    box.style.transform = "";
    box.style.transition = "";
    modal.setAttribute("aria-hidden", "false");
    watchlistOpenTimer = window.setTimeout(finishWatchlistOpening, 230);
    lockBodyScroll();
    renderWatchlist();
    watchlistGeneration += 1;
    connectWatchlist(watchlistGeneration);
  }

  function closeWatchlist(options = {}) {
    if (!isWatchlistOpen()) return;
    const modal = el("watchlist-modal");
    if (modal.classList.contains("watchlist-closing")) return;
    finishWatchlistOpening();
    watchlistGeneration += 1;
    closeWatchlistSocket();
    setWatchlistStatus("Offline");
    if (!mobileHubQuery.matches) {
      modal.classList.add("watchlist-closing");
      watchlistCloseTimer = window.setTimeout(finishWatchlistClose, 220);
      return;
    }
    const box = modal.querySelector(".watchlist-modal-box");
    modal.classList.remove("watchlist-dragging");
    if (options.fromDrag === true) {
      modal.classList.add("watchlist-swipe-closing");
      box.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
      window.requestAnimationFrame(() => {
        box.style.transform = "translateY(100dvh)";
      });
    } else {
      box.style.transform = "";
      box.style.transition = "";
    }
    modal.classList.add("watchlist-closing");
    watchlistCloseTimer = window.setTimeout(finishWatchlistClose, 220);
  }

  async function toggleWatchlistFavorite(button) {
    const exchange = String(button.dataset.watchlistFavoriteExchange || "");
    const symbol = String(button.dataset.watchlistFavoriteSymbol || "");
    const key = watchlistFavoriteKey(exchange, symbol);
    const favorite = !watchlistFavorites.has(key);
    if (favorite) watchlistFavorites.add(key);
    else watchlistFavorites.delete(key);
    renderWatchlist();
    try {
      const payload = await api("/api/watchlist/favorites", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exchange, symbol, favorite }),
      });
      applyWatchlistFavorites(payload);
    } catch (error) {
      if (favorite) watchlistFavorites.delete(key);
      else watchlistFavorites.add(key);
      watchlistUiError = error && error.message ? error.message : "Favorite could not be saved";
      renderWatchlist();
      window.setTimeout(() => {
        if (!watchlistUiError) return;
        watchlistUiError = "";
        if (isWatchlistOpen()) renderWatchlist();
      }, 5000);
    }
  }

  function defaultWatchlistHistorySince() {
    const now = new Date();
    const targetYear = now.getUTCFullYear();
    const targetMonth = now.getUTCMonth() - 2;
    const day = now.getUTCDate();
    const lastDay = new Date(Date.UTC(targetYear, targetMonth + 1, 0)).getUTCDate();
    return new Date(Date.UTC(targetYear, targetMonth, Math.min(day, lastDay)))
      .toISOString()
      .slice(0, 10);
  }

  function watchlistDateFromKey(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
    if (!match) return null;
    const year = Number(match[1]);
    const month = Number(match[2]) - 1;
    const day = Number(match[3]);
    const date = new Date(Date.UTC(year, month, day));
    if (
      date.getUTCFullYear() !== year
      || date.getUTCMonth() !== month
      || date.getUTCDate() !== day
    ) return null;
    return date;
  }

  function watchlistDateKey(date) {
    return new Date(Date.UTC(
      date.getUTCFullYear(),
      date.getUTCMonth(),
      date.getUTCDate(),
    )).toISOString().slice(0, 10);
  }

  function watchlistDateLabel(value) {
    const date = watchlistDateFromKey(value);
    if (!date) return "Select date";
    return date.toLocaleDateString("en-US", {
      timeZone: "UTC",
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  function setWatchlistAddHistoryDate(value) {
    const date = watchlistDateFromKey(value);
    if (!date) return;
    watchlistAddHistoryDate = watchlistDateKey(date);
    el("watchlist-add-date-label").textContent = watchlistDateLabel(watchlistAddHistoryDate);
  }

  function renderWatchlistAddCalendar() {
    const month = watchlistAddCalendarMonth;
    el("watchlist-add-calendar-title").textContent = month.toLocaleDateString("en-US", {
      timeZone: "UTC",
      year: "numeric",
      month: "long",
    });
    const todayKey = new Date().toISOString().slice(0, 10);
    const today = watchlistDateFromKey(todayKey);
    const currentMonthIndex = month.getUTCFullYear() * 12 + month.getUTCMonth();
    const todayMonthIndex = today.getUTCFullYear() * 12 + today.getUTCMonth();
    el("watchlist-add-calendar-next").disabled = currentMonthIndex >= todayMonthIndex;

    const year = month.getUTCFullYear();
    const monthIndex = month.getUTCMonth();
    const firstWeekday = new Date(Date.UTC(year, monthIndex, 1)).getUTCDay();
    const days = [];
    for (let index = 0; index < 42; index += 1) {
      const date = new Date(Date.UTC(year, monthIndex, 1 - firstWeekday + index));
      const key = watchlistDateKey(date);
      const outside = date.getUTCMonth() !== monthIndex;
      const future = key > todayKey;
      const classes = [
        "watchlist-add-calendar-day",
        outside ? "outside" : "",
        key === todayKey ? "today" : "",
        key === watchlistAddHistoryDate ? "selected" : "",
      ].filter(Boolean).join(" ");
      days.push(
        `<button type="button" class="${classes}" data-watchlist-history-date="${key}" `
        + `aria-label="${esc(watchlistDateLabel(key))}" `
        + `aria-selected="${key === watchlistAddHistoryDate ? "true" : "false"}"`
        + `${future ? " disabled" : ""}>${date.getUTCDate()}</button>`,
      );
    }
    el("watchlist-add-calendar-days").innerHTML = days.join("");
  }

  function closeWatchlistAddCalendar() {
    const calendar = el("watchlist-add-calendar");
    calendar.classList.add("hidden");
    calendar.classList.remove("above");
    el("watchlist-add-date-picker").classList.remove("open");
    el("watchlist-add-date-button").setAttribute("aria-expanded", "false");
  }

  function toggleWatchlistAddCalendar() {
    const calendar = el("watchlist-add-calendar");
    if (!calendar.classList.contains("hidden")) {
      closeWatchlistAddCalendar();
      return;
    }
    const selected = watchlistDateFromKey(watchlistAddHistoryDate) || new Date();
    watchlistAddCalendarMonth = new Date(Date.UTC(
      selected.getUTCFullYear(),
      selected.getUTCMonth(),
      1,
    ));
    closeWatchlistAddTimeframeOptions();
    renderWatchlistAddCalendar();
    el("watchlist-add-date-picker").classList.add("open");
    calendar.classList.remove("above");
    calendar.classList.remove("hidden");
    el("watchlist-add-date-button").setAttribute("aria-expanded", "true");
    window.requestAnimationFrame(() => {
      if (calendar.classList.contains("hidden")) return;
      const calendarRect = calendar.getBoundingClientRect();
      const buttonRect = el("watchlist-add-date-button").getBoundingClientRect();
      if (
        calendarRect.bottom > window.innerHeight - 8
        && buttonRect.top >= calendarRect.height + 8
      ) calendar.classList.add("above");
    });
  }

  function moveWatchlistAddCalendarMonth(delta) {
    const next = new Date(Date.UTC(
      watchlistAddCalendarMonth.getUTCFullYear(),
      watchlistAddCalendarMonth.getUTCMonth() + delta,
      1,
    ));
    const today = new Date();
    const nextIndex = next.getUTCFullYear() * 12 + next.getUTCMonth();
    const todayIndex = today.getUTCFullYear() * 12 + today.getUTCMonth();
    if (nextIndex > todayIndex) return;
    watchlistAddCalendarMonth = next;
    renderWatchlistAddCalendar();
    const days = el("watchlist-add-calendar-days");
    days.classList.remove("month-prev", "month-next");
    void days.offsetWidth;
    days.classList.add(delta < 0 ? "month-prev" : "month-next");
  }

  function normalizeWatchlistHistoryTime(raw) {
    const value = String(raw || "").trim();
    if (!value) return "00:00";
    let hours;
    let minutes;
    const colonMatch = /^(\d{1,2}):(\d{1,2})$/.exec(value);
    if (colonMatch) {
      hours = Number(colonMatch[1]);
      minutes = Number(colonMatch[2]);
    } else if (/^\d{1,4}$/.test(value)) {
      if (value.length <= 2) {
        hours = Number(value);
        minutes = 0;
      } else {
        hours = Number(value.slice(0, -2));
        minutes = Number(value.slice(-2));
      }
    } else {
      return null;
    }
    if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  }

  function isWatchlistAddOpen() {
    return !el("watchlist-add-modal").classList.contains("hidden");
  }

  function closeWatchlistAddTimeframeOptions() {
    setAnimatedScriptOptions(
      el("watchlist-add-timeframe-control"),
      el("watchlist-add-timeframe-button"),
      el("watchlist-add-timeframe-options"),
      false,
    );
  }

  function setWatchlistAddTimeframe(value) {
    watchlistAddTimeframe = String(value || "5m");
    el("watchlist-add-timeframe-label").textContent = watchlistAddTimeframe;
    el("watchlist-add-timeframe-options").querySelectorAll("[data-watchlist-timeframe]").forEach((option) => {
      const selected = option.dataset.watchlistTimeframe === watchlistAddTimeframe;
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-selected", String(selected));
    });
  }

  function toggleWatchlistAddTimeframeOptions() {
    const options = el("watchlist-add-timeframe-options");
    const opening = options.classList.contains("hidden");
    if (!opening) {
      closeWatchlistAddTimeframeOptions();
      return;
    }
    closeWatchlistAddCalendar();
    setAnimatedScriptOptions(
      el("watchlist-add-timeframe-control"),
      el("watchlist-add-timeframe-button"),
      options,
      true,
    );
  }

  function setWatchlistAddPending(pending) {
    watchlistAddPending = Boolean(pending);
    el("watchlist-add-submit").disabled = watchlistAddPending;
    el("watchlist-add-cancel").disabled = watchlistAddPending;
    el("watchlist-add-close").disabled = watchlistAddPending;
    el("watchlist-add-date-button").disabled = watchlistAddPending;
    el("watchlist-add-history-time").disabled = watchlistAddPending;
    el("watchlist-add-submit").textContent = watchlistAddPending ? "Adding" : "Add";
  }

  function openWatchlistAddModal(row) {
    if (!row || !row.exchange || !row.symbol) return;
    watchlistAddRow = row;
    watchlistAddCalendarSuppressTapUntil = 0;
    lastWatchlistAddCalendarTouchAt = 0;
    setWatchlistAddPending(false);
    setWatchlistAddTimeframe("5m");
    closeWatchlistAddTimeframeOptions();
    closeWatchlistAddCalendar();
    el("watchlist-add-error").textContent = "";
    el("watchlist-add-symbol").textContent = String(row.symbol);
    el("watchlist-add-exchange").textContent = watchlistExchangeNames[row.exchange]
      || String(row.exchange);
    setWatchlistAddHistoryDate(defaultWatchlistHistorySince());
    el("watchlist-add-history-time").value = "00:00";

    const logo = el("watchlist-add-logo");
    const primary = String(row.symbol_logo_url || "");
    const fallback = String(row.exchange_logo_url || "");
    logo.dataset.fallbackSrc = fallback;
    logo.onerror = () => {
      if (fallback && logo.getAttribute("src") !== fallback) {
        logo.src = fallback;
        logo.hidden = false;
      } else {
        logo.hidden = true;
      }
    };
    if (primary || fallback) {
      logo.src = primary || fallback;
      logo.hidden = false;
    } else {
      logo.removeAttribute("src");
      logo.hidden = true;
    }

    const modal = el("watchlist-add-modal");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    window.requestAnimationFrame(() => el("watchlist-add-timeframe-button").focus());
  }

  function closeWatchlistAddModal() {
    if (!isWatchlistAddOpen() || watchlistAddPending) return;
    closeWatchlistAddTimeframeOptions();
    closeWatchlistAddCalendar();
    el("watchlist-add-modal").classList.add("hidden");
    el("watchlist-add-modal").setAttribute("aria-hidden", "true");
    watchlistAddRow = null;
    el("watchlist-add-error").textContent = "";
  }

  async function submitWatchlistSession() {
    if (!watchlistAddRow || watchlistAddPending) return;
    if (!watchlistAddHistoryDate) {
      el("watchlist-add-error").textContent = "Select a history start date.";
      el("watchlist-add-date-button").focus();
      return;
    }
    const historyTime = normalizeWatchlistHistoryTime(el("watchlist-add-history-time").value);
    if (!historyTime) {
      el("watchlist-add-error").textContent = "Use a valid 24-hour time (HH:MM).";
      el("watchlist-add-history-time").focus();
      return;
    }
    el("watchlist-add-history-time").value = historyTime;
    const historySince = `${watchlistAddHistoryDate} ${historyTime}`;
    closeWatchlistAddCalendar();
    setWatchlistAddPending(true);
    el("watchlist-add-error").textContent = "";
    try {
      await api("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: "ccxt",
          exchange: String(watchlistAddRow.exchange),
          symbol: String(watchlistAddRow.symbol),
          timeframe: watchlistAddTimeframe,
          history_since: historySince,
          market_type: "linear",
        }),
      });
      setWatchlistAddPending(false);
      closeWatchlistAddModal();
    } catch (error) {
      setWatchlistAddPending(false);
      el("watchlist-add-error").textContent = error && error.message
        ? error.message
        : "Session could not be added.";
    }
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
    calendarAddOpen = false;
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

  function clearAssetsRefreshTimer() {
    if (assetsRefreshTimer === null) return;
    clearTimeout(assetsRefreshTimer);
    assetsRefreshTimer = null;
  }

  function scheduleAssetsRefresh() {
    clearAssetsRefreshTimer();
    if (
      !isAssetsOpen()
      || accountView !== "assets"
      || document.visibilityState !== "visible"
    ) return;
    assetsRefreshTimer = window.setTimeout(() => {
      assetsRefreshTimer = null;
      if (!isAssetsOpen() || accountView !== "assets") return;
      loadAssets(true, true);
    }, assetsRefreshIntervalMs);
  }

  function clearPnlRefreshTimer() {
    if (pnlRefreshTimer === null) return;
    clearTimeout(pnlRefreshTimer);
    pnlRefreshTimer = null;
  }

  function schedulePnlRefresh() {
    clearPnlRefreshTimer();
    if (
      !isAssetsOpen()
      || accountView !== "pnl"
      || document.visibilityState !== "visible"
    ) return;
    pnlRefreshTimer = window.setTimeout(() => {
      pnlRefreshTimer = null;
      if (!isAssetsOpen() || accountView !== "pnl") return;
      loadPnl();
    }, pnlRefreshIntervalMs);
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
    clearAssetsRefreshTimer();
    clearPnlRefreshTimer();
    closeAccountPositionsSocket();
    assetsCloseTimer = null;
    assetsOpenTimer = null;
    unlockBodyScroll();
  }

  async function openAccount(view = "assets") {
    const wasOpen = isAssetsOpen();
    closeHubMenu();
    closeAiChat();
    if (view === "assets") selectedAssetExchange = null;
    if (view === "pnl") selectedPnlExchange = null;
    if (!wasOpen && accountView === view && view === "position-history") {
      resetHistoryNavigation("position");
    }
    if (!wasOpen && accountView === view && view === "orders") {
      resetHistoryNavigation("order");
    }
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
    await switchAccountView(view, { load: true });
  }

  function closeAssets(options = {}) {
    if (!isAssetsOpen()) return;
    closeAssetTransferHistory();
    closeAssetTransfer();
    closePositionPnlPopover();
    const modal = el("assets-modal");
    if (modal.classList.contains("assets-closing")) return;
    const fromDrag = options && options.fromDrag === true;
    finishAssetsOpening();
    clearAssetsRefreshTimer();
    clearPnlRefreshTimer();
    assetsRequestSeq += 1;
    setAssetsLoading(false);
    positionsRequestSeq += 1;
    positionHistoryRequestSeq += 1;
    orderHistoryRequestSeq += 1;
    pnlRequestSeq += 1;
    setPnlLoading(false);
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

  const accountViewTitles = {
    assets: "Assets",
    positions: "Positions",
    "position-history": "Position History",
    orders: "Order History",
    "import-history": "Import History",
    pnl: "PnL",
  };

  function accountViewIsDrilledIn(view) {
    if (view === "assets") {
      return Boolean(selectedAssetExchange);
    }
    if (view === "position-history") {
      return positionHistoryNavigation.level !== "exchanges";
    }
    if (view === "orders") {
      return orderHistoryNavigation.level !== "exchanges";
    }
    if (view === "pnl") {
      return Boolean(selectedPnlExchange);
    }
    return false;
  }

  async function switchAccountView(view, options = {}) {
    if (!Object.prototype.hasOwnProperty.call(accountViewTitles, view)) return;
    const previousView = accountView;
    // Desktop: capture the modal-box height before the view swap so we can
    // animate it to the new view's height (avoids the snap between tabs).
    const accountBox = document.querySelector(".assets-modal-box");
    const animateAccountHeight = Boolean(options.animate)
      && accountBox
      && !el("assets-modal").classList.contains("hidden")
      && !mobileHubQuery.matches
      && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const accountStartHeight = animateAccountHeight ? accountBox.offsetHeight : 0;
    // History views reset their content and refetch on switch, so the height
    // right after the view swap is the (empty) reset state — animating to it
    // then jumping to the loaded height is the snap the user sees. For those we
    // hold the old height through the reload and tween once to the final height.
    const isAccountHistoryView = (
      view === "position-history"
      || view === "orders"
      || view === "import-history"
      || view === "pnl"
    );
    const deferAccountHeight = animateAccountHeight && isAccountHistoryView && Boolean(options.load);
    const runAccountHeightTween = () => {
      accountBox.style.height = "";
      const endHeight = accountBox.offsetHeight;
      if (Math.abs(endHeight - accountStartHeight) <= 1) return;
      accountBox.style.height = `${accountStartHeight}px`;
      void accountBox.offsetHeight; // reflow to lock the start height
      accountBox.classList.add("flip-animating");
      accountBox.style.height = `${endHeight}px`;
      const onHeightEnd = (event) => {
        if (event.target !== accountBox || event.propertyName !== "height") return;
        accountBox.removeEventListener("transitionend", onHeightEnd);
        accountBox.classList.remove("flip-animating");
        accountBox.style.height = "";
      };
      accountBox.addEventListener("transitionend", onHeightEnd);
    };
    if (accountView === "assets" && view !== "assets") {
      clearAssetsRefreshTimer();
      assetsRequestSeq += 1;
      setAssetsLoading(false);
    }
    if (accountView === "positions" && view !== "positions") {
      positionsRequestSeq += 1;
      closeAccountPositionsSocket();
      closePositionPnlPopover();
    }
    if (accountView === "position-history" && view !== "position-history") {
      positionHistoryRequestSeq += 1;
    }
    if (accountView === "orders" && view !== "orders") {
      orderHistoryRequestSeq += 1;
    }
    if (accountView === "pnl" && view !== "pnl") {
      clearPnlRefreshTimer();
      pnlRequestSeq += 1;
      setPnlLoading(false);
    }
    if (previousView !== view && view === "position-history") {
      resetHistoryNavigation("position");
    }
    if (previousView !== view && view === "orders") {
      resetHistoryNavigation("order");
    }
    if (previousView === view && accountViewIsDrilledIn(view)) {
      if (view === "assets") selectedAssetExchange = null;
      else if (view === "pnl") selectedPnlExchange = null;
      else resetHistoryNavigation(view === "position-history" ? "position" : "order");
    }
    accountView = view;
    document.querySelectorAll("[data-account-view]").forEach((button) => {
      if (!button.classList.contains("account-tab")) return;
      const active = button.dataset.accountView === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    Object.keys(accountViewTitles).forEach((name) => {
      el(`account-${name}-view`).classList.toggle("hidden", name !== view);
    });
    // mobile: keep the horizontal swipe pager aligned with the active view
    // (skip when the switch itself came from a pager snap)
    if (!options.fromPager && accountPagerScrollToView) {
      accountPagerScrollToView(view, Boolean(options.animate));
    }
    if (animateAccountHeight) {
      if (deferAccountHeight) {
        // hold the old height while the history content resets and reloads
        accountBox.style.height = `${accountStartHeight}px`;
      } else {
        runAccountHeightTween();
      }
    }

    const accountDetailSelected = (
      (view === "assets" && selectedAssetExchange)
      || (view === "pnl" && selectedPnlExchange)
    );
    el("assets-back").classList.toggle("hidden", !accountDetailSelected);
    el("assets-refresh").classList.toggle(
      "hidden",
      !new Set(["assets", "positions", "position-history", "orders", "pnl"]).has(view),
    );
    const refreshTitles = {
      assets: "Refresh assets",
      positions: "Refresh positions",
      "position-history": "Refresh position history",
      orders: "Refresh order history",
      pnl: "Refresh PnL",
    };
    el("assets-refresh").title = refreshTitles[view] || "Refresh";
    el("assets-refresh").setAttribute("aria-label", el("assets-refresh").title);
    el("assets-title").textContent = accountViewTitles[view];
    if (view === "assets" && assetsPayload) renderAssetsView();
    if (view === "pnl" && pnlPayload) renderPnlView();
    if (!options.load) {
      if (view === "assets") scheduleAssetsRefresh();
      if (view === "pnl") schedulePnlRefresh();
      return;
    }
    if (view === "assets") await loadAssets(false);
    else if (view === "positions") {
      await loadPositions(false);
      connectAccountPositions();
    }
    else if (view === "position-history") await loadPositionHistory(false);
    else if (view === "orders") await loadOrderHistory(false);
    else if (view === "import-history") await loadHistoryImports(true);
    else if (view === "pnl") await loadPnl();
    if (deferAccountHeight) {
      if (isAssetsOpen() && accountView === view) runAccountHeightTween();
      else accountBox.style.height = ""; // switched away or closed mid-load
    }
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

  function assetAccountTypeLabel(accountType, exchange = "") {
    const account = String(accountType || "").toLowerCase();
    const exchangeId = String(exchange || "").toLowerCase();
    if (exchangeId === "okx" && account === "spot") return "Trading";
    if (
      exchangeId === "bybit"
      && ["spot", "swap", "margin"].includes(account)
    ) return "Unified";
    if (exchangeId === "hyperliquid" && account === "swap") return "Perps";
    if (exchangeId === "bitget" && account === "swap") return "USDT-M Futures";
    const labels = {
      spot: "Spot",
      swap: "USD\u24c8-M Futures",
      margin: "Cross Margin",
      funding: "Funding",
      earn: "Earn",
    };
    return labels[account]
      || String(accountType || "Unknown");
  }

  function supportsAssetTransfer(exchange, accountType) {
    const sources = assetTransferSources[String(exchange || "").toLowerCase()];
    return Boolean(sources && sources.has(String(accountType || "").toLowerCase()));
  }

  function isAssetTransferOpen() {
    return !el("asset-transfer-modal").classList.contains("hidden");
  }

  function assetTransferSelectNodes(name) {
    const prefix = `asset-transfer-${name}`;
    return {
      control: el(`${prefix}-control`),
      select: el(prefix),
      button: el(`${prefix}-button`),
      label: el(`${prefix}-label`),
      options: el(`${prefix}-options`),
    };
  }

  function closeAssetTransferDropdown(name = null) {
    ["asset", "account", "destination"].forEach((key) => {
      if (name && key === name) return;
      const nodes = assetTransferSelectNodes(key);
      if (!nodes.control) return;
      nodes.control.classList.remove("open");
      nodes.button.setAttribute("aria-expanded", "false");
      nodes.options.classList.add("hidden");
    });
  }

  function syncAssetTransferDropdown(name) {
    const nodes = assetTransferSelectNodes(name);
    if (!nodes.select || !nodes.options) return;
    const options = Array.from(nodes.select.options);
    const selected = options.find((option) => option.value === nodes.select.value)
      || options.find((option) => option.selected)
      || null;
    nodes.label.textContent = selected
      ? selected.textContent
      : name === "asset"
        ? "Select asset"
        : name === "account" ? "Select account" : "Select destination";
    nodes.button.title = selected ? selected.textContent : "";
    nodes.button.disabled = !options.some((option) => !option.disabled);
    nodes.options.replaceChildren();
    options.forEach((option) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "asset-transfer-select-option";
      item.dataset.value = option.value;
      item.textContent = option.textContent;
      item.disabled = option.disabled;
      item.setAttribute("role", "option");
      item.setAttribute(
        "aria-selected",
        String(option.value === nodes.select.value),
      );
      item.classList.toggle("selected", option.value === nodes.select.value);
      nodes.options.appendChild(item);
    });
  }

  function positionAssetTransferDropdown(name) {
    const nodes = assetTransferSelectNodes(name);
    if (!nodes.button || !nodes.options || nodes.options.classList.contains("hidden")) {
      return;
    }
    const rect = nodes.button.getBoundingClientRect();
    const viewport = window.visualViewport;
    const viewportTop = viewport ? viewport.offsetTop : 0;
    const viewportLeft = viewport ? viewport.offsetLeft : 0;
    const viewportHeight = viewport ? viewport.height : window.innerHeight;
    const viewportWidth = viewport ? viewport.width : window.innerWidth;
    const viewportBottom = viewportTop + viewportHeight;
    const viewportRight = viewportLeft + viewportWidth;
    const gap = 5;
    const edge = 8;
    const width = Math.min(rect.width, viewportWidth - edge * 2);
    const left = Math.min(
      Math.max(rect.left, viewportLeft + edge),
      viewportRight - width - edge,
    );
    const spaceBelow = Math.max(0, viewportBottom - rect.bottom - gap - edge);
    const spaceAbove = Math.max(0, rect.top - viewportTop - gap - edge);
    const desiredHeight = Math.min(nodes.options.scrollHeight, 220);
    const openBelow = spaceBelow >= Math.min(desiredHeight, 120)
      || spaceBelow >= spaceAbove;
    const availableHeight = Math.max(72, openBelow ? spaceBelow : spaceAbove);
    const menuHeight = Math.min(desiredHeight, availableHeight);
    const top = openBelow
      ? rect.bottom + gap
      : rect.top - gap - menuHeight;

    nodes.options.style.left = `${Math.round(left)}px`;
    nodes.options.style.top = `${Math.round(Math.max(viewportTop + edge, top))}px`;
    nodes.options.style.width = `${Math.round(width)}px`;
    nodes.options.style.maxHeight = `${Math.round(availableHeight)}px`;
  }

  function openAssetTransferDropdown(name, focusOption = false) {
    const nodes = assetTransferSelectNodes(name);
    if (!nodes.control || nodes.button.disabled) return;
    closeAssetTransferDropdown(name);
    syncAssetTransferDropdown(name);
    nodes.control.classList.add("open");
    nodes.button.setAttribute("aria-expanded", "true");
    nodes.options.classList.remove("hidden");
    positionAssetTransferDropdown(name);
    if (focusOption) {
      const target = nodes.options.querySelector(
        ".asset-transfer-select-option.selected:not(:disabled), "
          + ".asset-transfer-select-option:not(:disabled)",
      );
      if (target) target.focus();
    }
  }

  function toggleAssetTransferDropdown(name) {
    const nodes = assetTransferSelectNodes(name);
    if (!nodes.options) return;
    if (nodes.options.classList.contains("hidden")) {
      openAssetTransferDropdown(name);
    } else {
      closeAssetTransferDropdown();
    }
  }

  function selectAssetTransferDropdownValue(name, value) {
    const nodes = assetTransferSelectNodes(name);
    if (!nodes.select) return;
    nodes.select.value = String(value || "");
    nodes.select.dispatchEvent(new Event("change", { bubbles: true }));
    syncAssetTransferDropdown(name);
  }

  function initAssetTransferDropdown(name) {
    const nodes = assetTransferSelectNodes(name);
    nodes.button.addEventListener("click", (event) => {
      event.preventDefault();
      toggleAssetTransferDropdown(name);
    });
    nodes.button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        openAssetTransferDropdown(name, true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeAssetTransferDropdown();
      }
    });
    nodes.options.addEventListener("click", (event) => {
      const option = event.target && event.target.closest
        ? event.target.closest(".asset-transfer-select-option")
        : null;
      if (!option || option.disabled) return;
      selectAssetTransferDropdownValue(name, option.dataset.value);
      closeAssetTransferDropdown();
      nodes.button.focus();
    });
    nodes.options.addEventListener("keydown", (event) => {
      const options = Array.from(
        nodes.options.querySelectorAll(".asset-transfer-select-option:not(:disabled)"),
      );
      const index = options.indexOf(document.activeElement);
      let next = null;
      if (event.key === "ArrowDown") next = Math.min(index + 1, options.length - 1);
      else if (event.key === "ArrowUp") next = Math.max(index - 1, 0);
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = options.length - 1;
      else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeAssetTransferDropdown();
        nodes.button.focus();
        return;
      }
      if (next !== null && options[next]) {
        event.preventDefault();
        options[next].focus();
      }
    });
  }

  function closeAssetTransfer() {
    if (!isAssetTransferOpen() || assetTransferSubmitting) return;
    assetTransferRequestSeq += 1;
    assetTransferContext = null;
    assetTransferReview = null;
    assetTransferMode = "options";
    assetTransferSubmitting = false;
    closeAssetTransferDropdown();
    const modal = el("asset-transfer-modal");
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  }

  function setAssetTransferError(message) {
    const error = el("asset-transfer-error");
    error.textContent = String(message || "");
    error.classList.toggle("hidden", !message);
  }

  function setAssetTransferMode(mode) {
    assetTransferMode = mode;
    const loading = mode === "loading";
    const options = mode === "options";
    const review = mode === "review";
    const result = mode === "result";
    el("asset-transfer-loading").classList.toggle("hidden", !loading);
    el("asset-transfer-form").classList.toggle("hidden", !options);
    el("asset-transfer-review").classList.toggle("hidden", !review);
    el("asset-transfer-result").classList.toggle("hidden", !result);
    el("asset-transfer-back").textContent = review ? "Edit" : "Cancel";
    el("asset-transfer-back").classList.toggle("hidden", result);
    el("asset-transfer-submit").textContent = review
      ? "Confirm transfer"
      : result ? "Done" : "Review";
    if (!options) closeAssetTransferDropdown();
  }

  function selectedAssetTransferItem() {
    if (!assetTransferContext) return null;
    const key = el("asset-transfer-asset").value;
    return (Array.isArray(assetTransferContext.assets)
      ? assetTransferContext.assets
      : []).find((item) => item.key === key) || null;
  }

  function selectedAssetTransferAccount() {
    if (!assetTransferContext) return null;
    const key = el("asset-transfer-account").value;
    return (Array.isArray(assetTransferContext.target_accounts)
      ? assetTransferContext.target_accounts
      : []).find((account) => account.key === key) || null;
  }

  function renderAssetTransferDestinations() {
    const account = selectedAssetTransferAccount();
    const destinations = account && Array.isArray(account.destinations)
      ? account.destinations
      : [];
    const destinationSelect = el("asset-transfer-destination");
    destinationSelect.replaceChildren();
    destinations.forEach((destination) => {
      const option = document.createElement("option");
      option.value = destination;
      option.textContent = assetAccountTypeLabel(
        destination,
        assetTransferContext && assetTransferContext.exchange,
      );
      destinationSelect.appendChild(option);
    });
    if (!destinations.length) {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "No supported destination";
      empty.disabled = true;
      empty.selected = true;
      destinationSelect.appendChild(empty);
    }
    syncAssetTransferDropdown("destination");
  }

  function updateAssetTransferForm() {
    if (!assetTransferContext || assetTransferMode !== "options") return;
    const item = selectedAssetTransferItem();
    const targetAccount = selectedAssetTransferAccount();
    const destination = el("asset-transfer-destination").value;
    const amount = Number(el("asset-transfer-amount").value);
    const available = item ? Number(item.available) : 0;
    const valid = Boolean(
      item
      && item.transferable
      && targetAccount
      && destination
      && Number.isFinite(amount)
      && amount > 0
      && amount <= available,
    );
    el("asset-transfer-submit").disabled = !valid;
    el("asset-transfer-max").disabled = !item || !item.transferable;
    el("asset-transfer-amount").disabled = !item || !item.transferable;
    el("asset-transfer-available").textContent = item
      ? `Available ${formatAssetAmount(item.available)} ${item.asset}`
      : "No transferable balance";

    const notes = [];
    if (item && item.note) notes.push(String(item.note));
    if (
      assetTransferContext.source === "earn"
      && item
      && item.source_kind === "flexible"
      && destination !== "spot"
    ) {
      notes.push(
        `This runs in two steps: redeem to Spot, then transfer to ${
          assetAccountTypeLabel(destination, assetTransferContext.exchange)
        }.`,
      );
    }
    const note = el("asset-transfer-note");
    note.textContent = notes.join(" ");
    note.classList.toggle("hidden", notes.length === 0);
  }

  function renderAssetTransferOptions(context) {
    assetTransferContext = context;
    const assetSelect = el("asset-transfer-asset");
    assetSelect.replaceChildren();
    const items = Array.isArray(context.assets) ? context.assets : [];
    items.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.key;
      const kind = item.source_kind === "flexible"
        ? "Flexible"
        : item.source_kind === "locked"
          ? "Locked"
          : item.source_kind === "fixed" ? "Fixed" : "";
      option.textContent = [
        item.asset,
        kind,
        formatAssetAmount(item.available),
      ].filter(Boolean).join(" \u00b7 ");
      option.disabled = !item.transferable;
      assetSelect.appendChild(option);
    });
    const firstTransferable = items.find((item) => item.transferable);
    if (firstTransferable) {
      assetSelect.value = firstTransferable.key;
    } else {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = items.length
        ? "No transferable balance"
        : "No assets available";
      empty.disabled = true;
      empty.selected = true;
      assetSelect.prepend(empty);
    }

    const accountSelect = el("asset-transfer-account");
    accountSelect.replaceChildren();
    const targetAccounts = Array.isArray(context.target_accounts)
      ? context.target_accounts
      : [{
        key: context.account,
        label: context.account,
        role: "standalone",
        destinations: context.destinations || [],
      }];
    targetAccounts.forEach((account) => {
      const option = document.createElement("option");
      option.value = account.key;
      option.textContent = account.role === "main"
        ? `${account.label} \u00b7 Main`
        : account.role === "sub"
          ? `${account.label} \u00b7 Sub`
          : account.label;
      accountSelect.appendChild(option);
    });
    syncAssetTransferDropdown("asset");
    syncAssetTransferDropdown("account");
    renderAssetTransferDestinations();
    el("asset-transfer-amount").value = "";
    el("asset-transfer-amount").placeholder = "0";
    setAssetTransferMode("options");
    setAssetTransferError("");
    updateAssetTransferForm();
  }

  async function openAssetTransfer(portfolio, source) {
    const exchangeId = String(portfolio.exchange || "").toLowerCase();
    if (!supportsAssetTransfer(exchangeId, source)) return;
    const requestId = ++assetTransferRequestSeq;
    assetTransferContext = null;
    assetTransferReview = null;
    assetTransferSubmitting = false;
    setAssetTransferError("");
    el("asset-transfer-review").replaceChildren();
    el("asset-transfer-result").replaceChildren();
    el("asset-transfer-title").textContent =
      `${assetExchangeLabel(exchangeId)} transfer`;
    el("asset-transfer-subtitle").textContent =
      `${portfolio.account} \u00b7 ${assetAccountTypeLabel(source, exchangeId)}`;
    el("asset-transfer-submit").disabled = true;
    setAssetTransferMode("loading");
    const modal = el("asset-transfer-modal");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    try {
      const query = new URLSearchParams({
        exchange: exchangeId,
        account: String(portfolio.account || ""),
        source: String(source || ""),
      });
      const context = await api(`/api/assets/transfer/options?${query}`);
      if (requestId !== assetTransferRequestSeq || !isAssetTransferOpen()) return;
      renderAssetTransferOptions(context);
    } catch (error) {
      if (requestId !== assetTransferRequestSeq || !isAssetTransferOpen()) return;
      setAssetTransferMode("options");
      el("asset-transfer-form").classList.add("hidden");
      setAssetTransferError(error.message || String(error));
    }
  }

  function appendAssetTransferReviewRow(container, label, value) {
    const row = document.createElement("div");
    row.className = "asset-transfer-review-row";
    const name = document.createElement("span");
    name.className = "asset-transfer-review-label";
    name.textContent = label;
    const detail = document.createElement("span");
    detail.className = "asset-transfer-review-value";
    detail.textContent = value;
    row.append(name, detail);
    container.appendChild(row);
  }

  function reviewAssetTransfer() {
    const item = selectedAssetTransferItem();
    const targetAccount = selectedAssetTransferAccount();
    const amount = el("asset-transfer-amount").value.trim();
    const amountNumber = Number(amount);
    const available = item ? Number(item.available) : 0;
    const destination = el("asset-transfer-destination").value;
    if (
      !assetTransferContext
      || !item
      || !item.transferable
      || !targetAccount
      || !destination
      || !Number.isFinite(amountNumber)
      || amountNumber <= 0
      || amountNumber > available
    ) {
      setAssetTransferError("Enter an amount within the available balance.");
      return;
    }
    assetTransferReview = {
      exchange: assetTransferContext.exchange,
      account: assetTransferContext.account,
      target_account: targetAccount.key,
      target_account_label: targetAccount.label,
      source: assetTransferContext.source,
      destination,
      asset: item.asset,
      amount,
      product_id: item.product_id || null,
      period_type: item.period_type || null,
      order_id: item.order_id || null,
    };
    const review = el("asset-transfer-review");
    review.replaceChildren();
    appendAssetTransferReviewRow(
      review,
      "From",
      `${assetTransferReview.account} \u00b7 ${assetAccountTypeLabel(
        assetTransferReview.source,
        assetTransferReview.exchange,
      )}`,
    );
    appendAssetTransferReviewRow(
      review,
      "To",
      `${assetTransferReview.target_account_label} \u00b7 ${assetAccountTypeLabel(
        assetTransferReview.destination,
        assetTransferReview.exchange,
      )}`,
    );
    appendAssetTransferReviewRow(
      review,
      "Amount",
      `${assetTransferReview.amount} ${assetTransferReview.asset}`,
    );
    const warning = document.createElement("p");
    warning.className = "asset-transfer-warning";
    warning.textContent = assetTransferReview.source === "earn"
      && assetTransferReview.destination !== "spot"
      ? "Earn redemption and the following wallet transfer are separate operations. "
        + "If the second step fails, redeemed funds remain in Spot."
      : `This moves real funds between the selected ${
        assetExchangeLabel(assetTransferReview.exchange)
      } wallets or accounts and cannot be undone here.`;
    review.appendChild(warning);
    setAssetTransferError("");
    setAssetTransferMode("review");
    el("asset-transfer-submit").disabled = false;
  }

  function renderAssetTransferResult(data) {
    const result = el("asset-transfer-result");
    result.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = data.status === "pending"
      ? "Transfer submitted"
      : "Transfer completed";
    const summary = document.createElement("span");
    summary.textContent =
      `${data.amount} ${data.asset} \u00b7 `
      + `${data.account} ${assetAccountTypeLabel(data.source, data.exchange)} \u2192 `
      + `${data.target_account_label || data.target_account || data.account} ${
        assetAccountTypeLabel(data.destination, data.exchange)
      }`;
    result.append(title, summary);
    (Array.isArray(data.steps) ? data.steps : []).forEach((step) => {
      const line = document.createElement("span");
      line.className = "asset-transfer-result-step";
      line.textContent = step.action === "redeem"
        ? `Earn redeemed to Spot${step.redeem_id ? ` \u00b7 ID ${step.redeem_id}` : ""}`
        : `${step.source_account ? `${step.source_account} \u00b7 ` : ""}`
          + `${assetAccountTypeLabel(step.source, data.exchange)} \u2192 `
          + `${step.target_account ? `${step.target_account} \u00b7 ` : ""}`
          + `${assetAccountTypeLabel(step.destination, data.exchange)}`
          + (step.status === "pending" ? " \u00b7 Pending" : "")
          + (step.transaction_id ? ` \u00b7 ID ${step.transaction_id}` : "");
      result.appendChild(line);
    });
    setAssetTransferMode("result");
    el("asset-transfer-submit").disabled = false;
  }

  async function executeAssetTransfer() {
    if (!assetTransferReview || assetTransferSubmitting) return;
    assetTransferSubmitting = true;
    el("asset-transfer-submit").disabled = true;
    el("asset-transfer-back").disabled = true;
    el("asset-transfer-close").disabled = true;
    el("asset-transfer-submit").textContent = "Transferring\u2026";
    setAssetTransferError("");
    let response;
    let data;
    try {
      response = await fetch("/api/assets/transfer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...assetTransferReview,
          confirm: "TRANSFER",
        }),
      });
      data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(data.error || `HTTP ${response.status}`);
        error.payload = data;
        throw error;
      }
      renderAssetTransferResult(data);
      loadAssets(true).catch(() => {});
    } catch (error) {
      const payload = error && error.payload ? error.payload : {};
      setAssetTransferError(error.message || String(error));
      if (payload.status === "partial" || payload.status === "unknown") {
        setAssetTransferMode("result");
        const result = el("asset-transfer-result");
        result.replaceChildren();
        const title = document.createElement("strong");
        title.textContent = payload.status === "partial"
          ? "Transfer partially completed"
          : "Transfer status unknown";
        result.appendChild(title);
        loadAssets(true).catch(() => {});
      } else {
        setAssetTransferMode("review");
      }
      el("asset-transfer-submit").disabled = false;
    } finally {
      assetTransferSubmitting = false;
      el("asset-transfer-back").disabled = false;
      el("asset-transfer-close").disabled = false;
      if (assetTransferMode === "review") {
        el("asset-transfer-submit").textContent = "Confirm transfer";
      } else if (assetTransferMode === "result") {
        el("asset-transfer-submit").textContent = "Done";
      }
    }
  }

  function isAssetTransferHistoryOpen() {
    return !el("asset-transfer-history-modal").classList.contains("hidden");
  }

  function closeAssetTransferHistory() {
    if (!isAssetTransferHistoryOpen()) return;
    assetTransferHistoryRequestSeq += 1;
    assetTransferHistoryPortfolio = null;
    assetTransferHistoryRows = [];
    assetTransferHistoryCursor = null;
    assetTransferHistoryLoading = false;
    const modal = el("asset-transfer-history-modal");
    const box = modal.querySelector(".asset-transfer-history-box");
    modal.classList.remove("asset-transfer-history-dragging");
    modal.classList.add("hidden");
    box.style.transform = "";
    box.style.transition = "";
    modal.setAttribute("aria-hidden", "true");
    el("asset-transfer-history-refresh").classList.remove("assets-refreshing");
  }

  function transferHistoryRoute(record, exchangeId) {
    const sourceAccount = String(record.from_account || record.account || "");
    const targetAccount = String(record.to_account || record.account || "");
    const sourceAccountLabel = transferHistoryAccountLabel(
      sourceAccount,
      record.from_account_label,
    );
    const targetAccountLabel = transferHistoryAccountLabel(
      targetAccount,
      record.to_account_label,
    );
    const sourceType = assetAccountTypeLabel(record.from_account_type, exchangeId);
    const targetType = assetAccountTypeLabel(record.to_account_type, exchangeId);
    const accountTransfer = String(record.transfer_kind || "wallet") === "account";
    const crossAccount = accountTransfer
      || (sourceAccount && targetAccount && sourceAccount !== targetAccount);
    if (crossAccount) {
      return `Account transfer · ${sourceAccountLabel} · ${sourceType} → ${targetAccountLabel} · ${targetType}`;
    }
    return `${sourceType} → ${targetType}`;
  }

  function transferHistoryAccountLabel(value, label) {
    const explicit = String(label || "").trim();
    if (explicit) return explicit;
    const raw = String(value || "").trim();
    if (!raw) return "Account";
    const normalized = raw.toLowerCase();
    if (normalized === "main_account") return "Main account";
    if (normalized === "sub_account") return "Sub account";
    return raw;
  }

  function renderAssetTransferHistory(payload, append = false) {
    const incoming = Array.isArray(payload.results) ? payload.results : [];
    if (append) {
      const byId = new Map(assetTransferHistoryRows.map((row) => [String(row.id), row]));
      incoming.forEach((row) => byId.set(String(row.id), row));
      assetTransferHistoryRows = Array.from(byId.values());
    } else {
      assetTransferHistoryRows = incoming;
    }
    assetTransferHistoryCursor = payload.next_cursor || null;
    const list = el("asset-transfer-history-list");
    list.replaceChildren();
    const exchangeId = String(payload.exchange || "");
    assetTransferHistoryRows.forEach((record) => {
      const row = document.createElement("article");
      row.className = "asset-transfer-history-row";

      const top = document.createElement("div");
      top.className = "asset-transfer-history-row-top";
      const amount = document.createElement("strong");
      amount.className = "asset-transfer-history-amount mono";
      const direction = String(record.direction || "internal");
      const prefix = direction === "out" ? "-" : direction === "in" ? "+" : "";
      amount.textContent = `${prefix}${formatAssetAmount(record.amount)} ${record.currency || ""}`;
      const status = document.createElement("span");
      const statusText = String(record.status || "unknown");
      const statusClass = statusText.toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
      status.className = `asset-transfer-history-status status-${statusClass}`;
      status.textContent = statusText;
      top.append(amount, status);

      const route = document.createElement("div");
      route.className = "asset-transfer-history-route";
      route.textContent = transferHistoryRoute(record, exchangeId);

      const meta = document.createElement("div");
      meta.className = "asset-transfer-history-meta";
      const occurred = document.createElement("time");
      occurred.textContent = formatAccountHistoryDate(record.datetime, true, true);
      const identifier = document.createElement("span");
      identifier.className = "mono";
      const rawId = String(record.id || "");
      identifier.textContent = rawId ? `ID ${rawId.length > 18 ? `${rawId.slice(0, 8)}…${rawId.slice(-6)}` : rawId}` : "";
      identifier.title = rawId;
      meta.append(occurred, identifier);
      row.append(top, route, meta);
      list.appendChild(row);
    });

    const sync = payload.sync && typeof payload.sync === "object" ? payload.sync : {};
    const warnings = Array.isArray(sync.warnings) ? [...sync.warnings] : [];
    if (sync.last_error) warnings.push(String(sync.last_error));
    const notice = el("asset-transfer-history-notice");
    notice.textContent = warnings.join(" ");
    notice.classList.toggle("hidden", warnings.length === 0);
    list.classList.toggle("hidden", assetTransferHistoryRows.length === 0);
    el("asset-transfer-history-empty").classList.toggle(
      "hidden",
      assetTransferHistoryRows.length !== 0,
    );
    el("asset-transfer-history-more").classList.toggle(
      "hidden",
      !assetTransferHistoryCursor,
    );
  }

  async function loadAssetTransferHistory({ append = false, force = false } = {}) {
    if (!assetTransferHistoryPortfolio || assetTransferHistoryLoading) return;
    const requestId = ++assetTransferHistoryRequestSeq;
    const firstLoad = !assetTransferHistoryRows.length && !append;
    assetTransferHistoryLoading = true;
    el("asset-transfer-history-loading").classList.toggle("hidden", !firstLoad);
    el("asset-transfer-history-error").classList.add("hidden");
    el("asset-transfer-history-refresh").classList.add("assets-refreshing");
    el("asset-transfer-history-more").disabled = true;
    const portfolio = assetTransferHistoryPortfolio;
    const assets = (Array.isArray(portfolio.assets) ? portfolio.assets : [])
      .map((item) => String(item.currency || "").toUpperCase())
      .filter(Boolean);
    const accountTypes = (Array.isArray(portfolio.account_type_statuses)
      ? portfolio.account_type_statuses
      : [])
      .filter((item) => item.status === "ok")
      .map((item) => String(item.account_type || "").toLowerCase())
      .filter(Boolean);
    const query = new URLSearchParams({
      exchange: String(portfolio.exchange || ""),
      account: String(portfolio.account || ""),
      limit: "50",
      force: force ? "true" : "false",
      assets: Array.from(new Set(assets)).join(","),
      account_types: Array.from(new Set(accountTypes)).join(","),
    });
    if (append && assetTransferHistoryCursor) {
      query.set("cursor", assetTransferHistoryCursor);
    }
    try {
      const payload = await api(`/api/assets/transfer/history?${query}`);
      if (requestId !== assetTransferHistoryRequestSeq || !isAssetTransferHistoryOpen()) return;
      renderAssetTransferHistory(payload, append);
    } catch (error) {
      if (requestId !== assetTransferHistoryRequestSeq || !isAssetTransferHistoryOpen()) return;
      const errorElement = el("asset-transfer-history-error");
      errorElement.textContent = error.message || String(error);
      errorElement.classList.remove("hidden");
      if (!assetTransferHistoryRows.length) {
        el("asset-transfer-history-empty").classList.add("hidden");
      }
    } finally {
      if (requestId === assetTransferHistoryRequestSeq) {
        assetTransferHistoryLoading = false;
        el("asset-transfer-history-loading").classList.add("hidden");
        el("asset-transfer-history-refresh").classList.remove("assets-refreshing");
        el("asset-transfer-history-more").disabled = false;
      }
    }
  }

  function openAssetTransferHistory(portfolio) {
    assetTransferHistoryPortfolio = portfolio;
    assetTransferHistoryRows = [];
    assetTransferHistoryCursor = null;
    assetTransferHistoryLoading = false;
    el("asset-transfer-history-title").textContent = "Transfer History";
    el("asset-transfer-history-subtitle").textContent =
      `${portfolio.account} · ${assetExchangeLabel(portfolio.exchange)}`;
    el("asset-transfer-history-list").replaceChildren();
    el("asset-transfer-history-list").classList.add("hidden");
    el("asset-transfer-history-empty").classList.add("hidden");
    el("asset-transfer-history-notice").classList.add("hidden");
    el("asset-transfer-history-error").classList.add("hidden");
    el("asset-transfer-history-loading").classList.remove("hidden");
    const modal = el("asset-transfer-history-modal");
    const box = modal.querySelector(".asset-transfer-history-box");
    modal.classList.remove("asset-transfer-history-dragging", "hidden");
    box.style.transform = "";
    box.style.transition = "";
    modal.setAttribute("aria-hidden", "false");
    loadAssetTransferHistory();
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

  function assetPortfolioDonutKey(portfolio) {
    return [portfolio.exchange, portfolio.account, portfolio.quote_currency]
      .map((value) => String(value || ""))
      .join("\u0000");
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
        currency: assetAccountTypeLabel(accountType, portfolio.exchange),
        value: Number(item.total_value || 0),
        accountType,
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
    const donutKey = assetPortfolioDonutKey(portfolio);
    let showAccountTypes = assetAccountTypeDonuts.has(donutKey);

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
        const canTransfer = Boolean(
          showAccountTypes
          && slice.accountType
          && Number(slice.value) > 0
          && supportsAssetTransfer(portfolio.exchange, slice.accountType),
        );
        const row = document.createElement(canTransfer ? "button" : "div");
        row.className = "assets-legend-row";
        if (showAccountTypes) {
          row.classList.add("assets-legend-account-row");
        }
        if (canTransfer) {
          row.type = "button";
          row.classList.add("assets-legend-action");
          row.setAttribute(
            "aria-label",
            `Transfer from ${
              assetAccountTypeLabel(slice.accountType, portfolio.exchange)
            } in ${portfolio.account}`,
          );
          row.addEventListener("click", (event) => {
            event.stopPropagation();
            openAssetTransfer(portfolio, slice.accountType);
          });
        }
        const dot = document.createElement("span");
        dot.className = "assets-legend-dot";
        dot.style.setProperty("--asset-color", color);
        const currency = document.createElement("span");
        currency.className = "assets-legend-currency";
        currency.textContent = slice.currency;
        const weight = document.createElement("span");
        weight.className = "assets-legend-weight";
        weight.textContent = `${ratio.toFixed(ratio < 0.1 ? 2 : 1)}%`;
        const chevron = document.createElement("span");
        chevron.className = "assets-legend-chevron";
        chevron.classList.toggle("assets-legend-chevron-placeholder", !canTransfer);
        chevron.textContent = "\u203a";
        chevron.setAttribute("aria-hidden", "true");
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
        if (showAccountTypes) {
          row.append(dot, currency, weight, chevron, detail);
        } else {
          row.append(dot, currency, weight, detail);
        }
        legend.appendChild(row);
      });
    }
    donut.addEventListener("click", () => {
      showAccountTypes = !showAccountTypes;
      if (showAccountTypes) assetAccountTypeDonuts.add(donutKey);
      else assetAccountTypeDonuts.delete(donutKey);
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
    const primaryFooter = document.createElement("div");
    primaryFooter.className = "assets-portfolio-footer-primary";
    const historyLink = document.createElement("button");
    historyLink.type = "button";
    historyLink.className = "assets-transfer-history-link";
    historyLink.textContent = "Transfer History";
    historyLink.setAttribute(
      "aria-label",
      `Show transfer history for ${portfolio.account}`,
    );
    historyLink.addEventListener("click", (event) => {
      event.stopPropagation();
      openAssetTransferHistory(portfolio);
    });
    primaryFooter.append(statusText, historyLink);
    footer.appendChild(primaryFooter);

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
        })}`
      : "";
    renderAssetsView();
    assetsHaveData = true;
  }

  function setAssetsLoading(loading, preserveContent = false) {
    if (accountView === "assets") {
      el("assets-refresh").disabled = loading;
      el("assets-refresh").classList.toggle("assets-refreshing", loading);
    }
    el("assets-loading").classList.toggle("hidden", !loading || preserveContent);
    if (loading) el("assets-error").classList.add("hidden");
    if (loading && !preserveContent) {
      assetsPayload = null;
      el("assets-summary").classList.add("hidden");
      el("assets-empty").classList.add("hidden");
      el("assets-portfolios").replaceChildren();
    }
  }

  async function loadAssets(force, autoRefresh = false) {
    clearAssetsRefreshTimer();
    const seq = ++assetsRequestSeq;
    const preserveContent = assetsHaveData;
    setAssetsLoading(true, preserveContent);
    try {
      const query = force
        ? `?refresh=true${autoRefresh ? "&auto_refresh=true" : ""}`
        : "";
      const payload = await api(`/api/account/assets${query}`);
      if (seq !== assetsRequestSeq || !isAssetsOpen() || accountView !== "assets") return;
      renderAssets(payload);
    } catch (error) {
      if (seq !== assetsRequestSeq || !isAssetsOpen() || accountView !== "assets") return;
      const errorElement = el("assets-error");
      errorElement.textContent = `${error.message}\nUse refresh to try again.`;
      errorElement.classList.remove("hidden");
      if (!preserveContent) {
        el("assets-summary").classList.add("hidden");
        el("assets-portfolios").replaceChildren();
      }
    } finally {
      if (seq === assetsRequestSeq) {
        setAssetsLoading(false);
        scheduleAssetsRefresh();
      }
    }
  }

  function formatPositionNumber(value, maximumFractionDigits = 6) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") {
      return "—";
    }
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return number.toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits,
    });
  }

  function positionTableCell(text, className = "", label = "") {
    const cell = document.createElement("td");
    cell.textContent = text;
    if (className) cell.className = className;
    if (label) cell.dataset.label = label;
    cell.title = text;
    return cell;
  }

  function formatSignedPositionNumber(value, maximumFractionDigits = 4) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") {
      return "—";
    }
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return `${number > 0 ? "+" : ""}${formatPositionNumber(number, maximumFractionDigits)}`;
  }

  function positionPnlClass(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "";
    return number > 0
      ? "account-pnl-positive"
      : number < 0 ? "account-pnl-negative" : "";
  }

  function updatePositionRealizedPnlCell(cell, position) {
    const value = position.realized_pnl === null || position.realized_pnl === undefined
      ? Number.NaN
      : Number(position.realized_pnl);
    if (!Number.isFinite(value)) {
      if (activePositionPnlButton && cell.contains(activePositionPnlButton)) {
        closePositionPnlPopover();
      }
      setClass(cell, "");
      setText(cell, "—");
      cell.title = "—";
      return;
    }
    setClass(cell, positionPnlClass(value));
    let button = cell.querySelector(".position-pnl-value");
    if (!button) {
      button = document.createElement("button");
      button.className = "position-pnl-value";
      button.type = "button";
      button.setAttribute("aria-label", "Show realized PnL calculation");
      button.setAttribute("aria-haspopup", "dialog");
      button.setAttribute("aria-expanded", "false");
      cell.replaceChildren(button);
    }
    setText(button, formatSignedPositionNumber(value));
    const breakdown = position.realized_pnl_breakdown;
    button.dataset.positionPnl = JSON.stringify(
      breakdown && typeof breakdown === "object"
        ? breakdown
        : {
          gross_pnl: null,
          fees: null,
          funding: null,
          net_pnl: value,
          complete: false,
        },
    );
    cell.removeAttribute("title");
  }

  let activePositionPnlButton = null;
  let positionPnlPress = null;
  let suppressPositionPnlClickUntil = 0;

  function closePositionPnlPopover() {
    if (activePositionPnlButton) {
      activePositionPnlButton.setAttribute("aria-expanded", "false");
      activePositionPnlButton = null;
    }
    const popover = el("position-pnl-popover");
    if (popover) popover.classList.add("hidden");
  }

  function showPositionPnlPopover(button) {
    if (activePositionPnlButton === button) {
      closePositionPnlPopover();
      return;
    }
    let breakdown;
    try {
      breakdown = JSON.parse(button.dataset.positionPnl || "{}");
    } catch {
      breakdown = {};
    }
    closePositionPnlPopover();
    activePositionPnlButton = button;
    button.setAttribute("aria-expanded", "true");

    const gross = Number(breakdown.gross_pnl);
    const fees = Number(breakdown.fees);
    const fundingValue = breakdown.funding;
    const funding = fundingValue === null || fundingValue === undefined
      ? Number.NaN
      : Number(fundingValue);
    const net = Number(breakdown.net_pnl);
    const complete = breakdown.complete === true
      && Number.isFinite(gross)
      && Number.isFinite(fees)
      && Number.isFinite(net);
    const feeAdjustment = complete ? -fees : Number.NaN;
    setText(el("position-pnl-gross"), complete ? formatSignedPositionNumber(gross) : "Unavailable");
    setText(
      el("position-pnl-fees"),
      complete ? formatSignedPositionNumber(feeAdjustment) : "Unavailable",
    );
    setClass(
      el("position-pnl-fees"),
      `mono ${positionPnlClass(feeAdjustment)}`.trim(),
    );
    setText(
      el("position-pnl-funding"),
      Number.isFinite(funding) ? formatSignedPositionNumber(funding) : "Unavailable",
    );
    setClass(
      el("position-pnl-funding"),
      `mono ${positionPnlClass(funding)}`.trim(),
    );
    setText(el("position-pnl-net"), formatSignedPositionNumber(net));
    setClass(el("position-pnl-net"), `mono ${positionPnlClass(net)}`.trim());
    const fundingAvailable = Number.isFinite(funding);
    const estimatedFunding = breakdown.funding_allocation === "estimated";
    el("position-pnl-note").classList.toggle(
      "hidden",
      complete && fundingAvailable && !estimatedFunding,
    );
    if (!complete) {
      setText(
        el("position-pnl-note"),
        "The exchange reports net realized PnL without a fee breakdown.",
      );
    } else if (!fundingAvailable) {
      setText(el("position-pnl-note"), "Funding data is unavailable for this position.");
    } else if (estimatedFunding) {
      setText(
        el("position-pnl-note"),
        "Daily funding is allocated by position size and holding time.",
      );
    }

    const popover = el("position-pnl-popover");
    popover.classList.remove("hidden");
    const buttonRect = button.getBoundingClientRect();
    const popoverRect = popover.getBoundingClientRect();
    const left = Math.min(
      Math.max(12, buttonRect.right - popoverRect.width),
      window.innerWidth - popoverRect.width - 12,
    );
    let top = buttonRect.bottom + 8;
    if (top + popoverRect.height > window.innerHeight - 12) {
      top = Math.max(12, buttonRect.top - popoverRect.height - 8);
    }
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
  }

  function positionRowKey(position) {
    return JSON.stringify([
      position.account || "",
      position.exchange || "",
      position.market_scope || "",
      position.dex || "",
      position.symbol || "",
      position.side || "",
    ]);
  }

  function createPositionRow() {
    const row = document.createElement("tr");
    const fields = [
      ["account", "Account", "account-table-account"],
      ["symbol", "Symbol", "account-table-symbol"],
      ["side", "Side", ""],
      ["size", "Size", ""],
      ["entry", "Entry", ""],
      ["mark", "Mark", ""],
      ["unrealized", "Unrealized PnL", ""],
      ["realized", "Realized PnL", ""],
      ["return", "Return", ""],
      ["leverage", "Leverage", ""],
      ["liquidation", "Liquidation", ""],
    ];
    const cells = {};
    fields.forEach(([field, label, className]) => {
      const cell = positionTableCell("", className, label);
      cell.dataset.positionField = field;
      cells[field] = cell;
      row.appendChild(cell);
    });
    row.positionCells = cells;
    return row;
  }

  function updatePositionCell(cell, text, className = "") {
    setClass(cell, className);
    setText(cell, text);
    cell.title = text;
  }

  function updatePositionRow(row, position) {
    const cells = row.positionCells;
    const accountName = String(position.account || "—");
    const exchangeName = String(position.exchange || "").toUpperCase();
    const accountIdentity = JSON.stringify([
      accountName,
      exchangeName,
      position.exchange_logo_url || "",
    ]);
    if (cells.account.dataset.identity !== accountIdentity) {
      const exchangeLogo = logoImg(
        position.exchange_logo_url,
        exchangeName,
        "exchange-logo",
      );
      setHTML(
        cells.account,
        `${exchangeLogo}<span class="account-table-account-name">${esc(accountName)}</span>`,
      );
      cells.account.dataset.identity = accountIdentity;
    }
    cells.account.title = accountName;

    const symbolText = String(position.symbol || "—");
    const scopeText = String(position.market_scope || position.dex || "");
    const symbolIdentity = JSON.stringify([symbolText, scopeText]);
    if (cells.symbol.dataset.identity !== symbolIdentity) {
      cells.symbol.replaceChildren(document.createTextNode(symbolText));
      if (scopeText) {
        const scope = document.createElement("small");
        scope.textContent = scopeText;
        cells.symbol.appendChild(scope);
      }
      cells.symbol.dataset.identity = symbolIdentity;
    }
    cells.symbol.title = symbolText;

    const sideText = String(position.side || "—");
    const sideClass = sideText.toLowerCase() === "long"
      ? "account-position-long"
      : sideText.toLowerCase() === "short" ? "account-position-short" : "";
    const pnl = position.unrealized_pnl === null || position.unrealized_pnl === undefined
      ? Number.NaN
      : Number(position.unrealized_pnl);
    const pnlClass = positionPnlClass(pnl);
    const percentage = position.percentage === null || position.percentage === undefined
      ? Number.NaN
      : Number(position.percentage);
    const percentageText = Number.isFinite(percentage)
      ? `${percentage > 0 ? "+" : ""}${formatPositionNumber(percentage, 2)}%`
      : "—";

    updatePositionCell(cells.side, sideText, sideClass);
    updatePositionCell(cells.size, formatPositionNumber(position.quantity ?? position.contracts));
    updatePositionCell(cells.entry, formatPositionNumber(position.entry_price));
    updatePositionCell(cells.mark, formatPositionNumber(position.mark_price));
    updatePositionCell(
      cells.unrealized,
      Number.isFinite(pnl)
        ? `${pnl > 0 ? "+" : ""}${formatPositionNumber(pnl, 4)}`
        : "—",
      pnlClass,
    );
    updatePositionRealizedPnlCell(cells.realized, position);
    updatePositionCell(cells.return, percentageText, pnlClass);
    updatePositionCell(
      cells.leverage,
      position.leverage !== null
        && position.leverage !== undefined
        && Number.isFinite(Number(position.leverage))
        ? `${formatPositionNumber(position.leverage, 2)}x`
        : "—",
    );
    updatePositionCell(cells.liquidation, formatPositionNumber(position.liquidation_price));
  }

  function renderPositions(payload) {
    const observedAt = Date.parse(payload.collected_at || "");
    if (Number.isFinite(observedAt) && observedAt < positionsObservedAt) return;
    if (Number.isFinite(observedAt)) positionsObservedAt = observedAt;
    positionsPayload = payload;
    positionsHaveData = true;
    const results = Array.isArray(payload.results) ? payload.results : [];
    const positions = [];
    const errors = [];
    results.forEach((result) => {
      if (result && result.status === "ok") {
        (Array.isArray(result.positions) ? result.positions : []).forEach((position) => {
          positions.push({
            ...position,
            account: result.account || "",
            exchange: result.exchange || "",
            exchange_logo_url: result.exchange_logo_url || "",
          });
        });
        return;
      }
      const error = result && result.error && result.error.message;
      errors.push(
        `${result && result.account || "Unknown account"}: ${error || "Position data unavailable"}`,
      );
    });

    const summary = payload.summary || {};
    el("positions-open-count").textContent = String(positions.length);
    el("positions-account-count").textContent =
      `${Number(summary.succeeded || 0)} of ${Number(summary.accounts || 0)} accounts`;
    el("positions-updated").textContent = Number.isFinite(observedAt)
      ? `Updated ${new Date(observedAt).toLocaleString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })}${payload.live ? " · live" : payload.cached ? " · cached" : ""}`
      : "";
    el("positions-summary").classList.remove("hidden");
    el("positions-empty").textContent = Number(summary.accounts || 0) === 0
      ? "No exchange accounts are configured in providers.toml."
      : "No open positions.";
    el("positions-empty").classList.toggle("hidden", positions.length !== 0);
    el("positions-list").classList.toggle("hidden", positions.length === 0);

    const list = el("positions-list");
    const existingRows = new Map(
      Array.from(list.children).map((row) => [row.dataset.positionKey || "", row]),
    );
    positions.forEach((position, index) => {
      const key = positionRowKey(position);
      let row = existingRows.get(key);
      if (!row) row = createPositionListRow();
      row.dataset.positionKey = key;
      updatePositionListRow(row, position);
      const currentRow = list.children[index];
      if (currentRow !== row) list.insertBefore(row, currentRow || null);
      existingRows.delete(key);
    });
    existingRows.forEach((row) => row.remove());

    const errorContainer = el("positions-account-errors");
    errorContainer.replaceChildren();
    errors.forEach((message) => {
      const item = document.createElement("div");
      item.textContent = message;
      errorContainer.appendChild(item);
    });
    errorContainer.classList.toggle("hidden", errors.length === 0);
  }

  function createPositionListRow() {
    const row = document.createElement("article");
    row.className = "account-history-record account-position-live-record";
    const header = document.createElement("div");
    header.className = "account-history-record-header";
    const identity = document.createElement("div");
    identity.className = "account-history-record-identity";
    const logo = document.createElement("span");
    logo.className = "account-position-live-logo";
    const copy = document.createElement("div");
    copy.className = "account-history-record-copy";
    const symbolLine = document.createElement("div");
    symbolLine.className = "account-history-record-symbol";
    const symbol = document.createElement("strong");
    const side = document.createElement("span");
    symbolLine.append(symbol, side);
    const account = document.createElement("small");
    copy.append(symbolLine, account);
    identity.append(logo, copy);
    const trailing = document.createElement("div");
    trailing.className = "account-history-record-trailing";
    const trailingLabel = document.createElement("span");
    trailingLabel.textContent = "Unrealized PnL";
    const trailingValue = document.createElement("strong");
    trailing.append(trailingLabel, trailingValue);
    header.append(identity, trailing);
    const metrics = document.createElement("div");
    metrics.className = "account-history-record-metrics";
    const makeMetric = (label) => {
      const metric = document.createElement("div");
      metric.className = "account-history-record-metric";
      const labelNode = document.createElement("span");
      labelNode.textContent = label;
      const valueNode = document.createElement("strong");
      metric.append(labelNode, valueNode);
      metrics.appendChild(metric);
      return valueNode;
    };
    row.positionListNodes = {
      logo,
      symbol,
      side,
      account,
      trailingValue,
      size: makeMetric("Size"),
      entry: makeMetric("Entry Price"),
      mark: makeMetric("Mark Price"),
      ret: makeMetric("Return"),
      leverage: makeMetric("Leverage"),
      realized: makeMetric("Realized PnL"),
      liquidation: makeMetric("Liquidation"),
    };
    row.append(header, metrics);
    return row;
  }

  function updatePositionListRow(row, position) {
    const nodes = row.positionListNodes;
    const exchangeName = String(position.exchange || "").toUpperCase();
    const logoIdentity = `${position.exchange_logo_url || ""}|${exchangeName}`;
    if (nodes.logo.dataset.identity !== logoIdentity) {
      setHTML(nodes.logo, logoImg(position.exchange_logo_url, exchangeName, "exchange-logo"));
      nodes.logo.querySelectorAll("[title]").forEach((n) => n.removeAttribute("title"));
      nodes.logo.dataset.identity = logoIdentity;
    }
    nodes.symbol.textContent = String(position.symbol || "—");
    const sideText = String(position.side || "—");
    nodes.side.textContent = sideText;
    nodes.side.className = sideText.toLowerCase() === "long"
      ? "account-position-long"
      : sideText.toLowerCase() === "short" ? "account-position-short" : "";
    const scope = String(position.market_scope || position.dex || "");
    nodes.account.textContent = [position.account || "—", scope].filter(Boolean).join(" · ");

    const pnl = position.unrealized_pnl === null || position.unrealized_pnl === undefined
      ? Number.NaN : Number(position.unrealized_pnl);
    const pnlClass = positionPnlClass(pnl);
    nodes.trailingValue.textContent = Number.isFinite(pnl)
      ? `${pnl > 0 ? "+" : ""}${formatPositionNumber(pnl, 4)}` : "—";
    nodes.trailingValue.className = pnlClass;

    nodes.size.textContent = formatPositionNumber(position.quantity ?? position.contracts);
    nodes.entry.textContent = formatPositionNumber(position.entry_price);
    nodes.mark.textContent = formatPositionNumber(position.mark_price);
    const percentage = position.percentage === null || position.percentage === undefined
      ? Number.NaN : Number(position.percentage);
    nodes.ret.textContent = Number.isFinite(percentage)
      ? `${percentage > 0 ? "+" : ""}${formatPositionNumber(percentage, 2)}%` : "—";
    nodes.ret.className = pnlClass;
    nodes.leverage.textContent = position.leverage !== null
      && position.leverage !== undefined
      && Number.isFinite(Number(position.leverage))
      ? `${formatPositionNumber(position.leverage, 2)}x` : "—";
    updatePositionRealizedPnlCell(nodes.realized, position);
    nodes.liquidation.textContent = formatPositionNumber(position.liquidation_price);
  }

  function setPositionsLoading(loading, preserveContent = false) {
    if (accountView === "positions") {
      el("assets-refresh").disabled = loading;
      el("assets-refresh").classList.toggle("assets-refreshing", loading);
    }
    el("positions-loading").classList.toggle("hidden", !loading || preserveContent);
    if (loading) el("positions-error").classList.add("hidden");
    if (loading && !preserveContent) {
      positionsPayload = null;
      el("positions-summary").classList.add("hidden");
      el("positions-empty").classList.add("hidden");
      el("positions-list").classList.add("hidden");
      el("positions-account-errors").classList.add("hidden");
      el("positions-list").replaceChildren();
    }
  }

  async function loadPositions(force) {
    const seq = ++positionsRequestSeq;
    const preserveContent = positionsHaveData;
    setPositionsLoading(true, preserveContent);
    try {
      const payload = await api(`/api/account/positions${force ? "?refresh=true" : ""}`);
      if (seq !== positionsRequestSeq || !isAssetsOpen() || accountView !== "positions") return;
      renderPositions(payload);
    } catch (error) {
      if (seq !== positionsRequestSeq || !isAssetsOpen() || accountView !== "positions") return;
      el("positions-error").textContent = `${error.message}\nUse refresh to try again.`;
      el("positions-error").classList.remove("hidden");
      if (!preserveContent) {
        el("positions-summary").classList.add("hidden");
        el("positions-list").classList.add("hidden");
      }
    } finally {
      if (seq === positionsRequestSeq) setPositionsLoading(false);
    }
  }

  function formatPnlValue(value, currency) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return `${number > 0 ? "+" : ""}${number.toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: Math.abs(number) > 0 && Math.abs(number) < 1 ? 6 : 2,
    })} ${currency || ""}`.trim();
  }

  function pnlRowKey(record) {
    return [record.exchange, record.account, record.currency].join("|");
  }

  function syncPnlTotals(container, totals, className = "pnl-summary-value") {
    const existing = new Map(
      Array.from(container.children).map((node) => [node.dataset.currency || "", node]),
    );
    totals.forEach((total, index) => {
      const currency = String(total.currency || "UNKNOWN");
      let value = existing.get(currency);
      if (!value) {
        value = document.createElement("strong");
        value.dataset.currency = currency;
      }
      value.className = `${className} ${positionPnlClass(total.net_pnl)}`.trim();
      value.textContent = `${formatPnlValue(total.net_pnl, currency)}${
        total.complete === true ? "" : "*"
      }`;
      const current = container.children[index];
      if (current !== value) container.insertBefore(value, current || null);
      existing.delete(currency);
    });
    existing.forEach((node) => node.remove());
  }

  function groupPnlByExchange(results) {
    const groups = new Map();
    results.forEach((record) => {
      const exchange = String(record.exchange || "unknown");
      let group = groups.get(exchange);
      if (!group) {
        group = {
          exchange,
          exchange_logo_url: record.exchange_logo_url || "",
          accounts: new Set(),
          rows: [],
          totals: new Map(),
        };
        groups.set(exchange, group);
      }
      group.accounts.add(String(record.account || ""));
      group.rows.push(record);
      const currency = String(record.currency || "UNKNOWN");
      let total = group.totals.get(currency);
      if (!total) {
        total = { currency, net_pnl: 0, complete: true };
        group.totals.set(currency, total);
      }
      const net = Number(record.net_pnl);
      if (Number.isFinite(net)) total.net_pnl += net;
      else total.complete = false;
      total.complete = total.complete && record.complete === true;
    });
    return Array.from(groups.values())
      .map((group) => ({
        ...group,
        account_count: Array.from(group.accounts).filter(Boolean).length,
        totals: Array.from(group.totals.values()).sort((left, right) => (
          left.currency.localeCompare(right.currency)
        )),
      }))
      .sort((left, right) => left.exchange.localeCompare(right.exchange));
  }

  function createPnlExchangeRow() {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "pnl-exchange-row";
    const identity = document.createElement("span");
    identity.className = "pnl-exchange-identity";
    const logo = document.createElement("span");
    logo.className = "pnl-exchange-logo";
    const copy = document.createElement("span");
    copy.className = "assets-exchange-identity";
    const title = document.createElement("span");
    title.className = "assets-exchange-title";
    const accounts = document.createElement("span");
    accounts.className = "assets-exchange-accounts";
    copy.append(title, accounts);
    identity.append(logo, copy);
    const totals = document.createElement("span");
    totals.className = "pnl-exchange-totals";
    const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chevron.classList.add("assets-exchange-chevron");
    chevron.setAttribute("viewBox", "0 0 24 24");
    chevron.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "m9 18 6-6-6-6");
    chevron.appendChild(path);
    row.append(identity, totals, chevron);
    row.pnlNodes = { logo, title, accounts, totals };
    row.addEventListener("click", () => {
      selectedPnlExchange = row.dataset.pnlExchange || null;
      renderPnlView();
      scrollPnlToTop();
    });
    return row;
  }

  function updatePnlExchangeRow(row, group) {
    row.dataset.pnlKey = `exchange:${group.exchange}`;
    row.dataset.pnlExchange = group.exchange;
    row.setAttribute("aria-label", `Show ${assetExchangeLabel(group.exchange)} PnL details`);
    const nodes = row.pnlNodes;
    const exchangeName = String(group.exchange || "").toUpperCase();
    const logoIdentity = `${group.exchange_logo_url || ""}|${exchangeName}`;
    if (nodes.logo.dataset.identity !== logoIdentity) {
      setHTML(nodes.logo, logoImg(group.exchange_logo_url, exchangeName, "exchange-logo"));
      nodes.logo.querySelectorAll("[title]").forEach((node) => node.removeAttribute("title"));
      nodes.logo.dataset.identity = logoIdentity;
    }
    nodes.title.textContent = assetExchangeLabel(group.exchange);
    nodes.accounts.textContent =
      `${group.account_count} account${group.account_count === 1 ? "" : "s"}`;
    syncPnlTotals(nodes.totals, group.totals, "pnl-exchange-total");
  }

  function createPnlRecord() {
    const row = document.createElement("article");
    row.className = "account-history-record account-pnl-record";

    const header = document.createElement("div");
    header.className = "account-history-record-header";
    const identity = document.createElement("div");
    identity.className = "account-history-record-identity";
    const logo = document.createElement("span");
    logo.className = "account-position-live-logo";
    identity.appendChild(logo);
    const copy = document.createElement("div");
    copy.className = "account-history-record-copy";
    const account = document.createElement("div");
    account.className = "account-history-record-symbol";
    const accountName = document.createElement("strong");
    const currency = document.createElement("span");
    account.append(accountName, currency);
    const partial = document.createElement("span");
    partial.className = "account-pnl-partial hidden";
    partial.textContent = "Partial data";
    account.appendChild(partial);
    const exchange = document.createElement("small");
    copy.append(account, exchange);
    identity.appendChild(copy);

    const trailing = document.createElement("div");
    trailing.className = "account-history-record-trailing";
    const trailingLabel = document.createElement("span");
    trailingLabel.textContent = "Net PnL";
    const trailingValue = document.createElement("strong");
    trailing.append(trailingLabel, trailingValue);
    header.append(identity, trailing);

    const metrics = document.createElement("div");
    metrics.className = "account-history-record-metrics";
    const makeMetric = (label) => {
      const metric = createHistoryMetric(label, "");
      metrics.appendChild(metric);
      return metric.querySelector("strong");
    };
    row.pnlNodes = {
      logo,
      accountName,
      currency,
      partial,
      exchange,
      trailingValue,
      realized: makeMetric("Realized"),
      unrealized: makeMetric("Unrealized"),
      fees: makeMetric("Fees"),
      closed: makeMetric("Closed"),
      open: makeMetric("Open"),
    };
    row.append(header, metrics);
    return row;
  }

  function updatePnlRecord(row, record) {
    row.dataset.pnlKey = `account:${pnlRowKey(record)}`;
    const nodes = row.pnlNodes;
    const exchangeName = String(record.exchange || "").toUpperCase();
    const logoIdentity = `${record.exchange_logo_url || ""}|${exchangeName}`;
    if (nodes.logo.dataset.identity !== logoIdentity) {
      setHTML(nodes.logo, logoImg(record.exchange_logo_url, exchangeName, "exchange-logo"));
      nodes.logo.querySelectorAll("[title]").forEach((node) => node.removeAttribute("title"));
      nodes.logo.dataset.identity = logoIdentity;
    }
    nodes.accountName.textContent = String(record.account || "—");
    nodes.currency.textContent = String(record.currency || "");
    nodes.partial.classList.toggle("hidden", record.complete === true);
    nodes.exchange.textContent = exchangeName || "—";
    nodes.trailingValue.textContent = formatPnlValue(record.net_pnl, record.currency);
    nodes.trailingValue.className = positionPnlClass(record.net_pnl);
    const feeAdjustment = record.fees === null || record.fees === undefined
      ? null
      : -Number(record.fees);
    const values = [
      [nodes.realized, record.realized_pnl, formatPnlValue(record.realized_pnl, record.currency)],
      [nodes.unrealized, record.unrealized_pnl, formatPnlValue(record.unrealized_pnl, record.currency)],
      [
        nodes.fees,
        feeAdjustment,
        feeAdjustment === null ? "—" : formatPnlValue(feeAdjustment, record.currency),
      ],
    ];
    values.forEach(([node, value, text]) => {
      node.textContent = text;
      node.className = value === null ? "" : positionPnlClass(value);
    });
    nodes.closed.textContent = formatPositionNumber(record.closed_positions, 0);
    nodes.open.textContent = formatPositionNumber(record.open_positions, 0);
  }

  function syncPnlList(items, keyFor, createRow, updateRow) {
    const list = el("pnl-list");
    const existing = new Map(
      Array.from(list.children).map((row) => [row.dataset.pnlKey || "", row]),
    );
    items.forEach((item, index) => {
      const key = keyFor(item);
      let row = existing.get(key);
      if (!row) row = createRow();
      updateRow(row, item);
      const current = list.children[index];
      if (current !== row) list.insertBefore(row, current || null);
      existing.delete(key);
    });
    existing.forEach((row) => row.remove());
  }

  function scrollPnlToTop() {
    el("pnl-list").scrollTop = 0;
    const body = el("account-pnl-view").querySelector(".account-view-body");
    if (body) body.scrollTop = 0;
  }

  function updatePnlListSizing() {
    const list = el("pnl-list");
    if (!list) return;
    if (
      mobileHubQuery.matches
      || !isAssetsOpen()
      || accountView !== "pnl"
      || list.classList.contains("hidden")
    ) {
      list.classList.remove("pnl-list-sized", "pnl-list-scrollable");
      list.style.removeProperty("--pnl-list-height");
      return;
    }
    const rows = Array.from(list.children);
    if (!rows.length) {
      list.classList.remove("pnl-list-sized", "pnl-list-scrollable");
      list.style.removeProperty("--pnl-list-height");
      return;
    }
    const visibleRows = rows.slice(0, 5);
    const style = getComputedStyle(list);
    const borders = Number.parseFloat(style.borderTopWidth || "0")
      + Number.parseFloat(style.borderBottomWidth || "0");
    const height = visibleRows.reduce((sum, row) => sum + row.offsetHeight, borders);
    list.classList.add("pnl-list-sized");
    list.classList.toggle("pnl-list-scrollable", rows.length > 5);
    list.style.setProperty("--pnl-list-height", `${Math.ceil(height)}px`);
  }

  function schedulePnlListSizing() {
    if (pnlListSizeFrame !== null) cancelAnimationFrame(pnlListSizeFrame);
    pnlListSizeFrame = requestAnimationFrame(() => {
      pnlListSizeFrame = null;
      updatePnlListSizing();
    });
  }

  function renderPnlView() {
    if (!pnlPayload) return;
    const results = Array.isArray(pnlPayload.results) ? pnlPayload.results : [];
    const groups = groupPnlByExchange(results);
    let selected = selectedPnlExchange
      ? groups.find((group) => group.exchange === selectedPnlExchange)
      : null;
    if (selectedPnlExchange && !selected) {
      selectedPnlExchange = null;
      selected = null;
    }
    const visibleResults = selected ? selected.rows : results;
    const totals = selected
      ? selected.totals
      : (Array.isArray(pnlPayload.totals) ? pnlPayload.totals : []);
    const totalsElement = el("pnl-summary-totals");
    syncPnlTotals(totalsElement, totals);
    const accountCount = selected
      ? selected.account_count
      : Number((pnlPayload.summary || {}).accounts || 0);
    el("pnl-summary-counts").textContent =
      `${selected ? "" : `${groups.length} exchanges · `}${accountCount} accounts`;
    const collectedAt = Date.parse(pnlPayload.collected_at || "");
    el("pnl-updated").textContent = Number.isFinite(collectedAt)
      ? `Updated ${new Date(collectedAt).toLocaleString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })}`
      : "";
    el("assets-back").classList.toggle("hidden", !selected);
    el("assets-title").textContent = selected
      ? `${assetExchangeLabel(selected.exchange)} PnL`
      : "PnL";
    el("pnl-summary").classList.toggle("hidden", results.length === 0);
    el("pnl-empty").classList.toggle("hidden", results.length !== 0);
    el("pnl-list").classList.toggle("hidden", results.length === 0);
    el("pnl-list").classList.toggle("pnl-exchange-overview", !selected);
    if (selected) {
      syncPnlList(
        visibleResults,
        (record) => `account:${pnlRowKey(record)}`,
        createPnlRecord,
        updatePnlRecord,
      );
    } else {
      syncPnlList(
        groups,
        (group) => `exchange:${group.exchange}`,
        createPnlExchangeRow,
        updatePnlExchangeRow,
      );
    }
    const partial = visibleResults.some((result) => result && result.complete !== true);
    const coverage = pnlPayload.summary || {};
    const unavailable = [];
    if (coverage.funding_available !== true) unavailable.push("funding");
    if (coverage.borrow_interest_available !== true) unavailable.push("borrow interest");
    const notes = [];
    if (partial) notes.push("Unavailable PnL values are excluded");
    if (unavailable.length) notes.push(`${unavailable.join(" and ")} are not included`);
    el("pnl-coverage-note").textContent = notes.length ? `* ${notes.join(". ")}.` : "";
    el("pnl-coverage-note").classList.toggle(
      "hidden",
      results.length === 0 || notes.length === 0,
    );
    el("pnl-loading").classList.add("hidden");
    updatePnlListSizing();
    historyFlipCommit();
  }

  function renderPnl(payload) {
    pnlPayload = payload;
    pnlHaveData = true;
    renderPnlView();
  }

  function setPnlLoading(loading, preserveContent = false) {
    if (accountView === "pnl") {
      el("assets-refresh").disabled = loading;
      el("assets-refresh").classList.toggle("assets-refreshing", loading);
    }
    el("pnl-loading").classList.toggle("hidden", !loading || preserveContent);
    if (loading) el("pnl-error").classList.add("hidden");
    if (loading && !preserveContent) {
      pnlPayload = null;
      el("pnl-summary").classList.add("hidden");
      el("pnl-empty").classList.add("hidden");
      el("pnl-list").classList.add("hidden");
      el("pnl-coverage-note").classList.add("hidden");
      el("pnl-list").replaceChildren();
    }
  }

  async function loadPnl() {
    clearPnlRefreshTimer();
    const seq = ++pnlRequestSeq;
    const preserveContent = pnlHaveData;
    setPnlLoading(true, preserveContent);
    try {
      const payload = await api(`/api/account/pnl?days=${pnlPeriodDays}`);
      if (seq !== pnlRequestSeq || !isAssetsOpen() || accountView !== "pnl") return;
      renderPnl(payload);
    } catch (error) {
      if (seq !== pnlRequestSeq || !isAssetsOpen() || accountView !== "pnl") return;
      el("pnl-error").textContent = `${error.message}\nUse refresh to try again.`;
      el("pnl-error").classList.remove("hidden");
      if (!preserveContent) {
        el("pnl-summary").classList.add("hidden");
        el("pnl-list").classList.add("hidden");
      }
    } finally {
      if (seq === pnlRequestSeq) {
        setPnlLoading(false);
        schedulePnlRefresh();
      }
    }
  }

  function setHistoryImportLoading(loading, text = "") {
    historyImportBusy = loading;
    el("history-import-loading").classList.toggle("hidden", !loading);
    if (text) el("history-import-loading-text").textContent = text;
    el("history-import-dropzone").disabled = loading;
    renderHistoryImportActions();
  }

  function setHistoryImportHelpOpen(open) {
    const help = el("history-import-help");
    const button = el("history-import-help-button");
    if (!help || !button) return;
    help.classList.toggle("show", open);
    button.setAttribute("aria-expanded", String(open));
  }

  function historyImportTypeLabel(value) {
    return String(value || "")
      .split("_")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }

  function historyImportSelectMarkup(fileId, field, options, selected, emptyLabel) {
    const selectedLabel = options.includes(selected) ? selected : emptyLabel;
    const optionMarkup = options.length
      ? options.map((value) => (
        `<button type="button" class="history-import-select-option${value === selected ? " selected" : ""}" `
        + `data-history-import-option="${esc(fileId)}" data-field="${esc(field)}" `
        + `data-value="${esc(value)}" role="option" aria-selected="${value === selected}">`
        + `${esc(value)}</button>`
      )).join("")
      : `<div class="script-select-empty">${esc(emptyLabel)}</div>`;
    return `<div class="history-import-select" data-history-import-select-wrap="${esc(fileId)}:${esc(field)}">`
      + `<button type="button" class="history-import-select-button" `
      + `data-history-import-select="${esc(fileId)}" data-field="${esc(field)}" `
      + `aria-haspopup="listbox" aria-expanded="false">`
      + `<span>${esc(selectedLabel)}</span><span aria-hidden="true">&#9662;</span></button>`
      + `<div class="history-import-select-options hidden" role="listbox">${optionMarkup}</div>`
      + `</div>`;
  }

  function closeHistoryImportSelects(except = null) {
    document.querySelectorAll(".history-import-select").forEach((control) => {
      if (control === except) return;
      control.classList.remove("open");
      const button = control.querySelector(".history-import-select-button");
      const options = control.querySelector(".history-import-select-options");
      if (button) button.setAttribute("aria-expanded", "false");
      if (options) options.classList.add("hidden");
    });
  }

  function historyImportReady() {
    if (!historyImportPreview || historyImportBusy) return false;
    const files = Array.isArray(historyImportPreview.files) ? historyImportPreview.files : [];
    return files.length > 0 && files.every((file) => {
      const selection = historyImportSelections[file.file_id] || {};
      return Boolean(
        selection.account
        && selection.source_timezone
        && selection.timezone_confirmed,
      );
    });
  }

  function renderHistoryImportActions() {
    const hasPreview = Boolean(
      historyImportPreview
      && Array.isArray(historyImportPreview.files)
      && historyImportPreview.files.length,
    );
    el("history-import-actions").classList.toggle("hidden", !hasPreview);
    el("history-import-submit").disabled = !historyImportReady();
    el("history-import-status").textContent = historyImportStatusText;
  }

  function renderHistoryImportPreview() {
    const container = el("history-import-preview");
    const files = historyImportPreview && Array.isArray(historyImportPreview.files)
      ? historyImportPreview.files
      : [];
    container.classList.toggle("hidden", !files.length);
    if (!files.length) {
      container.replaceChildren();
      renderHistoryImportActions();
      return;
    }
    const timezoneOptions = Array.isArray(historyImportPreview.timezone_options)
      ? historyImportPreview.timezone_options
      : [];
    container.innerHTML = files.map((file) => {
      const selection = historyImportSelections[file.file_id] || {};
      const accounts = Array.isArray(file.account_candidates) ? file.account_candidates : [];
      const warnings = Array.isArray(file.warnings) ? file.warnings : [];
      const range = file.first_occurred_at && file.last_occurred_at
        ? `${formatAccountHistoryDate(file.first_occurred_at, false, true)} – ${formatAccountHistoryDate(file.last_occurred_at, false, true)}`
        : "No dated rows";
      return `<article class="history-import-file" data-history-import-file="${esc(file.file_id)}">`
        + `<header><div><strong title="${esc(file.name)}">${esc(file.name)}</strong>`
        + `<span>${esc(file.exchange)} · ${esc(historyImportTypeLabel(file.file_type))}</span></div>`
        + `<aside><b>${Number(file.row_count || 0).toLocaleString()} rows</b>`
        + `<button type="button" class="btn btn-icon history-import-remove" `
        + `data-history-import-remove="${esc(file.file_id)}" title="Remove file" aria-label="Remove file">&times;</button>`
        + `</aside></header>`
        + `<div class="history-import-range"><span>Coverage</span><strong>${esc(range)}</strong></div>`
        + `<div class="history-import-fields">`
        + `<label><span>Account</span>${historyImportSelectMarkup(
          file.file_id,
          "account",
          accounts,
          selection.account || "",
          accounts.length ? "Select account" : "No configured account",
        )}</label>`
        + `<label><span>Source timezone</span>${historyImportSelectMarkup(
          file.file_id,
          "source_timezone",
          timezoneOptions,
          selection.source_timezone || "",
          "Select timezone",
        )}</label>`
        + `</div>`
        + `<label class="history-import-confirm"><input type="checkbox" `
        + `data-history-import-timezone-confirm="${esc(file.file_id)}" `
        + `${selection.timezone_confirmed ? "checked" : ""} />`
        + `<span>Timezone confirmed as ${esc(selection.source_timezone || "not selected")}</span></label>`
        + (warnings.length
          ? `<div class="history-import-warnings">${warnings.map((warning) => `<span>${esc(warning)}</span>`).join("")}</div>`
          : "")
        + `</article>`;
    }).join("");
    renderHistoryImportActions();
  }

  function renderHistoryImportLog(payload) {
    const results = payload && Array.isArray(payload.results) ? payload.results : [];
    historyImportLogPayload = {
      ...(payload || {}),
      results,
      total: Number(payload && payload.total || results.length),
    };
    el("history-import-log-count").textContent = historyImportLogPayload.total
      ? `${historyImportLogPayload.total.toLocaleString()} files`
      : "";
    el("history-import-log-empty").classList.toggle("hidden", results.length > 0);
    el("history-import-log-list").innerHTML = results.map((item) => {
      const warningCount = Array.isArray(item.warnings) ? item.warnings.length : 0;
      const coverage = item.first_occurred_at && item.last_occurred_at
        ? `${formatAccountHistoryDate(item.first_occurred_at, false, true)} – ${formatAccountHistoryDate(item.last_occurred_at, false, true)}`
        : "No dated rows";
      return `<div class="history-import-log-row">`
        + `<span class="history-import-log-logo">${logoImg(item.exchange_logo_url, item.exchange, "exchange-logo")}</span>`
        + `<span><strong title="${esc(item.original_name)}">${esc(item.original_name)}</strong>`
        + `<small>${esc(item.account)} · ${esc(historyImportTypeLabel(item.file_type))} · ${Number(item.row_count || 0).toLocaleString()} rows</small>`
        + `<small>${esc(coverage)}</small></span>`
        + `<span class="history-import-log-actions"><span class="history-import-log-status ${item.status === "partial" ? "partial" : ""}">`
        + `${esc(item.status || "imported")}${warningCount ? ` · ${warningCount} warning${warningCount === 1 ? "" : "s"}` : ""}`
        + `</span>`
        + (item.enrichment_retry_available
          ? `<button type="button" class="btn btn-icon" data-history-import-retry="${esc(item.import_id)}" title="Retry Hyperliquid Order History" aria-label="Retry Hyperliquid Order History">`
            + `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 0 1 15.7-6L21 8"></path><path d="M21 3v5h-5"></path><path d="M21 12a9 9 0 0 1-15.7 6L3 16"></path><path d="M3 21v-5h5"></path></svg></button>`
          : "")
        + `<button type="button" class="btn btn-danger btn-icon" data-history-import-delete="${esc(item.import_id)}" title="Delete import record" aria-label="Delete import record">`
        + `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v5M14 11v5"></path></svg></button>`
        + `</span></div>`;
    }).join("");
  }

  async function loadHistoryImports(force = false) {
    if (historyImportLogLoaded && !force) return;
    try {
      const payload = await api("/api/account/history/imports?limit=100");
      historyImportLogLoaded = true;
      renderHistoryImportLog(payload);
      const activeJobs = Array.isArray(payload.active_jobs) ? payload.active_jobs : [];
      if (activeJobs.length && historyImportJobTimer === null && !historyImportBusy) {
        const job = activeJobs[activeJobs.length - 1];
        const mode = job.kind === "enrichment" ? "enrichment" : "import";
        historyImportStatusText = mode === "enrichment"
          ? "Refreshing Hyperliquid Order History..."
          : "Merging history into the account cache...";
        setHistoryImportLoading(true, historyImportStatusText);
        pollHistoryImportJob(job.job_id, mode);
      }
    } catch (error) {
      el("history-import-error").textContent = error.message;
      el("history-import-error").classList.remove("hidden");
    }
  }

  async function previewHistoryImport(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length || historyImportBusy) return;
    historyImportPreview = null;
    historyImportSelections = {};
    historyImportStatusText = "";
    el("history-import-error").classList.add("hidden");
    renderHistoryImportPreview();
    setHistoryImportLoading(true, "Inspecting CSV files...");
    const body = new FormData();
    files.forEach((file) => body.append("files", file, file.name));
    try {
      const payload = await api("/api/account/history/import/preview", {
        method: "POST",
        body,
      });
      historyImportPreview = payload;
      (payload.files || []).forEach((file) => {
        historyImportSelections[file.file_id] = {
          account: file.suggested_account || "",
          source_timezone: file.source_timezone || "",
          timezone_confirmed: false,
        };
      });
      renderHistoryImportPreview();
    } catch (error) {
      el("history-import-error").textContent = error.message;
      el("history-import-error").classList.remove("hidden");
    } finally {
      el("history-import-files").value = "";
      setHistoryImportLoading(false);
    }
  }

  async function removeHistoryImportPreviewFile(fileId) {
    if (!historyImportPreview || historyImportBusy || !fileId) return;
    el("history-import-error").classList.add("hidden");
    setHistoryImportLoading(true, "Removing CSV file...");
    try {
      await api(
        `/api/account/history/import/previews/${encodeURIComponent(historyImportPreview.preview_id)}`
          + `/files/${encodeURIComponent(fileId)}`,
        { method: "DELETE" },
      );
      historyImportPreview.files = (historyImportPreview.files || []).filter(
        (file) => file.file_id !== fileId,
      );
      delete historyImportSelections[fileId];
      if (!historyImportPreview.files.length) historyImportPreview = null;
      historyImportStatusText = "";
      renderHistoryImportPreview();
    } catch (error) {
      el("history-import-error").textContent = error.message;
      el("history-import-error").classList.remove("hidden");
    } finally {
      setHistoryImportLoading(false);
    }
  }

  function pollHistoryImportJob(jobId, mode = "import") {
    if (historyImportJobTimer !== null) clearTimeout(historyImportJobTimer);
    historyImportJobTimer = window.setTimeout(async () => {
      historyImportJobTimer = null;
      try {
        const job = await api(`/api/account/history/import/jobs/${encodeURIComponent(jobId)}`);
        if (job.status === "completed") {
          const summary = job.summary || {};
          historyImportStatusText = mode === "enrichment"
            ? `${Number(summary.orders || 0).toLocaleString()} Hyperliquid orders refreshed`
            : `${Number(summary.imported || 0)} imported · ${Number(summary.already_imported || 0)} already imported`;
          setHistoryImportLoading(false);
          if (mode === "import") {
            historyImportPreview = null;
            historyImportSelections = {};
            historyImportStatusText = "";
            renderHistoryImportPreview();
          }
          historyImportLogLoaded = false;
          positionHistoryGroupsHaveData = false;
          orderHistoryGroupsHaveData = false;
          pnlHaveData = false;
          await loadHistoryImports(true);
          return;
        }
        if (job.status === "failed") {
          throw new Error(job.error || "History import failed");
        }
        historyImportStatusText = job.status === "queued"
          ? "Waiting for Account Worker..."
          : mode === "enrichment"
            ? "Refreshing Hyperliquid Order History..."
            : "Merging history into the account cache...";
        renderHistoryImportActions();
        pollHistoryImportJob(jobId, mode);
      } catch (error) {
        historyImportStatusText = "";
        setHistoryImportLoading(false);
        el("history-import-error").textContent = error.message;
        el("history-import-error").classList.remove("hidden");
      }
    }, 900);
  }

  async function submitHistoryImport() {
    if (!historyImportReady()) return;
    historyImportStatusText = "Preparing normalized history...";
    el("history-import-error").classList.add("hidden");
    setHistoryImportLoading(true, "Preparing import...");
    try {
      const payload = await api("/api/account/history/import/commit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preview_id: historyImportPreview.preview_id,
          files: historyImportPreview.files.map((file) => ({
            file_id: file.file_id,
            ...historyImportSelections[file.file_id],
          })),
        }),
      });
      historyImportStatusText = "Waiting for Account Worker...";
      el("history-import-loading-text").textContent = "Importing history...";
      renderHistoryImportActions();
      pollHistoryImportJob(payload.job_id);
    } catch (error) {
      setHistoryImportLoading(false);
      historyImportStatusText = "";
      el("history-import-error").textContent = error.message;
      el("history-import-error").classList.remove("hidden");
    }
  }

  async function retryHistoryImportEnrichment(importId) {
    if (!importId || historyImportBusy) return;
    historyImportStatusText = "Preparing Hyperliquid Order History refresh...";
    el("history-import-error").classList.add("hidden");
    setHistoryImportLoading(true, "Refreshing Hyperliquid Order History...");
    try {
      const payload = await api(
        `/api/account/history/imports/${encodeURIComponent(importId)}/retry-enrichment`,
        { method: "POST" },
      );
      pollHistoryImportJob(payload.job_id, "enrichment");
    } catch (error) {
      setHistoryImportLoading(false);
      historyImportStatusText = "";
      el("history-import-error").textContent = error.message;
      el("history-import-error").classList.remove("hidden");
    }
  }

  async function deleteHistoryImport(importId, button) {
    if (!importId || historyImportBusy) return;
    const row = button && button.closest(".history-import-log-row");
    el("history-import-error").classList.add("hidden");
    if (button) button.disabled = true;
    if (row) row.classList.add("is-deleting");
    try {
      await api(`/api/account/history/imports/${encodeURIComponent(importId)}`, {
        method: "DELETE",
      });
      const results = (historyImportLogPayload.results || []).filter(
        (item) => String(item && item.import_id || "") !== importId,
      );
      historyImportLogPayload = {
        ...historyImportLogPayload,
        results,
        total: Math.max(0, Number(historyImportLogPayload.total || 0) - 1),
      };
      if (row) row.remove();
      el("history-import-log-count").textContent = historyImportLogPayload.total
        ? `${historyImportLogPayload.total.toLocaleString()} files`
        : "";
      el("history-import-log-empty").classList.toggle("hidden", results.length > 0);
      historyImportLogLoaded = true;
    } catch (error) {
      if (button) button.disabled = false;
      if (row) row.classList.remove("is-deleting");
      el("history-import-error").textContent = error.message;
      el("history-import-error").classList.remove("hidden");
    }
  }

  function formatAccountHistoryDate(value, includeSeconds = false, includeYear = false) {
    const timestamp = Date.parse(String(value || ""));
    if (!Number.isFinite(timestamp)) return "—";
    const options = {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    };
    if (includeYear) options.year = "numeric";
    if (includeSeconds) options.second = "2-digit";
    return new Date(timestamp).toLocaleString("en-US", options);
  }

  function formatAccountHistoryDuration(openedAt, closedAt) {
    const opened = Date.parse(String(openedAt || ""));
    const closed = Date.parse(String(closedAt || ""));
    if (!Number.isFinite(opened) || !Number.isFinite(closed) || closed < opened) return "—";
    let seconds = Math.floor((closed - opened) / 1000);
    const days = Math.floor(seconds / 86400);
    seconds %= 86400;
    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m`;
    return `${seconds}s`;
  }

  function createHistoryMetric(label, value, className = "") {
    const metric = document.createElement("div");
    metric.className = "account-history-record-metric";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const valueNode = document.createElement("strong");
    valueNode.textContent = value;
    if (className) valueNode.className = className;
    metric.append(labelNode, valueNode);
    return metric;
  }

  function createHistoryRecordHeader(
    record,
    detailParts,
    trailingLabel,
    trailingValue,
    trailingClass = "",
  ) {
    const header = document.createElement("div");
    header.className = "account-history-record-header";

    const identity = document.createElement("div");
    identity.className = "account-history-record-identity";
    const exchangeName = String(record.exchange || "").toUpperCase();
    setHTML(identity, logoImg(record.exchange_logo_url, exchangeName, "exchange-logo"));
    identity.querySelectorAll("[title]").forEach((node) => node.removeAttribute("title"));

    const copy = document.createElement("div");
    copy.className = "account-history-record-copy";
    const symbolLine = document.createElement("div");
    symbolLine.className = "account-history-record-symbol";
    const symbol = document.createElement("strong");
    symbol.textContent = String(record.symbol || "—");
    symbolLine.appendChild(symbol);
    detailParts.filter(Boolean).forEach(({ text, className = "" }) => {
      const detail = document.createElement("span");
      detail.textContent = text;
      if (className) detail.className = className;
      symbolLine.appendChild(detail);
    });
    const account = document.createElement("small");
    const scope = String(record.market_scope || record.dex || "");
    account.textContent = [record.account || "—", scope].filter(Boolean).join(" · ");
    copy.append(symbolLine, account);
    identity.appendChild(copy);

    header.appendChild(identity);
    if (trailingValue !== null && trailingValue !== undefined) {
      const trailing = document.createElement("div");
      trailing.className = "account-history-record-trailing";
      if (trailingLabel) {
        const trailingLabelNode = document.createElement("span");
        trailingLabelNode.textContent = trailingLabel;
        trailing.appendChild(trailingLabelNode);
      }
      const trailingNode = document.createElement("strong");
      trailingNode.textContent = trailingValue;
      if (trailingClass) trailingNode.className = trailingClass;
      trailing.appendChild(trailingNode);
      header.appendChild(trailing);
    }
    return header;
  }

  function createPositionHistoryRow(record) {
    const position = record.position && typeof record.position === "object"
      ? record.position
      : {};
    const row = document.createElement("article");
    row.className = "account-history-record account-position-history-record";
    const side = String(record.side || position.side || "—");
    const sideClass = side.toLowerCase() === "long"
      ? "account-position-long"
      : side.toLowerCase() === "short" ? "account-position-short" : "";
    const partiallyClosed = record.close_status === "partially_closed";
    const closeStatus = partiallyClosed ? "Partially closed" : "Fully closed";
    const closeStatusClass = partiallyClosed
      ? "account-position-close-status account-position-partially-closed"
      : "account-position-close-status account-position-fully-closed";
    const header = createHistoryRecordHeader(
      record,
      [
        { text: side, className: sideClass },
        { text: closeStatus, className: closeStatusClass },
      ],
      "Realized PnL",
      formatSignedPositionNumber(position.realized_pnl),
      positionPnlClass(position.realized_pnl),
    );
    const realizedNode = header.querySelector(".account-history-record-trailing > strong");
    if (realizedNode) updatePositionRealizedPnlCell(realizedNode, position);
    row.appendChild(header);
    const metrics = document.createElement("div");
    metrics.className = "account-history-record-metrics";
    metrics.append(
      createHistoryMetric("Opened", formatAccountHistoryDate(record.opened_at, true, true)),
      createHistoryMetric("Closed", formatAccountHistoryDate(record.closed_at, true, true)),
      createHistoryMetric("Entry Price", formatPositionNumber(position.entry_price)),
      createHistoryMetric("Avg Close Price", formatPositionNumber(position.exit_price)),
      createHistoryMetric("Size", formatPositionNumber(position.quantity ?? position.contracts)),
      createHistoryMetric(
        "Duration",
        formatAccountHistoryDuration(record.opened_at, record.closed_at),
      ),
    );
    row.appendChild(metrics);
    return row;
  }

  function createOrderHistoryRow(record) {
    const order = record.order && typeof record.order === "object" ? record.order : {};
    const row = document.createElement("article");
    row.className = "account-history-record account-order-history-record";
    const side = String(record.side || order.side || "—");
    const sideClass = side.toLowerCase() === "buy"
      ? "account-position-long"
      : side.toLowerCase() === "sell" ? "account-position-short" : "";
    const status = String(record.status || order.status || "—");
    const statusClass = `account-order-status account-order-status-${status
      .toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
    row.appendChild(createHistoryRecordHeader(
      record,
      [
        { text: String(record.order_type || order.type || "—") },
        { text: side, className: sideClass },
      ],
      "",
      null,
    ));
    const metrics = document.createElement("div");
    metrics.className = "account-history-record-metrics";
    metrics.append(
      createHistoryMetric("Status", status, statusClass),
      createHistoryMetric("Amount", formatPositionNumber(order.amount)),
      createHistoryMetric("Filled", formatPositionNumber(order.filled)),
      createHistoryMetric(
        "Avg Price",
        formatPositionNumber(order.average_price ?? order.price),
      ),
      createHistoryMetric("Created", formatAccountHistoryDate(record.created_at, true, true)),
    );
    row.appendChild(metrics);
    return row;
  }

  function historyNavigation(kind) {
    return kind === "position" ? positionHistoryNavigation : orderHistoryNavigation;
  }

  function historyPrefix(kind) {
    return kind === "position" ? "position-history" : "order-history";
  }

  function updateHistoryNavigation(kind) {
    const prefix = historyPrefix(kind);
    const navigation = historyNavigation(kind);
    const isDetails = navigation.level === "details";
    el(`${prefix}-groups`).classList.toggle("hidden", isDetails);
    el(`${prefix}-details`).classList.toggle("hidden", !isDetails);
    el(`${prefix}-back`).classList.toggle(
      "hidden",
      navigation.level === "exchanges",
    );

    let title = "Exchanges";
    let subtitle = "Choose an exchange";
    if (navigation.level === "symbols") {
      title = navigation.exchange.toUpperCase();
      subtitle = "Choose a symbol";
    } else if (isDetails) {
      title = navigation.symbol || "History";
      subtitle = navigation.exchange.toUpperCase();
    }
    el(`${prefix}-navigation-title`).textContent = title;
    el(`${prefix}-navigation-subtitle`).textContent = subtitle;
  }

  function historyQuery(prefix, navigation, cursor, includeStatus = false) {
    const params = new URLSearchParams({ limit: String(accountHistoryPageSize) });
    if (cursor) params.set("cursor", cursor);
    const account = String(el(`${prefix}-account`).value || "").trim();
    if (account) params.set("account", account);
    if (navigation.exchange) params.set("exchange", navigation.exchange);
    if (navigation.symbol) params.set("symbol", navigation.symbol);
    params.set("exact_market", "true");
    if (includeStatus) {
      const status = String(el(`${prefix}-status`).value || "").trim();
      if (status) params.set("status", status);
    }
    return params;
  }

  async function refreshAccountHistory(kind) {
    const navigation = historyNavigation(kind);
    const prefix = historyPrefix(kind);
    const account = navigation.level === "details"
      ? String(el(`${prefix}-account`).value || "").trim()
      : "";
    await api("/api/account/history/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind,
        account,
        exchange: navigation.exchange,
        symbol: navigation.symbol,
      }),
    });
  }

  function historyGroupRow(kind, group, level) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "account-history-group-row";
    const exchange = String(group.exchange || "");
    const symbol = String(group.symbol || "");
    const label = level === "exchanges" ? exchange.toUpperCase() : symbol;
    row.setAttribute("aria-label", `Show ${label} history`);

    const identity = document.createElement("span");
    identity.className = "account-history-group-identity";
    if (level === "exchanges") {
      const logo = document.createElement("span");
      setHTML(logo, logoImg(group.exchange_logo_url, label, "exchange-logo"));
      logo.querySelectorAll("[title]").forEach((node) => node.removeAttribute("title"));
      identity.appendChild(logo);
    }
    const copy = document.createElement("span");
    copy.className = "account-history-group-copy";
    const name = document.createElement("strong");
    name.textContent = label || "Unknown";
    const accounts = document.createElement("small");
    const accountCount = Number(group.account_count || 0);
    accounts.textContent = `${accountCount} account${accountCount === 1 ? "" : "s"}`;
    copy.append(name, accounts);
    identity.appendChild(copy);

    const summary = document.createElement("span");
    summary.className = "account-history-group-summary";
    const count = document.createElement("span");
    const recordCount = Number(group.record_count || 0);
    count.textContent = `${recordCount.toLocaleString()} record${recordCount === 1 ? "" : "s"}`;
    const latest = document.createElement("small");
    latest.textContent = `Latest ${formatAccountHistoryDate(group.latest_at)}`;
    summary.append(count, latest);

    const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chevron.classList.add("account-history-group-chevron");
    chevron.setAttribute("viewBox", "0 0 24 24");
    chevron.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "m9 18 6-6-6-6");
    chevron.appendChild(path);
    row.append(identity, summary, chevron);
    row.addEventListener("click", () => openHistoryGroup(kind, group));
    return row;
  }

  // Smoothly animate the modal-box height across an account content change
  // (history exchange/symbol/detail navigation, a history filter reload, or a
  // PnL period change). begin() pins the current height before the swap;
  // commit() (from the render) releases to the natural height and transitions.
  let historyFlipHeight = null;
  let historyFlipTimer = null;

  function historyFlipBegin() {
    const box = document.querySelector(".assets-modal-box");
    if (!box
      || mobileHubQuery.matches
      || window.matchMedia("(prefers-reduced-motion: reduce)").matches
      || el("assets-modal").classList.contains("hidden")) {
      return;
    }
    historyFlipHeight = box.offsetHeight;
    box.style.height = `${historyFlipHeight}px`;
    if (historyFlipTimer !== null) clearTimeout(historyFlipTimer);
    historyFlipTimer = window.setTimeout(() => {
      historyFlipTimer = null;
      historyFlipHeight = null;
      box.style.height = "";
    }, 2000);
  }

  function historyFlipCommit() {
    if (historyFlipHeight === null) return;
    if (historyFlipTimer !== null) {
      clearTimeout(historyFlipTimer);
      historyFlipTimer = null;
    }
    const box = document.querySelector(".assets-modal-box");
    const start = historyFlipHeight;
    historyFlipHeight = null;
    if (!box) return;
    box.style.height = "";
    const end = box.offsetHeight;
    if (Math.abs(end - start) <= 1) return;
    box.style.height = `${start}px`;
    void box.offsetHeight; // reflow to lock the start height
    box.classList.add("flip-animating");
    box.style.height = `${end}px`;
    const onEnd = (event) => {
      if (event.target !== box || event.propertyName !== "height") return;
      box.removeEventListener("transitionend", onEnd);
      box.classList.remove("flip-animating");
      box.style.height = "";
    };
    box.addEventListener("transitionend", onEnd);
  }

  function renderHistoryGroups(kind, payload) {
    const prefix = historyPrefix(kind);
    const navigation = historyNavigation(kind);
    updateHistoryNavigation(kind);
    const groups = Array.isArray(payload.results) ? payload.results : [];
    const container = el(`${prefix}-groups`);
    const list = document.createElement("div");
    list.className = "account-history-group-list";
    groups.forEach((group) => {
      list.appendChild(historyGroupRow(kind, group, navigation.level));
    });
    container.replaceChildren(list);
    if (kind === "position") positionHistoryGroupsHaveData = true;
    else orderHistoryGroupsHaveData = true;

    const summary = payload.summary || {};
    const groupLabel = navigation.level === "exchanges" ? "exchanges" : "symbols";
    el(`${prefix}-navigation-subtitle`).textContent = groups.length
      ? `${groups.length} ${groupLabel} · ${Number(summary.total || 0).toLocaleString()} records`
      : navigation.level === "exchanges" ? "Choose an exchange" : "Choose a symbol";
    el(`${prefix}-empty`).textContent = navigation.level === "exchanges"
      ? `No ${kind === "position" ? "position" : "order"} history.`
      : `No history found for ${navigation.exchange.toUpperCase()}.`;
    el(`${prefix}-empty`).classList.toggle("hidden", groups.length !== 0);
    el(`${prefix}-meta`).classList.add("hidden");
    el(`${prefix}-loading`).classList.add("hidden");
    historyFlipCommit();
  }

  function openHistoryGroup(kind, group) {
    historyFlipBegin();
    const navigation = historyNavigation(kind);
    if (navigation.level === "exchanges") {
      navigation.level = "symbols";
      navigation.exchange = String(group.exchange || "");
      navigation.symbol = "";
      if (kind === "position") positionHistoryGroupsHaveData = false;
      else orderHistoryGroupsHaveData = false;
    } else if (navigation.level === "symbols") {
      navigation.level = "details";
      navigation.symbol = String(group.symbol || "");
      if (kind === "position") {
        el("position-history-account").value = "";
        resetPositionHistory();
      } else {
        el("order-history-account").value = "";
        el("order-history-status").value = "";
        resetOrderHistory();
      }
    }
    updateHistoryNavigation(kind);
    if (kind === "position") loadPositionHistory(false);
    else loadOrderHistory(false);
  }

  function navigateHistoryBack(kind) {
    historyFlipBegin();
    const navigation = historyNavigation(kind);
    if (navigation.level === "details") {
      navigation.level = "symbols";
      navigation.symbol = "";
    } else if (navigation.level === "symbols") {
      navigation.level = "exchanges";
      navigation.exchange = "";
    } else {
      return;
    }
    if (kind === "position") {
      resetPositionHistory();
      positionHistoryGroupsHaveData = false;
    } else {
      resetOrderHistory();
      orderHistoryGroupsHaveData = false;
    }
    updateHistoryNavigation(kind);
    if (kind === "position") loadPositionHistory(false);
    else loadOrderHistory(false);
  }

  function resetHistoryNavigation(kind) {
    const navigation = historyNavigation(kind);
    navigation.level = "exchanges";
    navigation.exchange = "";
    navigation.symbol = "";
    if (kind === "position") {
      positionHistoryGroupsHaveData = false;
      el("position-history-account").value = "";
      resetPositionHistory();
    } else {
      orderHistoryGroupsHaveData = false;
      el("order-history-account").value = "";
      el("order-history-status").value = "";
      resetOrderHistory();
    }
    updateHistoryNavigation(kind);
  }

  function fillHistorySelect(selectEl, values, allLabel) {
    const current = selectEl.value;
    const seen = [];
    values.forEach((value) => {
      const text = String(value || "").trim();
      if (text && !seen.includes(text)) seen.push(text);
    });
    seen.sort((a, b) => a.localeCompare(b));
    selectEl.replaceChildren();
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = allLabel;
    selectEl.appendChild(allOption);
    seen.forEach((text) => {
      const option = document.createElement("option");
      option.value = text;
      option.textContent = text;
      selectEl.appendChild(option);
    });
    selectEl.value = seen.includes(current) ? current : "";
    const custom = historyCustomSelects[selectEl.id];
    if (custom) custom.sync();
  }

  // Custom dropdown skin over a native <select>, matching the Add-session
  // script selector. Reads its options from the native select and dispatches a
  // native "change" event on choose (so existing filter handlers still fire).
  function setupHistoryCustomSelect(selectId) {
    const select = el(selectId);
    const control = el(`${selectId}-control`);
    const button = el(`${selectId}-button`);
    const label = el(`${selectId}-label`);
    const optionsBox = el(`${selectId}-options`);
    if (!select || !control || !button || !label || !optionsBox) return;
    let activeIndex = -1;

    const sync = () => {
      const selected = select.options[select.selectedIndex];
      const text = selected ? selected.textContent : "";
      label.textContent = text;
      button.title = text;
      optionsBox.querySelectorAll(".script-select-option").forEach((opt, index) => {
        const on = opt.dataset.value === select.value;
        opt.classList.toggle("selected", on);
        opt.classList.toggle("active", index === activeIndex);
        opt.setAttribute("aria-selected", on ? "true" : "false");
      });
    };
    const render = () => {
      const opts = Array.from(select.options);
      if (!opts.length) {
        optionsBox.innerHTML = `<div class="script-select-empty">No options</div>`;
        return;
      }
      optionsBox.innerHTML = opts.map((option, index) => {
        const on = option.value === select.value;
        const active = index === activeIndex;
        return `<button type="button" role="option" `
          + `class="script-select-option${on ? " selected" : ""}${active ? " active" : ""}" `
          + `data-value="${esc(option.value)}" aria-selected="${on ? "true" : "false"}" `
          + `title="${esc(option.textContent)}">${esc(option.textContent)}</button>`;
      }).join("");
    };
    const open = () => {
      activeIndex = Math.max(0, select.selectedIndex);
      render();
      control.classList.add("open");
      button.setAttribute("aria-expanded", "true");
      optionsBox.classList.remove("hidden");
    };
    const close = () => {
      control.classList.remove("open");
      button.setAttribute("aria-expanded", "false");
      optionsBox.classList.add("hidden");
    };
    const choose = (value) => {
      close();
      if (select.value === value) return;
      select.value = value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    };
    const move = (delta) => {
      const count = select.options.length;
      if (!count) return;
      activeIndex = Math.max(0, Math.min((activeIndex < 0 ? 0 : activeIndex) + delta, count - 1));
      render();
      const active = optionsBox.querySelector(".script-select-option.active");
      if (active) active.scrollIntoView({ block: "nearest" });
    };
    const commit = () => {
      if (activeIndex < 0 || activeIndex >= select.options.length) return;
      choose(select.options[activeIndex].value);
      button.focus();
    };

    button.addEventListener("click", (event) => {
      event.preventDefault();
      if (optionsBox.classList.contains("hidden")) open();
      else close();
    });
    button.addEventListener("keydown", (event) => {
      const isOpen = !optionsBox.classList.contains("hidden");
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!isOpen) open();
        else move(event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (isOpen) commit();
        else open();
      } else if (event.key === "Escape") {
        close();
      }
    });
    optionsBox.addEventListener("click", (event) => {
      const opt = event.target && event.target.closest
        ? event.target.closest(".script-select-option")
        : null;
      if (!opt) return;
      choose(opt.dataset.value || "");
      button.focus();
    });
    optionsBox.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        move(event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        commit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        close();
        button.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (event.target && !control.contains(event.target)) close();
    });
    select.addEventListener("change", sync);

    historyCustomSelects[selectId] = { sync, close };
    sync();
  }

  function renderPositionHistory(payload, append) {
    const incoming = Array.isArray(payload.results) ? payload.results : [];
    positionHistoryRows = append ? positionHistoryRows.concat(incoming) : incoming;
    positionHistoryCursor = payload.next_cursor || null;
    positionHistoryTotal = Number(payload.summary && payload.summary.total || 0);
    positionHistoryHaveData = true;
    if (!append && !el("position-history-account").value) {
      fillHistorySelect(
        el("position-history-account"),
        positionHistoryRows.map((row) => row.account),
        "All accounts",
      );
    }
    updateHistoryNavigation("position");
    const body = el("position-history-table-body");
    closePositionPnlPopover();
    body.replaceChildren(...positionHistoryRows.map(createPositionHistoryRow));
    el("position-history-table-wrap").classList.toggle("hidden", positionHistoryRows.length === 0);
    el("position-history-empty").classList.toggle("hidden", positionHistoryRows.length !== 0);
    el("position-history-meta").textContent = positionHistoryRows.length
      ? `${positionHistoryRows.length} of ${positionHistoryTotal} records`
      : "";
    el("position-history-meta").classList.toggle("hidden", positionHistoryRows.length === 0);
    el("position-history-more").classList.toggle("hidden", !positionHistoryCursor);
    el("position-history-loading").classList.add("hidden");
    historyFlipCommit();
  }

  function renderOrderHistory(payload, append) {
    const incoming = Array.isArray(payload.results) ? payload.results : [];
    orderHistoryRows = append ? orderHistoryRows.concat(incoming) : incoming;
    orderHistoryCursor = payload.next_cursor || null;
    orderHistoryTotal = Number(payload.summary && payload.summary.total || 0);
    orderHistoryHaveData = true;
    if (!append) {
      if (!el("order-history-account").value) {
        fillHistorySelect(
          el("order-history-account"),
          orderHistoryRows.map((row) => row.account),
          "All accounts",
        );
      }
      if (!el("order-history-status").value) {
        fillHistorySelect(
          el("order-history-status"),
          orderHistoryRows.map((row) => row.status),
          "All statuses",
        );
      }
    }
    updateHistoryNavigation("order");
    const body = el("order-history-table-body");
    body.replaceChildren(...orderHistoryRows.map(createOrderHistoryRow));
    el("order-history-table-wrap").classList.toggle("hidden", orderHistoryRows.length === 0);
    el("order-history-empty").classList.toggle("hidden", orderHistoryRows.length !== 0);
    el("order-history-meta").textContent = orderHistoryRows.length
      ? `${orderHistoryRows.length} of ${orderHistoryTotal} records`
      : "";
    el("order-history-meta").classList.toggle("hidden", orderHistoryRows.length === 0);
    el("order-history-more").classList.toggle("hidden", !orderHistoryCursor);
    el("order-history-loading").classList.add("hidden");
    historyFlipCommit();
  }

  function setPositionHistoryLoading(loading, preserveContent, append) {
    if (accountView === "position-history") {
      el("assets-refresh").disabled = loading;
      el("assets-refresh").classList.toggle("assets-refreshing", loading);
    }
    el("position-history-loading").classList.toggle("hidden", !loading || preserveContent);
    el("position-history-more").disabled = loading;
    el("position-history-more").textContent = loading && append ? "Loading..." : "Load more";
    if (loading) el("position-history-error").classList.add("hidden");
    if (loading && !preserveContent) {
      el("position-history-meta").classList.add("hidden");
      el("position-history-empty").classList.add("hidden");
      el("position-history-table-wrap").classList.add("hidden");
      el("position-history-more").classList.add("hidden");
      el("position-history-table-body").replaceChildren();
      if (positionHistoryNavigation.level !== "details") {
        el("position-history-groups").replaceChildren();
      }
    }
  }

  function setOrderHistoryLoading(loading, preserveContent, append) {
    if (accountView === "orders") {
      el("assets-refresh").disabled = loading;
      el("assets-refresh").classList.toggle("assets-refreshing", loading);
    }
    el("order-history-loading").classList.toggle("hidden", !loading || preserveContent);
    el("order-history-more").disabled = loading;
    el("order-history-more").textContent = loading && append ? "Loading..." : "Load more";
    if (loading) el("order-history-error").classList.add("hidden");
    if (loading && !preserveContent) {
      el("order-history-meta").classList.add("hidden");
      el("order-history-empty").classList.add("hidden");
      el("order-history-table-wrap").classList.add("hidden");
      el("order-history-more").classList.add("hidden");
      el("order-history-table-body").replaceChildren();
      if (orderHistoryNavigation.level !== "details") {
        el("order-history-groups").replaceChildren();
      }
    }
  }

  async function loadPositionHistoryGroups(force = false) {
    const seq = ++positionHistoryRequestSeq;
    const level = positionHistoryNavigation.level;
    const exchange = positionHistoryNavigation.exchange;
    const preserveContent = positionHistoryGroupsHaveData;
    setPositionHistoryLoading(true, preserveContent, false);
    updateHistoryNavigation("position");
    const params = new URLSearchParams();
    if (level === "symbols" && exchange) params.set("exchange", exchange);
    try {
      if (force) await refreshAccountHistory("position");
      const query = params.toString();
      const suffix = query ? `?${query}` : "";
      const payload = await api(`/api/account/position-history/groups${suffix}`);
      if (
        seq !== positionHistoryRequestSeq
        || !isAssetsOpen()
        || accountView !== "position-history"
        || positionHistoryNavigation.level !== level
        || positionHistoryNavigation.exchange !== exchange
      ) return;
      renderHistoryGroups("position", payload);
    } catch (error) {
      if (seq !== positionHistoryRequestSeq || accountView !== "position-history") return;
      el("position-history-error").textContent = `${error.message}\nUse refresh to try again.`;
      el("position-history-error").classList.remove("hidden");
    } finally {
      if (seq === positionHistoryRequestSeq) {
        setPositionHistoryLoading(false, positionHistoryGroupsHaveData, false);
      }
    }
  }

  async function loadOrderHistoryGroups(force = false) {
    const seq = ++orderHistoryRequestSeq;
    const level = orderHistoryNavigation.level;
    const exchange = orderHistoryNavigation.exchange;
    const preserveContent = orderHistoryGroupsHaveData;
    setOrderHistoryLoading(true, preserveContent, false);
    updateHistoryNavigation("order");
    const params = new URLSearchParams();
    if (level === "symbols" && exchange) params.set("exchange", exchange);
    try {
      if (force) await refreshAccountHistory("order");
      const query = params.toString();
      const suffix = query ? `?${query}` : "";
      const payload = await api(`/api/account/orders/groups${suffix}`);
      if (
        seq !== orderHistoryRequestSeq
        || !isAssetsOpen()
        || accountView !== "orders"
        || orderHistoryNavigation.level !== level
        || orderHistoryNavigation.exchange !== exchange
      ) return;
      renderHistoryGroups("order", payload);
    } catch (error) {
      if (seq !== orderHistoryRequestSeq || accountView !== "orders") return;
      el("order-history-error").textContent = `${error.message}\nUse refresh to try again.`;
      el("order-history-error").classList.remove("hidden");
    } finally {
      if (seq === orderHistoryRequestSeq) {
        setOrderHistoryLoading(false, orderHistoryGroupsHaveData, false);
      }
    }
  }

  async function loadPositionHistory(append = false, force = false) {
    if (positionHistoryNavigation.level !== "details") {
      await loadPositionHistoryGroups(force);
      return;
    }
    if (append && !positionHistoryCursor) return;
    const seq = ++positionHistoryRequestSeq;
    const preserveContent = positionHistoryHaveData || append;
    setPositionHistoryLoading(true, preserveContent, append);
    try {
      if (force) await refreshAccountHistory("position");
      const query = historyQuery(
        "position-history",
        positionHistoryNavigation,
        append ? positionHistoryCursor : null,
      );
      const payload = await api(`/api/account/position-history?${query}`);
      if (
        seq !== positionHistoryRequestSeq
        || !isAssetsOpen()
        || accountView !== "position-history"
      ) return;
      renderPositionHistory(payload, append);
    } catch (error) {
      if (
        seq !== positionHistoryRequestSeq
        || !isAssetsOpen()
        || accountView !== "position-history"
      ) return;
      el("position-history-error").textContent = `${error.message}\nUse refresh to try again.`;
      el("position-history-error").classList.remove("hidden");
    } finally {
      if (seq === positionHistoryRequestSeq) {
        setPositionHistoryLoading(false, positionHistoryHaveData, append);
      }
    }
  }

  async function loadOrderHistory(append = false, force = false) {
    if (orderHistoryNavigation.level !== "details") {
      await loadOrderHistoryGroups(force);
      return;
    }
    if (append && !orderHistoryCursor) return;
    const seq = ++orderHistoryRequestSeq;
    const preserveContent = orderHistoryHaveData || append;
    setOrderHistoryLoading(true, preserveContent, append);
    try {
      if (force) await refreshAccountHistory("order");
      const query = historyQuery(
        "order-history",
        orderHistoryNavigation,
        append ? orderHistoryCursor : null,
        true,
      );
      const payload = await api(`/api/account/orders?${query}`);
      if (seq !== orderHistoryRequestSeq || !isAssetsOpen() || accountView !== "orders") return;
      renderOrderHistory(payload, append);
    } catch (error) {
      if (seq !== orderHistoryRequestSeq || !isAssetsOpen() || accountView !== "orders") return;
      el("order-history-error").textContent = `${error.message}\nUse refresh to try again.`;
      el("order-history-error").classList.remove("hidden");
    } finally {
      if (seq === orderHistoryRequestSeq) {
        setOrderHistoryLoading(false, orderHistoryHaveData, append);
      }
    }
  }

  function resetPositionHistory() {
    positionHistoryRequestSeq += 1;
    positionHistoryHaveData = false;
    positionHistoryRows = [];
    positionHistoryCursor = null;
    positionHistoryTotal = 0;
  }

  function resetOrderHistory() {
    orderHistoryRequestSeq += 1;
    orderHistoryHaveData = false;
    orderHistoryRows = [];
    orderHistoryCursor = null;
    orderHistoryTotal = 0;
  }

  function clearAccountPositionsTimers() {
    if (accountPositionsReconnectTimer !== null) {
      clearTimeout(accountPositionsReconnectTimer);
      accountPositionsReconnectTimer = null;
    }
    if (accountPositionsKeepaliveTimer !== null) {
      clearInterval(accountPositionsKeepaliveTimer);
      accountPositionsKeepaliveTimer = null;
    }
  }

  function closeAccountPositionsSocket() {
    accountPositionsGeneration += 1;
    clearAccountPositionsTimers();
    const ws = accountPositionsWs;
    accountPositionsWs = null;
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onclose = null;
      ws.onerror = null;
      if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
        try { ws.close(); } catch {}
      }
    }
  }

  function connectAccountPositions(generation = accountPositionsGeneration) {
    if (
      generation !== accountPositionsGeneration
      || !isAssetsOpen()
      || accountView !== "positions"
      || document.visibilityState === "hidden"
    ) return;
    if (
      accountPositionsWs
      && [WebSocket.OPEN, WebSocket.CONNECTING].includes(accountPositionsWs.readyState)
    ) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws/account`);
    accountPositionsWs = ws;
    ws.onopen = () => {
      if (ws !== accountPositionsWs || generation !== accountPositionsGeneration) return;
      accountPositionsKeepaliveTimer = setInterval(() => {
        if (ws === accountPositionsWs && ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 15000);
    };
    ws.onmessage = (event) => {
      if (
        ws !== accountPositionsWs
        || generation !== accountPositionsGeneration
        || !isAssetsOpen()
        || accountView !== "positions"
      ) return;
      try {
        const message = JSON.parse(event.data);
        if (message.type === "account.positions" && message.payload) {
          renderPositions(message.payload);
        }
      } catch {}
    };
    ws.onclose = () => {
      if (ws !== accountPositionsWs || generation !== accountPositionsGeneration) return;
      accountPositionsWs = null;
      clearAccountPositionsTimers();
      if (!isAssetsOpen() || accountView !== "positions") return;
      accountPositionsReconnectTimer = setTimeout(
        () => connectAccountPositions(generation),
        1500,
      );
    };
    ws.onerror = () => {
      try { ws.close(); } catch {}
    };
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
        ? `Updated ${new Date(updated).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`
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
    const title = calendarMonth.toLocaleDateString("en-US", { year: "numeric", month: "long" });
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
      const dots = events.slice(0, 5).map((event, index) => (
        `<span class="calendar-event-dot" style="--event-color:${calendarEventColor(index)}"></span>`
      )).join("");
      const eventRow = events.length
        ? `<span class="calendar-day-event-row">${dots}</span>`
        : "";
      cells.push(
        `<button class="${classes}" type="button" data-calendar-date="${key}" ` +
        `aria-label="${esc(day.toLocaleDateString("en-US"))}, ${events.length} events">` +
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
      ? selected.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" })
      : calendarSelectedDate;
    renderCalendarAddControls();
    const events = calendarEvents.filter((event) => event.date === calendarSelectedDate);
    if (!events.length) {
      el("calendar-detail-events").innerHTML = `<div class="calendar-empty-day">No events</div>`;
      section.classList.remove("hidden");
      return;
    }
    el("calendar-detail-events").innerHTML = events.map((event, eventIndex) => {
      const forecastState = calendarForecastState(event);
      const affectedSessions = calendarAffectedSessions(event);
      const sessionsExpanded = calendarExpandedSessionEvents.has(String(event.id || ""));
      const sessionSummary = affectedSessions.length === 1
        ? calendarSessionLabel(affectedSessions[0])
        : `${affectedSessions.length} affected sessions`;
      const sessionToggle = `<button class="calendar-event-session-toggle" type="button" ` +
        `data-calendar-event-sessions="${esc(event.id)}" aria-expanded="${sessionsExpanded}" ` +
        `aria-label="${sessionsExpanded ? "Hide" : "Show"} affected sessions">` +
        `<span>${esc(sessionSummary)}</span>` +
        `<span class="calendar-event-session-chevron" aria-hidden="true">&#8250;</span></button>`;
      const sessionList = sessionsExpanded
        ? `<div class="calendar-event-session-list">` +
          affectedSessions.map((session) => {
            const linkedSessionId = String(session.session_id || "");
            return `<div class="calendar-event-session-item">` +
              `<span class="calendar-event-session-dot" ` +
              `style="--event-color:${calendarSessionColor(linkedSessionId)}"></span>` +
              `<span>${esc(calendarSessionLabel(session))}</span></div>`;
          }).join("") +
          `</div>`
        : "";
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
        ? `Cancel ${event.title} forecast`
        : forecastState.status === "ready"
          ? `Open ${event.title} forecast`
          : `Forecast ${event.title}`;
      // ready-but-not-open: a tiny "alien speech" bubble signals Pepe has a
      // forecast to say. It shakes with the button while unread and stays put
      // (no shake) once viewed; hidden while the full bubble is open.
      const forecastSay = (forecastState.status === "ready" && !forecastState.open)
        ? `<span class="pepe-say" aria-hidden="true">&#x2827;&#x2837;&#x282e;</span>`
        : "";
      const forecastThinking = forecastState.status === "running"
        ? `<span class="calendar-forecast-thinking" aria-hidden="true">` +
          `<span class="ai-dot">&#9679;</span><span class="ai-dot">&#9679;</span>` +
          `<span class="ai-dot">&#9679;</span></span>`
        : "";
      const forecastButton = `<button class="${forecastClasses}" type="button" ` +
        `data-calendar-forecast-event="${esc(event.id)}" aria-label="${esc(forecastLabel)}" ` +
        `title="${esc(forecastLabel)}" ` +
        `${forecastState.status === "running" ? 'aria-busy="true"' : ""} ` +
        `${aiEnabled ? "" : "disabled"}>${forecastThinking}${forecastSay}</button>`;
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
      return `<article class="calendar-event-card has-forecast" style="--event-color:${calendarEventColor(eventIndex)}">` +
        `${forecastButton}` +
        `${sessionToggle}` +
        `<div class="calendar-event-title">${esc(event.title || "Schedule")}${time}</div>` +
        `${sessionList}${details}${source}${forecastBubble}</article>`;
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
    const controller = new AbortController();
    state.status = "running";
    state.localStream = true; // this client owns the stream; reconcile must not clobber it
    state.abortController = controller;
    state.cancelPending = false;
    state.cancelRequested = false;
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
        signal: controller.signal,
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
      if (state.cancelRequested || (error && error.name === "AbortError")) {
        state.status = state.answer ? "ready" : "idle";
        state.error = "";
        state.open = false;
      } else {
        state.status = "error";
        state.error = error && error.message ? error.message : "Calendar forecast failed";
        state.open = true;
        state.viewed = true;
      }
      renderCalendarDetails();
    } finally {
      if (state.abortController === controller) state.abortController = null;
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

  function applyCalendarForecastCancelled(eventId) {
    const state = calendarForecastState({ id: eventId });
    state.cancelRequested = true;
    state.status = state.answer ? "ready" : "idle";
    state.error = "";
    state.open = false;
    if (state.abortController) state.abortController.abort();
    if (isCalendarOpen() && calendarSelectedDate) renderCalendarDetails();
  }

  async function cancelCalendarForecast(event) {
    const state = calendarForecastState(event);
    if (state.status !== "running" || state.cancelPending) return;
    state.cancelPending = true;
    try {
      const result = await api(
        `/api/calendar/events/${encodeURIComponent(event.id)}/forecast/cancel`,
        { method: "POST" },
      );
      if (result.cancelled) {
        applyCalendarForecastCancelled(event.id);
      } else if (isCalendarOpen()) {
        await loadCalendarEvents();
      }
    } catch {
      state.cancelPending = false;
    }
  }

  function toggleCalendarForecast(event) {
    const state = calendarForecastState(event);
    if (state.status === "running") {
      cancelCalendarForecast(event);
      return;
    }
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
    calendarAddOpen = false;
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

  // Mobile: swipe in from the left screen edge to open the hub menu drawer.
  function initMobileHubMenuEdgeSwipe() {
    const edge = el("hub-edge-swipe");
    const menu = el("hub-menu");
    const backdrop = el("hub-menu-backdrop");
    if (!edge) return;
    let drag = null;

    edge.addEventListener("pointerdown", (event) => {
      if (!mobileHubQuery.matches || !event.isPrimary) return;
      if (menu.classList.contains("open")) return;
      // a modal/sheet is open (it locks body scroll) — leave the edge to it
      if (document.body.style.position === "fixed") return;
      drag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        dx: 0,
        active: false,
        width: Math.max(1, menu.getBoundingClientRect().width),
      };
      try { edge.setPointerCapture(event.pointerId); } catch {}
    });

    edge.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.startX;
      if (!drag.active) {
        const dy = event.clientY - drag.startY;
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 6) return;
        drag.active = true;
        menu.classList.add("dragging");
        backdrop.classList.add("dragging");
        menu.setAttribute("aria-hidden", "false");
        backdrop.setAttribute("aria-hidden", "false");
      }
      // follow the finger's horizontal component only — vertical drift never
      // cancels the drawer (the strip has touch-action:none so nothing scrolls)
      drag.dx = Math.max(0, Math.min(dx, drag.width));
      menu.style.transform = `translateX(${-drag.width + drag.dx}px)`;
      backdrop.style.opacity = String(Math.max(0, Math.min(1, drag.dx / drag.width)));
    });

    function endEdgeDrag(event, cancelled = false) {
      if (!drag || (event && event.pointerId !== drag.id)) return;
      const current = drag;
      drag = null;
      try { edge.releasePointerCapture(current.id); } catch {}
      if (!current.active) return;
      if (!cancelled && current.dx > current.width * 0.3) openHubMenu();
      else closeHubMenu();
    }
    edge.addEventListener("pointerup", (event) => endEdgeDrag(event));
    edge.addEventListener("pointercancel", (event) => endEdgeDrag(event, true));
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

  // Mobile: the account views form a horizontal snap pager so the content
  // slides in real time under the finger. The active tab is derived from the
  // scrolled page, and tab taps / programmatic switches scroll the pager.
  function initMobileAccountPager() {
    const track = el("account-view-track");
    if (!track) return;
    let syncing = false;
    let syncTimer = null;
    let settleTimer = null;
    let resizeFrame = null;

    const tabOrder = () => Array.from(
      document.querySelectorAll(".account-tabs .account-tab"),
    ).map((tab) => tab.dataset.accountView);

    // highlight the tab for `view` immediately (visual only, no load) so the
    // active tab tracks the finger as the pager scrolls
    const highlightTab = (view) => {
      document.querySelectorAll(".account-tabs .account-tab").forEach((tab) => {
        const active = tab.dataset.accountView === view;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
    };

    accountPagerScrollToView = (view, smooth) => {
      if (!mobileHubQuery.matches) return;
      const index = tabOrder().indexOf(view);
      if (index < 0) return;
      const width = track.clientWidth;
      if (!width) {
        requestAnimationFrame(() => accountPagerScrollToView(view, false));
        return;
      }
      const left = index * width;
      if (Math.abs(track.scrollLeft - left) < 2) return;
      syncing = true;
      track.scrollTo({ left, behavior: smooth ? "smooth" : "auto" });
      if (syncTimer !== null) clearTimeout(syncTimer);
      syncTimer = window.setTimeout(() => {
        syncTimer = null;
        syncing = false;
      }, smooth ? 500 : 60);
    };

    const alignCurrentView = () => {
      if (!mobileHubQuery.matches || !isAssetsOpen()) return;
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = null;
        accountPagerScrollToView(accountView, false);
      });
    };
    window.addEventListener("resize", alignCurrentView, { passive: true });
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", alignCurrentView, { passive: true });
    }

    track.addEventListener("scroll", () => {
      if (!mobileHubQuery.matches || syncing) return;
      const width = track.clientWidth || 1;
      const views = tabOrder();
      const index = Math.max(0, Math.min(Math.round(track.scrollLeft / width), views.length - 1));
      const view = views[index];
      // live: move the tab highlight in step with the sliding content
      if (view) highlightTab(view);
      // settled: activate + load the landed page
      if (settleTimer !== null) clearTimeout(settleTimer);
      settleTimer = window.setTimeout(() => {
        settleTimer = null;
        if (view && view !== accountView) {
          switchAccountView(view, { load: true, fromPager: true });
        }
      }, 90);
    }, { passive: true });
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

  function initMobileAssetTransferHistoryGestures() {
    const modal = el("asset-transfer-history-modal");
    const box = modal.querySelector(".asset-transfer-history-box");
    const header = modal.querySelector(".asset-transfer-history-header");
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
        modal.classList.add("asset-transfer-history-dragging");
      }
      event.preventDefault();
      closeDrag.dy = Math.max(0, dy);
      box.style.transform = `translateY(${closeDrag.dy}px)`;
    }, { passive: false });
    function endCloseDrag(event, cancelled = false) {
      if (!closeDrag || (event && event.pointerId !== closeDrag.id)) return;
      const current = closeDrag;
      closeDrag = null;
      try { header.releasePointerCapture(current.id); } catch {}
      modal.classList.remove("asset-transfer-history-dragging");
      if (!cancelled && current.active && current.dy > 100) {
        box.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
        box.style.transform = "translateY(100dvh)";
        window.setTimeout(() => {
          if (isAssetTransferHistoryOpen()) closeAssetTransferHistory();
        }, 220);
        return;
      }
      if (!current.active) return;
      box.style.transition = "transform 180ms ease";
      box.style.transform = "translateY(0)";
      window.setTimeout(() => {
        if (
          !isAssetTransferHistoryOpen()
        ) return;
        box.style.transition = "";
        box.style.transform = "";
      }, 190);
    }
    header.addEventListener("pointerup", (event) => endCloseDrag(event));
    header.addEventListener("pointercancel", (event) => endCloseDrag(event, true));
  }

  function initMobileWatchlistGestures() {
    const modal = el("watchlist-modal");
    const box = modal.querySelector(".watchlist-modal-box");
    const header = modal.querySelector(".watchlist-modal-header");
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
        finishWatchlistOpening();
        modal.classList.add("watchlist-dragging");
      }
      event.preventDefault();
      closeDrag.dy = Math.max(0, dy);
      box.style.transform = `translateY(${closeDrag.dy}px)`;
    }, { passive: false });
    function endWatchlistCloseDrag(event, cancelled = false) {
      if (!closeDrag || (event && event.pointerId !== closeDrag.id)) return;
      const current = closeDrag;
      closeDrag = null;
      try { header.releasePointerCapture(current.id); } catch {}
      modal.classList.remove("watchlist-dragging");
      if (!cancelled && current.active && current.dy > 100) {
        closeWatchlist({ fromDrag: true });
        return;
      }
      if (!current.active) return;
      box.style.transition = "transform 180ms ease";
      box.style.transform = "translateY(0)";
      window.setTimeout(() => {
        if (!isWatchlistOpen() || modal.classList.contains("watchlist-closing")) return;
        box.style.transition = "";
        box.style.transform = "";
      }, 190);
    }
    header.addEventListener("pointerup", (event) => endWatchlistCloseDrag(event));
    header.addEventListener("pointercancel", (event) => endWatchlistCloseDrag(event, true));
  }

  function initHubMenuCalendar() {
    const calendarModal = el("calendar-modal");
    const watchlistModal = el("watchlist-modal");
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
    el("hub-watchlist-open").addEventListener("click", openWatchlist);
    el("hub-calendar-open").addEventListener("click", openCalendar);
    // Expand/collapse an accordion section to its exact content height so the
    // easing settles on-screen instead of being cut off mid-curve (which reads
    // as an abrupt "snap"). scrollHeight ignores the max-height clamp, so it
    // always reports the true content height in either state.
    const setSectionOpen = (menu, open) => {
      if (open) {
        menu.classList.add("open");
        menu.style.maxHeight = `${menu.scrollHeight}px`;
      } else {
        menu.style.maxHeight = `${menu.scrollHeight}px`;
        void menu.offsetHeight; // lock the current height before collapsing to 0
        menu.classList.remove("open");
        menu.style.maxHeight = "0px";
      }
    };
    el("hub-account-toggle").addEventListener("click", () => {
      const menu = el("hub-account-menu");
      const toggle = el("hub-account-toggle");
      const willOpen = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(willOpen));
      menu.setAttribute("aria-hidden", String(!willOpen));
      setSectionOpen(menu, willOpen);
    });
    el("hub-account-menu").addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest("[data-account-view]")
        : null;
      if (!button) return;
      openAccount(String(button.dataset.accountView || "assets"));
    });
    el("hub-history-toggle").addEventListener("click", () => {
      const menu = el("hub-history-menu");
      const toggle = el("hub-history-toggle");
      const willOpen = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(willOpen));
      menu.setAttribute("aria-hidden", String(!willOpen));
      // release the account submenu's fixed clamp so it grows/shrinks to fit the
      // nested History section as it animates
      el("hub-account-menu").style.maxHeight = "none";
      setSectionOpen(menu, willOpen);
    });
    el("hub-update-button").addEventListener("click", checkForUpdate);
    el("update-close").addEventListener("click", closeUpdateModal);
    el("update-cancel").addEventListener("click", closeUpdateModal);
    el("update-confirm").addEventListener("click", startUpdate);
    el("update-modal").addEventListener("click", (event) => {
      if (event.target === el("update-modal")) closeUpdateModal();
    });
    el("calendar-close").addEventListener("click", closeCalendar);
    el("watchlist-close").addEventListener("click", closeWatchlist);
    watchlistModal.addEventListener("click", (event) => {
      if (event.target === watchlistModal) closeWatchlist();
    });
    const watchlistAddModal = el("watchlist-add-modal");
    watchlistAddModal.addEventListener("touchend", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest("#watchlist-add-calendar-prev, #watchlist-add-calendar-next")
        : null;
      if (!button) return;
      const now = Date.now();
      const isDoubleTap = now - lastWatchlistAddCalendarTouchAt < 360;
      lastWatchlistAddCalendarTouchAt = now;
      if (!isDoubleTap) return;
      event.preventDefault();
      button.click();
    }, { passive: false });
    el("watchlist-add-close").addEventListener("click", closeWatchlistAddModal);
    el("watchlist-add-cancel").addEventListener("click", closeWatchlistAddModal);
    watchlistAddModal.addEventListener("click", (event) => {
      if (event.target === watchlistAddModal) closeWatchlistAddModal();
    });
    el("watchlist-add-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitWatchlistSession();
    });
    el("watchlist-add-timeframe-button").addEventListener("click", (event) => {
      event.preventDefault();
      toggleWatchlistAddTimeframeOptions();
    });
    el("watchlist-add-timeframe-options").addEventListener("click", (event) => {
      const option = event.target && event.target.closest
        ? event.target.closest("[data-watchlist-timeframe]")
        : null;
      if (!option) return;
      setWatchlistAddTimeframe(option.dataset.watchlistTimeframe);
      closeWatchlistAddTimeframeOptions();
      el("watchlist-add-timeframe-button").focus();
    });
    el("watchlist-add-date-button").addEventListener("click", (event) => {
      event.preventDefault();
      toggleWatchlistAddCalendar();
    });
    el("watchlist-add-calendar-prev").addEventListener("click", () => {
      moveWatchlistAddCalendarMonth(-1);
    });
    el("watchlist-add-calendar-next").addEventListener("click", () => {
      moveWatchlistAddCalendarMonth(1);
    });
    const watchlistAddCalendarDays = el("watchlist-add-calendar-days");
    let watchlistAddMonthDrag = null;
    watchlistAddCalendarDays.addEventListener("pointerdown", (event) => {
      if (!mobileHubQuery.matches || !event.isPrimary) return;
      watchlistAddMonthDrag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        dx: 0,
        axis: null,
      };
    });
    window.addEventListener("pointermove", (event) => {
      if (!watchlistAddMonthDrag || event.pointerId !== watchlistAddMonthDrag.id) return;
      const dx = event.clientX - watchlistAddMonthDrag.startX;
      const dy = event.clientY - watchlistAddMonthDrag.startY;
      if (!watchlistAddMonthDrag.axis) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        watchlistAddMonthDrag.axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
        if (watchlistAddMonthDrag.axis === "x") {
          try { watchlistAddCalendarDays.setPointerCapture(event.pointerId); } catch {}
        }
      }
      if (watchlistAddMonthDrag.axis !== "x") return;
      event.preventDefault();
      watchlistAddMonthDrag.dx = dx;
      watchlistAddCalendarSuppressTapUntil = Date.now() + 500;
    }, { passive: false });
    function endWatchlistAddMonthDrag(event) {
      if (
        !watchlistAddMonthDrag
        || (event && event.pointerId !== watchlistAddMonthDrag.id)
      ) return;
      const current = watchlistAddMonthDrag;
      watchlistAddMonthDrag = null;
      try { watchlistAddCalendarDays.releasePointerCapture(current.id); } catch {}
      if (current.axis !== "x" || Math.abs(current.dx) < 42) return;
      moveWatchlistAddCalendarMonth(current.dx < 0 ? 1 : -1);
    }
    window.addEventListener("pointerup", endWatchlistAddMonthDrag);
    window.addEventListener("pointercancel", endWatchlistAddMonthDrag);
    watchlistAddCalendarDays.addEventListener("click", (event) => {
      const day = event.target && event.target.closest
        ? event.target.closest("[data-watchlist-history-date]")
        : null;
      if (!day || day.disabled) return;
      setWatchlistAddHistoryDate(day.dataset.watchlistHistoryDate);
      closeWatchlistAddCalendar();
      el("watchlist-add-date-button").focus();
    });
    watchlistAddCalendarDays.addEventListener("click", (event) => {
      if (Date.now() >= watchlistAddCalendarSuppressTapUntil) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    el("watchlist-add-history-time").addEventListener("input", (event) => {
      event.target.value = String(event.target.value || "").replace(/[^0-9:]/g, "").slice(0, 5);
    });
    el("watchlist-add-history-time").addEventListener("blur", (event) => {
      const normalized = normalizeWatchlistHistoryTime(event.target.value);
      if (normalized) event.target.value = normalized;
    });
    watchlistAddModal.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!el("watchlist-add-timeframe-options").classList.contains("hidden")) {
        closeWatchlistAddTimeframeOptions();
        el("watchlist-add-timeframe-button").focus();
      } else if (!el("watchlist-add-calendar").classList.contains("hidden")) {
        closeWatchlistAddCalendar();
        el("watchlist-add-date-button").focus();
      } else {
        closeWatchlistAddModal();
      }
    });
    document.addEventListener("pointerdown", (event) => {
      if (!isWatchlistAddOpen()) return;
      const target = event.target && event.target.closest ? event.target : null;
      if (!target || !target.closest("#watchlist-add-timeframe-control")) {
        closeWatchlistAddTimeframeOptions();
      }
      if (!target || !target.closest("#watchlist-add-date-picker")) {
        closeWatchlistAddCalendar();
      }
    }, { passive: true });
    const scriptChangeModal = el("script-change-modal");
    el("script-change-close").addEventListener("click", closeScriptChange);
    el("script-change-cancel").addEventListener("click", closeScriptChange);
    el("script-change-save").addEventListener("click", saveScriptChange);
    scriptChangeModal.addEventListener("click", (event) => {
      if (event.target === scriptChangeModal) closeScriptChange();
    });
    el("script-change-select-button").addEventListener("click", (event) => {
      event.preventDefault();
      toggleScriptChangeOptions();
    });
    el("script-change-options").addEventListener("click", (event) => {
      const option = event.target && event.target.closest
        ? event.target.closest("[data-script-change-value]")
        : null;
      if (!option) return;
      scriptChangeSelected = String(option.dataset.scriptChangeValue || "");
      syncScriptChangeSelection();
      closeScriptChangeOptions();
      el("script-change-select-button").focus();
    });
    scriptChangeModal.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!el("script-change-options").classList.contains("hidden")) {
        closeScriptChangeOptions();
        el("script-change-select-button").focus();
      } else {
        closeScriptChange();
      }
    });
    document.addEventListener("pointerdown", (event) => {
      if (!isScriptChangeOpen()) return;
      const target = event.target && event.target.closest ? event.target : null;
      if (target && target.closest("#script-change-select")) return;
      closeScriptChangeOptions();
    }, { passive: true });
    el("watchlist-search").addEventListener("input", (event) => {
      watchlistSearch = String(event.target.value || "");
      renderWatchlist(true);
    });
    el("watchlist-exchange-filters").addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest("[data-watchlist-exchange]")
        : null;
      if (!button) return;
      watchlistExchange = String(button.dataset.watchlistExchange || "all");
      renderWatchlist(true);
    });
    el("watchlist-market-filters").addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest("[data-watchlist-market]")
        : null;
      if (!button) return;
      watchlistMarketFilter = String(button.dataset.watchlistMarket || "all");
      renderWatchlist(true);
    });
    watchlistModal.querySelector(".watchlist-list-header").addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest("[data-watchlist-sort]")
        : null;
      if (!button) return;
      const field = String(button.dataset.watchlistSort || "turnover_24h");
      if (watchlistSort === field) watchlistSortDescending = !watchlistSortDescending;
      else {
        watchlistSort = field;
        watchlistSortDescending = field !== "symbol";
      }
      renderWatchlist(true);
    });
    const watchlistList = el("watchlist-list");
    watchlistList.addEventListener("scroll", scheduleWatchlistWindowRender, { passive: true });
    watchlistList.addEventListener("click", (event) => {
      const addButton = event.target && event.target.closest
        ? event.target.closest("[data-watchlist-add-symbol]")
        : null;
      if (addButton) {
        const row = addButton.closest(".watchlist-row");
        openWatchlistAddModal(row && row._watchlistRow);
        return;
      }
      const button = event.target && event.target.closest
        ? event.target.closest("[data-watchlist-favorite-symbol]")
        : null;
      if (button) toggleWatchlistFavorite(button);
    });
    window.addEventListener("resize", () => {
      if (isWatchlistOpen()) scheduleWatchlistWindowRender();
    });
    calendarModal.addEventListener("click", (event) => {
      if (event.target === calendarModal) closeCalendar();
    });
    el("calendar-prev").addEventListener("click", () => moveCalendarMonth(-1));
    el("calendar-next").addEventListener("click", () => moveCalendarMonth(1));
    el("calendar-today").addEventListener("click", () => {
      const now = new Date();
      calendarMonth = new Date(now.getFullYear(), now.getMonth(), 1);
      calendarSelectedDate = null;
      calendarAddOpen = false;
      calendarEvents = [];
      renderCalendar();
      loadCalendarEvents();
    });
    el("calendar-grid").addEventListener("click", (event) => {
      const day = event.target && event.target.closest
        ? event.target.closest("[data-calendar-date]")
        : null;
      if (!day) return;
      if (calendarAddPending) return;
      calendarSelectedDate = day.dataset.calendarDate || null;
      calendarAddOpen = false;
      calendarAddText = "";
      calendarAddStatus = "";
      calendarAddError = false;
      closeCalendarAddSessionMenu();
      renderCalendar();
    });
    el("calendar-add-toggle").addEventListener("click", () => {
      if (calendarAddPending) return;
      calendarAddOpen = !calendarAddOpen;
      if (!calendarAddOpen) closeCalendarAddSessionMenu();
      renderCalendarAddControls();
      if (calendarAddOpen) {
        window.requestAnimationFrame(() => el("calendar-add-input").focus());
      }
    });
    el("calendar-add-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitCalendarEvent();
    });
    el("calendar-add-input").addEventListener("input", (event) => {
      calendarAddText = event.target.value;
      renderCalendarAddControls();
    });
    el("calendar-add-input").addEventListener("compositionstart", () => {
      calendarAddComposing = true;
    });
    el("calendar-add-input").addEventListener("compositionend", (event) => {
      calendarAddComposing = false;
      calendarAddText = event.target.value;
      renderCalendarAddControls();
    });
    el("calendar-add-input").addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.isComposing || calendarAddComposing) return;
      event.preventDefault();
      el("calendar-add-form").requestSubmit();
    });
    el("calendar-add-session-button").addEventListener("click", () => {
      if (calendarAddPending || !sessions.length) return;
      calendarAddSessionMenuOpen = !calendarAddSessionMenuOpen;
      renderCalendarAddControls();
    });
    el("calendar-add-session-options").addEventListener("click", (event) => {
      const option = event.target && event.target.closest
        ? event.target.closest("[data-calendar-add-session]")
        : null;
      if (!option || calendarAddPending) return;
      const sessionIdValue = String(option.dataset.calendarAddSession || "");
      if (sessionIdValue === "__auto__") {
        calendarAddSessionIds.clear();
        calendarAddSessionMenuOpen = false;
      } else if (calendarAddSessionIds.has(sessionIdValue)) {
        calendarAddSessionIds.delete(sessionIdValue);
      } else {
        calendarAddSessionIds.add(sessionIdValue);
      }
      renderCalendarAddControls();
    });
    el("calendar-detail-events").addEventListener("click", (event) => {
      const sessionToggle = event.target && event.target.closest
        ? event.target.closest("[data-calendar-event-sessions]")
        : null;
      if (sessionToggle) {
        const eventId = String(sessionToggle.dataset.calendarEventSessions || "");
        if (calendarExpandedSessionEvents.has(eventId)) {
          calendarExpandedSessionEvents.delete(eventId);
        } else {
          calendarExpandedSessionEvents.add(eventId);
        }
        renderCalendarDetails();
        return;
      }
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
      if (!target || !target.closest(".calendar-add-session-select")) {
        closeCalendarAddSessionMenu();
      }
      if (target && target.closest(".calendar-forecast-bubble, [data-calendar-forecast-event]")) return;
      closeCalendarForecastBubbles();
    }, { passive: true });
    window.addEventListener("resize", fitCalendarForecastBubbles, { passive: true });
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", fitCalendarForecastBubbles, { passive: true });
    }
    initMobileHubMenuSwipe();
    initMobileHubMenuEdgeSwipe();
    initMobileCalendarGestures();
    initMobileWatchlistGestures();
    const accountTabs = document.querySelector(".account-tabs");
    let accountTabGesture = null;
    let suppressAccountTabClickUntil = 0;
    function accountTabFromEvent(event) {
      return event.target && event.target.closest
        ? event.target.closest("[data-account-view]")
        : null;
    }
    accountTabs.addEventListener("pointerdown", (event) => {
      if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return;
      const button = accountTabFromEvent(event);
      if (!button) return;
      if (event.pointerType === "mouse") return;
      accountTabGesture = {
        id: event.pointerId,
        button,
        startX: event.clientX,
        startY: event.clientY,
        moved: false,
      };
    });
    accountTabs.addEventListener("pointermove", (event) => {
      if (!accountTabGesture || event.pointerId !== accountTabGesture.id) return;
      if (
        Math.abs(event.clientX - accountTabGesture.startX) > 8
        || Math.abs(event.clientY - accountTabGesture.startY) > 8
      ) {
        accountTabGesture.moved = true;
      }
    }, { passive: true });
    accountTabs.addEventListener("pointerup", (event) => {
      if (!accountTabGesture || event.pointerId !== accountTabGesture.id) return;
      const gesture = accountTabGesture;
      accountTabGesture = null;
      if (gesture.moved) return;
      event.preventDefault();
      event.stopPropagation();
      suppressAccountTabClickUntil = Date.now() + 400;
      const view = String(gesture.button.dataset.accountView || "assets");
      if (view !== accountView || accountViewIsDrilledIn(view)) {
        switchAccountView(view, { load: true, animate: true });
      }
    });
    accountTabs.addEventListener("pointercancel", () => {
      accountTabGesture = null;
    });
    accountTabs.addEventListener("click", (event) => {
      if (Date.now() < suppressAccountTabClickUntil) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const button = accountTabFromEvent(event);
      if (!button) return;
      const view = String(button.dataset.accountView || "assets");
      if (view !== accountView || accountViewIsDrilledIn(view)) {
        switchAccountView(view, { load: true, animate: true });
      }
    });
    el("history-import-dropzone").addEventListener("click", () => {
      if (!historyImportBusy) el("history-import-files").click();
    });
    el("history-import-help-button").addEventListener("click", (event) => {
      event.stopPropagation();
      setHistoryImportHelpOpen(!el("history-import-help").classList.contains("show"));
    });
    el("history-import-dropzone").addEventListener("dragover", (event) => {
      event.preventDefault();
      if (!historyImportBusy) el("history-import-dropzone").classList.add("dragover");
    });
    el("history-import-dropzone").addEventListener("dragleave", () => {
      el("history-import-dropzone").classList.remove("dragover");
    });
    el("history-import-dropzone").addEventListener("drop", (event) => {
      event.preventDefault();
      el("history-import-dropzone").classList.remove("dragover");
      if (!historyImportBusy) previewHistoryImport(event.dataTransfer && event.dataTransfer.files);
    });
    el("history-import-files").addEventListener("change", (event) => {
      previewHistoryImport(event.target.files);
    });
    el("history-import-preview").addEventListener("click", (event) => {
      const removeButton = event.target && event.target.closest
        ? event.target.closest("[data-history-import-remove]")
        : null;
      if (removeButton) {
        removeHistoryImportPreviewFile(String(removeButton.dataset.historyImportRemove || ""));
        return;
      }
      const option = event.target && event.target.closest
        ? event.target.closest("[data-history-import-option]")
        : null;
      if (option) {
        const fileId = String(option.dataset.historyImportOption || "");
        const field = String(option.dataset.field || "");
        if (historyImportSelections[fileId] && ["account", "source_timezone"].includes(field)) {
          historyImportSelections[fileId][field] = String(option.dataset.value || "");
          if (field === "source_timezone") {
            historyImportSelections[fileId].timezone_confirmed = false;
          }
          closeHistoryImportSelects();
          renderHistoryImportPreview();
        }
        return;
      }
      const button = event.target && event.target.closest
        ? event.target.closest("[data-history-import-select]")
        : null;
      if (!button) return;
      const control = button.closest(".history-import-select");
      const options = control && control.querySelector(".history-import-select-options");
      if (!control || !options) return;
      const willOpen = options.classList.contains("hidden");
      closeHistoryImportSelects(control);
      control.classList.toggle("open", willOpen);
      options.classList.toggle("hidden", !willOpen);
      button.setAttribute("aria-expanded", String(willOpen));
    });
    el("history-import-preview").addEventListener("change", (event) => {
      const checkbox = event.target && event.target.closest
        ? event.target.closest("[data-history-import-timezone-confirm]")
        : null;
      if (!checkbox) return;
      const fileId = String(checkbox.dataset.historyImportTimezoneConfirm || "");
      if (!historyImportSelections[fileId]) return;
      historyImportSelections[fileId].timezone_confirmed = checkbox.checked;
      renderHistoryImportActions();
    });
    el("history-import-submit").addEventListener("click", submitHistoryImport);
    el("history-import-log-list").addEventListener("click", (event) => {
      const deleteButton = event.target && event.target.closest
        ? event.target.closest("[data-history-import-delete]")
        : null;
      if (deleteButton) {
        deleteHistoryImport(
          String(deleteButton.dataset.historyImportDelete || ""),
          deleteButton,
        );
        return;
      }
      const button = event.target && event.target.closest
        ? event.target.closest("[data-history-import-retry]")
        : null;
      if (!button) return;
      retryHistoryImportEnrichment(String(button.dataset.historyImportRetry || ""));
    });
    document.addEventListener("click", (event) => {
      if (!event.target || !event.target.closest(".history-import-select")) {
        closeHistoryImportSelects();
      }
      if (!event.target || !event.target.closest("#history-import-help")) {
        setHistoryImportHelpOpen(false);
      }
    });
    el("assets-close").addEventListener("click", closeAssets);
    el("assets-back").addEventListener("click", () => {
      if (accountView === "pnl") {
        selectedPnlExchange = null;
        renderPnlView();
        scrollPnlToTop();
        return;
      }
      selectedAssetExchange = null;
      renderAssetsView();
      el("assets-body").scrollTop = 0;
    });
    el("assets-refresh").addEventListener("click", () => {
      if (accountView === "positions") loadPositions(true);
      else if (accountView === "position-history") loadPositionHistory(false, true);
      else if (accountView === "orders") loadOrderHistory(false, true);
      else if (accountView === "pnl") loadPnl();
      else if (accountView === "assets") loadAssets(true);
    });
    document.querySelectorAll("[data-pnl-days]").forEach((button) => {
      button.addEventListener("click", () => {
        const rawDays = String(button.dataset.pnlDays || "");
        const days = rawDays === "all" ? "all" : Number(rawDays);
        if ((days !== "all" && !Number.isInteger(days)) || days === pnlPeriodDays) return;
        pnlPeriodDays = days;
        // keep the current rows (preserveContent) so the list updates in place
        // via syncPnlList's keyed diff instead of clearing → no empty flash
        document.querySelectorAll("[data-pnl-days]").forEach((candidate) => {
          const candidateRaw = String(candidate.dataset.pnlDays || "");
          const candidateDays = candidateRaw === "all" ? "all" : Number(candidateRaw);
          const active = candidateDays === pnlPeriodDays;
          candidate.classList.toggle("active", active);
          candidate.setAttribute("aria-pressed", String(active));
        });
        historyFlipBegin();
        loadPnl();
      });
    });
    el("position-history-filters").addEventListener("submit", (event) => {
      event.preventDefault();
      resetPositionHistory();
      loadPositionHistory(false);
    });
    el("position-history-account").addEventListener("change", () => {
      historyFlipBegin();
      resetPositionHistory();
      loadPositionHistory(false);
    });
    el("position-history-back").addEventListener("click", () => {
      navigateHistoryBack("position");
    });
    el("position-history-more").addEventListener("click", () => {
      loadPositionHistory(true);
    });
    el("order-history-filters").addEventListener("submit", (event) => {
      event.preventDefault();
      resetOrderHistory();
      loadOrderHistory(false);
    });
    ["account", "status"].forEach((field) => {
      el(`order-history-${field}`).addEventListener("change", () => {
        historyFlipBegin();
        resetOrderHistory();
        loadOrderHistory(false);
      });
    });
    setupHistoryCustomSelect("position-history-account");
    setupHistoryCustomSelect("order-history-account");
    setupHistoryCustomSelect("order-history-status");
    el("order-history-back").addEventListener("click", () => {
      navigateHistoryBack("order");
    });
    el("order-history-more").addEventListener("click", () => {
      loadOrderHistory(true);
    });
    const assetsModal = el("assets-modal");
    assetsModal.addEventListener("pointerdown", (event) => {
      const target = event.target && event.target.closest ? event.target : null;
      const button = target && target.closest(".position-pnl-value");
      if (!button) return;
      positionPnlPress = {
        button,
        pointerId: event.pointerId,
        startedAt: performance.now(),
        x: event.clientX,
        y: event.clientY,
        moved: false,
      };
    }, { passive: true });
    assetsModal.addEventListener("pointermove", (event) => {
      if (!positionPnlPress || positionPnlPress.pointerId !== event.pointerId) return;
      if (Math.hypot(
        event.clientX - positionPnlPress.x,
        event.clientY - positionPnlPress.y,
      ) > 8) {
        positionPnlPress.moved = true;
      }
    }, { passive: true });
    assetsModal.addEventListener("pointerup", (event) => {
      if (!positionPnlPress || positionPnlPress.pointerId !== event.pointerId) return;
      if (positionPnlPress.moved || performance.now() - positionPnlPress.startedAt > 450) {
        suppressPositionPnlClickUntil = Date.now() + 500;
      }
      positionPnlPress = null;
    }, { passive: true });
    assetsModal.addEventListener("pointercancel", () => {
      positionPnlPress = null;
      suppressPositionPnlClickUntil = Date.now() + 500;
    }, { passive: true });
    assetsModal.addEventListener("click", (event) => {
      if (Date.now() < suppressAccountTabClickUntil) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const target = event.target && event.target.closest ? event.target : null;
      const pnlButton = target && target.closest(".position-pnl-value");
      if (pnlButton) {
        event.preventDefault();
        event.stopPropagation();
        if (Date.now() >= suppressPositionPnlClickUntil) {
          showPositionPnlPopover(pnlButton);
        }
        return;
      }
      if (event.target === assetsModal) closeAssets();
    });
    document.addEventListener("pointerdown", (event) => {
      if (!activePositionPnlButton) return;
      const target = event.target && event.target.closest ? event.target : null;
      if (
        target
        && (
          target.closest("#position-pnl-popover")
          || target.closest(".position-pnl-value")
        )
      ) return;
      closePositionPnlPopover();
    }, { passive: true });
    window.addEventListener("resize", closePositionPnlPopover, { passive: true });
    window.addEventListener("resize", schedulePnlListSizing, { passive: true });
    el("asset-transfer-close").addEventListener("click", closeAssetTransfer);
    el("asset-transfer-history-close").addEventListener(
      "click",
      closeAssetTransferHistory,
    );
    el("asset-transfer-history-refresh").addEventListener("click", () => {
      loadAssetTransferHistory({ force: true });
    });
    el("asset-transfer-history-more").addEventListener("click", () => {
      loadAssetTransferHistory({ append: true });
    });
    const transferHistoryModal = el("asset-transfer-history-modal");
    transferHistoryModal.addEventListener("click", (event) => {
      if (event.target === transferHistoryModal) closeAssetTransferHistory();
    });
    el("asset-transfer-back").addEventListener("click", () => {
      if (assetTransferMode === "review") {
        assetTransferReview = null;
        setAssetTransferMode("options");
        updateAssetTransferForm();
        return;
      }
      closeAssetTransfer();
    });
    el("asset-transfer-submit").addEventListener("click", () => {
      if (assetTransferMode === "options") reviewAssetTransfer();
      else if (assetTransferMode === "review") executeAssetTransfer();
      else if (assetTransferMode === "result") closeAssetTransfer();
    });
    el("asset-transfer-form").addEventListener("submit", (event) => {
      event.preventDefault();
      if (assetTransferMode === "options" && !el("asset-transfer-submit").disabled) {
        reviewAssetTransfer();
      }
    });
    el("asset-transfer-asset").addEventListener("change", () => {
      el("asset-transfer-amount").value = "";
      setAssetTransferError("");
      syncAssetTransferDropdown("asset");
      updateAssetTransferForm();
    });
    el("asset-transfer-destination").addEventListener("change", () => {
      setAssetTransferError("");
      syncAssetTransferDropdown("destination");
      updateAssetTransferForm();
    });
    el("asset-transfer-account").addEventListener("change", () => {
      setAssetTransferError("");
      syncAssetTransferDropdown("account");
      renderAssetTransferDestinations();
      updateAssetTransferForm();
    });
    initAssetTransferDropdown("asset");
    initAssetTransferDropdown("account");
    initAssetTransferDropdown("destination");
    el("asset-transfer-amount").addEventListener("input", () => {
      setAssetTransferError("");
      updateAssetTransferForm();
    });
    el("asset-transfer-max").addEventListener("click", () => {
      const item = selectedAssetTransferItem();
      if (!item || !item.transferable) return;
      el("asset-transfer-amount").value = item.available;
      setAssetTransferError("");
      updateAssetTransferForm();
    });
    const transferModal = el("asset-transfer-modal");
    transferModal.addEventListener("click", (event) => {
      if (event.target === transferModal && assetTransferMode !== "review") {
        closeAssetTransfer();
      }
    });
    document.addEventListener("pointerdown", (event) => {
      const target = event.target && event.target.closest ? event.target : null;
      if (target && target.closest(".asset-transfer-select")) return;
      closeAssetTransferDropdown();
    }, { passive: true });
    const positionOpenTransferDropdown = () => {
      ["asset", "account", "destination"].forEach((name) => {
        positionAssetTransferDropdown(name);
      });
    };
    window.addEventListener("resize", positionOpenTransferDropdown, { passive: true });
    if (window.visualViewport) {
      window.visualViewport.addEventListener(
        "resize",
        positionOpenTransferDropdown,
        { passive: true },
      );
    }
    const transferBody = el("asset-transfer-body");
    if (transferBody) {
      transferBody.addEventListener(
        "scroll",
        () => closeAssetTransferDropdown(),
        { passive: true },
      );
    }
    initMobileAssetsGestures();
    initMobileAssetTransferHistoryGestures();
    initMobileAccountPager();
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
    if (isCalendarOpen() && calendarSelectedDate) renderCalendarAddControls();
  }

  function runnerButtons(s) {
    const r = s.runner || "stopped";
    const dataReady = !!s.history_ready;
    if (r === "running" || r === "starting") {
      return `<button class="btn" data-runner="stop">Stop</button>` +
             `<button class="btn" data-runner="restart"${dataReady ? "" : " disabled"}>Restart</button>`;
    }
    const canStart = dataReady && Boolean(s.script_name);
    const title = s.script_name ? "" : ' title="Select a script first"';
    return `<button class="btn btn-primary" data-runner="start"${canStart ? "" : " disabled"}${title}>Start</button>`;
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

  function positionDataSincePopover(wrap) {
    const popover = wrap && wrap.querySelector(".data-since-popover");
    if (!popover) return;
    const carousel = wrap.closest("#sessions > tbody");
    const carouselRect = carousel ? carousel.getBoundingClientRect() : null;
    const viewportPadding = 12;
    const leftBoundary = Math.max(
      viewportPadding,
      carouselRect ? carouselRect.left + 2 : viewportPadding,
    );
    const rightBoundary = Math.min(
      window.innerWidth - viewportPadding,
      carouselRect ? carouselRect.right - 2 : window.innerWidth - viewportPadding,
    );
    popover.style.right = "auto";
    popover.style.left = "0px";
    popover.style.maxWidth = `${Math.max(120, rightBoundary - leftBoundary)}px`;
    const rect = popover.getBoundingClientRect();
    const minOffset = leftBoundary - rect.left;
    const maxOffset = rightBoundary - rect.right;
    const offset = Math.min(maxOffset, Math.max(minOffset, 0));
    popover.style.left = `${Math.round(offset)}px`;
  }

  function runnerStatusText(session, now = Date.now()) {
    const runner = String((session && session.runner) || "stopped");
    const phase = String((session && session.runner_phase) || "");
    if (runner === "stopped" || runner === "crashed") return runner;
    if (phase === "prerun_active") return "warming up";
    if (phase === "prerun_scheduled") {
      const target = Number(session && session.next_prerun_at);
      if (Number.isFinite(target) && target > 0) {
        const remaining = Math.ceil((target - now) / 1000);
        if (remaining <= 0) return "warming up";
        if (remaining <= 5) return `warming up in ${remaining}s`;
      }
      return "running";
    }
    if (runner === "starting") return "warming up";
    return "running";
  }

  function closeRunnerStatusTooltips(exceptAnchor = null) {
    document.querySelectorAll(".runner-status-anchor.show-runner-status").forEach((anchor) => {
      if (anchor !== exceptAnchor) anchor.classList.remove("show-runner-status");
    });
  }

  function updateRunnerStatusTooltips() {
    const now = Date.now();
    document.querySelectorAll("#sessions tbody tr[data-session-id]").forEach((tr) => {
      const session = sessions.find((item) => sessionId(item) === tr.dataset.sessionId);
      if (!session) return;
      const anchor = tr.querySelector('[data-act="runner-status"]');
      const tooltip = tr.querySelector('[data-field="runner-status-tooltip"]');
      const text = runnerStatusText(session, now);
      setText(tooltip, text);
      const led = tr.querySelector('[data-field="runner-led"]');
      if (led) led.classList.toggle("led-warming", session.runner_phase === "prerun_active");
      if (anchor) anchor.setAttribute("aria-label", `Runner status: ${text}`);
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

  function setAnimatedScriptOptions(control, button, options, expanded) {
    if (!control || !button || !options) return;
    button.setAttribute("aria-expanded", String(expanded));
    if (expanded) {
      options.classList.remove("hidden");
      options.style.maxHeight = "0px";
      void options.offsetHeight;
      control.classList.add("open");
      const limit = Math.min(320, window.innerHeight * 0.48);
      options.style.maxHeight = `${Math.min(options.scrollHeight, limit)}px`;
      return;
    }
    options.style.maxHeight = `${options.getBoundingClientRect().height}px`;
    void options.offsetHeight;
    control.classList.remove("open");
    options.classList.add("hidden");
    options.style.maxHeight = "0px";
  }

  function openScriptDropdown() {
    const nodes = scriptSelectNodes();
    if (!nodes.control || !nodes.button || !nodes.options) return;
    const currentIndex = scriptOptions.indexOf(selectedScriptValue());
    scriptActiveIndex = currentIndex >= 0 ? currentIndex : 0;
    renderScriptOptions();
    setAnimatedScriptOptions(nodes.control, nodes.button, nodes.options, true);
    nodes.options.setAttribute("tabindex", "-1");
  }

  function closeScriptDropdown() {
    const nodes = scriptSelectNodes();
    if (!nodes.control || !nodes.button || !nodes.options) return;
    setAnimatedScriptOptions(nodes.control, nodes.button, nodes.options, false);
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

  function isScriptChangeOpen() {
    return !el("script-change-modal").classList.contains("hidden");
  }

  function scriptChangeSession() {
    return sessions.find((session) => sessionId(session) === scriptChangeSessionId) || null;
  }

  function syncScriptChangeSelection() {
    const session = scriptChangeSession();
    const current = String(session && session.script_name || "");
    el("script-change-select-label").textContent = scriptChangeSelected || "Select script";
    el("script-change-select-button").title = scriptChangeSelected || "Select script";
    el("script-change-options").querySelectorAll("[data-script-change-value]").forEach((option) => {
      const selected = option.dataset.scriptChangeValue === scriptChangeSelected;
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-selected", String(selected));
    });
    el("script-change-save").disabled = (
      scriptChangePending || !scriptChangeSelected || scriptChangeSelected === current
    );
  }

  function renderScriptChangeOptions(loading = false) {
    const options = el("script-change-options");
    if (loading) {
      options.innerHTML = '<div class="script-select-empty">Loading scripts...</div>';
    } else if (!scriptOptions.length) {
      options.innerHTML = '<div class="script-select-empty">No scripts found</div>';
    } else {
      options.innerHTML = scriptOptions.map((script) => (
        `<button type="button" role="option" class="script-select-option" `
        + `data-script-change-value="${esc(script)}" title="${esc(script)}">${esc(script)}</button>`
      )).join("");
    }
    syncScriptChangeSelection();
  }

  function closeScriptChangeOptions() {
    setAnimatedScriptOptions(
      el("script-change-select"),
      el("script-change-select-button"),
      el("script-change-options"),
      false,
    );
  }

  function toggleScriptChangeOptions() {
    const options = el("script-change-options");
    if (!options.classList.contains("hidden")) {
      closeScriptChangeOptions();
      return;
    }
    renderScriptChangeOptions();
    setAnimatedScriptOptions(
      el("script-change-select"),
      el("script-change-select-button"),
      options,
      true,
    );
  }

  function setScriptChangePending(pending) {
    scriptChangePending = Boolean(pending);
    el("script-change-close").disabled = scriptChangePending;
    el("script-change-cancel").disabled = scriptChangePending;
    el("script-change-select-button").disabled = scriptChangePending;
    el("script-change-save").textContent = scriptChangePending ? "Saving" : "Save";
    syncScriptChangeSelection();
  }

  async function openScriptChange(id) {
    const session = sessions.find((item) => sessionId(item) === id);
    if (!session) return;
    const runner = String(session.runner || "stopped");
    if (runner === "running" || runner === "starting") return;
    scriptChangeSessionId = id;
    scriptChangeSelected = String(session.script_name || "");
    setScriptChangePending(false);
    closeScriptChangeOptions();
    el("script-change-error").textContent = "";
    el("script-change-session").textContent = `${session.symbol} · ${session.timeframe} · ${String(session.exchange || "").toUpperCase()}`;
    el("script-change-title").textContent = session.script_name ? "Change script" : "Select script";
    renderScriptChangeOptions(true);
    const modal = el("script-change-modal");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    lockBodyScroll();
    await loadScripts();
    if (!isScriptChangeOpen() || scriptChangeSessionId !== id) return;
    renderScriptChangeOptions();
    el("script-change-select-button").focus();
  }

  function closeScriptChange() {
    if (!isScriptChangeOpen() || scriptChangePending) return;
    closeScriptChangeOptions();
    el("script-change-modal").classList.add("hidden");
    el("script-change-modal").setAttribute("aria-hidden", "true");
    el("script-change-error").textContent = "";
    scriptChangeSessionId = null;
    scriptChangeSelected = "";
    unlockBodyScroll();
  }

  async function saveScriptChange() {
    if (!scriptChangeSessionId || !scriptChangeSelected || scriptChangePending) return;
    const id = scriptChangeSessionId;
    setScriptChangePending(true);
    closeScriptChangeOptions();
    el("script-change-error").textContent = "";
    try {
      await api(`/api/sessions/${encodeURIComponent(id)}/script`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script_name: scriptChangeSelected }),
      });
      setScriptChangePending(false);
      closeScriptChange();
      await refresh();
    } catch (error) {
      setScriptChangePending(false);
      el("script-change-error").textContent = error && error.message
        ? error.message
        : "Script could not be changed.";
    }
  }

  function toggleDataSinceTooltip(tr) {
    const wrap = tr.querySelector(".data-badge-wrap");
    if (!wrap) return;
    const show = !wrap.classList.contains("show-since");
    closeDataSinceTooltips(wrap);
    if (show) positionDataSincePopover(wrap);
    wrap.classList.toggle("show-since", show);
  }

  // gear/cog line icon (matches the pencil/magnifier .icon line-icon style)
  const settingsGearIcon =
    `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">`
    + `<circle cx="12" cy="12" r="3"></circle>`
    + `<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>`
    + `</svg>`;

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
          `<button type="button" class="runner-status-anchor" data-act="runner-status" ` +
          `aria-label="Runner status">` +
            `<span data-field="runner-led" class="led" aria-hidden="true"></span>` +
            `<span data-field="runner-status-tooltip" class="runner-status-tooltip" role="status"></span>` +
          `</button>` +
        `</span></td>` +
      `<td data-label="Symbol" class="mono"><span data-field="symbol-cell" class="symbol-cell"></span></td>` +
      `<td data-label="TF" data-field="timeframe"></td>` +
      `<td data-label="Exchange"><span data-field="exchange-cell" class="exchange-cell"></span></td>` +
      `<td data-label="Script" class="mono script-cell">` +
        `<button type="button" class="session-script-button" data-act="script-edit">` +
          `<span data-field="script-name" class="session-script-label"></span>` +
          `<svg class="session-script-edit-icon" viewBox="0 0 24 24" aria-hidden="true">` +
            `<path d="M12 20h9"></path>` +
            `<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"></path>` +
          `</svg>` +
        `</button>` +
      `</td>` +
      `<td data-label="Data" class="data-cell"><span class="data-controls">` +
        `<span class="data-badge-wrap">` +
          `<span data-field="collector-badge" class="badge data-badge" ` +
          `data-act="data-since"></span>` +
          `<span data-field="data-since-popover" class="data-since-popover"></span></span>` +
        `<button class="btn btn-icon data-edit-btn" data-act="data-edit" ` +
        `title="Data settings" aria-label="Data settings">` +
          settingsGearIcon +
        `</button>` +
        `<button class="btn btn-icon data-integrity-btn" data-act="data-integrity" ` +
        `title="Verify OHLCV data" aria-label="Verify OHLCV data">` +
          `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">` +
            `<circle cx="11" cy="11" r="8"></circle>` +
            `<path d="m21 21-4.3-4.3"></path>` +
          `</svg>` +
        `</button>` +
        `<span data-field="history-loading" class="muted">loading</span>` +
        `</span></td>` +
      `<td data-label="Last bar" class="muted">${lastBarCell({})}</td>` +
      `<td data-label="Webhook"><span class="cell-inline">` +
        `<input type="checkbox" data-act="webhook">` +
        `<button class="btn btn-icon" data-act="webhook-settings" title="Webhook URL">` + settingsGearIcon + `</button></span></td>` +
      `<td data-label="Telegram"><span class="cell-inline">` +
        `<input type="checkbox" data-act="telegram">` +
        `<button class="btn btn-icon" data-act="telegram-settings" title="Telegram bot">` + settingsGearIcon + `</button></span></td>` +
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
      else if (act === "data-integrity") openSettings(id, "data-integrity");
      else if (act === "runner-status") {
        e.preventDefault();
        e.stopPropagation();
        const anchor = target.closest(".runner-status-anchor");
        const show = anchor && !anchor.classList.contains("show-runner-status");
        closeRunnerStatusTooltips(anchor);
        if (anchor) anchor.classList.toggle("show-runner-status", Boolean(show));
      }
      else if (act === "script-edit") openScriptChange(id);
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
    const runnerControlsKey = `${runner}:${s.history_ready ? "ready" : "preparing"}:${s.script_name ? "script" : "no-script"}`;
    const collector = s.collector || "stopped";
    const wh = s.webhook || {};
    const exchange = (s.exchange || "").toUpperCase();

    const led = tr.querySelector('[data-field="runner-led"]');
    setClass(led, `led led-${runner}`);
    const statusText = runnerStatusText(s);
    if (led) led.classList.toggle("led-warming", s.runner_phase === "prerun_active");
    setText(tr.querySelector('[data-field="runner-status-tooltip"]'), statusText);
    const statusAnchor = tr.querySelector('[data-act="runner-status"]');
    if (statusAnchor) statusAnchor.setAttribute("aria-label", `Runner status: ${statusText}`);

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
    const scriptButton = tr.querySelector('[data-act="script-edit"]');
    const scriptEditable = runner !== "running" && runner !== "starting";
    const scriptName = String(s.script_name || "");
    setText(tr.querySelector('[data-field="script-name"]'), scriptName || "Select script");
    if (scriptButton) {
      setClass(
        scriptButton,
        `session-script-button${scriptName ? "" : " empty"}`,
      );
      scriptButton.disabled = !scriptEditable;
      scriptButton.title = scriptEditable
        ? (scriptName ? "Change script" : "Select script")
        : "Stop runner to change script";
      scriptButton.setAttribute("aria-label", scriptButton.title);
    }

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

  function setUpdateMessage(message, isError = false, autoClearMs = 0) {
    const node = el("hub-update-message");
    if (updateMessageTimer !== null) {
      clearTimeout(updateMessageTimer);
      updateMessageTimer = null;
    }
    node.textContent = String(message || "");
    node.classList.toggle("error", Boolean(isError));
    node.title = node.textContent;
    if (autoClearMs > 0 && node.textContent) {
      updateMessageTimer = window.setTimeout(() => {
        updateMessageTimer = null;
        node.textContent = "";
        node.title = "";
        node.classList.remove("error");
      }, autoClearMs);
    }
  }

  function updateInProgress(status) {
    return ["stopping", "updating", "restarting", "resuming_runners"].includes(status);
  }

  function setUpdateDetail(message, pending = false) {
    const node = el("update-detail");
    node.textContent = String(message || "").replace(/\s*\.{3}$/, "");
    node.classList.toggle("update-detail-pending", Boolean(pending));
  }

  function applyUpdateStatus(payload) {
    if (!payload || typeof payload !== "object") return;
    el("hub-version").textContent = String(payload.display || payload.commit || "-------");
    const state = payload.update && typeof payload.update === "object" ? payload.update : {};
    const status = String(state.status || "");
    const button = el("hub-update-button");
    if (updateRestartPending && typeof state.restart_required === "boolean") {
      updateRequiresRestart = state.restart_required;
    }
    if (updateRestartPending && state.message) {
      setUpdateDetail(state.message, updateInProgress(status));
    }
    if (updateInProgress(status)) {
      button.disabled = true;
      button.textContent = "Updating";
      setUpdateMessage(state.message || "Updating...");
      return;
    }
    button.disabled = false;
    button.textContent = "Update";
    if (status === "failed") {
      const failedAt = Number(state.updated_at || 0) * 1000;
      const remaining = Math.max(0, 5000 - (Date.now() - failedAt));
      if (remaining > 0) {
        setUpdateMessage(state.error || state.message || "Update failed.", true, remaining);
      } else {
        setUpdateMessage("");
      }
    } else if (status === "completed" && state.commit === payload.commit) {
      setUpdateMessage("");
    }
  }

  async function loadUpdateStatus() {
    try {
      const payload = await api("/api/update/status");
      applyUpdateStatus(payload);
      const status = String(payload.update && payload.update.status || "");
      if (updateRestartPending && status === "completed" && payload.update.commit === payload.commit) {
        showUpdateCompleted();
        return;
      }
      if (updateRestartPending && status === "failed") {
        updateRestartPending = false;
        showUpdateFailure(payload.update.error || payload.update.message || "Update failed.");
      }
      if (updateInProgress(status)) scheduleUpdatePoll();
    } catch (error) {
      if (updateRestartPending) {
        setUpdateDetail(
          updateRequiresRestart ? "Applying confirmed update" : "Applying frontend update",
          true,
        );
        scheduleUpdatePoll();
      }
    }
  }

  function scheduleUpdatePoll() {
    if (updatePollTimer !== null) return;
    updatePollTimer = window.setTimeout(() => {
      updatePollTimer = null;
      loadUpdateStatus();
    }, 1000);
  }

  function closeUpdateModal() {
    if (updateRestartPending) return;
    if (updateCompleteTimer !== null) {
      clearTimeout(updateCompleteTimer);
      updateCompleteTimer = null;
    }
    const modal = el("update-modal");
    modal.classList.add("hidden");
    modal.classList.remove("updating", "completed");
    modal.setAttribute("aria-hidden", "true");
    updateConfirmationToken = "";
    updateRequiresRestart = true;
  }

  function openUpdateConfirmation(result) {
    closeHubMenu();
    setUpdateMessage("");
    updateConfirmationToken = String(result.confirmation_token || "");
    updateRequiresRestart = result.restart_required !== false;
    const current = result.version
      ? `${result.version} · ${result.commit}`
      : result.commit;
    const target = result.target_version
      ? `${result.target_version} · ${result.target_commit}`
      : result.target_commit;
    el("update-revisions").textContent = `${current}  →  ${target}`;
    setUpdateDetail(
      `${result.branch} · ${result.behind} new commit${result.behind === 1 ? "" : "s"}`,
    );
    el("update-error").textContent = "";
    el("update-confirm-message").textContent = updateRequiresRestart
      ? "Running sessions will stop while the update is installed, then resume automatically."
      : "Only frontend files changed. Running sessions will continue without interruption.";
    el("update-confirm").disabled = false;
    el("update-confirm").textContent = "Update";
    el("update-cancel").textContent = "Cancel";
    const modal = el("update-modal");
    modal.classList.remove("hidden", "updating", "completed");
    modal.setAttribute("aria-hidden", "false");
  }

  function showUpdateCompleted() {
    updateRestartPending = false;
    setUpdateMessage("");
    const modal = el("update-modal");
    modal.classList.remove("hidden", "updating");
    modal.classList.add("completed");
    modal.setAttribute("aria-hidden", "false");
    el("update-confirm-message").textContent = "Update completed.";
    setUpdateDetail(updateRequiresRestart
      ? "Data service restarted and running sessions were restored."
      : "Frontend refreshed without interrupting running sessions.");
    el("update-error").textContent = "";
    if (updateCompleteTimer !== null) clearTimeout(updateCompleteTimer);
    updateCompleteTimer = window.setTimeout(() => {
      updateCompleteTimer = null;
      closeUpdateModal();
      window.location.reload();
    }, 4000);
  }

  function showUpdateFailure(message) {
    const modal = el("update-modal");
    modal.classList.remove("hidden", "updating", "completed");
    modal.setAttribute("aria-hidden", "false");
    el("update-confirm-message").textContent = "The update could not be completed.";
    el("update-detail").classList.remove("update-detail-pending");
    el("update-error").textContent = String(message || "Update failed.");
    el("update-confirm").disabled = true;
    el("update-confirm").textContent = "Update";
    el("update-cancel").textContent = "Close";
    setUpdateMessage(message || "Update failed.", true, 5000);
  }

  async function checkForUpdate() {
    const button = el("hub-update-button");
    button.disabled = true;
    button.textContent = "Checking";
    setUpdateMessage("Checking for updates...");
    try {
      const result = await api("/api/update/check", { method: "POST" });
      el("hub-version").textContent = String(result.display || result.commit || "-------");
      if (!result.available) {
        const blocked = Boolean(result.blocked_reason);
        setUpdateMessage(
          result.blocked_reason || "No updates available.",
          blocked,
          5000,
        );
        return;
      }
      if (!result.can_update) {
        setUpdateMessage(
          result.blocked_reason || "Update requires manual action.",
          true,
          5000,
        );
        return;
      }
      openUpdateConfirmation(result);
    } catch (error) {
      setUpdateMessage(error.message || "Update check failed.", true, 5000);
    } finally {
      button.disabled = false;
      button.textContent = "Update";
    }
  }

  async function startUpdate() {
    if (!updateConfirmationToken) return;
    el("update-confirm").disabled = true;
    el("update-confirm").textContent = "Updating";
    el("update-error").textContent = "";
    setUpdateDetail(
      updateRequiresRestart ? "Stopping runners and data service" : "Applying frontend update",
      true,
    );
    el("update-modal").classList.add("updating");
    try {
      const result = await api("/api/update/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation_token: updateConfirmationToken }),
      });
      updateRequiresRestart = result.restart_required !== false;
      updateConfirmationToken = "";
      updateRestartPending = true;
      setUpdateMessage("Updating...");
      scheduleUpdatePoll();
    } catch (error) {
      updateRestartPending = false;
      showUpdateFailure(error.message || "Update failed to start.");
    }
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
  let aiChatSwipeCloseTimer = null;
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
    const panel = el("ai-chat-panel");
    if (aiChatSwipeCloseTimer !== null) {
      clearTimeout(aiChatSwipeCloseTimer);
      aiChatSwipeCloseTimer = null;
    }
    panel.classList.remove("hidden", "ai-chat-swipe-closing");
    panel.style.transition = "";
    panel.style.transform = "";
    panel.setAttribute("aria-hidden", "false");
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

  function finishAiChatClose() {
    const panel = el("ai-chat-panel");
    if (aiChatSwipeCloseTimer !== null) clearTimeout(aiChatSwipeCloseTimer);
    aiChatSwipeCloseTimer = null;
    panel.classList.add("hidden");
    panel.classList.remove("ai-chat-swipe-closing");
    panel.setAttribute("aria-hidden", "true");
    panel.style.top = "";
    panel.style.height = "";
    panel.style.bottom = "";
    panel.style.paddingBottom = "";
    panel.style.transition = "";
    panel.style.transform = "";
    el("ai-chat-fab").setAttribute("aria-expanded", "false");
    if (aiChatLockedScroll) {
      unlockBodyScroll();
      aiChatLockedScroll = false;
    }
  }

  function closeAiChat(options = {}) {
    if (!isAiChatOpen()) return;
    closeAiModelMenu();
    const panel = el("ai-chat-panel");
    const fromDrag = options && options.fromDrag === true && !desktopReorderQuery.matches;
    if (panel.classList.contains("ai-chat-swipe-closing")) {
      if (!fromDrag) finishAiChatClose();
      return;
    }
    el("ai-chat-fab").setAttribute("aria-expanded", "false");

    if (fromDrag) {
      panel.classList.add("ai-chat-swipe-closing");
      panel.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
      window.requestAnimationFrame(() => {
        panel.style.transform = "translateY(100dvh)";
      });
      aiChatSwipeCloseTimer = window.setTimeout(finishAiChatClose, 220);
      return;
    }
    finishAiChatClose();
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
      sheetDrag = {
        id: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        dy: 0,
        active: false,
      };
      try { sheetHeader.setPointerCapture(e.pointerId); } catch {}
    });
    sheetHeader.addEventListener("pointermove", (e) => {
      if (!sheetDrag || e.pointerId !== sheetDrag.id) return;
      const dx = e.clientX - sheetDrag.startX;
      const dy = e.clientY - sheetDrag.startY;
      if (!sheetDrag.active) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        if (dy <= 0 || Math.abs(dy) <= Math.abs(dx)) {
          sheetDrag = null;
          return;
        }
        sheetDrag.active = true;
        panel.style.transition = "none";
      }
      e.preventDefault();
      sheetDrag.dy = Math.max(0, dy);
      panel.style.transform = `translateY(${sheetDrag.dy}px)`;
    }, { passive: false });
    function endSheetDrag(e, cancelled = false) {
      if (!sheetDrag || (e && e.pointerId !== sheetDrag.id)) return;
      const current = sheetDrag;
      sheetDrag = null;
      try { sheetHeader.releasePointerCapture(current.id); } catch {}
      if (!cancelled && current.active && current.dy > 100) {
        closeAiChat({ fromDrag: true });
        return;
      }
      if (!current.active) return;
      panel.style.transition = "transform 180ms ease";
      panel.style.transform = "translateY(0)";
      window.setTimeout(() => {
        if (!isAiChatOpen() || panel.classList.contains("ai-chat-swipe-closing")) return;
        panel.style.transition = "";
        panel.style.transform = "";
      }, 190);
    }
    sheetHeader.addEventListener("pointerup", endSheetDrag);
    sheetHeader.addEventListener("pointercancel", (e) => endSheetDrag(e, true));

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
  let settingsMode = null; // "webhook" | "telegram" | "data-since" | "data-integrity"
  let settingsOriginalHistorySince = null;
  let settingsOriginalPrerunMode = "auto";
  let settingsOriginalPrerunOffset = null;
  let settingsIntegrityAction = null;
  let settingsIntegrityReport = null;
  let settingsIntegrityRunning = null; // "check" | "repair"
  let settingsIntegrityCancelRequested = false;
  let integrityCancelSubmitting = false;

  function setIntegrityOperation(operation) {
    settingsIntegrityRunning = operation;
    const running = Boolean(operation);
    el("settings-cancel").hidden = !running;
    el("settings-close").disabled = running;
    if (running) el("settings-save").hidden = true;
    if (!running) closeIntegrityCancelConfirm(true);
  }

  function integrityCount(value) {
    const count = Number(value);
    return Number.isFinite(count) ? count.toLocaleString("en-US") : "0";
  }

  function integrityTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : formatUtcDate(date);
  }

  function integrityTimeframeSeconds(timeframe) {
    const match = /^(\d+)([mhd])$/.exec(String(timeframe || ""));
    if (!match) return null;
    const value = Number(match[1]);
    const multiplier = match[2] === "m" ? 60 : match[2] === "h" ? 3600 : 86400;
    return value * multiplier;
  }

  function formatPrerunOffset(value) {
    const total = Math.max(0, Number(value) || 0);
    const minutes = Math.floor(total / 60);
    const seconds = Math.floor(total % 60);
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  function parsePrerunOffset(value) {
    const match = /^(\d+):([0-5]\d)$/.exec(String(value || "").trim());
    if (!match) return null;
    return Number(match[1]) * 60 + Number(match[2]);
  }

  function prerunScheduleRange(timeframe) {
    const value = String(timeframe || "").trim();
    if (value === "1m") return { min: 10, max: 30 };
    if (value === "5m") return { min: 15, max: 150 };
    const match = /^(\d+)([smhdwM])$/.exec(value);
    if (!match) return null;
    const amount = Number(match[1]);
    const unit = match[2];
    const multiplier = unit === "s" ? 1
      : unit === "m" ? 60
        : unit === "h" ? 3600
          : unit === "d" ? 86400
            : unit === "w" ? 604800
              : 30 * 86400;
    const duration = amount * multiplier;
    return duration > 300 ? { min: 15, max: duration - 300 } : null;
  }

  function setPrerunSettingsMode(mode) {
    const normalized = mode === "custom" ? "custom" : "auto";
    const modeInput = el("settings-prerun-mode");
    if (modeInput) modeInput.value = normalized;
    document.querySelectorAll("[data-prerun-mode]").forEach((button) => {
      const selected = button.dataset.prerunMode === normalized;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    const customRow = el("settings-prerun-custom-row");
    if (customRow) customRow.hidden = normalized !== "custom";
  }

  function renderDataIntegrityIntro(id) {
    const session = sessions.find((item) => sessionId(item) === id) || {};
    const start = Number(session.data_since_time);
    const interval = integrityTimeframeSeconds(session.timeframe);
    const now = Math.floor(Date.now() / 1000);
    const end = interval ? Math.floor(now / interval) * interval - (2 * interval) : null;
    const startText = Number.isFinite(start) && start > 0
      ? formatUtcDate(new Date(start * 1000))
      : "-";
    const endText = Number.isFinite(end) && end > 0
      ? formatUtcDate(new Date(end * 1000))
      : "-";
    settingsIntegrityAction = "check";
    setIntegrityOperation(null);
    el("settings-fields").innerHTML =
      `<div class="integrity-intro">` +
        `<strong>Compare local OHLCV with the exchange</strong>` +
        `<span>Checks missing candles, different values, invalid OHLCV, and extra local candles.</span>` +
      `</div>` +
      `<div class="integrity-section-title integrity-check-range-title">Check range</div>` +
      `<div class="integrity-summary integrity-check-range">` +
        `<span>Start candle</span><strong class="mono">${esc(startText)}</strong>` +
        `<span>End candle</span><strong class="mono">${esc(endText)}</strong>` +
      `</div>`;
    const saveBtn = el("settings-save");
    saveBtn.hidden = false;
    saveBtn.disabled = false;
    saveBtn.textContent = "Check";
  }

  function renderDataIntegrityReport(report) {
    settingsIntegrityReport = report;
    const issues = report && report.issues ? report.issues : {};
    const repaired = Boolean(report && report.repair_applied);
    const hasIssues = report && report.status === "issues";
    const noData = report && report.status === "no_data";
    const statusClass = repaired || (!hasIssues && !noData) ? "verified" : "issues";
    const statusTitle = repaired
      ? "Repair completed"
      : noData ? "No confirmed candles" : hasIssues ? "Issues found" : "Verified";
    const statusText = repaired
      ? `${integrityCount(report.imported_reference_bars)} exchange candles were applied.`
      : noData
        ? "The local cache does not contain confirmed candles in this data range."
        : hasIssues
          ? "Local confirmed candles differ from the exchange reference."
          : "Local confirmed candles match the exchange reference.";
    const rangeText = report && report.range_start_time && report.range_end_time
      ? `${integrityTime(report.range_start_time)} → ${integrityTime(report.range_end_time)}`
      : "-";
    const samples = Array.isArray(report && report.samples) ? report.samples : [];
    const sampleHtml = samples.length
      ? `<div class="integrity-section-title">Issue samples</div>` +
        `<div class="integrity-issues">${samples.map((sample) => (
          `<div class="integrity-issue">` +
            `<span class="mono">${esc(integrityTime(sample.time || sample.timestamp))}</span>` +
            `<span>${esc(sample.details || sample.type || "Issue")}</span>` +
          `</div>`
        )).join("")}</div>`
      : "";
    const zeroVolume = Number(issues.synthetic_zero_volume || 0);
    el("settings-fields").innerHTML =
      `<div class="integrity-status ${statusClass}">` +
        `<span class="integrity-status-mark" aria-hidden="true">${statusClass === "verified" ? "✓" : "!"}</span>` +
        `<div><strong>${esc(statusTitle)}</strong><span>${esc(statusText)}</span></div>` +
      `</div>` +
      `<div class="integrity-section-title">Comparison</div>` +
      `<div class="integrity-summary">` +
        `<span>Local candles</span><strong>${integrityCount(report && report.bars_scanned)}</strong>` +
        `<span>Exchange candles</span><strong>${integrityCount(report && report.reference_bars)}</strong>` +
        `<span>Missing locally</span><strong>${integrityCount(issues.missing_local)}</strong>` +
        `<span>Different values</span><strong>${integrityCount(issues.mismatched)}</strong>` +
        `<span>Invalid OHLCV</span><strong>${integrityCount(issues.invalid)}</strong>` +
        `<span>Extra local candles</span><strong>${integrityCount(issues.extra_local)}</strong>` +
      `</div>` +
      `<div class="integrity-section-title">Range</div>` +
      `<div class="integrity-range mono">${esc(rangeText)}</div>` +
      (zeroVolume > 0
        ? `<div class="muted integrity-note">${integrityCount(zeroVolume)} zero-volume synthetic candles were ignored.</div>`
        : "") +
      sampleHtml +
      `<div class="muted integrity-note">The current candle and the immediately preceding candle are excluded.</div>`;

    setIntegrityOperation(null);
    const saveBtn = el("settings-save");
    if (repaired) {
      settingsIntegrityAction = "verify";
      saveBtn.hidden = false;
      saveBtn.textContent = "Verify again";
    } else if (report && report.repairable) {
      settingsIntegrityAction = "repair";
      saveBtn.hidden = false;
      saveBtn.textContent = "Repair";
    } else {
      settingsIntegrityAction = null;
      saveBtn.hidden = true;
    }
    saveBtn.disabled = false;
  }

  async function loadDataIntegrity(id) {
    settingsIntegrityAction = null;
    settingsIntegrityCancelRequested = false;
    setIntegrityOperation("check");
    el("settings-fields").innerHTML =
      `<div class="settings-loading integrity-loading">` +
        `<span class="settings-spinner" aria-hidden="true"></span>` +
        `<span>Comparing confirmed candles with exchange data…</span>` +
      `</div>`;
    try {
      const report = await api(`/api/sessions/${encodeURIComponent(id)}/data-integrity`);
      if (settingsSession !== id || settingsMode !== "data-integrity") return;
      if (settingsIntegrityCancelRequested) return;
      renderDataIntegrityReport(report);
    } catch (e) {
      if (settingsSession !== id || settingsMode !== "data-integrity") return;
      if (settingsIntegrityCancelRequested) return;
      setIntegrityOperation(null);
      el("settings-fields").innerHTML = "";
      el("settings-error").textContent = e.message;
    }
  }

  async function openSettings(id, mode) {
    settingsSession = id;
    settingsMode = mode;
    settingsOriginalHistorySince = null;
    settingsOriginalPrerunMode = "auto";
    settingsOriginalPrerunOffset = null;
    settingsIntegrityAction = null;
    settingsIntegrityReport = null;
    settingsIntegrityRunning = null;
    settingsIntegrityCancelRequested = false;
    el("settings-error").textContent = "";
    const saveBtn = el("settings-save");
    el("settings-cancel").hidden = true;
    el("settings-close").disabled = false;
    saveBtn.hidden = false;
    saveBtn.disabled = false;
    saveBtn.textContent = "Save";
    el("settings-title").textContent =
      (mode === "webhook" ? "Webhook URL — "
        : mode === "telegram" ? "Telegram bot — "
        : mode === "data-integrity" ? "Data integrity — "
        : "Data settings — ") + id;
    el("settings-fields").innerHTML = "loading…";
    el("settings-modal").classList.remove("hidden");
    lockBodyScroll();
    if (mode === "data-integrity") {
      renderDataIntegrityIntro(id);
      return;
    }
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
      settingsOriginalPrerunMode = s.prerun_mode === "custom" ? "custom" : "auto";
      settingsOriginalPrerunOffset = s.prerun_offset_seconds == null
        ? null
        : Number(s.prerun_offset_seconds);
      const effectiveOffset = Number(s.prerun_effective_offset_seconds) || 0;
      const prerunRange = prerunScheduleRange(s.timeframe);
      const supportsCustom = Boolean(prerunRange);
      const customInitial = settingsOriginalPrerunOffset == null
        ? effectiveOffset
        : settingsOriginalPrerunOffset;
      el("settings-fields").innerHTML =
        `<div class="settings-section-title">Data start</div>` +
        `<label class="settings-label" for="settings-history-since">UTC date and time</label>` +
        `<input id="settings-history-since" type="text" ` +
        `placeholder="YYYY-MM-DD or YYYY-MM-DD HH:MM" value="${esc(initial)}">` +
        `<div class="muted">UTC date or datetime, e.g. 2026-05-01 or 2026-06-01 07:30.</div>` +
        `<div class="muted">Saving re-syncs market data and restarts the running strategy.</div>` +
        (shared.length
          ? `<div class="muted">Shares this market's data, so it changes too: ` +
            `<span class="mono">${esc(shared.join(", "))}</span></div>`
          : "") +
        `<div class="settings-section-title settings-prerun-title">Warm-up schedule</div>` +
        `<div class="muted">Warm-up recalculates the strategy over historical candles before processing the next confirmed candle.</div>` +
        (supportsCustom
          ? `<input id="settings-prerun-mode" type="hidden" value="${esc(settingsOriginalPrerunMode)}">` +
            `<div class="settings-segmented" role="group" aria-label="Warm-up schedule mode">` +
              `<button type="button" data-prerun-mode="auto">Auto</button>` +
              `<button type="button" data-prerun-mode="custom">Custom</button>` +
            `</div>` +
            `<div class="settings-prerun-assigned">Assigned time ` +
              `<strong>${esc(formatPrerunOffset(effectiveOffset))}</strong>` +
              (s.prerun_duplicate ? `<span>shared slot</span>` : "") +
            `</div>` +
            `<label id="settings-prerun-custom-row" class="settings-prerun-custom" hidden>` +
              `<span>Start after candle open</span>` +
              `<input id="settings-prerun-offset" type="text" inputmode="numeric" ` +
              `value="${esc(formatPrerunOffset(customInitial))}" placeholder="MM:SS">` +
              `<small>${formatPrerunOffset(prerunRange.min)} - ${formatPrerunOffset(prerunRange.max)}</small>` +
            `</label>`
          : `<div class="muted">This timeframe uses the default midpoint schedule.</div>`) +
        `<div id="settings-loading" class="settings-loading" hidden>` +
          `<span class="settings-spinner" aria-hidden="true"></span>` +
          `<span class="settings-loading-text">Re-syncing data…</span>` +
        `</div>`;
      if (supportsCustom) {
        document.querySelectorAll("[data-prerun-mode]").forEach((button) => {
          button.addEventListener("click", () => setPrerunSettingsMode(button.dataset.prerunMode));
        });
        setPrerunSettingsMode(settingsOriginalPrerunMode);
      }
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

  function closeSettings(force = false) {
    if (el("settings-modal").classList.contains("hidden")) return;
    if (settingsIntegrityRunning && !force) return;
    el("settings-modal").classList.add("hidden");
    unlockBodyScroll();
    const saveBtn = el("settings-save");
    if (saveBtn) { saveBtn.hidden = false; saveBtn.disabled = false; saveBtn.textContent = "Save"; }
    el("settings-cancel").hidden = true;
    el("settings-close").disabled = false;
    settingsSession = null;
    settingsMode = null;
    settingsOriginalHistorySince = null;
    settingsOriginalPrerunMode = "auto";
    settingsOriginalPrerunOffset = null;
    settingsIntegrityAction = null;
    settingsIntegrityReport = null;
    settingsIntegrityRunning = null;
    settingsIntegrityCancelRequested = false;
  }

  function setDataSettingsLoading(on, reSyncing = false) {
    const saveBtn = el("settings-save");
    const inputEl = el("settings-history-since");
    const status = el("settings-loading");
    if (saveBtn) {
      saveBtn.disabled = on;
      saveBtn.textContent = on ? "Applying…" : "Save";
    }
    if (inputEl) inputEl.disabled = on;
    const offsetInput = el("settings-prerun-offset");
    if (offsetInput) offsetInput.disabled = on;
    document.querySelectorAll("[data-prerun-mode]").forEach((button) => {
      button.disabled = on;
    });
    const loadingText = status && status.querySelector(".settings-loading-text");
    if (loadingText) loadingText.textContent = reSyncing ? "Re-syncing data…" : "Applying settings…";
    if (status) status.hidden = !on;
  }

  async function saveSettings() {
    if (!settingsSession) return;
    if (settingsMode === "data-integrity") {
      const sid = settingsSession;
      if (settingsIntegrityAction === "check" || settingsIntegrityAction === "verify") {
        el("settings-error").textContent = "";
        await loadDataIntegrity(sid);
        return;
      }
      if (settingsIntegrityAction !== "repair") return;
      const saveBtn = el("settings-save");
      settingsIntegrityCancelRequested = false;
      setIntegrityOperation("repair");
      el("settings-error").textContent = "";
      el("settings-fields").innerHTML =
        `<div class="settings-loading integrity-loading">` +
          `<span class="settings-spinner" aria-hidden="true"></span>` +
          `<span>Repairing local OHLCV data…</span>` +
        `</div>`;
      try {
        const report = await api(
          `/api/sessions/${encodeURIComponent(sid)}/data-integrity/repair`,
          { method: "POST" },
        );
        if (settingsSession === sid && settingsMode === "data-integrity") {
          if (settingsIntegrityCancelRequested) return;
          renderDataIntegrityReport(report);
        }
      } catch (e) {
        if (settingsSession === sid && settingsMode === "data-integrity") {
          if (settingsIntegrityCancelRequested) return;
          setIntegrityOperation(null);
          if (settingsIntegrityReport) renderDataIntegrityReport(settingsIntegrityReport);
          el("settings-error").textContent = e.message;
          if (!settingsIntegrityReport) {
            saveBtn.hidden = false;
            saveBtn.disabled = false;
            saveBtn.textContent = "Repair";
          }
        }
      }
      return;
    }
    if (settingsMode === "data-since") {
      const inputEl = el("settings-history-since");
      const value = (inputEl ? inputEl.value : "").trim();
      const sid = settingsSession;
      const session = sessions.find((item) => sessionId(item) === sid) || {};
      const historyChanged = !isSameUtcDateInput(value, settingsOriginalHistorySince);
      const modeInput = el("settings-prerun-mode");
      const prerunMode = modeInput && modeInput.value === "custom" ? "custom" : "auto";
      let prerunOffset = null;
      if (prerunMode === "custom") {
        prerunOffset = parsePrerunOffset(el("settings-prerun-offset")?.value);
        const range = prerunScheduleRange(session.timeframe);
        if (!range || prerunOffset == null || prerunOffset < range.min || prerunOffset > range.max) {
          el("settings-error").textContent =
            range
              ? `Warm-up time must be between ${formatPrerunOffset(range.min)} and ${formatPrerunOffset(range.max)}.`
              : "Custom warm-up timing is not available for this timeframe.";
          return;
        }
      }
      const scheduleChanged = prerunMode !== settingsOriginalPrerunMode ||
        (prerunMode === "custom" && prerunOffset !== settingsOriginalPrerunOffset);
      if (!historyChanged && !scheduleChanged) {
        closeSettings();
        return;
      }
      el("settings-error").textContent = "";
      setDataSettingsLoading(true, historyChanged);
      try {
        if (scheduleChanged) {
          await api(`/api/sessions/${encodeURIComponent(sid)}/prerun-schedule`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: prerunMode, offset_seconds: prerunOffset }),
          });
        }
        if (historyChanged) {
          await api(`/api/sessions/${encodeURIComponent(sid)}/history-since`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ history_since: value }),
          });
        }
      } catch (e) {
        el("settings-error").textContent = e.message;
        setDataSettingsLoading(false);
        return;
      }
      if (settingsSession === sid && settingsMode === "data-since") {
        setDataSettingsLoading(false);
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

  function closeIntegrityCancelConfirm(force = false) {
    if (integrityCancelSubmitting && !force) return;
    const modal = el("integrity-cancel-modal");
    if (modal.classList.contains("hidden")) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    el("integrity-cancel-error").textContent = "";
  }

  function openIntegrityCancelConfirm() {
    const operation = settingsIntegrityRunning;
    if (!operation || !settingsSession) return;
    const repair = operation === "repair";
    integrityCancelSubmitting = false;
    el("integrity-cancel-title").textContent = repair ? "Cancel repair?" : "Cancel data check?";
    el("integrity-cancel-message").textContent = repair
      ? "The repair will be stopped. Any cache write already in progress will finish safely before cancellation."
      : "The exchange download and candle comparison will be stopped.";
    el("integrity-cancel-confirm").textContent = repair ? "Cancel repair" : "Cancel check";
    el("integrity-cancel-confirm").disabled = false;
    el("integrity-cancel-keep").disabled = false;
    el("integrity-cancel-close").disabled = false;
    el("integrity-cancel-error").textContent = "";
    const modal = el("integrity-cancel-modal");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
  }

  async function confirmIntegrityCancel() {
    if (!settingsIntegrityRunning || !settingsSession || integrityCancelSubmitting) return;
    const sid = settingsSession;
    integrityCancelSubmitting = true;
    settingsIntegrityCancelRequested = true;
    el("integrity-cancel-confirm").disabled = true;
    el("integrity-cancel-keep").disabled = true;
    el("integrity-cancel-close").disabled = true;
    el("integrity-cancel-confirm").textContent = "Cancelling…";
    const loadingText = el("settings-fields").querySelector(".integrity-loading span:last-child");
    if (loadingText) loadingText.textContent = "Cancelling operation…";
    try {
      await api(`/api/sessions/${encodeURIComponent(sid)}/data-integrity/cancel`, {
        method: "POST",
      });
    } catch (e) {
      settingsIntegrityCancelRequested = false;
      integrityCancelSubmitting = false;
      el("integrity-cancel-error").textContent = e.message;
      el("integrity-cancel-confirm").disabled = false;
      el("integrity-cancel-keep").disabled = false;
      el("integrity-cancel-close").disabled = false;
      el("integrity-cancel-confirm").textContent = settingsIntegrityRunning === "repair"
        ? "Cancel repair"
        : "Cancel check";
      return;
    }
    integrityCancelSubmitting = false;
    closeIntegrityCancelConfirm(true);
    closeSettings(true);
  }

  el("settings-close").addEventListener("click", () => closeSettings());
  el("settings-cancel").addEventListener("click", openIntegrityCancelConfirm);
  el("settings-save").addEventListener("click", saveSettings);
  el("settings-modal").addEventListener("click", (e) => {
    if (e.target === el("settings-modal")) closeSettings();
  });
  el("integrity-cancel-close").addEventListener("click", () => closeIntegrityCancelConfirm());
  el("integrity-cancel-keep").addEventListener("click", () => closeIntegrityCancelConfirm());
  el("integrity-cancel-confirm").addEventListener("click", confirmIntegrityCancel);
  el("integrity-cancel-modal").addEventListener("click", (e) => {
    if (e.target === el("integrity-cancel-modal")) closeIntegrityCancelConfirm();
  });
  el("remove-close").addEventListener("click", closeRemoveConfirm);
  el("remove-cancel").addEventListener("click", closeRemoveConfirm);
  el("remove-confirm").addEventListener("click", confirmRemoveSession);
  el("remove-modal").addEventListener("click", (e) => {
    if (e.target === el("remove-modal")) closeRemoveConfirm();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!el("integrity-cancel-modal").classList.contains("hidden")) {
      closeIntegrityCancelConfirm();
      return;
    }
    closeHubMenu();
    if (isCalendarOpen()) closeCalendar();
    if (isAssetTransferHistoryOpen()) closeAssetTransferHistory();
    else if (isAssetTransferOpen()) closeAssetTransfer();
    else if (isAssetsOpen()) closeAssets();
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
    if (!e.target || !e.target.closest || !e.target.closest(".runner-status-anchor")) {
      closeRunnerStatusTooltips();
    }
    if (!isTouchTooltipMode()) return;
    if (!e.target || !e.target.closest || e.target.closest(".data-badge-wrap")) return;
    closeDataSinceTooltips();
  });

  setInterval(updateRunnerStatusTooltips, 1000);

  // ---- collapsible "Add session" card (collapsed by default) ---------------
  let addCardAnimation = null;
  function toggleAddCard() {
    const card = el("add-card");
    const body = el("add-body");
    if (addCardAnimation) return;
    const opening = card.classList.contains("collapsed");
    el("add-toggle").setAttribute("aria-expanded", String(opening));

    if (opening) {
      card.classList.remove("collapsed", "add-card-closing");
      loadScripts();  // refresh the script list each time it opens
    } else {
      card.classList.add("add-card-closing");
      closeScriptDropdown();
    }

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      card.classList.toggle("collapsed", !opening);
      card.classList.remove("add-card-closing");
      return;
    }

    body.style.overflow = "hidden";
    const height = body.scrollHeight;
    const animation = body.animate(
      opening
        ? [
            { height: "0px", opacity: 0, transform: "translateY(-6px)" },
            { height: `${height}px`, opacity: 1, transform: "translateY(0)" },
          ]
        : [
            { height: `${height}px`, opacity: 1, transform: "translateY(0)" },
            { height: "0px", opacity: 0, transform: "translateY(-6px)" },
          ],
      { duration: 220, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
    );
    addCardAnimation = animation;
    animation.finished.then(() => {
      if (addCardAnimation !== animation) return;
      if (!opening) card.classList.add("collapsed");
      card.classList.remove("add-card-closing");
      body.style.overflow = "";
      addCardAnimation = null;
    }).catch(() => {
      body.style.overflow = "";
      addCardAnimation = null;
    });
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
        } else if (msg.type === "calendar_forecast_cancelled") {
          if (isCalendarOpen()) applyCalendarForecastCancelled(String(msg.event_id || ""));
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
    if (document.visibilityState === "visible") {
      rebuildHub();
      connectAccountPositions();
      scheduleAssetsRefresh();
      schedulePnlRefresh();
      if (isWatchlistOpen()) {
        watchlistGeneration += 1;
        connectWatchlist(watchlistGeneration);
      }
    } else {
      clearAssetsRefreshTimer();
      clearPnlRefreshTimer();
      closeForBackground();
      closeAccountPositionsSocket();
      watchlistGeneration += 1;
      closeWatchlistSocket();
      if (isWatchlistOpen()) setWatchlistStatus("Paused");
    }
  });
  window.addEventListener("online", () => {
    rebuildHub();
    connectAccountPositions();
    if (isWatchlistOpen()) {
      watchlistGeneration += 1;
      connectWatchlist(watchlistGeneration);
    }
  });

  initHubMenuCalendar();
  initSessionReordering();
  initDesktopCardCarousel();
  initAiChat();
  initPepeFaces();
  startHubClock();
  refresh();   // one-time initial load; thereafter the hub pushes via /ws/hub
  connect();
})();
