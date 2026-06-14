var App = window.App || (window.App = {});

App.ui = {
  elements: {
    chartInfo: document.getElementById("chart-info"),
    chartInfoLine: document.getElementById("chart-info-line"),
    chartInfoTitleRow: document.getElementById("chart-info-title-row"),
    chartInfoTitle: document.getElementById("chart-info-title"),
    alertsToggle: document.getElementById("alerts-toggle"),
    alertsMenu: document.getElementById("alerts-menu"),
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
    this.setMobileViewportLock(shouldOpen);
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
    // 발생한 zoom을 사후에 되돌리는 건 최신 iOS에서 신뢰성이 낮으므로, 소스 패널이
    // 열려 있는 동안 viewport에 maximum-scale=1을 걸어 확대 자체를 막는다(11px 표시
    // 크기 유지). 닫히면 원복해 핀치줌을 다시 허용한다.
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
        this.toggleSourcePanel(false);
      }
    });

    document.addEventListener("click", (e) => {
      if (!alertsMenu.contains(e.target) && e.target !== alertsToggle) {
        this.toggleAlertsMenu(false);
      }
    });

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
