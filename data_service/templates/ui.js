var App = window.App || (window.App = {});

App.ui = {
  elements: {
    chartInfo: document.getElementById("chart-info"),
    chartInfoLine: document.getElementById("chart-info-line"),
    chartInfoTitleRow: document.getElementById("chart-info-title-row"),
    chartInfoTitle: document.getElementById("chart-info-title"),
    alertsToggle: document.getElementById("alerts-toggle"),
    alertsMenu: document.getElementById("alerts-menu"),
    alertTemplateSettings: document.getElementById("alert-template-settings"),
    alertTemplateBackdrop: document.getElementById("alert-template-backdrop"),
    alertTemplateModal: document.getElementById("alert-template-modal"),
    alertTemplateHelpToggle: document.getElementById("alert-template-help-toggle"),
    alertTemplateHelp: document.getElementById("alert-template-help"),
    alertTemplatePlaceholderTip: document.getElementById("alert-template-placeholder-tip"),
    alertTemplateRows: document.getElementById("alert-template-rows"),
    alertTemplateAdd: document.getElementById("alert-template-add"),
    alertTemplateSave: document.getElementById("alert-template-save"),
    alertTemplateStatus: document.getElementById("alert-template-status"),
    manualAlertMenu: document.getElementById("manual-alert-menu"),
    manualAlertDrag: document.getElementById("manual-alert-drag"),
    manualAlertPrice: document.getElementById("manual-alert-price"),
    manualAlertTemplatePicker: document.getElementById("manual-alert-template-picker"),
    manualAlertTemplateButton: document.getElementById("manual-alert-template-button"),
    manualAlertTemplateLabel: document.getElementById("manual-alert-template-label"),
    manualAlertTemplateOptions: document.getElementById("manual-alert-template-options"),
    manualAlertSend: document.getElementById("manual-alert-send"),
    manualAlertStatus: document.getElementById("manual-alert-status"),
    manualAlertConfirmBackdrop: document.getElementById("manual-alert-confirm-backdrop"),
    manualAlertConfirm: document.getElementById("manual-alert-confirm"),
    manualAlertConfirmUrl: document.getElementById("manual-alert-confirm-url"),
    manualAlertConfirmCancel: document.getElementById("manual-alert-confirm-cancel"),
    manualAlertConfirmSend: document.getElementById("manual-alert-confirm-send"),
    webhookToggle: document.getElementById("alert-webhook-toggle"),
    telegramToggle: document.getElementById("alert-telegram-toggle"),
    sourceToggle: document.getElementById("source-toggle"),
    sourcePanel: document.getElementById("source-panel"),
    sourceBackdrop: document.getElementById("source-backdrop"),
    sourceClose: document.getElementById("source-close"),
    sourceSave: document.getElementById("source-save"),
    sourceResizeHandle: document.getElementById("source-resize-handle"),
    sourcePanelName: document.getElementById("source-panel-name"),
    sourceStatus: document.getElementById("source-status"),
    sourceHighlight: document.getElementById("source-highlight"),
    sourceCode: document.getElementById("source-code")
  },
  manualAlertDragState: null,
  manualAlertPendingSend: null,
  manualAlertStatusClearTimer: null,
  activeTemplatePlaceholder: null,
  setChartInfo(ohlcvText = null) {
    const state = App.state;
    const baseLine = ohlcvText
      ? `${state.baseInfoTop} | <span class="info-ohlcv">${ohlcvText}</span>`
      : state.baseInfoTop;
    state.baseInfoText = baseLine;
    this.elements.chartInfoLine.innerHTML = baseLine;
    if (state.scriptTitleVisible) {
      this.elements.chartInfoTitle.textContent = state.scriptTitle;
      this.elements.chartInfoTitleRow.classList.remove("hidden");
    } else {
      this.elements.chartInfoTitle.textContent = "";
      this.elements.chartInfoTitleRow.classList.add("hidden");
    }
  },
  toggleAlertsMenu(forceOpen = null) {
    const menu = this.elements.alertsMenu;
    const shouldOpen = forceOpen === null ? !menu.classList.contains("open") : forceOpen;
    menu.classList.toggle("open", shouldOpen);
    if (shouldOpen) {
      App.data.loadWebhookConfig();
    }
  },
  isAlertsMenuEventTarget(target) {
    return Boolean(
      target &&
      (this.elements.alertsMenu.contains(target) || this.elements.alertsToggle.contains(target))
    );
  },
  alertTemplateStorageKey() {
    return App.config.storageKey("manualAlertTemplates");
  },
  alertTemplateMigrationKey() {
    return App.config.storageKey("manualAlertTemplatesMigrated");
  },
  readLocalManualAlertTemplates() {
    try {
      const raw = localStorage.getItem(this.alertTemplateStorageKey());
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed)
        ? parsed.filter(t => t && typeof t.title === "string" && typeof t.message === "string")
        : [];
    } catch {
      return [];
    }
  },
  clearLocalManualAlertTemplates() {
    try {
      localStorage.removeItem(this.alertTemplateStorageKey());
    } catch {}
  },
  async migrateLocalManualAlertTemplatesIfNeeded(serverTemplates) {
    if (serverTemplates.length > 0) return serverTemplates;
    try {
      if (localStorage.getItem(this.alertTemplateMigrationKey()) === "1") {
        return serverTemplates;
      }
    } catch {
      return serverTemplates;
    }
    const localTemplates = this.readLocalManualAlertTemplates();
    if (!localTemplates.length) {
      try {
        localStorage.setItem(this.alertTemplateMigrationKey(), "1");
      } catch {}
      return serverTemplates;
    }
    const result = await App.data.saveManualAlertTemplates(localTemplates);
    if (result.ok) {
      try {
        localStorage.setItem(this.alertTemplateMigrationKey(), "1");
      } catch {}
      this.clearLocalManualAlertTemplates();
      return result.templates;
    }
    return serverTemplates;
  },
  async loadManualAlertTemplates({ migrateLocal = false } = {}) {
    const result = await App.data.loadManualAlertTemplates();
    if (!result.ok) return App.state.manualAlertTemplates;
    if (migrateLocal) {
      return await this.migrateLocalManualAlertTemplatesIfNeeded(result.templates);
    }
    return result.templates;
  },
  async persistManualAlertTemplates(templates) {
    const result = await App.data.saveManualAlertTemplates(templates);
    if (result.ok) {
      try {
        localStorage.setItem(this.alertTemplateMigrationKey(), "1");
      } catch {}
      this.clearLocalManualAlertTemplates();
    }
    return result;
  },
  setTemplateStatus(text = "", isError = false) {
    const status = this.elements.alertTemplateStatus;
    status.textContent = text;
    status.classList.toggle("error", isError);
  },
  toggleAlertTemplateHelp(forceOpen = null) {
    const shouldOpen = forceOpen === null
      ? this.elements.alertTemplateHelp.classList.contains("hidden")
      : forceOpen;
    this.elements.alertTemplateHelp.classList.toggle("hidden", !shouldOpen);
    this.elements.alertTemplateHelpToggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    if (!shouldOpen) {
      this.clearActiveTemplatePlaceholder();
    }
  },
  isCoarsePointer() {
    return window.matchMedia("(hover: none) and (pointer: coarse)").matches;
  },
  showTemplatePlaceholderTip(button) {
    if (!button) return;
    this.activeTemplatePlaceholder = button;
    const tooltip = this.elements.alertTemplatePlaceholderTip;
    tooltip.textContent = button.dataset.help || "";
    tooltip.classList.remove("hidden");
    this.positionTemplatePlaceholderTip(button);
  },
  positionTemplatePlaceholderTip(button) {
    const tooltip = this.elements.alertTemplatePlaceholderTip;
    if (!button || tooltip.classList.contains("hidden")) return;
    tooltip.style.left = "0px";
    tooltip.style.top = "0px";
    const rect = button.getBoundingClientRect();
    const tipRect = tooltip.getBoundingClientRect();
    const margin = 8;
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - tipRect.width - margin));
    let top = rect.bottom + margin;
    if (top + tipRect.height > window.innerHeight - margin) {
      top = rect.top - tipRect.height - margin;
    }
    top = Math.max(margin, Math.min(top, window.innerHeight - tipRect.height - margin));
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
  },
  clearActiveTemplatePlaceholder(button = null) {
    if (button && this.activeTemplatePlaceholder !== button) {
      return;
    }
    this.activeTemplatePlaceholder = null;
    this.elements.alertTemplatePlaceholderTip.textContent = "";
    this.elements.alertTemplatePlaceholderTip.classList.add("hidden");
  },
  attachAlertTemplatePlaceholderHelp() {
    this.elements.alertTemplateHelp.querySelectorAll(".template-placeholder").forEach((button) => {
      button.addEventListener("mouseenter", () => {
        if (!this.isCoarsePointer()) {
          this.showTemplatePlaceholderTip(button);
        }
      });
      button.addEventListener("mouseleave", () => {
        if (!this.isCoarsePointer()) {
          this.clearActiveTemplatePlaceholder(button);
        }
      });
      button.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this.isCoarsePointer()) {
          this.showTemplatePlaceholderTip(button);
        }
      });
    });
  },
  addAlertTemplateRow(template = { title: "", message: "{}" }) {
    const row = document.createElement("div");
    row.className = "template-row";
    row.innerHTML =
      `<input type="text" class="template-title-input" value="${this.escapeHtml(template.title || "")}" placeholder="TITLE" spellcheck="false">` +
      `<textarea class="template-message-input" placeholder="MESSAGE JSON" spellcheck="false">${this.escapeHtml(template.message || "{}")}</textarea>` +
      `<button class="template-remove" title="Remove" aria-label="Remove">&times;</button>`;
    row.querySelector(".template-remove").addEventListener("click", () => {
      row.remove();
      this.setTemplateStatus("");
    });
    this.elements.alertTemplateRows.appendChild(row);
  },
  renderAlertTemplateRows() {
    this.elements.alertTemplateRows.innerHTML = "";
    App.state.manualAlertTemplates.forEach(t => this.addAlertTemplateRow(t));
  },
  collectAlertTemplateRows() {
    const templates = [];
    const rows = Array.from(this.elements.alertTemplateRows.querySelectorAll(".template-row"));
    for (const row of rows) {
      const title = row.querySelector(".template-title-input").value.trim();
      const message = row.querySelector(".template-message-input").value.trim();
      if (!title && !message) continue;
      if (!title) return { ok: false, error: "TITLE is required" };
      if (!message) return { ok: false, error: "MESSAGE is required" };
      try {
        this.parseAlertTemplateMessage(message, { price: 1, market: 1, time: 0, title });
      } catch (e) {
        return { ok: false, error: `Invalid JSON: ${title}` };
      }
      templates.push({ title, message });
    }
    return { ok: true, templates };
  },
  async openAlertTemplateModal() {
    this.toggleAlertsMenu(false);
    this.setTemplateStatus("Loading...");
    this.elements.alertTemplateBackdrop.classList.remove("hidden");
    this.elements.alertTemplateModal.classList.remove("hidden");
    this.elements.alertTemplateModal.setAttribute("aria-hidden", "false");
    this.toggleAlertTemplateHelp(false);
    this.refreshMobileViewportLock();
    await this.loadManualAlertTemplates({ migrateLocal: true });
    this.renderAlertTemplateRows();
    this.setTemplateStatus("");
  },
  closeAlertTemplateModal() {
    if (this.elements.alertTemplateModal.contains(document.activeElement)) {
      document.activeElement.blur();
    }
    this.elements.alertTemplateBackdrop.classList.add("hidden");
    this.elements.alertTemplateModal.classList.add("hidden");
    this.elements.alertTemplateModal.setAttribute("aria-hidden", "true");
    this.clearActiveTemplatePlaceholder();
    this.refreshMobileViewportLock();
  },
  async saveAlertTemplates() {
    const result = this.collectAlertTemplateRows();
    if (!result.ok) {
      this.setTemplateStatus(result.error, true);
      return false;
    }
    this.setTemplateStatus("Saving...");
    this.elements.alertTemplateSave.disabled = true;
    const saved = await this.persistManualAlertTemplates(result.templates);
    this.elements.alertTemplateSave.disabled = false;
    if (!saved.ok) {
      this.setTemplateStatus(saved.error || "Save failed", true);
      return false;
    }
    this.setTemplateStatus("Saved");
    return true;
  },
  alertTemplateReplacements(context = {}) {
    return {
      "{{price}}": context.price,
      "{{market}}": context.market,
      "{{time}}": context.time,
      "{{symbol}}": App.state.symbol || "",
      "{{ticker}}": App.state.symbol || "",
      "{{exchange}}": App.state.exchange || "",
      "{{timeframe}}": App.state.timeframe || "",
      "{{title}}": context.title || ""
    };
  },
  isInsideJsonString(text, offset) {
    let inString = false;
    let escaped = false;
    for (let i = 0; i < offset; i += 1) {
      const ch = text[i];
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === "\"") {
        inString = !inString;
      }
    }
    return inString;
  },
  renderRawAlertTemplateJson(text, context) {
    const replacements = this.alertTemplateReplacements(context);
    return text.replace(/\{\{(price|market|time|symbol|ticker|exchange|timeframe|title)\}\}/g, (match, _key, offset) => {
      if (this.isInsideJsonString(text, offset)) return match;
      const replacement = replacements[match];
      return JSON.stringify(replacement === undefined ? "" : replacement);
    });
  },
  parseAlertTemplateMessage(message, context) {
    try {
      return JSON.parse(message);
    } catch (initialError) {
      try {
        return JSON.parse(this.renderRawAlertTemplateJson(message, context));
      } catch {
        throw initialError;
      }
    }
  },
  replaceAlertTemplateValue(value, context) {
    if (typeof value === "string") {
      const replacements = this.alertTemplateReplacements(context);
      if (Object.prototype.hasOwnProperty.call(replacements, value)) {
        return replacements[value];
      }
      return value.replace(/\{\{(price|market|time|symbol|ticker|exchange|timeframe|title)\}\}/g, (match) => {
        const replacement = replacements[match];
        return replacement == null ? "" : String(replacement);
      });
    }
    if (Array.isArray(value)) {
      return value.map(item => this.replaceAlertTemplateValue(item, context));
    }
    if (value && typeof value === "object") {
      const out = {};
      Object.entries(value).forEach(([key, item]) => {
        out[key] = this.replaceAlertTemplateValue(item, context);
      });
      return out;
    }
    return value;
  },
  buildManualAlertMessage(template, context) {
    const parsed = this.parseAlertTemplateMessage(template.message, { ...context, title: template.title });
    return this.replaceAlertTemplateValue(parsed, { ...context, title: template.title });
  },
  currentMarketPrice() {
    const lastPrice = Number(App.state.lastPrice);
    if (Number.isFinite(lastPrice) && lastPrice !== 0) return lastPrice;
    const lastClose = App.state.lastOhlcv ? Number(App.state.lastOhlcv.close) : NaN;
    return Number.isFinite(lastClose) ? lastClose : null;
  },
  buildManualAlertPayload(pending) {
    if (pending && pending.message !== undefined) return pending;
    const template = pending.template;
    const context = {
      ...pending.context,
      market: this.currentMarketPrice(),
      title: template.title
    };
    return {
      title: template.title,
      price: context.price,
      market: context.market,
      time: context.time,
      message: this.buildManualAlertMessage(template, context)
    };
  },
  clampManualAlertMenuPosition(left, top) {
    const menu = this.elements.manualAlertMenu;
    const rect = menu.getBoundingClientRect();
    return {
      left: Math.min(window.innerWidth - rect.width - 8, Math.max(8, left)),
      top: Math.min(window.innerHeight - rect.height - 8, Math.max(8, top))
    };
  },
  setManualAlertMenuPosition(left, top) {
    const pos = this.clampManualAlertMenuPosition(left, top);
    this.elements.manualAlertMenu.style.left = `${pos.left}px`;
    this.elements.manualAlertMenu.style.top = `${pos.top}px`;
    return pos;
  },
  manualAlertPriceCenterY() {
    const rect = this.elements.manualAlertPrice.getBoundingClientRect();
    return rect.top + rect.height / 2;
  },
  updateManualAlertPriceFromMenu() {
    const context = App.state.manualAlertContext;
    if (!context || !App.chart || !App.chart.priceFromClientY) return;
    const price = App.chart.priceFromClientY(this.manualAlertPriceCenterY());
    if (price == null) return;
    context.price = price;
    this.elements.manualAlertPrice.textContent = `Price ${this.formatNumber(price, 2)}`;
    App.chart.updateManualAlertPriceGuide(price);
  },
  positionManualAlertMenu(x, y) {
    const menu = this.elements.manualAlertMenu;
    menu.classList.remove("hidden");
    menu.style.left = "8px";
    menu.style.top = "8px";
    const menuRect = menu.getBoundingClientRect();
    const priceRect = this.elements.manualAlertPrice.getBoundingClientRect();
    const priceOffsetY = (priceRect.top - menuRect.top) + priceRect.height / 2;
    this.setManualAlertMenuPosition(x + 10, y - priceOffsetY);
    this.updateManualAlertPriceFromMenu();
  },
  closeManualAlertTemplateList() {
    App.state.manualAlertTemplateOpen = false;
    this.elements.manualAlertTemplateOptions.classList.add("hidden");
    this.elements.manualAlertTemplateButton.setAttribute("aria-expanded", "false");
  },
  showManualAlertConfirm(payload, url) {
    this.manualAlertPendingSend = payload;
    App.state.manualAlertConfirmOpen = true;
    this.elements.manualAlertConfirmUrl.textContent = url;
    this.elements.manualAlertConfirmBackdrop.classList.remove("hidden");
    this.elements.manualAlertConfirm.classList.remove("hidden");
    this.elements.manualAlertConfirm.setAttribute("aria-hidden", "false");
  },
  closeManualAlertConfirm() {
    this.manualAlertPendingSend = null;
    App.state.manualAlertConfirmOpen = false;
    this.elements.manualAlertConfirmBackdrop.classList.add("hidden");
    this.elements.manualAlertConfirm.classList.add("hidden");
    this.elements.manualAlertConfirm.setAttribute("aria-hidden", "true");
    this.elements.manualAlertConfirmSend.disabled = false;
  },
  async confirmManualAlertSend() {
    const pending = this.manualAlertPendingSend;
    const status = this.elements.manualAlertStatus;
    if (!pending) return;
    if (this.manualAlertStatusClearTimer !== null) {
      clearTimeout(this.manualAlertStatusClearTimer);
      this.manualAlertStatusClearTimer = null;
    }
    let payload;
    try {
      payload = this.buildManualAlertPayload(pending);
    } catch (e) {
      this.closeManualAlertConfirm();
      status.textContent = "Invalid JSON";
      status.classList.add("error");
      return;
    }
    status.textContent = "Sending...";
    status.classList.remove("error");
    this.elements.manualAlertConfirmSend.disabled = true;
    this.elements.manualAlertSend.disabled = true;
    this.closeManualAlertConfirm();
    const result = await App.data.sendManualAlert(payload);
    this.elements.manualAlertSend.disabled = false;
    if (!result || !result.ok) {
      status.textContent = (result && result.error) || "Send failed";
      status.classList.add("error");
      return;
    }
    const telegram = result.data && result.data.telegram;
    if (telegram && telegram.error) {
      const detail = String(telegram.error || "").slice(0, 180);
      status.textContent = detail ? `Webhook sent; Telegram failed: ${detail}` : "Webhook sent; Telegram failed";
      status.classList.add("error");
      return;
    }
    status.textContent = "Sent";
    this.manualAlertStatusClearTimer = setTimeout(() => {
      if (status.textContent === "Sent" && !status.classList.contains("error")) {
        status.textContent = "";
      }
      this.manualAlertStatusClearTimer = null;
    }, 3000);
  },
  toggleManualAlertTemplateList(forceOpen = null) {
    const button = this.elements.manualAlertTemplateButton;
    if (button.disabled) return;
    const shouldOpen = forceOpen === null ? !App.state.manualAlertTemplateOpen : forceOpen;
    App.state.manualAlertTemplateOpen = shouldOpen;
    this.elements.manualAlertTemplateOptions.classList.toggle("hidden", !shouldOpen);
    button.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    if (shouldOpen) {
      const active = this.elements.manualAlertTemplateOptions.querySelector(".active");
      if (active) active.scrollIntoView({ block: "nearest" });
    }
  },
  selectManualAlertTemplate(index) {
    const templates = App.state.manualAlertTemplates;
    const safeIndex = Number.isInteger(index) && index >= 0 && index < templates.length ? index : -1;
    App.state.manualAlertSelectedTemplateIndex = safeIndex;
    this.elements.manualAlertTemplateLabel.textContent = safeIndex >= 0 ? templates[safeIndex].title : "No templates";
    Array.from(this.elements.manualAlertTemplateOptions.children).forEach((option) => {
      const active = Number(option.dataset.index) === safeIndex;
      option.classList.toggle("active", active);
      option.setAttribute("aria-selected", active ? "true" : "false");
    });
    this.closeManualAlertTemplateList();
  },
  renderManualAlertTemplatePicker(templates) {
    const button = this.elements.manualAlertTemplateButton;
    const options = this.elements.manualAlertTemplateOptions;
    const hasTemplates = templates.length > 0;
    options.innerHTML = "";
    App.state.manualAlertTemplateOpen = false;
    button.disabled = !hasTemplates;
    button.setAttribute("aria-expanded", "false");
    options.classList.add("hidden");

    templates.forEach((template, index) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "manual-alert-template-option";
      option.dataset.index = String(index);
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", index === 0 ? "true" : "false");
      option.textContent = template.title;
      option.addEventListener("click", (e) => {
        e.stopPropagation();
        this.selectManualAlertTemplate(index);
      });
      options.appendChild(option);
    });

    App.state.manualAlertSelectedTemplateIndex = hasTemplates ? 0 : -1;
    this.elements.manualAlertTemplateLabel.textContent = hasTemplates ? templates[0].title : "No templates";
    if (options.firstElementChild) {
      options.firstElementChild.classList.add("active");
    }
  },
  async showManualAlertMenu(context) {
    const status = this.elements.manualAlertStatus;
    App.state.manualAlertContext = context;
    App.state.manualAlertMenuOpen = true;
    App.state.manualAlertSuppressClickUntil = Date.now() + 500;
    this.elements.manualAlertPrice.textContent = `Price ${this.formatNumber(context.price, 2)}`;
    this.elements.manualAlertTemplateLabel.textContent = "Loading...";
    this.elements.manualAlertTemplateButton.disabled = true;
    this.elements.manualAlertTemplateOptions.innerHTML = "";
    this.closeManualAlertTemplateList();
    this.elements.manualAlertSend.disabled = true;
    status.textContent = "Loading...";
    status.classList.remove("error");
    this.positionManualAlertMenu(context.clientX, context.clientY);

    const templates = await this.loadManualAlertTemplates({ migrateLocal: true });
    if (App.state.manualAlertContext !== context) return;
    this.renderManualAlertTemplatePicker(templates);
    this.elements.manualAlertSend.disabled = templates.length === 0;
    status.textContent = templates.length ? "" : "No templates";
    status.classList.toggle("error", templates.length === 0);
  },
  closeManualAlertMenu() {
    this.manualAlertDragState = null;
    if (this.manualAlertStatusClearTimer !== null) {
      clearTimeout(this.manualAlertStatusClearTimer);
      this.manualAlertStatusClearTimer = null;
    }
    App.state.manualAlertContext = null;
    App.state.manualAlertMenuOpen = false;
    this.elements.manualAlertMenu.classList.add("hidden");
    this.elements.manualAlertStatus.textContent = "";
    this.elements.manualAlertStatus.classList.remove("error");
    this.closeManualAlertTemplateList();
    this.closeManualAlertConfirm();
    App.state.manualAlertSelectedTemplateIndex = -1;
    if (App.chart && App.chart.removeManualAlertPriceGuide) {
      App.chart.removeManualAlertPriceGuide();
    }
    if (App.chart && App.chart.restoreMagnetMode) {
      App.chart.restoreMagnetMode();
    }
  },
  startManualAlertDrag(e) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    this.closeManualAlertTemplateList();
    const menu = this.elements.manualAlertMenu;
    const rect = menu.getBoundingClientRect();
    this.manualAlertDragState = {
      pointerId: e.pointerId,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top
    };
    try {
      this.elements.manualAlertDrag.setPointerCapture(e.pointerId);
    } catch {}
    e.preventDefault();
  },
  moveManualAlertDrag(e) {
    const drag = this.manualAlertDragState;
    if (!drag || drag.pointerId !== e.pointerId) return;
    this.setManualAlertMenuPosition(e.clientX - drag.offsetX, e.clientY - drag.offsetY);
    this.updateManualAlertPriceFromMenu();
    e.preventDefault();
  },
  endManualAlertDrag(e) {
    if (this.manualAlertDragState && this.manualAlertDragState.pointerId === e.pointerId) {
      this.manualAlertDragState = null;
    }
  },
  async sendManualAlertFromMenu() {
    const context = App.state.manualAlertContext;
    const templates = App.state.manualAlertTemplates;
    const index = App.state.manualAlertSelectedTemplateIndex;
    const template = templates[index];
    const status = this.elements.manualAlertStatus;
    if (!context || !template) return;
    this.updateManualAlertPriceFromMenu();
    try {
      this.buildManualAlertMessage(template, {
        ...context,
        market: this.currentMarketPrice(),
        title: template.title
      });
    } catch (e) {
      status.textContent = "Invalid JSON";
      status.classList.add("error");
      return;
    }
    status.textContent = "Checking URL...";
    status.classList.remove("error");
    this.elements.manualAlertSend.disabled = true;
    const cfg = await App.data.loadWebhookConfig();
    this.elements.manualAlertSend.disabled = false;
    if (!cfg) {
      status.textContent = "Failed to load webhook URL";
      status.classList.add("error");
      return;
    }
    const url = cfg.url || "";
    if (!url) {
      status.textContent = "Webhook URL is empty";
      status.classList.add("error");
      return;
    }
    this.showManualAlertConfirm({
      title: template.title,
      context: { ...context },
      template
    }, url);
    status.textContent = "";
  },
  renderSourcePanel() {
    const state = App.state;
    const name = state.scriptSourceName || state.scriptTitle || "No source";
    const source = state.scriptSourceLoaded ? state.scriptSource : "No source loaded.";
    this.elements.sourcePanelName.textContent = name;
    if (!state.sourceDirty) {
      this.elements.sourceCode.value = source;
    }
    this.renderSourceHighlight();
    this.updateSourceSaveState();
  },
  renderSourceHighlight() {
    const source = this.elements.sourceCode.value || "No source loaded.";
    this.elements.sourceHighlight.innerHTML = this.highlightPython(source);
    this.syncSourceScroll();
  },
  syncSourceScroll() {
    const editor = this.elements.sourceCode;
    const highlight = this.elements.sourceHighlight;
    highlight.scrollTop = editor.scrollTop;
    highlight.scrollLeft = editor.scrollLeft;
  },
  updateSourceSaveState() {
    const state = App.state;
    this.elements.sourceSave.disabled = state.sourceSaving || !state.sourceDirty || !state.scriptSourceLoaded;
    this.elements.sourceSave.classList.toggle("dirty", state.sourceDirty);
    this.elements.sourceSave.classList.toggle("saving", state.sourceSaving);
    this.elements.sourceStatus.textContent = state.sourceSaving ? "Saving..." : (state.sourceSaveStatus || "");
  },
  handleSourceInput() {
    const state = App.state;
    state.sourceDirty = state.scriptSourceLoaded && this.elements.sourceCode.value !== state.scriptSource;
    state.sourceSaveStatus = "";
    this.renderSourceHighlight();
    this.updateSourceSaveState();
  },
  async saveSourcePanel() {
    const state = App.state;
    if (state.sourceSaving || !state.sourceDirty) return;

    state.sourceSaving = true;
    state.sourceSaveStatus = "";
    this.updateSourceSaveState();
    const source = this.elements.sourceCode.value;
    const result = await App.data.saveScriptSource(source);
    state.sourceSaving = false;

    if (result && result.ok) {
      state.sourceSaveStatus = "Saved";
      this.elements.sourceCode.value = state.scriptSource;
      this.renderSourceHighlight();
      this.updateSourceSaveState();
      setTimeout(() => {
        if (!state.sourceDirty && state.sourceSaveStatus === "Saved") {
          state.sourceSaveStatus = "";
          this.updateSourceSaveState();
        }
      }, 1500);
      return;
    }

    state.sourceDirty = true;
    state.sourceSaveStatus = (result && result.error) || "Save failed";
    this.updateSourceSaveState();
  },
  insertSourceText(text) {
    const editor = this.elements.sourceCode;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    editor.value = editor.value.slice(0, start) + text + editor.value.slice(end);
    editor.selectionStart = start + text.length;
    editor.selectionEnd = start + text.length;
    this.handleSourceInput();
  },
  escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;"
    })[ch]);
  },
  wrapPythonToken(className, value) {
    return `<span class="${className}">${this.escapeHtml(value)}</span>`;
  },
  highlightPython(source) {
    if (!source) {
      return this.escapeHtml("No source loaded.");
    }

    const keywords = new Set([
      "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
      "del", "elif", "else", "except", "finally", "for", "from", "global", "if",
      "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise",
      "return", "try", "while", "with", "yield"
    ]);
    const builtins = new Set([
      "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
      "len", "list", "map", "max", "min", "open", "print", "range", "reversed",
      "round", "set", "sorted", "str", "sum", "super", "tuple", "type", "zip"
    ]);
    const constants = new Set(["False", "None", "True", "Ellipsis", "NotImplemented"]);
    let html = "";
    let i = 0;

    const isIdentStart = (ch) => /[A-Za-z_]/.test(ch);
    const isIdent = (ch) => /[A-Za-z0-9_]/.test(ch);
    const stringPrefixMatch = (offset) => {
      const part = source.slice(offset, offset + 3);
      const match = /^(?:[rRuUbBfF]|[rR][fF]|[fF][rR]|[bB][rR]|[rR][bB])(?=['"])/.exec(part);
      return match ? match[0] : "";
    };

    while (i < source.length) {
      const ch = source[i];
      const prefix = stringPrefixMatch(i);
      const quoteOffset = i + prefix.length;

      if ((prefix || ch === "\"" || ch === "'") && (source[quoteOffset] === "\"" || source[quoteOffset] === "'")) {
        const quote = source[quoteOffset];
        const triple = source.slice(quoteOffset, quoteOffset + 3) === quote.repeat(3);
        let end = quoteOffset + (triple ? 3 : 1);
        while (end < source.length) {
          if (triple && source.slice(end, end + 3) === quote.repeat(3)) {
            end += 3;
            break;
          }
          if (!triple && source[end] === "\n") {
            break;
          }
          if (!triple && source[end] === "\\") {
            end += 2;
          } else if (!triple && source[end] === quote) {
            end += 1;
            break;
          } else {
            end += 1;
          }
        }
        html += this.wrapPythonToken("py-string", source.slice(i, end));
        i = end;
        continue;
      }

      if (ch === "#") {
        let end = i;
        while (end < source.length && source[end] !== "\n") end += 1;
        html += this.wrapPythonToken("py-comment", source.slice(i, end));
        i = end;
        continue;
      }

      if (ch === "@" && (i === 0 || source[i - 1] === "\n")) {
        let end = i + 1;
        while (end < source.length && /[A-Za-z0-9_.]/.test(source[end])) end += 1;
        html += this.wrapPythonToken("py-decorator", source.slice(i, end));
        i = end;
        continue;
      }

      if (/[0-9]/.test(ch)) {
        const match = /^(?:0[xX][0-9A-Fa-f_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?j?)/.exec(source.slice(i));
        if (match) {
          html += this.wrapPythonToken("py-number", match[0]);
          i += match[0].length;
          continue;
        }
      }

      if (isIdentStart(ch)) {
        let end = i + 1;
        while (end < source.length && isIdent(source[end])) end += 1;
        const word = source.slice(i, end);
        if (keywords.has(word)) {
          html += this.wrapPythonToken("py-keyword", word);
        } else if (builtins.has(word)) {
          html += this.wrapPythonToken("py-builtin", word);
        } else if (constants.has(word)) {
          html += this.wrapPythonToken("py-constant", word);
        } else {
          html += this.escapeHtml(word);
        }
        i = end;
        continue;
      }

      html += this.escapeHtml(ch);
      i += 1;
    }

    return html;
  },
  async toggleSourcePanel(forceOpen = null) {
    const state = App.state;
    const shouldOpen = forceOpen === null ? !state.sourcePanelOpen : forceOpen;
    state.sourcePanelOpen = shouldOpen;
    document.body.classList.toggle("source-open", shouldOpen);
    this.elements.sourcePanel.setAttribute("aria-hidden", shouldOpen ? "false" : "true");
    this.elements.sourceToggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    this.refreshMobileViewportLock();
    if (shouldOpen) {
      this.toggleAlertsMenu(false);
      // 열 때마다 디스크 최신본을 다시 불러와 다른 기기에서 저장한 내용이 즉시 보이게 한다.
      // 단, 저장 안 한 로컬 편집(dirty)이 있으면 덮어쓰지 않는다.
      if (!state.sourceDirty) {
        await App.data.loadScriptSource();
      }
      this.renderSourcePanel();
    } else if (this.elements.sourceCode) {
      this.elements.sourceCode.blur();
    }
    if (App.chart && App.chart.resizeToContainer) {
      requestAnimationFrame(() => App.chart.resizeToContainer());
    }
  },
  setMobileViewportLock(lock) {
    // iOS Safari는 포커스되는 입력 요소의 font-size가 16px 미만이면 자동 확대한다.
    // 발생한 zoom을 사후에 되돌리는 건 최신 iOS에서 신뢰성이 낮으므로, 작은 입력창이
    // 있는 패널이 열려 있는 동안 viewport에 maximum-scale=1을 걸어 확대 자체를 막는다.
    // 닫히면 원복해 핀치줌을 다시 허용한다.
    if (!window.matchMedia("(max-width: 640px), (hover: none) and (pointer: coarse)").matches) {
      return;
    }
    const viewport = document.querySelector('meta[name="viewport"]');
    if (!viewport) return;
    viewport.setAttribute(
      "content",
      lock
        ? "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover"
        : "width=device-width, initial-scale=1, viewport-fit=cover"
    );
  },
  refreshMobileViewportLock() {
    const templateOpen = !this.elements.alertTemplateModal.classList.contains("hidden");
    this.setMobileViewportLock(App.state.sourcePanelOpen || templateOpen);
  },
  getSourcePaneBounds() {
    const viewportWidth = Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1);
    const min = Math.min(360, Math.max(280, viewportWidth - 320));
    const max = Math.max(min, Math.min(920, Math.round(viewportWidth * 0.74), viewportWidth - 220));
    return { min, max };
  },
  setSourcePaneWidth(width) {
    const { min, max } = this.getSourcePaneBounds();
    const clamped = Math.max(min, Math.min(max, Math.round(width)));
    document.documentElement.style.setProperty("--source-pane-width", `${clamped}px`);
    if (App.chart && App.chart.resizeToContainer) {
      requestAnimationFrame(() => App.chart.resizeToContainer());
    }
  },
  attachSourceResize() {
    const handle = this.elements.sourceResizeHandle;
    if (!handle) return;

    let resizeFrame = null;
    let pendingWidth = null;
    const applyPendingWidth = () => {
      resizeFrame = null;
      if (pendingWidth != null) {
        this.setSourcePaneWidth(pendingWidth);
      }
    };
    const scheduleWidth = (width) => {
      pendingWidth = width;
      if (resizeFrame !== null) return;
      resizeFrame = requestAnimationFrame(applyPendingWidth);
    };
    const onPointerMove = (e) => {
      scheduleWidth(window.innerWidth - e.clientX);
    };
    const onPointerUp = () => {
      document.body.classList.remove("source-resizing");
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      if (resizeFrame !== null) {
        cancelAnimationFrame(resizeFrame);
        applyPendingWidth();
      }
    };

    handle.addEventListener("pointerdown", (e) => {
      if (window.matchMedia("(max-width: 640px), (hover: none) and (pointer: coarse)").matches) {
        return;
      }
      e.preventDefault();
      document.body.classList.add("source-resizing");
      scheduleWidth(window.innerWidth - e.clientX);
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
      window.addEventListener("pointercancel", onPointerUp);
    });

    window.addEventListener("resize", () => {
      if (window.matchMedia("(max-width: 640px), (hover: none) and (pointer: coarse)").matches) {
        return;
      }
      const panelWidth = this.elements.sourcePanel.getBoundingClientRect().width;
      if (panelWidth > 0) {
        this.setSourcePaneWidth(panelWidth);
      }
    });
  },
  formatNumber(value, decimals) {
    if (value == null || Number.isNaN(value)) return "-";
    return Number(value).toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    });
  },
  init() {
    const {
      alertsToggle,
      alertsMenu,
      alertTemplateSettings,
      alertTemplateBackdrop,
      alertTemplateHelpToggle,
      alertTemplateAdd,
      alertTemplateSave,
      manualAlertDrag,
      manualAlertTemplateButton,
      manualAlertSend,
      manualAlertConfirm,
      manualAlertConfirmBackdrop,
      manualAlertConfirmCancel,
      manualAlertConfirmSend,
      webhookToggle,
      telegramToggle,
      sourceToggle,
      sourceBackdrop,
      sourceClose,
      sourceSave,
      sourceCode
    } = this.elements;
    alertsToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleAlertsMenu();
    });

    alertTemplateSettings.addEventListener("click", (e) => {
      e.stopPropagation();
      this.openAlertTemplateModal();
    });

    alertTemplateBackdrop.addEventListener("click", () => {
      this.closeAlertTemplateModal();
    });

    alertTemplateBackdrop.addEventListener("pointerdown", () => {
      this.closeAlertTemplateModal();
    });

    alertTemplateHelpToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleAlertTemplateHelp();
    });

    this.attachAlertTemplatePlaceholderHelp();

    window.addEventListener("resize", () => {
      if (this.activeTemplatePlaceholder) {
        this.positionTemplatePlaceholderTip(this.activeTemplatePlaceholder);
      }
    });

    alertTemplateAdd.addEventListener("click", () => {
      this.addAlertTemplateRow();
      this.setTemplateStatus("");
    });

    alertTemplateSave.addEventListener("click", () => {
      this.saveAlertTemplates();
    });

    manualAlertSend.addEventListener("click", () => {
      this.sendManualAlertFromMenu();
    });

    manualAlertTemplateButton.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleManualAlertTemplateList();
    });

    manualAlertConfirm.addEventListener("click", (e) => {
      e.stopPropagation();
    });

    manualAlertConfirmBackdrop.addEventListener("click", (e) => {
      e.stopPropagation();
      this.closeManualAlertConfirm();
    });

    manualAlertConfirmBackdrop.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      this.closeManualAlertConfirm();
    });

    manualAlertConfirmCancel.addEventListener("click", (e) => {
      e.stopPropagation();
      this.closeManualAlertConfirm();
    });

    manualAlertConfirmSend.addEventListener("click", (e) => {
      e.stopPropagation();
      this.confirmManualAlertSend();
    });

    manualAlertDrag.addEventListener("pointerdown", (e) => {
      this.startManualAlertDrag(e);
    });

    window.addEventListener("pointermove", (e) => {
      this.moveManualAlertDrag(e);
    });

    window.addEventListener("pointerup", (e) => {
      this.endManualAlertDrag(e);
    });

    window.addEventListener("pointercancel", (e) => {
      this.endManualAlertDrag(e);
    });

    sourceToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleSourcePanel();
    });

    sourceBackdrop.addEventListener("click", () => {
      this.toggleSourcePanel(false);
    });

    sourceClose.addEventListener("click", () => {
      this.toggleSourcePanel(false);
    });

    sourceSave.addEventListener("click", () => {
      this.saveSourcePanel();
    });

    sourceCode.addEventListener("input", () => {
      this.handleSourceInput();
    });

    sourceCode.addEventListener("scroll", () => {
      this.syncSourceScroll();
    });

    sourceCode.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        this.saveSourcePanel();
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        this.insertSourceText("    ");
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        this.closeManualAlertConfirm();
        this.closeManualAlertMenu();
        this.closeAlertTemplateModal();
        this.toggleSourcePanel(false);
      }
    });

    document.addEventListener("click", (e) => {
      if (!this.isAlertsMenuEventTarget(e.target)) {
        this.toggleAlertsMenu(false);
      }
      if (Date.now() < App.state.manualAlertSuppressClickUntil) {
        return;
      }
      if (App.state.manualAlertConfirmOpen) {
        return;
      }
      if (!this.elements.manualAlertMenu.classList.contains("hidden") &&
          !this.elements.manualAlertMenu.contains(e.target)) {
        this.closeManualAlertMenu();
      }
      if (!this.elements.manualAlertTemplateOptions.classList.contains("hidden") &&
          !this.elements.manualAlertTemplatePicker.contains(e.target)) {
        this.closeManualAlertTemplateList();
      }
    });

    document.addEventListener("pointerdown", (e) => {
      if (!this.isAlertsMenuEventTarget(e.target)) {
        this.toggleAlertsMenu(false);
      }
      if (!e.target.closest(".template-placeholder")) {
        this.clearActiveTemplatePlaceholder();
      }
      if (App.state.manualAlertConfirmOpen) {
        return;
      }
      if (!this.elements.manualAlertMenu.classList.contains("hidden") &&
          !this.elements.manualAlertMenu.contains(e.target)) {
        this.closeManualAlertMenu();
      }
      if (!this.elements.manualAlertTemplateOptions.classList.contains("hidden") &&
          !this.elements.manualAlertTemplatePicker.contains(e.target)) {
        this.closeManualAlertTemplateList();
      }
    }, { capture: true });

    webhookToggle.addEventListener("change", async () => {
      const enabled = webhookToggle.checked;
      const res = await App.data.updateWebhookConfig({ enabled });
      if (!res) {
        webhookToggle.checked = !enabled;
      }
    });

    telegramToggle.addEventListener("change", async () => {
      const enabled = telegramToggle.checked;
      const res = await App.data.updateWebhookConfig({ telegram_notification: enabled });
      if (!res) {
        telegramToggle.checked = !enabled;
      }
    });

    this.attachSourceResize();
  }
};

App.ui.init();
