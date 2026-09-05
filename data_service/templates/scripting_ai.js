(function () {
  const HISTORY_LIMIT = 40;
  const HISTORY_CONTEXT_LIMIT = 12;
  const STORAGE_PREFIX = "pynereal.scripting.ai.v1";
  const GEOMETRY_KEY = `${STORAGE_PREFIX}.geometry.v2`;
  const EFFORT_LABELS = {
    minimal: "Minimal",
    low: "Low",
    medium: "Medium",
    high: "High",
    xhigh: "Extra High",
  };

  let initialized = false;
  let available = false;
  let api = null;
  let streamSse = null;
  let mobileQuery = null;
  let onDraftChanged = null;
  let context = { path: "", revision: "", content: "", dirty: false, ready: false };
  let messages = [];
  let conversationId = "";
  let pending = false;
  let streamingResponse = false;
  let renderFrame = null;
  let models = [];
  let selectedModel = "";
  let selectedEffort = "";
  let modelMenuAnchor = null;
  let activeRequest = null;
  let openTimer = null;
  let closeTimer = null;
  let desktopDrag = null;
  let desktopResize = null;
  let mobileDrag = null;

  const el = (id) => document.getElementById(id);

  function storageKey(path) {
    return `${STORAGE_PREFIX}.thread.${encodeURIComponent(path)}`;
  }

  function loadThread(path) {
    messages = [];
    conversationId = "";
    if (!path) return;
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey(path)) || "{}");
      if (Array.isArray(parsed.messages)) {
        messages = parsed.messages.filter((message) => (
          message
          && ["user", "assistant"].includes(message.role)
          && typeof message.content === "string"
        )).slice(-HISTORY_LIMIT);
      }
      conversationId = typeof parsed.conversationId === "string"
        ? parsed.conversationId
        : "";
    } catch {
      messages = [];
      conversationId = "";
    }
  }

  function saveThread(path = context.path) {
    if (!path) return;
    const stored = messages.slice(-HISTORY_LIMIT).map((message) => ({
      role: message.role,
      content: message.content,
      ...(message.html ? { html: message.html } : {}),
      ...(message.error ? { error: true } : {}),
    }));
    try {
      localStorage.setItem(storageKey(path), JSON.stringify({
        messages: stored,
        conversationId,
      }));
    } catch {}
  }

  function isOpen() {
    return !el("scripting-ai-panel").classList.contains("hidden");
  }

  function isMobile() {
    return Boolean(mobileQuery && mobileQuery.matches);
  }

  function updateTrigger() {
    const button = el("scripting-ai");
    if (!button) return;
    button.classList.toggle("hidden", !available);
    const usable = available && context.ready;
    button.disabled = !usable;
    button.title = "Script AI";
    button.setAttribute("aria-label", button.title);
    button.setAttribute("aria-expanded", String(isOpen()));
  }

  function setAvailable(value) {
    available = Boolean(value);
    if (!available) close({ immediate: true });
    updateTrigger();
    if (initialized) updateControls();
  }

  function setContext(next = {}) {
    const nextContext = {
      path: String(next.path || ""),
      revision: String(next.revision || ""),
      content: String(next.content || ""),
      dirty: Boolean(next.dirty),
      ready: Boolean(next.ready),
    };
    if (context.path && nextContext.path !== context.path && isOpen()) {
      if (activeRequest) activeRequest.abort();
      activeRequest = null;
      pending = false;
      close({ immediate: true });
    }
    context = nextContext;
    updateTrigger();
    if (initialized) updateControls();
  }

  function autosizeInput() {
    const input = el("scripting-ai-input");
    input.style.height = "auto";
    input.style.height = `${input.scrollHeight + 2}px`;
  }

  function scheduleRender() {
    if (renderFrame !== null) return;
    renderFrame = window.requestAnimationFrame(() => {
      renderFrame = null;
      renderMessages();
    });
  }

  function renderMessages() {
    const box = el("scripting-ai-messages");
    box.replaceChildren();
    if (!messages.length && !pending) {
      const empty = document.createElement("div");
      empty.className = "ai-chat-empty";
      empty.textContent = context.path
        ? `Start a conversation about ${context.path.split("/").pop()}.`
        : "Select a script to start a conversation.";
      box.appendChild(empty);
      return;
    }
    messages.forEach((message) => {
      const node = document.createElement("div");
      node.className = `ai-msg ai-msg-${message.role === "user" ? "user" : "assistant"}`;
      if (message.error) node.classList.add("ai-msg-error");
      if (message.role === "assistant" && !message.error && typeof message.html === "string") {
        node.classList.add("ai-msg-markdown");
        node.innerHTML = message.html;
        node.querySelectorAll("a").forEach((link) => {
          link.target = "_blank";
          link.rel = "noopener noreferrer";
        });
      } else {
        node.textContent = message.content;
      }
      if (message.content || !message.transient || message.error) box.appendChild(node);
      if (message.role === "assistant" && message.transient && message.workStatus) {
        const work = document.createElement("div");
        work.className = "ai-msg-work";
        work.textContent = message.workStatus;
        box.appendChild(work);
      }
    });
    if (pending && !streamingResponse) {
      const waiting = document.createElement("div");
      waiting.className = "ai-msg ai-msg-assistant ai-msg-pending";
      waiting.innerHTML = '<span class="ai-dot">&#9679;</span><span class="ai-dot">&#9679;</span><span class="ai-dot">&#9679;</span>';
      box.appendChild(waiting);
    }
    box.scrollTop = box.scrollHeight;
  }

  function supportedEfforts() {
    const selected = models.find((model) => model.value === selectedModel);
    return selected && Array.isArray(selected.efforts) ? selected.efforts : [];
  }

  function clampEffort() {
    const efforts = supportedEfforts();
    if (!efforts.length || efforts.includes(selectedEffort)) return;
    selectedEffort = efforts.includes("medium") ? "medium" : efforts[efforts.length - 1];
  }

  function updateModelControls() {
    const selected = models.find((model) => model.value === selectedModel);
    let label = selected ? selected.label : "Default model";
    if (selected && selectedEffort) {
      label += ` · ${EFFORT_LABELS[selectedEffort] || selectedEffort}`;
    }
    el("scripting-ai-model-label").textContent = label;
    el("scripting-ai-model-selector").disabled = pending || models.length === 0;
    el("scripting-ai-model-menu").querySelectorAll(".ai-model-option").forEach((option) => {
      const current = option.dataset.kind === "effort" ? selectedEffort : selectedModel;
      option.setAttribute("aria-selected", String(option.dataset.value === current));
    });
  }

  function pushPreferences() {
    api("/api/ai/chat/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: selectedModel || null, effort: selectedEffort || null }),
    }).catch(() => {});
  }

  function renderModelMenu() {
    const menu = el("scripting-ai-model-menu");
    menu.replaceChildren();
    const heading = (text) => {
      const node = document.createElement("div");
      node.className = "ai-model-menu-heading";
      node.textContent = text;
      menu.appendChild(node);
    };
    const option = (kind, value, label, description, selected, handler) => {
      const button = document.createElement("button");
      button.className = "ai-model-option";
      button.type = "button";
      button.dataset.kind = kind;
      button.dataset.value = value;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(selected));
      const labelNode = document.createElement("span");
      labelNode.className = "ai-model-option-label";
      labelNode.textContent = label;
      button.appendChild(labelNode);
      if (description) {
        const descriptionNode = document.createElement("span");
        descriptionNode.className = "ai-model-option-description";
        descriptionNode.textContent = description;
        button.appendChild(descriptionNode);
      }
      button.addEventListener("click", handler);
      menu.appendChild(button);
    };
    heading("Model");
    models.forEach((model) => option(
      "model",
      model.value,
      model.label,
      model.description,
      model.value === selectedModel,
      () => {
        selectedModel = model.value;
        clampEffort();
        pushPreferences();
        renderModelMenu();
        updateModelControls();
        positionModelMenu(modelMenuAnchor);
      },
    ));
    const efforts = supportedEfforts();
    if (efforts.length) {
      heading("Reasoning");
      efforts.forEach((effort) => option(
        "effort",
        effort,
        EFFORT_LABELS[effort] || effort,
        "",
        effort === selectedEffort,
        () => {
          selectedEffort = effort;
          pushPreferences();
          updateModelControls();
          closeModelMenu();
        },
      ));
    }
  }

  async function loadModels() {
    if (models.length) return;
    try {
      const response = await api("/api/ai/models");
      models = Array.isArray(response.models)
        ? response.models.filter((model) => model && model.value && model.label)
        : [];
      const selected = models.find((model) => model.value === response.selected_model)
        || models.find((model) => model.is_default)
        || models[0];
      selectedModel = selected ? selected.value : "";
      selectedEffort = typeof response.selected_effort === "string"
        ? response.selected_effort
        : "";
      clampEffort();
      renderModelMenu();
      updateModelControls();
    } catch {
      models = [];
      el("scripting-ai-model-label").textContent = "Models unavailable";
      el("scripting-ai-model-selector").disabled = true;
    }
  }

  function positionModelMenu(anchor) {
    if (!anchor || el("scripting-ai-model-menu").classList.contains("hidden")) return;
    const panel = el("scripting-ai-panel");
    const menu = el("scripting-ai-model-menu");
    const panelRect = panel.getBoundingClientRect();
    const anchorRect = anchor.getBoundingClientRect();
    const margin = 8;
    let left = isMobile() ? 12 : anchorRect.right - panelRect.left - menu.offsetWidth;
    let top = isMobile()
      ? anchorRect.bottom - panelRect.top + 6
      : anchorRect.top - panelRect.top - menu.offsetHeight - 7;
    left = Math.min(Math.max(left, margin), panel.clientWidth - menu.offsetWidth - margin);
    top = Math.min(Math.max(top, margin), panel.clientHeight - menu.offsetHeight - margin);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  function openModelMenu(anchor) {
    if (!models.length || pending) return;
    const menu = el("scripting-ai-model-menu");
    if (!menu.classList.contains("hidden") && modelMenuAnchor === anchor) {
      closeModelMenu();
      return;
    }
    modelMenuAnchor = anchor;
    menu.classList.remove("hidden");
    el("scripting-ai-title").setAttribute("aria-expanded", String(anchor === el("scripting-ai-title")));
    el("scripting-ai-model-selector").setAttribute(
      "aria-expanded",
      String(anchor === el("scripting-ai-model-selector")),
    );
    positionModelMenu(anchor);
  }

  function closeModelMenu() {
    modelMenuAnchor = null;
    el("scripting-ai-model-menu").classList.add("hidden");
    el("scripting-ai-title").setAttribute("aria-expanded", "false");
    el("scripting-ai-model-selector").setAttribute("aria-expanded", "false");
  }

  function updateControls() {
    const usable = available && context.ready;
    el("scripting-ai-send").disabled = pending || !usable;
    el("scripting-ai-send").title = pending
      ? "Sending"
      : !available
        ? "AI unavailable"
        : context.ready
          ? "Send"
          : "Select a script first";
    el("scripting-ai-clear").disabled = pending;
    updateModelControls();
  }

  async function sendMessage() {
    if (pending || !context.ready) return;
    const input = el("scripting-ai-input");
    const message = input.value.trim();
    if (!message) return;
    const requestContext = { ...context };
    const requestPath = requestContext.path;
    const history = messages.filter((item) => !item.error).slice(-HISTORY_CONTEXT_LIMIT)
      .map((item) => ({ role: item.role, content: item.content }));
    messages.push({ role: "user", content: message });
    input.value = "";
    autosizeInput();
    pending = true;
    streamingResponse = false;
    updateControls();
    saveThread(requestPath);
    renderMessages();
    const controller = new AbortController();
    activeRequest = controller;
    let assistantMessage = null;
    let completed = false;
    let finalAnswerStarted = false;
    try {
      await streamSse("/api/scripting/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          message,
          history,
          path: requestPath,
          revision: requestContext.revision,
          draft_content: requestContext.content,
          draft_dirty: requestContext.dirty,
          conversation_id: conversationId || null,
          model: selectedModel || null,
          effort: selectedEffort || null,
        }),
      }, (eventName, data) => {
        if (eventName === "conversation") {
          conversationId = data.conversation_id || conversationId;
          saveThread(requestPath);
          return;
        }
        if (eventName === "status") {
          if (finalAnswerStarted) return;
          if (!assistantMessage) {
            assistantMessage = { role: "assistant", content: "", transient: true };
            messages.push(assistantMessage);
          }
          assistantMessage.content = data.text || "...";
          delete assistantMessage.html;
          assistantMessage.transient = true;
          streamingResponse = true;
          scheduleRender();
          return;
        }
        if (eventName === "work_status") {
          if (finalAnswerStarted) return;
          if (!assistantMessage) {
            assistantMessage = { role: "assistant", content: "", transient: true };
            messages.push(assistantMessage);
          }
          assistantMessage.workStatus = data.text || "Working...";
          assistantMessage.transient = true;
          streamingResponse = true;
          scheduleRender();
          return;
        }
        if (eventName === "delta") {
          if (!assistantMessage) {
            assistantMessage = { role: "assistant", content: "" };
            messages.push(assistantMessage);
          }
          if (!finalAnswerStarted) {
            assistantMessage.content = "";
            delete assistantMessage.html;
            delete assistantMessage.transient;
            delete assistantMessage.workStatus;
            finalAnswerStarted = true;
          }
          assistantMessage.content += data.text || "";
          if (typeof data.html === "string") assistantMessage.html = data.html;
          streamingResponse = true;
          scheduleRender();
          return;
        }
        if (eventName === "done") {
          if (!assistantMessage) {
            assistantMessage = { role: "assistant", content: "" };
            messages.push(assistantMessage);
          }
          delete assistantMessage.transient;
          delete assistantMessage.workStatus;
          assistantMessage.content = data.answer || assistantMessage.content || "(empty response)";
          if (typeof data.html === "string") assistantMessage.html = data.html;
          finalAnswerStarted = true;
          completed = true;
          const revisedDraft = typeof data.draft_content === "string"
            ? data.draft_content
            : null;
          if (revisedDraft !== null && onDraftChanged) {
            void onDraftChanged(requestPath, revisedDraft, requestContext.content);
          }
          return;
        }
        if (eventName === "stream_error") throw new Error(data.error || "AI stream failed");
      });
      if (!completed) throw new Error("AI stream ended before completion");
    } catch (error) {
      if (error && error.name === "AbortError") return;
      if (assistantMessage && assistantMessage.transient) {
        assistantMessage.content = `AI call failed: ${error.message}`;
        assistantMessage.error = true;
        delete assistantMessage.html;
        delete assistantMessage.transient;
        delete assistantMessage.workStatus;
      } else {
        messages.push({ role: "assistant", content: `AI call failed: ${error.message}`, error: true });
      }
    } finally {
      if (activeRequest === controller) activeRequest = null;
      pending = false;
      streamingResponse = false;
      updateControls();
      saveThread(requestPath);
      renderMessages();
    }
  }

  async function clearChat() {
    if (pending) return;
    const previousConversation = conversationId;
    messages = [];
    conversationId = "";
    saveThread();
    renderMessages();
    if (previousConversation) {
      api("/api/scripting/ai/chat/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: previousConversation }),
      }).catch(() => {});
    }
  }

  function geometry() {
    try {
      const parsed = JSON.parse(localStorage.getItem(GEOMETRY_KEY) || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function saveGeometry() {
    if (isMobile() || !isOpen()) return;
    const rect = el("scripting-ai-panel").getBoundingClientRect();
    try {
      localStorage.setItem(GEOMETRY_KEY, JSON.stringify({
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      }));
    } catch {}
  }

  function applyDesktopGeometry() {
    const panel = el("scripting-ai-panel");
    const stored = geometry();
    const margin = 10;
    const width = Math.min(
      Math.max(Number(stored.width) || 520, 320),
      Math.max(320, window.innerWidth - 40),
    );
    const height = Math.min(
      Math.max(Number(stored.height) || 520, 300),
      Math.max(300, window.innerHeight - 130),
    );
    const defaultLeft = window.innerWidth - width - 24;
    const defaultTop = window.innerHeight - height - 24;
    const left = Math.min(
      Math.max(Number.isFinite(Number(stored.left)) ? Number(stored.left) : defaultLeft, margin),
      Math.max(margin, window.innerWidth - width - margin),
    );
    const top = Math.min(
      Math.max(Number.isFinite(Number(stored.top)) ? Number(stored.top) : defaultTop, margin),
      Math.max(margin, window.innerHeight - height - margin),
    );
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.width = `${width}px`;
    panel.style.height = `${height}px`;
    panel.style.maxHeight = "";
  }

  function applyMobileGeometry() {
    const panel = el("scripting-ai-panel");
    panel.style.left = "";
    panel.style.top = "";
    panel.style.right = "";
    panel.style.bottom = "";
    panel.style.width = "";
    panel.style.height = "";
    panel.style.maxHeight = "";
    updateForKeyboard();
  }

  function applyLayout() {
    if (!isOpen() || el("scripting-ai-panel").classList.contains("closing")) return;
    if (isMobile()) applyMobileGeometry();
    else applyDesktopGeometry();
    closeModelMenu();
  }

  function updateForKeyboard() {
    if (!isOpen() || !isMobile() || !window.visualViewport) return;
    const panel = el("scripting-ai-panel");
    if (panel.classList.contains("closing")) return;
    const viewport = window.visualViewport;
    const overlap = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
    if (overlap > 50) {
      panel.style.bottom = `${overlap + 7}px`;
      panel.style.maxHeight = `${Math.max(180, viewport.height - 24)}px`;
    } else {
      panel.style.bottom = "";
      panel.style.maxHeight = "";
    }
  }

  function activateDesktopWindow() {
    if (isMobile()) {
      el("scripting-ai-panel").style.removeProperty("z-index");
      return;
    }
    el("scripting-ai-panel").style.zIndex = String(window.PyneFloatingLayerManager.next());
  }

  function open() {
    if (!available || !context.ready) return;
    if (openTimer !== null) {
      clearTimeout(openTimer);
      openTimer = null;
    }
    if (closeTimer !== null) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    if (!pending) loadThread(context.path);
    const panel = el("scripting-ai-panel");
    activateDesktopWindow();
    panel.classList.remove("hidden", "closing", "dragging", "swipe-closing");
    panel.classList.add("opening");
    panel.style.transform = "";
    panel.style.transition = "";
    panel.setAttribute("aria-hidden", "false");
    applyLayout();
    renderMessages();
    updateControls();
    updateTrigger();
    void loadModels();
    if (!isMobile()) el("scripting-ai-input").focus({ preventScroll: true });
    openTimer = window.setTimeout(() => {
      openTimer = null;
      panel.classList.remove("opening");
    }, 230);
  }

  function finishClose() {
    if (openTimer !== null) clearTimeout(openTimer);
    openTimer = null;
    if (closeTimer !== null) clearTimeout(closeTimer);
    closeTimer = null;
    const panel = el("scripting-ai-panel");
    panel.classList.add("hidden");
    panel.classList.remove("opening", "closing", "dragging", "resizing", "swipe-closing");
    panel.style.transform = "";
    panel.style.transition = "";
    panel.setAttribute("aria-hidden", "true");
    closeModelMenu();
    updateTrigger();
  }

  function close(options = {}) {
    if (!el("scripting-ai-panel") || !isOpen()) return;
    if (openTimer !== null) clearTimeout(openTimer);
    openTimer = null;
    el("scripting-ai-panel").classList.remove("opening");
    if (options.immediate || !isMobile()) {
      finishClose();
      return;
    }
    const panel = el("scripting-ai-panel");
    panel.classList.remove("dragging");
    if (options.fromDrag === true) {
      panel.classList.add("swipe-closing");
      panel.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
      window.requestAnimationFrame(() => {
        panel.style.transform = "translateY(100dvh)";
      });
    } else {
      const rect = panel.getBoundingClientRect();
      panel.style.top = `${rect.top}px`;
      panel.style.bottom = "auto";
      panel.style.height = `${rect.height}px`;
      panel.style.maxHeight = "none";
      panel.style.transform = "";
      panel.style.transition = "";
    }
    panel.classList.add("closing");
    closeTimer = window.setTimeout(finishClose, 220);
  }

  function initDesktopGestures() {
    const panel = el("scripting-ai-panel");
    const header = el("scripting-ai-header");
    const resizeHandle = el("scripting-ai-resize");
    header.addEventListener("pointerdown", (event) => {
      if (isMobile() || !event.isPrimary || event.button !== 0) return;
      const target = event.target instanceof Element ? event.target : null;
      if (target && target.closest(".modal-actions")) return;
      const rect = panel.getBoundingClientRect();
      desktopDrag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
      panel.classList.add("dragging");
      header.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    header.addEventListener("pointermove", (event) => {
      if (!desktopDrag || desktopDrag.pointerId !== event.pointerId) return;
      const rect = panel.getBoundingClientRect();
      const margin = 8;
      const left = Math.min(
        Math.max(desktopDrag.left + event.clientX - desktopDrag.x, margin),
        Math.max(margin, window.innerWidth - rect.width - margin),
      );
      const top = Math.min(
        Math.max(desktopDrag.top + event.clientY - desktopDrag.y, margin),
        Math.max(margin, window.innerHeight - rect.height - margin),
      );
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
    });
    const finishDrag = (event) => {
      if (!desktopDrag || desktopDrag.pointerId !== event.pointerId) return;
      desktopDrag = null;
      panel.classList.remove("dragging");
      if (header.hasPointerCapture(event.pointerId)) header.releasePointerCapture(event.pointerId);
      saveGeometry();
    };
    header.addEventListener("pointerup", finishDrag);
    header.addEventListener("pointercancel", finishDrag);

    resizeHandle.addEventListener("pointerdown", (event) => {
      if (isMobile() || !event.isPrimary || event.button !== 0) return;
      const rect = panel.getBoundingClientRect();
      desktopResize = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        width: rect.width,
        height: rect.height,
      };
      panel.classList.add("resizing");
      resizeHandle.setPointerCapture(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
    });
    resizeHandle.addEventListener("pointermove", (event) => {
      if (!desktopResize || desktopResize.pointerId !== event.pointerId) return;
      const rect = panel.getBoundingClientRect();
      panel.style.width = `${Math.min(
        Math.max(320, desktopResize.width + event.clientX - desktopResize.x),
        window.innerWidth - rect.left - 8,
      )}px`;
      panel.style.height = `${Math.min(
        Math.max(300, desktopResize.height + event.clientY - desktopResize.y),
        window.innerHeight - rect.top - 8,
      )}px`;
    });
    const finishResize = (event) => {
      if (!desktopResize || desktopResize.pointerId !== event.pointerId) return;
      desktopResize = null;
      panel.classList.remove("resizing");
      if (resizeHandle.hasPointerCapture(event.pointerId)) resizeHandle.releasePointerCapture(event.pointerId);
      saveGeometry();
    };
    resizeHandle.addEventListener("pointerup", finishResize);
    resizeHandle.addEventListener("pointercancel", finishResize);
  }

  function initMobileGestures() {
    const panel = el("scripting-ai-panel");
    const header = el("scripting-ai-header");
    const handle = el("scripting-ai-sheet-handle");
    const dragTargets = [header, handle];
    const start = (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (
        !isMobile()
        || !event.isPrimary
        || !isOpen()
        || (target && target.closest("button"))
      ) return;
      mobileDrag = {
        id: event.pointerId,
        handle: event.currentTarget,
        startX: event.clientX,
        startY: event.clientY,
        dy: 0,
        active: false,
      };
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch {}
    };
    const move = (event) => {
      if (!mobileDrag || mobileDrag.id !== event.pointerId) return;
      const dx = event.clientX - mobileDrag.startX;
      const dy = event.clientY - mobileDrag.startY;
      if (!mobileDrag.active) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        if (dy <= 0 || Math.abs(dy) <= Math.abs(dx)) {
          mobileDrag = null;
          return;
        }
        mobileDrag.active = true;
        if (openTimer !== null) clearTimeout(openTimer);
        openTimer = null;
        panel.classList.remove("opening");
        closeModelMenu();
        panel.classList.add("dragging");
      }
      event.preventDefault();
      mobileDrag.dy = Math.max(0, dy);
      panel.style.transform = `translateY(${mobileDrag.dy}px)`;
    };
    const finish = (event, cancelled = false) => {
      if (!mobileDrag || mobileDrag.id !== event.pointerId) return;
      const current = mobileDrag;
      mobileDrag = null;
      try { current.handle.releasePointerCapture(current.id); } catch {}
      panel.classList.remove("dragging");
      if (!cancelled && current.active && current.dy > 100) {
        close({ fromDrag: true });
        return;
      }
      if (!current.active) return;
      panel.style.transition = "transform 180ms ease";
      panel.style.transform = "translateY(0)";
      window.setTimeout(() => {
        if (!isOpen()) return;
        panel.style.transition = "";
        panel.style.transform = "";
      }, 190);
    };
    dragTargets.forEach((target) => {
      target.addEventListener("pointerdown", start);
      target.addEventListener("pointermove", move, { passive: false });
      target.addEventListener("pointerup", (event) => finish(event));
      target.addEventListener("pointercancel", (event) => finish(event, true));
    });
  }

  function init(options = {}) {
    if (initialized) return;
    api = options.api;
    streamSse = options.streamSse;
    mobileQuery = options.mobileQuery;
    onDraftChanged = options.onDraftChanged;
    if (typeof api !== "function" || typeof streamSse !== "function" || !mobileQuery) {
      throw new Error("PyneScriptingAi initialization dependencies are unavailable");
    }
    el("scripting-ai").addEventListener("click", open);
    el("scripting-ai-panel").addEventListener("pointerdown", activateDesktopWindow, {
      capture: true,
    });
    el("scripting-ai-close").addEventListener("click", () => close());
    el("scripting-ai-clear").addEventListener("click", () => void clearChat());
    el("scripting-ai-form").addEventListener("submit", (event) => {
      event.preventDefault();
      void sendMessage();
    });
    const input = el("scripting-ai-input");
    let composing = false;
    input.addEventListener("input", autosizeInput);
    input.addEventListener("compositionstart", () => { composing = true; });
    input.addEventListener("compositionend", () => { composing = false; });
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey || composing || event.isComposing) return;
      event.preventDefault();
      void sendMessage();
    });
    input.addEventListener("focus", () => {
      window.setTimeout(updateForKeyboard, 120);
      window.setTimeout(updateForKeyboard, 400);
    });
    el("scripting-ai-model-selector").addEventListener("click", () => {
      if (!isMobile()) openModelMenu(el("scripting-ai-model-selector"));
    });
    el("scripting-ai-title").addEventListener("click", () => {
      if (isMobile()) openModelMenu(el("scripting-ai-title"));
    });
    document.addEventListener("pointerdown", (event) => {
      if (el("scripting-ai-model-menu").classList.contains("hidden")) return;
      const target = event.target instanceof Element ? event.target : null;
      if (target && target.closest("#scripting-ai-model-menu, #scripting-ai-model-selector, #scripting-ai-title")) return;
      closeModelMenu();
    });
    document.addEventListener("pointerdown", (event) => {
      if (!isMobile() || !isOpen()) return;
      const target = event.target instanceof Element ? event.target : null;
      if (!target || el("scripting-ai-panel").contains(target)) return;
      if (target.closest(".scripting-editor-pane")) close();
    }, { capture: true });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !isOpen()) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      close();
    });
    window.addEventListener("resize", applyLayout, { passive: true });
    mobileQuery.addEventListener("change", applyLayout);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", updateForKeyboard);
      window.visualViewport.addEventListener("scroll", updateForKeyboard);
    }
    initDesktopGestures();
    initMobileGestures();
    updateTrigger();
    initialized = true;
  }

  window.PyneScriptingAi = {
    init,
    setAvailable,
    setContext,
    open,
    close,
  };
})();
