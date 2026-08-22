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
    manualAlertPriceInput: document.getElementById("manual-alert-price-input"),
    manualAlertSet: document.getElementById("manual-alert-set"),
    manualAlertTemplatePicker: document.getElementById("manual-alert-template-picker"),
    manualAlertTemplateButton: document.getElementById("manual-alert-template-button"),
    manualAlertTemplateLabel: document.getElementById("manual-alert-template-label"),
    manualAlertTemplateOptions: document.getElementById("manual-alert-template-options"),
    manualAlertSend: document.getElementById("manual-alert-send"),
    manualAlertStatus: document.getElementById("manual-alert-status"),
    manualAlertConfirmBackdrop: document.getElementById("manual-alert-confirm-backdrop"),
    manualAlertConfirm: document.getElementById("manual-alert-confirm"),
    manualAlertConfirmUrl: document.getElementById("manual-alert-confirm-url"),
    manualAlertConfirmNote: document.getElementById("manual-alert-confirm-note"),
    manualAlertConfirmCancel: document.getElementById("manual-alert-confirm-cancel"),
    manualAlertConfirmSend: document.getElementById("manual-alert-confirm-send"),
    webhookToggle: document.getElementById("alert-webhook-toggle"),
    telegramToggle: document.getElementById("alert-telegram-toggle"),
    sourceToggle: document.getElementById("source-toggle"),
    sourcePanel: document.getElementById("source-panel"),
    sourceBackdrop: document.getElementById("source-backdrop"),
    sourceClose: document.getElementById("source-close"),
    sourceUndo: document.getElementById("source-undo"),
    sourceSave: document.getElementById("source-save"),
    sourceResizeHandle: document.getElementById("source-resize-handle"),
    sourcePanelName: document.getElementById("source-panel-name"),
    sourceStatus: document.getElementById("source-status"),
    sourceHighlight: document.getElementById("source-highlight"),
    sourceDiffGutter: document.getElementById("source-diff-gutter"),
    sourceDiffMarkers: document.getElementById("source-diff-markers"),
    sourceCode: document.getElementById("source-code")
  },
  manualAlertDragState: null,
  manualAlertPendingSend: null,
  manualAlertStatusClearTimer: null,
  manualAlertTriggerSyncTimer: null,
  manualAlertArmedInputDirty: false,
  activeTemplatePlaceholder: null,
  sourceDiffTimer: null,
  sourceUndoStack: [],
  sourceUndoInputType: "",
  sourceUndoCapturedAt: 0,
  sourceApplyingUndo: false,
  sourceComposing: false,
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
  addAlertTemplateRow(template = { title: "", message: "{}", ai: "" }) {
    const row = document.createElement("div");
    row.className = "template-row";
    row.innerHTML =
      `<input type="text" class="template-title-input" value="${this.escapeHtml(template.title || "")}" placeholder="TITLE" spellcheck="false">` +
      `<textarea class="template-message-input" placeholder="MESSAGE JSON" spellcheck="false">${this.escapeHtml(template.message || "{}")}</textarea>` +
      `<textarea class="template-ai-input" placeholder="AI runs when this alert triggers (optional)" spellcheck="false">${this.escapeHtml(template.ai || "")}</textarea>` +
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
      const ai = row.querySelector(".template-ai-input").value.trim();
      if (!title && !message && !ai) continue;
      if (!title) return { ok: false, error: "TITLE is required" };
      if (!message) return { ok: false, error: "MESSAGE is required" };
      if (ai.length > 4000) return { ok: false, error: `AI instruction is too long: ${title}` };
      try {
        this.parseAlertTemplateMessage(message, { price: 1, market: 1, time: 0, title });
      } catch (e) {
        return { ok: false, error: `Invalid JSON: ${title}` };
      }
      templates.push({ title, message, ...(ai ? { ai } : {}) });
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
  buildManualAlertAiInstruction(template, context) {
    const instruction = String(template.ai || "").trim();
    if (!instruction) return "";
    return String(this.replaceAlertTemplateValue(
      instruction,
      { ...context, title: template.title }
    )).trim();
  },
  currentMarketPrice() {
    const lastPrice = Number(App.state.lastPrice);
    if (Number.isFinite(lastPrice) && lastPrice !== 0) return lastPrice;
    const lastClose = App.state.lastOhlcv ? Number(App.state.lastOhlcv.close) : NaN;
    return Number.isFinite(lastClose) ? lastClose : null;
  },
  formatPriceInput(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "";
    return n.toFixed(8).replace(/\.?0+$/, "");
  },
  parseManualAlertPriceInput() {
    const raw = String(this.elements.manualAlertPriceInput.value || "").replace(/,/g, "").trim();
    if (!raw) return null;
    const price = Number(raw);
    return Number.isFinite(price) ? price : null;
  },
  manualAlertSelectedTemplate() {
    const templates = App.state.manualAlertTemplates;
    const index = App.state.manualAlertSelectedTemplateIndex;
    return Number.isInteger(index) && index >= 0 && index < templates.length ? templates[index] : null;
  },
  manualAlertTriggerActive() {
    return this.manualAlertTriggers().length > 0;
  },
  manualAlertTriggers() {
    return Array.isArray(App.state.manualAlertTriggers)
      ? App.state.manualAlertTriggers.filter(t => t && t.enabled && Number.isFinite(Number(t.price)))
      : [];
  },
  setManualAlertTriggerState(triggers) {
    const normalized = Array.isArray(triggers)
      ? triggers.filter(t => t && t.enabled && Number.isFinite(Number(t.price)))
      : [];
    App.state.manualAlertTriggers = normalized;
  },
  setManualAlertPriceValue(price, { updateInput = true } = {}) {
    const context = App.state.manualAlertContext;
    const n = Number(price);
    if (!Number.isFinite(n)) return false;
    if (context) context.price = n;
    if (updateInput) {
      this.elements.manualAlertPriceInput.value = this.formatPriceInput(n);
    }
    if (App.state.manualAlertMenuOpen && App.chart && App.chart.updateManualAlertPreviewGuide) {
      App.chart.updateManualAlertPreviewGuide(n);
    }
    return true;
  },
  moveManualAlertMenuToPrice(price) {
    if (!App.state.manualAlertMenuOpen || !App.chart || !App.chart.clientYFromPrice) return false;
    const n = Number(price);
    if (!Number.isFinite(n)) return false;
    const targetY = App.chart.clientYFromPrice(n);
    if (targetY == null) return false;
    const menu = this.elements.manualAlertMenu;
    const menuRect = menu.getBoundingClientRect();
    const priceRect = this.elements.manualAlertPriceInput.getBoundingClientRect();
    const priceOffsetY = (priceRect.top - menuRect.top) + priceRect.height / 2;
    this.setManualAlertMenuPosition(menuRect.left, targetY - priceOffsetY);
    return true;
  },
  updateManualAlertSetButtonState() {
    const template = this.manualAlertSelectedTemplate();
    const button = this.elements.manualAlertSet;
    button.textContent = "Set";
    button.disabled = !template;
    button.classList.remove("armed");
  },
  clearManualAlertTriggerSyncTimer() {
    if (this.manualAlertTriggerSyncTimer !== null) {
      clearTimeout(this.manualAlertTriggerSyncTimer);
      this.manualAlertTriggerSyncTimer = null;
    }
  },
  restoreManualAlertActiveTriggerPrice() {
    return false;
  },
  previewManualAlertTriggerPrice(triggerId, price) {
    const n = Number(price);
    if (!triggerId || !Number.isFinite(n)) return false;
    const triggers = this.manualAlertTriggers();
    const trigger = triggers.find(t => String(t.id) === String(triggerId));
    if (!trigger) return false;
    trigger.price = n;
    this.setManualAlertTriggerState(triggers);
    if (App.state.manualAlertMenuOpen) {
      this.elements.manualAlertPriceInput.value = this.formatPriceInput(n);
    }
    if (App.chart && App.chart.renderManualAlertTriggerGuides) {
      App.chart.renderManualAlertTriggerGuides(triggers);
    }
    return true;
  },
  applyManualAlertTriggerState(triggers) {
    if (!Array.isArray(triggers)) triggers = [];
    this.setManualAlertTriggerState(triggers);
    const context = App.state.manualAlertContext;
    if (App.chart && App.chart.renderManualAlertTriggerGuides) {
      App.chart.renderManualAlertTriggerGuides(App.state.manualAlertTriggers);
    }
    this.clearManualAlertTriggerSyncTimer();
    this.manualAlertArmedInputDirty = false;
    if (context && App.state.manualAlertMenuOpen && App.chart && App.chart.updateManualAlertPreviewGuide) {
      App.chart.updateManualAlertPreviewGuide(context.price);
    }
    this.updateManualAlertSetButtonState();
  },
  async loadManualAlertTrigger() {
    const result = await App.data.loadManualAlertTrigger();
    if (result && result.ok) {
      this.applyManualAlertTriggerState(result.triggers || []);
    }
    return result;
  },
  buildManualAlertTriggerPayload() {
    const template = this.manualAlertSelectedTemplate();
    const price = this.parseManualAlertPriceInput();
    if (!template || price == null) return null;
    return {
      enabled: true,
      price,
      template: {
        title: template.title,
        message: template.message,
        ...(template.ai ? { ai: template.ai } : {})
      }
    };
  },
  async saveManualAlertTriggers(triggers, { quiet = false } = {}) {
    const status = this.elements.manualAlertStatus;
    const result = await App.data.saveManualAlertTriggers(triggers || []);
    if (!result || !result.ok) {
      if (!quiet) {
        status.textContent = (result && result.error) || "Set failed";
        status.classList.add("error");
      }
      this.updateManualAlertSetButtonState();
      return false;
    }
    const savedTriggers = result.triggers || [];
    this.applyManualAlertTriggerState(savedTriggers);
    if (!quiet) {
      status.textContent = "Set";
      status.classList.remove("error");
    }
    return true;
  },
  async setManualAlertTriggerFromInput({ moveMenu = false } = {}) {
    const status = this.elements.manualAlertStatus;
    this.clearManualAlertTriggerSyncTimer();
    const trigger = this.buildManualAlertTriggerPayload();
    if (!trigger) {
      status.textContent = "Select template and price";
      status.classList.add("error");
      this.updateManualAlertSetButtonState();
      return false;
    }
    status.textContent = "Setting...";
    status.classList.remove("error");
    this.elements.manualAlertSet.disabled = true;
    const saved = await this.saveManualAlertTriggers([...this.manualAlertTriggers(), trigger]);
    if (saved && moveMenu) {
      this.moveManualAlertMenuToPrice(trigger.price);
    }
    this.updateManualAlertSetButtonState();
    return saved;
  },
  async saveManualAlertTriggerPriceFromLine(triggerId, price) {
    const n = Number(price);
    if (!triggerId || !Number.isFinite(n)) return false;
    const triggers = this.manualAlertTriggers();
    const trigger = triggers.find(t => String(t.id) === String(triggerId));
    if (!trigger) return false;
    const template = trigger.template || {};
    if (!template.title || !template.message) return false;
    trigger.price = n;
    return await this.saveManualAlertTriggers(triggers, { quiet: true });
  },
  async unsetManualAlertTriggerFromLine(triggerId) {
    if (!triggerId || !this.manualAlertTriggerActive()) return false;
    this.clearManualAlertTriggerSyncTimer();
    const triggers = this.manualAlertTriggers().filter(t => String(t.id) !== String(triggerId));
    return await this.saveManualAlertTriggers(triggers, { quiet: true });
  },
  scheduleManualAlertTriggerSync() {
    return;
  },
  flushManualAlertTriggerSync() {
    this.clearManualAlertTriggerSyncTimer();
  },
  async toggleManualAlertTrigger() {
    await this.setManualAlertTriggerFromInput({ moveMenu: true });
  },
  buildManualAlertPayload(pending) {
    if (pending && pending.message !== undefined) return pending;
    const template = pending.template;
    const context = {
      ...pending.context,
      market: this.currentMarketPrice(),
      title: template.title
    };
    const payload = {
      title: template.title,
      price: context.price,
      market: context.market,
      time: context.time,
      message: this.buildManualAlertMessage(template, context)
    };
    const aiInstruction = this.buildManualAlertAiInstruction(template, context);
    if (aiInstruction) payload.ai_instruction = aiInstruction;
    return payload;
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
    const rect = this.elements.manualAlertPriceInput.getBoundingClientRect();
    return rect.top + rect.height / 2;
  },
  updateManualAlertPriceFromMenu() {
    const context = App.state.manualAlertContext;
    if (!context || !App.chart || !App.chart.priceFromClientY) return;
    const price = App.chart.priceFromClientY(this.manualAlertPriceCenterY());
    if (price == null) return;
    this.setManualAlertPriceValue(price);
    this.scheduleManualAlertTriggerSync();
  },
  positionManualAlertMenu(x, y, { updatePrice = true } = {}) {
    const menu = this.elements.manualAlertMenu;
    menu.classList.remove("hidden");
    menu.style.left = "8px";
    menu.style.top = "8px";
    const menuRect = menu.getBoundingClientRect();
    const priceRect = this.elements.manualAlertPriceInput.getBoundingClientRect();
    const priceOffsetY = (priceRect.top - menuRect.top) + priceRect.height / 2;
    this.setManualAlertMenuPosition(x + 10, y - priceOffsetY);
    if (updatePrice) {
      this.updateManualAlertPriceFromMenu();
    }
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
    const hasAiInstruction = Boolean(
      payload && payload.template && String(payload.template.ai || "").trim()
    );
    this.elements.manualAlertConfirmNote.textContent = hasAiInstruction
      ? "Telegram will also be sent if configured. The AI instruction will run after a successful webhook when AI is enabled."
      : "Telegram will also be sent if credentials are configured.";
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
    const webhook = result.data && result.data.webhook;
    const telegram = result.data && result.data.telegram;
    if (!webhook || !webhook.sent) {
      const webhookDetail = String((webhook && webhook.error) || "unknown error").slice(0, 180);
      if (telegram && telegram.sent) {
        status.textContent = webhookDetail
          ? `Webhook failed: ${webhookDetail}; Telegram sent`
          : "Webhook failed; Telegram sent";
      } else {
        const telegramDetail = String((telegram && telegram.error) || "").slice(0, 180);
        status.textContent = telegramDetail
          ? `Webhook failed: ${webhookDetail}; Telegram failed: ${telegramDetail}`
          : `Webhook failed: ${webhookDetail}`;
      }
      status.classList.add("error");
      return;
    }
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
    this.updateManualAlertSetButtonState();
    this.scheduleManualAlertTriggerSync();
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
    this.updateManualAlertSetButtonState();
  },
  async showManualAlertMenu(context) {
    const status = this.elements.manualAlertStatus;
    App.state.manualAlertContext = context;
    App.state.manualAlertMenuOpen = true;
    this.refreshMobileViewportLock();
    App.state.manualAlertSuppressClickUntil = Date.now() + 500;
    this.elements.manualAlertPriceInput.value = this.formatPriceInput(context.price);
    this.elements.manualAlertTemplateLabel.textContent = "Loading...";
    this.elements.manualAlertTemplateButton.disabled = true;
    this.elements.manualAlertTemplateOptions.innerHTML = "";
    this.closeManualAlertTemplateList();
    this.elements.manualAlertSend.disabled = true;
    this.updateManualAlertSetButtonState();
    status.textContent = "Loading...";
    status.classList.remove("error");
    this.positionManualAlertMenu(context.clientX, context.clientY, { updatePrice: true });

    const templates = await this.loadManualAlertTemplates({ migrateLocal: true });
    if (App.state.manualAlertContext !== context) return;
    this.renderManualAlertTemplatePicker(templates);
    this.elements.manualAlertSend.disabled = templates.length === 0;
    status.textContent = templates.length ? "" : "No templates";
    status.classList.toggle("error", templates.length === 0);
    this.updateManualAlertSetButtonState();
  },
  closeManualAlertMenu() {
    this.manualAlertDragState = null;
    this.flushManualAlertTriggerSync();
    if (this.elements.manualAlertMenu.contains(document.activeElement)) {
      document.activeElement.blur();
    }
    if (this.manualAlertStatusClearTimer !== null) {
      clearTimeout(this.manualAlertStatusClearTimer);
      this.manualAlertStatusClearTimer = null;
    }
    App.state.manualAlertContext = null;
    App.state.manualAlertMenuOpen = false;
    this.refreshMobileViewportLock();
    this.elements.manualAlertMenu.classList.add("hidden");
    this.elements.manualAlertStatus.textContent = "";
    this.elements.manualAlertStatus.classList.remove("error");
    this.closeManualAlertTemplateList();
    this.closeManualAlertConfirm();
    App.state.manualAlertSelectedTemplateIndex = -1;
    if (App.chart && App.chart.removeManualAlertPreviewGuide) {
      App.chart.removeManualAlertPreviewGuide();
    }
    if (App.chart && App.chart.renderManualAlertTriggerGuides) {
      App.chart.renderManualAlertTriggerGuides(App.state.manualAlertTriggers || []);
    }
    if (App.chart && App.chart.restoreMagnetMode) {
      App.chart.restoreMagnetMode();
    }
  },
  startManualAlertDrag(e) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (e.target && e.target.closest && e.target.closest("input, button, textarea, select")) return;
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
    const inputPrice = this.parseManualAlertPriceInput();
    if (inputPrice == null) {
      status.textContent = "Invalid price";
      status.classList.add("error");
      return;
    }
    this.setManualAlertPriceValue(inputPrice, { updateInput: false });
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
      this.resetSourceUndo();
    }
    this.renderSourceHighlight();
    this.updateSourceSaveState();
  },
  renderSourceHighlight() {
    const source = this.elements.sourceCode.value || "No source loaded.";
    let lineIndex = 0;
    const highlighted = this.highlightPython(source).replace(/\n/g, () => {
      lineIndex += 1;
      return `\n<span class="source-line-anchor" data-line="${lineIndex}"></span>`;
    });
    this.elements.sourceHighlight.innerHTML =
      `<span class="source-line-anchor" data-line="0"></span>${highlighted}`;
    this.syncSourceScroll();
    this.scheduleSourceDiff();
  },
  syncSourceScroll() {
    const editor = this.elements.sourceCode;
    const highlight = this.elements.sourceHighlight;
    highlight.scrollTop = editor.scrollTop;
    highlight.scrollLeft = editor.scrollLeft;
    this.syncSourceDiffScroll();
  },
  syncSourceDiffScroll() {
    const markers = this.elements.sourceDiffMarkers;
    const editor = this.elements.sourceCode;
    if (!markers || !editor) return;
    markers.style.transform = `translateY(${-editor.scrollTop}px)`;
  },
  clearSourceDiff() {
    if (this.sourceDiffTimer !== null) {
      clearTimeout(this.sourceDiffTimer);
      this.sourceDiffTimer = null;
    }
    if (this.elements.sourceDiffMarkers) {
      this.elements.sourceDiffMarkers.replaceChildren();
      this.syncSourceDiffScroll();
    }
  },
  scheduleSourceDiff() {
    if (!App.state.sourceDirty) {
      this.clearSourceDiff();
      return;
    }
    if (this.sourceDiffTimer !== null) {
      clearTimeout(this.sourceDiffTimer);
    }
    this.sourceDiffTimer = setTimeout(() => {
      this.sourceDiffTimer = null;
      requestAnimationFrame(() => this.renderSourceDiff());
    }, 100);
  },
  sourceLineDiffOperations(before, after) {
    const oldLines = String(before ?? "").replace(/\r\n?/g, "\n").split("\n");
    const newLines = String(after ?? "").replace(/\r\n?/g, "\n").split("\n");
    let prefix = 0;
    while (
      prefix < oldLines.length &&
      prefix < newLines.length &&
      oldLines[prefix] === newLines[prefix]
    ) {
      prefix += 1;
    }

    let suffix = 0;
    while (
      suffix < oldLines.length - prefix &&
      suffix < newLines.length - prefix &&
      oldLines[oldLines.length - suffix - 1] === newLines[newLines.length - suffix - 1]
    ) {
      suffix += 1;
    }

    const operations = Array(prefix).fill("equal");
    const oldMiddle = oldLines.slice(prefix, oldLines.length - suffix);
    const newMiddle = newLines.slice(prefix, newLines.length - suffix);
    operations.push(...this.sourceLineMyersDiff(oldMiddle, newMiddle));
    operations.push(...Array(suffix).fill("equal"));
    return { operations, lineCount: newLines.length };
  },
  sourceLineMyersDiff(oldLines, newLines) {
    if (!oldLines.length) return Array(newLines.length).fill("insert");
    if (!newLines.length) return Array(oldLines.length).fill("delete");

    const oldCount = oldLines.length;
    const newCount = newLines.length;
    const frontier = new Map([[1, 0]]);
    const trace = [];
    const maxDistance = Math.min(oldCount + newCount, 600);

    for (let distance = 0; distance <= maxDistance; distance += 1) {
      trace.push(new Map(frontier));
      for (let diagonal = -distance; diagonal <= distance; diagonal += 2) {
        const left = frontier.get(diagonal - 1) ?? Number.NEGATIVE_INFINITY;
        const down = frontier.get(diagonal + 1) ?? Number.NEGATIVE_INFINITY;
        let oldIndex;
        if (diagonal === -distance || (diagonal !== distance && left < down)) {
          oldIndex = Number.isFinite(down) ? down : 0;
        } else {
          oldIndex = (Number.isFinite(left) ? left : 0) + 1;
        }
        let newIndex = oldIndex - diagonal;
        while (
          oldIndex < oldCount &&
          newIndex < newCount &&
          oldLines[oldIndex] === newLines[newIndex]
        ) {
          oldIndex += 1;
          newIndex += 1;
        }
        frontier.set(diagonal, oldIndex);
        if (oldIndex >= oldCount && newIndex >= newCount) {
          return this.backtrackSourceLineDiff(trace, oldLines, newLines);
        }
      }
    }

    return [
      ...Array(oldCount).fill("delete"),
      ...Array(newCount).fill("insert")
    ];
  },
  backtrackSourceLineDiff(trace, oldLines, newLines) {
    const operations = [];
    let oldIndex = oldLines.length;
    let newIndex = newLines.length;

    for (let distance = trace.length - 1; distance >= 0; distance -= 1) {
      const frontier = trace[distance];
      const diagonal = oldIndex - newIndex;
      const left = frontier.get(diagonal - 1) ?? Number.NEGATIVE_INFINITY;
      const down = frontier.get(diagonal + 1) ?? Number.NEGATIVE_INFINITY;
      const previousDiagonal = (
        diagonal === -distance || (diagonal !== distance && left < down)
      ) ? diagonal + 1 : diagonal - 1;
      const previousOldIndex = frontier.get(previousDiagonal) ?? 0;
      const previousNewIndex = previousOldIndex - previousDiagonal;

      while (oldIndex > previousOldIndex && newIndex > previousNewIndex) {
        operations.push("equal");
        oldIndex -= 1;
        newIndex -= 1;
      }
      if (distance === 0) break;
      if (oldIndex === previousOldIndex) {
        operations.push("insert");
        newIndex -= 1;
      } else {
        operations.push("delete");
        oldIndex -= 1;
      }
    }
    return operations.reverse();
  },
  sourceDiffMarkers(before, after) {
    const { operations, lineCount } = this.sourceLineDiffOperations(before, after);
    const lines = new Map();
    const deletions = new Set();
    let currentLine = 0;
    let index = 0;

    while (index < operations.length) {
      if (operations[index] === "equal") {
        currentLine += 1;
        index += 1;
        continue;
      }

      const insertedLines = [];
      let deletedCount = 0;
      while (index < operations.length && operations[index] !== "equal") {
        if (operations[index] === "insert") {
          insertedLines.push(currentLine);
          currentLine += 1;
        } else {
          deletedCount += 1;
        }
        index += 1;
      }

      const modifiedCount = Math.min(deletedCount, insertedLines.length);
      insertedLines.forEach((line, insertedIndex) => {
        lines.set(line, insertedIndex < modifiedCount ? "modified" : "added");
      });
      if (deletedCount > modifiedCount) {
        deletions.add(Math.max(0, Math.min(currentLine, lineCount - 1)));
      }
    }
    return { lines, deletions };
  },
  renderSourceDiff() {
    const state = App.state;
    const markers = this.elements.sourceDiffMarkers;
    const highlight = this.elements.sourceHighlight;
    if (!markers || !highlight || !state.scriptSourceLoaded || !state.sourceDirty) {
      this.clearSourceDiff();
      return;
    }

    const current = this.elements.sourceCode.value;
    if (current === state.scriptSource) {
      this.clearSourceDiff();
      return;
    }

    const changes = this.sourceDiffMarkers(state.scriptSource, current);
    const anchors = highlight.querySelectorAll(".source-line-anchor");
    const highlightRect = highlight.getBoundingClientRect();
    const lineHeight = parseFloat(getComputedStyle(highlight).lineHeight) || 18;
    const topCache = new Map();
    const lineTop = (line) => {
      const clamped = Math.max(0, Math.min(line, anchors.length - 1));
      if (topCache.has(clamped)) return topCache.get(clamped);
      const anchor = anchors[clamped];
      const top = anchor
        ? anchor.getBoundingClientRect().top - highlightRect.top + highlight.scrollTop
        : 0;
      topCache.set(clamped, top);
      return top;
    };
    const fragment = document.createDocumentFragment();

    [...changes.lines.entries()].sort((a, b) => a[0] - b[0]).forEach(([line, type]) => {
      const marker = document.createElement("span");
      const top = lineTop(line);
      const nextTop = line + 1 < anchors.length ? lineTop(line + 1) : top + lineHeight;
      marker.className = `source-diff-marker ${type}`;
      marker.style.top = `${top}px`;
      marker.style.height = `${Math.max(2, nextTop - top)}px`;
      fragment.appendChild(marker);
    });

    [...changes.deletions].sort((a, b) => a - b).forEach((line) => {
      const marker = document.createElement("span");
      marker.className = "source-diff-marker deleted";
      marker.style.top = `${lineTop(line)}px`;
      fragment.appendChild(marker);
    });

    markers.replaceChildren(fragment);
    this.syncSourceDiffScroll();
  },
  resetSourceUndo() {
    this.sourceUndoStack = [];
    this.sourceUndoInputType = "";
    this.sourceUndoCapturedAt = 0;
    this.updateSourceUndoState();
  },
  updateSourceUndoState() {
    const button = this.elements.sourceUndo;
    if (!button) return;
    button.disabled = !App.state.sourceDirty || this.sourceUndoStack.length === 0;
  },
  captureSourceUndo(inputType = "") {
    if (inputType === "historyUndo" || inputType === "historyRedo") {
      this.resetSourceUndo();
      return;
    }
    if (
      this.sourceApplyingUndo ||
      this.sourceComposing
    ) {
      return;
    }
    const editor = this.elements.sourceCode;
    const now = Date.now();
    const groupedTypes = new Set([
      "insertText",
      "insertCompositionText",
      "deleteContentBackward",
      "deleteContentForward"
    ]);
    const shouldGroup = (
      groupedTypes.has(inputType) &&
      inputType === this.sourceUndoInputType &&
      now - this.sourceUndoCapturedAt < 700
    );
    if (!shouldGroup) {
      this.sourceUndoStack.push({
        value: editor.value,
        selectionStart: editor.selectionStart,
        selectionEnd: editor.selectionEnd
      });
      if (this.sourceUndoStack.length > 100) {
        this.sourceUndoStack.shift();
      }
    }
    this.sourceUndoInputType = inputType;
    this.sourceUndoCapturedAt = now;
  },
  undoSourceEdit() {
    const editor = this.elements.sourceCode;
    const snapshot = this.sourceUndoStack.pop();
    if (!snapshot) return;

    this.sourceApplyingUndo = true;
    editor.value = snapshot.value;
    editor.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
    this.handleSourceInput();
    this.sourceApplyingUndo = false;
    this.sourceUndoInputType = "";
    this.sourceUndoCapturedAt = 0;
    if (App.state.sourceDirty) {
      this.updateSourceUndoState();
    } else {
      this.resetSourceUndo();
    }
    editor.focus({ preventScroll: true });
  },
  updateSourceSaveState() {
    const state = App.state;
    this.elements.sourceSave.disabled = state.sourceSaving || !state.sourceDirty || !state.scriptSourceLoaded;
    this.elements.sourceSave.classList.toggle("dirty", state.sourceDirty);
    this.elements.sourceSave.classList.toggle("saving", state.sourceSaving);
    this.elements.sourceStatus.textContent = state.sourceSaving ? "Saving..." : (state.sourceSaveStatus || "");
    this.updateSourceUndoState();
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
      this.resetSourceUndo();
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
    this.captureSourceUndo("insertText");
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
    this.setMobileViewportLock(App.state.sourcePanelOpen || templateOpen || App.state.manualAlertMenuOpen);
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
    this.scheduleSourceDiff();
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
        this.scheduleSourceDiff();
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
      manualAlertPriceInput,
      manualAlertSet,
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
      sourceUndo,
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

    manualAlertSet.addEventListener("click", () => {
      this.toggleManualAlertTrigger();
    });

    manualAlertPriceInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      e.stopPropagation();
      const price = this.parseManualAlertPriceInput();
      if (price != null) {
        this.elements.manualAlertPriceInput.value = this.formatPriceInput(price);
        this.setManualAlertPriceValue(price, { updateInput: false });
      }
      this.setManualAlertTriggerFromInput({ moveMenu: true });
    });

    manualAlertPriceInput.addEventListener("input", () => {
      const price = this.parseManualAlertPriceInput();
      if (price == null) {
        this.updateManualAlertSetButtonState();
        return;
      }
      this.setManualAlertPriceValue(price, { updateInput: false });
      this.updateManualAlertSetButtonState();
    });

    manualAlertPriceInput.addEventListener("change", () => {
      const price = this.parseManualAlertPriceInput();
      if (price != null) {
        this.elements.manualAlertPriceInput.value = this.formatPriceInput(price);
        this.setManualAlertPriceValue(price, { updateInput: false });
      }
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

    sourceUndo.addEventListener("click", () => {
      this.undoSourceEdit();
    });

    sourceSave.addEventListener("click", () => {
      this.saveSourcePanel();
    });

    sourceCode.addEventListener("beforeinput", (e) => {
      this.captureSourceUndo(e.inputType || "");
    });

    sourceCode.addEventListener("compositionstart", () => {
      this.captureSourceUndo("insertCompositionText");
      this.sourceComposing = true;
    });

    sourceCode.addEventListener("compositionend", () => {
      this.sourceComposing = false;
      this.sourceUndoInputType = "insertCompositionText";
      this.sourceUndoCapturedAt = Date.now();
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
