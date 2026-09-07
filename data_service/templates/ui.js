var App = window.App || (window.App = {});

App.ui = {
  elements: {
    chartInfo: document.getElementById("chart-info"),
    chartInfoLine: document.getElementById("chart-info-line"),
    chartInfoBase: document.getElementById("chart-info-base"),
    chartInfoSeparator: document.getElementById("chart-info-separator"),
    chartInfoOhlcv: document.getElementById("chart-info-ohlcv"),
    chartRunnerStatus: document.getElementById("chart-runner-status"),
    chartRunnerPopover: document.getElementById("chart-runner-popover"),
    chartRunnerStatusText: document.getElementById("chart-runner-status-text"),
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
    sourceFind: document.getElementById("source-find-toggle"),
    sourceFindPanel: document.getElementById("source-find-panel"),
    sourceFindInput: document.getElementById("source-find-input"),
    sourceFindCount: document.getElementById("source-find-count"),
    sourceFindPrevious: document.getElementById("source-find-previous"),
    sourceFindNext: document.getElementById("source-find-next"),
    sourceFindClose: document.getElementById("source-find-close"),
    sourceReplaceToggle: document.getElementById("source-replace-toggle"),
    sourceReplaceRow: document.getElementById("source-replace-row"),
    sourceReplaceInput: document.getElementById("source-replace-input"),
    sourceReplaceOne: document.getElementById("source-replace-one"),
    sourceReplaceAll: document.getElementById("source-replace-all"),
    sourceNote: document.getElementById("source-note-toggle"),
    sourceNoteEditor: document.getElementById("source-note-editor"),
    sourceNoteInput: document.getElementById("source-note-input"),
    sourceNoteClose: document.getElementById("source-note-close"),
    sourceNoteSave: document.getElementById("source-note-save"),
    sourceHistory: document.getElementById("source-history"),
    sourceSave: document.getElementById("source-save"),
    sourceResizeHandle: document.getElementById("source-resize-handle"),
    sourcePanelName: document.getElementById("source-panel-name"),
    sourceStatus: document.getElementById("source-status"),
    sourceNotice: document.getElementById("source-notice"),
    sourceNoticeText: document.getElementById("source-notice-text"),
    sourceReload: document.getElementById("source-reload"),
    sourceCode: document.getElementById("source-code"),
    sourceHistoryPanel: document.getElementById("source-history-panel"),
    sourceHistoryPath: document.getElementById("source-history-path"),
    sourceHistoryClose: document.getElementById("source-history-close"),
    sourceHistoryState: document.getElementById("source-history-state"),
    sourceHistoryContent: document.getElementById("source-history-content"),
    sourceHistoryList: document.getElementById("source-history-list"),
    sourceHistoryTitle: document.getElementById("source-history-title"),
    sourceHistoryMeta: document.getElementById("source-history-meta"),
    sourceHistoryDiff: document.getElementById("source-history-diff"),
    sourceHistoryRestore: document.getElementById("source-history-restore")
  },
  manualAlertDragState: null,
  manualAlertPendingSend: null,
  manualAlertStatusClearTimer: null,
  manualAlertTriggerSyncTimer: null,
  manualAlertArmedInputDirty: false,
  activeTemplatePlaceholder: null,
  sourceDiffTimer: null,
  sourceEditorController: null,
  sourceUndoPointerType: "",
  sourceHistoryRequestSeq: 0,
  sourceHistoryDiffRequestSeq: 0,
  setChartInfo(ohlcvText = null) {
    const state = App.state;
    if (ohlcvText !== null) {
      state.baseInfoText = ohlcvText || "";
    }
    this.elements.chartInfoBase.innerHTML = String(state.baseInfoTop || "")
      .split(" | ")
      .map((part) => this.escapeHtml(part))
      .join('<span class="chart-info-sep chart-info-sep-strong" aria-hidden="true"></span>');
    this.elements.chartInfoOhlcv.innerHTML = state.baseInfoText || "";
    this.elements.chartInfoSeparator.classList.toggle("hidden", !state.baseInfoText);
    this.updateRunnerStatus();
    if (state.scriptTitleVisible) {
      this.elements.chartInfoTitle.textContent = state.scriptTitle;
      this.elements.chartInfoTitleRow.classList.remove("hidden");
    } else {
      this.elements.chartInfoTitle.textContent = "";
      this.elements.chartInfoTitleRow.classList.add("hidden");
    }
  },
  runnerStatusText(now = Date.now()) {
    const state = App.state;
    if (!state.runnerConnected || state.runnerPhase === "stopped") return "stopped";
    if (state.runnerPhase === "prerun_active") return "warming up";
    if (state.runnerPhase === "prerun_scheduled" && Number.isFinite(state.nextPrerunAt)) {
      const remaining = Math.ceil((state.nextPrerunAt - now) / 1000);
      if (remaining >= 1 && remaining <= 5) return `warming up in ${remaining}s`;
      if (remaining <= 0) return "warming up";
    }
    return "running";
  },
  updateRunnerStatus() {
    const { chartRunnerStatus, chartRunnerStatusText } = this.elements;
    const state = App.state;
    const visible = Boolean(state.runnerConnected);
    chartRunnerStatus.classList.toggle("hidden", !visible);
    if (!visible) {
      chartRunnerStatus.classList.remove("open");
      chartRunnerStatus.setAttribute("aria-expanded", "false");
      return;
    }
    const text = this.runnerStatusText();
    chartRunnerStatusText.textContent = text;
    chartRunnerStatus.setAttribute("aria-label", `Strategy status: ${text}`);
    chartRunnerStatus.classList.toggle("warming", state.runnerPhase === "prerun_active");
  },
  positionRunnerStatusPopover() {
    const { chartRunnerStatus: button, chartRunnerPopover: popover } = this.elements;
    if (!button || !popover) return;
    popover.style.left = "0px";
    const rect = popover.getBoundingClientRect();
    const viewportPadding = 12;
    const minOffset = viewportPadding - rect.left;
    const maxOffset = window.innerWidth - viewportPadding - rect.right;
    const offset = Math.min(maxOffset, Math.max(minOffset, 0));
    popover.style.left = `${Math.round(offset)}px`;
  },
  initRunnerStatus() {
    const button = this.elements.chartRunnerStatus;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = !button.classList.contains("open");
      if (open) this.positionRunnerStatusPopover();
      button.classList.toggle("open", open);
      button.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("pointerdown", (event) => {
      if (button.contains(event.target)) return;
      button.classList.remove("open");
      button.setAttribute("aria-expanded", "false");
    }, true);
    window.addEventListener("resize", () => {
      if (button.classList.contains("open")) this.positionRunnerStatusPopover();
    });
    window.setInterval(() => this.updateRunnerStatus(), 1000);
    this.updateRunnerStatus();
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
    if (this.elements.sourceNoteInput.value !== state.sourceNote) {
      this.elements.sourceNoteInput.value = state.sourceNote;
    }
    if (!state.sourceDirty) {
      this.elements.sourceCode._pyneCodeEditor.setValue(source);
      this.resetSourceUndo();
    }
    this.renderSourceHighlight();
    if (this.sourceEditorController && this.sourceEditorController.isOpen()) {
      this.sourceEditorController.refresh();
    }
    this.setSourceNotice(state.sourceConflict
      ? "This file changed outside this editor. Reload before saving again."
      : "");
    this.updateSourceSaveState();
  },
  setSourceNotice(message = "") {
    this.elements.sourceNoticeText.textContent = String(message || "");
    this.elements.sourceNotice.classList.toggle("hidden", !message);
  },
  renderSourceHighlight() {
    const codeEditor = this.elements.sourceCode._pyneCodeEditor;
    codeEditor.setLanguage("python");
    this.scheduleSourceDiff();
  },
  clearSourceDiff() {
    if (this.sourceDiffTimer !== null) {
      clearTimeout(this.sourceDiffTimer);
      this.sourceDiffTimer = null;
    }
    this.elements.sourceCode._pyneCodeEditor.setChangedLines();
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
    if (!state.scriptSourceLoaded || !state.sourceDirty) {
      this.clearSourceDiff();
      return;
    }

    const current = this.elements.sourceCode._pyneCodeEditor.getValue();
    if (current === state.scriptSource) {
      this.clearSourceDiff();
      return;
    }

    const changes = this.sourceDiffMarkers(state.scriptSource, current);
    this.elements.sourceCode._pyneCodeEditor.setChangedLines({
      lines: [...changes.lines.entries()].map(([line, type]) => ({ line, type })),
      deletionLines: [...changes.deletions],
    });
  },
  resetSourceUndo() {
    this.elements.sourceCode._pyneCodeEditor.clearHistory();
    this.updateSourceUndoState();
  },
  updateSourceUndoState() {
    const button = this.elements.sourceUndo;
    if (!button) return;
    const codeEditor = this.elements.sourceCode._pyneCodeEditor;
    button.disabled = !App.state.sourceDirty || !codeEditor.canUndo();
  },
  undoSourceEdit({ focusEditor = true } = {}) {
    const codeEditor = this.elements.sourceCode._pyneCodeEditor;
    codeEditor.undo();
    if (focusEditor) codeEditor.focus({ preventScroll: true });
  },
  updateSourceSaveState() {
    const state = App.state;
    this.elements.sourceSave.disabled = state.sourceSaving
      || state.sourceConflict
      || !state.sourceDirty
      || !state.scriptSourceLoaded;
    this.elements.sourceSave.classList.toggle("dirty", state.sourceDirty);
    this.elements.sourceSave.classList.toggle("saving", state.sourceSaving);
    this.elements.sourceNote.disabled = !state.scriptSourceLoaded || state.sourceSaving;
    this.elements.sourceFind.disabled = !state.scriptSourceLoaded;
    this.elements.sourceNote.classList.toggle("note-active", Boolean(state.sourceNote.trim()));
    this.elements.sourceNoteClose.disabled = !state.scriptSourceLoaded
      || state.sourceSaving
      || !state.sourceNote;
    this.elements.sourceNoteSave.disabled = !state.scriptSourceLoaded
      || state.sourceSaving
      || state.sourceConflict
      || !this.sourceNoteChanged();
    this.elements.sourceStatus.textContent = state.sourceSaving ? "Saving..." : (state.sourceSaveStatus || "");
    this.elements.sourceHistory.disabled = !state.scriptSourceLoaded || !state.scriptSourcePath;
    if (state.sourceHistoryOpen) {
      this.elements.sourceHistoryRestore.disabled = state.sourceDirty
        || this.sourceNoteChanged()
        || state.sourceSaving
        || state.sourceConflict
        || !state.sourceHistoryCanRestore;
    }
    this.updateSourceUndoState();
  },
  setSourceNote(value = "") {
    const note = String(value || "").slice(0, 240);
    App.state.sourceNote = note;
    if (this.sourceNoteChanged()) App.state.sourceSaveStatus = "";
    if (this.elements.sourceNoteInput.value !== note) {
      this.elements.sourceNoteInput.value = note;
    }
    this.updateSourceSaveState();
  },
  normalizeSourceNote(value) {
    return String(value || "").trim().replace(/\s+/g, " ");
  },
  sourceNoteChanged() {
    const state = App.state;
    return this.normalizeSourceNote(state.sourceNote)
      !== this.normalizeSourceNote(state.sourceBaseNote);
  },
  sourceHasUnsavedChanges() {
    return App.state.sourceDirty || this.sourceNoteChanged();
  },
  setSourceNoteOpen(open) {
    const state = App.state;
    const resolved = Boolean(open) && state.scriptSourceLoaded;
    state.sourceNoteOpen = resolved;
    this.elements.sourceNoteEditor.classList.toggle("hidden", !resolved);
    this.elements.sourceNote.setAttribute("aria-expanded", String(resolved));
    if (resolved) {
      requestAnimationFrame(() => this.elements.sourceNoteInput.focus());
    }
  },
  handleSourceInput() {
    const state = App.state;
    state.sourceDirty = state.scriptSourceLoaded
      && this.elements.sourceCode._pyneCodeEditor.getValue() !== state.scriptSource;
    state.sourceSaveStatus = "";
    if (!state.sourceDirty && !state.sourceConflict) {
      this.setSourceNotice();
    }
    this.renderSourceHighlight();
    if (this.sourceEditorController && this.sourceEditorController.isOpen()) {
      this.sourceEditorController.refresh();
    }
    this.updateSourceSaveState();
  },
  async saveSourcePanel() {
    const state = App.state;
    if (state.sourceSaving || state.sourceConflict || !this.sourceHasUnsavedChanges()) return;

    state.sourceSaving = true;
    state.sourceSaveStatus = "";
    this.updateSourceSaveState();
    const source = this.elements.sourceCode._pyneCodeEditor.getValue();
    const result = await App.data.saveScriptSource(source, state.sourceNote);
    state.sourceSaving = false;

    if (result && result.ok) {
      state.sourceSaveStatus = result.data.saved ? "Applies at next warm-up" : "Note saved";
      state.sourceConflict = false;
      this.setSourceNotice();
      this.elements.sourceCode._pyneCodeEditor.setValue(state.scriptSource);
      this.setSourceNote(state.sourceBaseNote);
      this.setSourceNoteOpen(false);
      this.resetSourceUndo();
      this.renderSourceHighlight();
      this.updateSourceSaveState();
      if (state.sourceHistoryOpen) {
        await this.openSourceHistory();
      }
      setTimeout(() => {
        if (
          !this.sourceHasUnsavedChanges()
          && ["Applies at next warm-up", "Note saved"].includes(state.sourceSaveStatus)
        ) {
          state.sourceSaveStatus = "";
          this.updateSourceSaveState();
        }
      }, 1500);
      return;
    }

    state.sourceSaveStatus = (result && result.error) || "Save failed";
    if (result && result.status === 409) {
      state.sourceConflict = true;
      this.setSourceNotice("This file changed outside this editor. Reload before saving again.");
      state.sourceSaveStatus = "Conflict";
    }
    this.updateSourceSaveState();
  },
  async saveSourceNote() {
    const state = App.state;
    if (state.sourceSaving || state.sourceConflict || !this.sourceNoteChanged()) return;

    state.sourceSaving = true;
    state.sourceSaveStatus = "";
    this.updateSourceSaveState();
    const result = await App.data.saveScriptNote(state.sourceNote);
    state.sourceSaving = false;

    if (result && result.ok) {
      state.sourceSaveStatus = "Note saved";
      state.sourceConflict = false;
      this.setSourceNotice();
      this.setSourceNote(state.sourceBaseNote);
      this.updateSourceSaveState();
      if (state.sourceHistoryOpen) {
        await this.openSourceHistory();
      }
      setTimeout(() => {
        if (!this.sourceNoteChanged() && state.sourceSaveStatus === "Note saved") {
          state.sourceSaveStatus = "";
          this.updateSourceSaveState();
        }
      }, 1500);
      return;
    }

    state.sourceSaveStatus = (result && result.error) || "Save failed";
    if (result && result.status === 409) {
      state.sourceConflict = true;
      this.setSourceNotice("This file changed outside this editor. Reload before saving again.");
      state.sourceSaveStatus = "Conflict";
    }
    this.updateSourceSaveState();
  },
  sourceRevisionLabel(source) {
    return ({
      baseline: "Baseline",
      manual: "Manual save",
      ai: "AI edit",
      external: "External edit",
      restore: "Restored version"
    })[String(source || "")] || "Saved version";
  },
  sourceRevisionTime(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return String(value || "");
    return date.toLocaleString([], {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    });
  },
  setSourceHistoryState(message = "", error = false) {
    this.elements.sourceHistoryState.textContent = String(message || "");
    this.elements.sourceHistoryState.classList.toggle("hidden", !message);
    this.elements.sourceHistoryState.classList.toggle("error", error);
    this.elements.sourceHistoryContent.classList.toggle("hidden", Boolean(message));
  },
  renderSourceHistoryList({ preserveScroll = true } = {}) {
    const state = App.state;
    const scrollTop = preserveScroll ? this.elements.sourceHistoryList.scrollTop : 0;
    const fragment = document.createDocumentFragment();
    state.sourceHistoryRows.forEach((revision) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "source-history-item";
      button.classList.toggle("selected", revision.id === state.sourceHistorySelectedId);
      button.dataset.revisionId = String(revision.id);
      const title = document.createElement("strong");
      title.textContent = this.sourceRevisionLabel(revision.source);
      const time = document.createElement("time");
      time.textContent = this.sourceRevisionTime(revision.created_at);
      const meta = document.createElement("span");
      meta.textContent = `${String(revision.revision || "").slice(0, 8)} · ${revision.line_count || 0} lines`;
      button.append(title, time, meta);
      const note = String(revision.note || "").trim();
      if (note) {
        const noteRow = document.createElement("small");
        noteRow.className = "source-history-item-note";
        noteRow.textContent = note;
        noteRow.title = note;
        button.appendChild(noteRow);
      }
      fragment.appendChild(button);
    });
    this.elements.sourceHistoryList.replaceChildren(fragment);
    this.elements.sourceHistoryList.scrollTop = scrollTop;
  },
  renderSourceHistoryDiff(diff) {
    const fragment = document.createDocumentFragment();
    String(diff || "").split("\n").forEach((line) => {
      const row = document.createElement("span");
      row.className = "history-diff-line";
      if (line.startsWith("@@")) row.classList.add("hunk");
      else if (line.startsWith("+++ ") || line.startsWith("--- ")) row.classList.add("file");
      else if (line.startsWith("+")) row.classList.add("added");
      else if (line.startsWith("-")) row.classList.add("removed");
      else if (line.startsWith("\\ No newline")) row.classList.add("meta");
      row.textContent = line;
      fragment.appendChild(row);
    });
    this.elements.sourceHistoryDiff.replaceChildren(fragment);
  },
  async openSourceHistory() {
    const state = App.state;
    if (!state.scriptSourcePath) return;
    state.sourceHistoryOpen = true;
    state.sourceHistorySelectedId = null;
    state.sourceHistoryCanRestore = false;
    this.elements.sourceHistoryPath.textContent = state.scriptSourcePath;
    this.elements.sourceHistoryPanel.classList.remove("hidden");
    this.elements.sourceHistoryPanel.setAttribute("aria-hidden", "false");
    this.elements.sourceHistoryTitle.textContent = "Select a version";
    this.elements.sourceHistoryMeta.textContent = "";
    this.elements.sourceHistoryDiff.textContent = "Select a version to review its changes.";
    this.elements.sourceHistoryRestore.disabled = true;
    this.setSourceHistoryState("Loading history...");
    const seq = ++this.sourceHistoryRequestSeq;
    const result = await App.data.loadScriptHistory();
    if (seq !== this.sourceHistoryRequestSeq || !state.sourceHistoryOpen) return;
    if (!result || !result.ok) {
      this.setSourceHistoryState((result && result.error) || "Version history could not be loaded", true);
      return;
    }
    state.sourceHistoryRows = Array.isArray(result.data.revisions) ? result.data.revisions : [];
    if (
      state.scriptSourceRevision
      && result.data.current_revision
      && result.data.current_revision !== state.scriptSourceRevision
    ) {
      state.sourceConflict = true;
      state.sourceSaveStatus = "Conflict";
      this.setSourceNotice("This file changed outside this editor. Reload before saving or restoring.");
    }
    this.renderSourceHistoryList({ preserveScroll: false });
    if (!state.sourceHistoryRows.length) {
      this.setSourceHistoryState("No saved versions.");
      return;
    }
    this.setSourceHistoryState();
    await this.selectSourceHistoryRevision(state.sourceHistoryRows[0].id);
  },
  closeSourceHistory() {
    const state = App.state;
    state.sourceHistoryOpen = false;
    state.sourceHistorySelectedId = null;
    state.sourceHistoryCanRestore = false;
    this.sourceHistoryRequestSeq += 1;
    this.sourceHistoryDiffRequestSeq += 1;
    this.elements.sourceHistoryPanel.classList.add("hidden");
    this.elements.sourceHistoryPanel.setAttribute("aria-hidden", "true");
  },
  async selectSourceHistoryRevision(revisionId) {
    const state = App.state;
    const resolvedId = Number(revisionId);
    const revision = state.sourceHistoryRows.find((item) => item.id === resolvedId);
    if (!revision) return;
    state.sourceHistorySelectedId = resolvedId;
    state.sourceHistoryCanRestore = false;
    this.renderSourceHistoryList();
    this.elements.sourceHistoryTitle.textContent = this.sourceRevisionLabel(revision.source);
    this.elements.sourceHistoryMeta.textContent = this.sourceRevisionTime(revision.created_at);
    this.elements.sourceHistoryDiff.textContent = "Loading diff...";
    this.elements.sourceHistoryRestore.disabled = true;
    const seq = ++this.sourceHistoryDiffRequestSeq;
    const result = await App.data.loadScriptDiff(resolvedId);
    if (seq !== this.sourceHistoryDiffRequestSeq || state.sourceHistorySelectedId !== resolvedId) return;
    if (!result || !result.ok) {
      this.elements.sourceHistoryDiff.textContent = (result && result.error) || "Diff could not be loaded";
      return;
    }
    state.sourceHistoryCanRestore = Boolean(result.data.changed) && !state.sourceConflict;
    if (result.data.changed) {
      this.renderSourceHistoryDiff(result.data.diff || "No textual changes.");
    } else {
      this.elements.sourceHistoryDiff.textContent = "This is the current file content.";
    }
    this.updateSourceSaveState();
  },
  async restoreSourceHistoryRevision() {
    const state = App.state;
    if (
      this.sourceHasUnsavedChanges()
      || state.sourceSaving
      || state.sourceConflict
      || !state.sourceHistoryCanRestore
      || state.sourceHistorySelectedId == null
    ) return;
    state.sourceSaving = true;
    state.sourceSaveStatus = "";
    this.updateSourceSaveState();
    const result = await App.data.restoreScriptRevision(state.sourceHistorySelectedId);
    state.sourceSaving = false;
    if (!result || !result.ok) {
      if (result && result.status === 409) {
        state.sourceConflict = true;
        state.sourceSaveStatus = "Conflict";
        this.setSourceNotice("This file changed before restore. Reload and review the version again.");
      } else {
        state.sourceSaveStatus = (result && result.error) || "Restore failed";
      }
      this.updateSourceSaveState();
      return;
    }
    state.sourceSaveStatus = "Applies at next warm-up";
    this.elements.sourceCode._pyneCodeEditor.setValue(state.scriptSource);
    this.setSourceNote(state.sourceBaseNote);
    this.setSourceNoteOpen(false);
    this.resetSourceUndo();
    this.renderSourceHighlight();
    this.setSourceNotice();
    this.updateSourceSaveState();
    await App.data.loadInfo();
    await this.openSourceHistory();
  },
  async reloadSourceFromDisk() {
    const loaded = await App.data.loadScriptSource();
    if (!loaded) {
      App.state.sourceSaveStatus = "Reload failed";
      this.updateSourceSaveState();
      return;
    }
    App.state.sourceConflict = false;
    this.setSourceNote(App.state.sourceBaseNote);
    this.setSourceNoteOpen(false);
    this.setSourceNotice();
    this.renderSourcePanel();
  },
  toggleSourceComments() {
    this.elements.sourceCode._pyneCodeEditor.toggleComment();
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
      if (!this.sourceHasUnsavedChanges()) {
        await App.data.loadScriptSource();
      }
      this.renderSourcePanel();
    } else if (this.elements.sourceCode) {
      this.closeSourceHistory();
      if (this.sourceEditorController) {
        this.sourceEditorController.close({ focusEditor: false });
      }
      this.elements.sourceCode._pyneCodeEditor.blur();
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
      sourceFind,
      sourceNote,
      sourceNoteInput,
      sourceNoteClose,
      sourceNoteSave,
      sourceHistory,
      sourceSave,
      sourceCode,
      sourceReload,
      sourceHistoryClose,
      sourceHistoryList,
      sourceHistoryRestore
    } = this.elements;
    window.PyneCodeMirror.create(sourceCode, {
      value: sourceCode.textContent || "No source loaded.",
      language: "python",
      ariaLabel: "Script source",
    });
    this.sourceEditorController = window.PyneEditor.create({
      editor: sourceCode,
      panel: this.elements.sourceFindPanel,
      findInput: this.elements.sourceFindInput,
      replaceInput: this.elements.sourceReplaceInput,
      replaceRow: this.elements.sourceReplaceRow,
      replaceToggle: this.elements.sourceReplaceToggle,
      count: this.elements.sourceFindCount,
      previousButton: this.elements.sourceFindPrevious,
      nextButton: this.elements.sourceFindNext,
      closeButton: this.elements.sourceFindClose,
      replaceButton: this.elements.sourceReplaceOne,
      replaceAllButton: this.elements.sourceReplaceAll,
      afterReplace: () => this.handleSourceInput(),
      onOpen: () => this.setSourceNoteOpen(false),
    });
    this.elements.sourcePanel.addEventListener("keydown", (event) => {
      this.sourceEditorController.handleShortcut(event);
    });
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

    sourceUndo.addEventListener("pointerdown", (e) => {
      this.sourceUndoPointerType = e.pointerType || "";
    });

    sourceUndo.addEventListener("pointercancel", () => {
      this.sourceUndoPointerType = "";
    });

    sourceUndo.addEventListener("click", () => {
      const focusEditor = this.sourceUndoPointerType !== "touch";
      this.sourceUndoPointerType = "";
      this.undoSourceEdit({ focusEditor });
    });

    sourceFind.addEventListener("click", () => {
      this.sourceEditorController.toggle();
    });

    sourceHistory.addEventListener("click", () => {
      this.openSourceHistory();
    });

    sourceHistoryClose.addEventListener("click", () => {
      this.closeSourceHistory();
    });

    sourceHistoryList.addEventListener("click", (e) => {
      const item = e.target && e.target.closest
        ? e.target.closest("[data-revision-id]")
        : null;
      if (!item) return;
      this.selectSourceHistoryRevision(Number(item.dataset.revisionId));
    });

    sourceHistoryRestore.addEventListener("click", () => {
      this.restoreSourceHistoryRevision();
    });

    sourceReload.addEventListener("click", () => {
      this.reloadSourceFromDisk();
    });

    sourceSave.addEventListener("click", () => {
      this.saveSourcePanel();
    });

    sourceNote.addEventListener("click", () => {
      const opening = !App.state.sourceNoteOpen;
      if (opening) this.sourceEditorController.close({ focusEditor: false });
      if (!opening) this.setSourceNote(App.state.sourceBaseNote);
      this.setSourceNoteOpen(opening);
    });

    sourceNoteClose.addEventListener("click", () => {
      this.setSourceNote("");
      sourceNoteInput.focus({ preventScroll: true });
    });

    sourceNoteSave.addEventListener("click", () => {
      this.saveSourceNote();
    });

    sourceNoteInput.addEventListener("input", (e) => {
      this.setSourceNote(e.target.value);
    });

    sourceNoteInput.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" && e.key !== "Enter") return;
      e.preventDefault();
      e.stopPropagation();
      this.setSourceNoteOpen(false);
      sourceCode._pyneCodeEditor.focus({ preventScroll: true });
    });

    sourceCode.addEventListener("input", () => {
      this.handleSourceInput();
    });

    sourceCode.addEventListener("keydown", (e) => {
      if (this.sourceEditorController.handleShortcut(e)) return;
      if (e.defaultPrevented) return;
      if (
        (e.metaKey || e.ctrlKey) &&
        !e.altKey &&
        !e.shiftKey &&
        e.key.toLowerCase() === "z"
      ) {
        e.preventDefault();
        this.undoSourceEdit();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        this.saveSourcePanel();
        return;
      }
      if (
        (e.metaKey || e.ctrlKey) &&
        !e.altKey &&
        (e.key === "/" || e.code === "Slash")
      ) {
        e.preventDefault();
        this.toggleSourceComments();
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        this.elements.chartRunnerStatus.classList.remove("open");
        this.elements.chartRunnerStatus.setAttribute("aria-expanded", "false");
        this.closeManualAlertConfirm();
        this.closeManualAlertMenu();
        this.closeAlertTemplateModal();
        if (App.state.sourceHistoryOpen) {
          this.closeSourceHistory();
        } else {
          this.toggleSourcePanel(false);
        }
      }
    });

    window.addEventListener("beforeunload", (e) => {
      if (!this.sourceHasUnsavedChanges()) return;
      e.preventDefault();
      e.returnValue = "";
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
    this.initRunnerStatus();
  }
};

App.ui.init();
