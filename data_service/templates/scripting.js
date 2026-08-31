(function () {
  const SCRIPTING_TREE_WIDTH_KEY = "pynereal.scripting.tree.width";
  const SCRIPTING_TREE_COLLAPSED_KEY = "pynereal.scripting.tree.collapsed";
  const SCRIPTING_TREE_MIN_WIDTH = 210;
  const SCRIPTING_TREE_MAX_WIDTH = 520;
  const SCRIPTING_TREE_DEFAULT_WIDTH = 290;
  let initialized = false;
  let assignedFileHandler = null;

  function init(options = {}) {
    if (initialized) return;

    const {
      api,
      closeAiChat,
      closeHubMenu,
      esc,
      lockBodyScroll,
      mobileQuery: mobileHubQuery,
      unlockBodyScroll,
    } = options;
    if (
      typeof api !== "function"
      || typeof closeAiChat !== "function"
      || typeof closeHubMenu !== "function"
      || typeof esc !== "function"
      || typeof lockBodyScroll !== "function"
      || !mobileHubQuery
      || typeof unlockBodyScroll !== "function"
      || !window.PyneEditor
    ) {
      throw new Error("PyneScripting initialization dependencies are unavailable");
    }

    let scriptingEntries = null;
    let scriptingSelectedPath = "";
    let scriptingTreeSelectedPath = "";
    let scriptingTreeSelectedType = "";
    let scriptingTreeSelectionAnchorPath = "";
    let scriptingTreeRequestSeq = 0;
    let scriptingFileRequestSeq = 0;
    let scriptingOpenTimer = null;
    let scriptingCloseTimer = null;
    let scriptingBaseContent = "";
    let scriptingBaseRevision = "";
    let scriptingBaseNote = "";
    let scriptingLanguage = "";
    let scriptingDirty = false;
    let scriptingSaving = false;
    let scriptingConflict = false;
    let scriptingNote = "";
    let scriptingStatusTimer = null;
    let scriptingPendingNavigation = null;
    let scriptingHistoryRows = [];
    let scriptingHistorySelectedId = null;
    let scriptingHistoryRequestSeq = 0;
    let scriptingHistoryDiffRequestSeq = 0;
    let scriptingDiffTimer = null;
    let scriptingUndoStack = [];
    let scriptingUndoPointerType = "";
    let scriptingUndoInputType = "";
    let scriptingUndoCapturedAt = 0;
    let scriptingApplyingUndo = false;
    let scriptingComposing = false;
    let scriptingEditorController = null;
    let scriptingTreeWidth = SCRIPTING_TREE_DEFAULT_WIDTH;
    let scriptingTreeCollapsed = false;
    let scriptingOperationAction = "";
    let scriptingOperationUsage = null;
    let scriptingOperationPaths = [];
    let scriptingOperationCopyTargets = [];
    let scriptingOperationTemplate = "empty";
    let scriptingOperationBusy = false;
    let scriptingOperationCloseTimer = null;
    let scriptingApplyTimer = null;
    let scriptingRestartBusy = false;
    let scriptingRestartPath = "";
    let scriptingPendingAssignedPath = "";
    const scriptingApplyRefreshes = new Set();
    const scriptingPendingApplies = new Map();
    const scriptingExpandedDirectories = new Set();
    const scriptingTreeSelectedPaths = new Set();
    const el = (id) => document.getElementById(id);

    function isScriptingOpen() {
      return !el("scripting-modal").classList.contains("hidden");
    }

    function selectAssignedFile(path) {
      const normalizedPath = String(path || "").trim();
      if (!normalizedPath || scriptingSelectedPath) return;
      scriptingPendingAssignedPath = normalizedPath;
      scriptingEntries = null;
      if (isScriptingOpen()) void loadScriptingTree();
    }

    assignedFileHandler = selectAssignedFile;

    function setScriptingStatus(message, state = "", options = {}) {
      if (scriptingStatusTimer !== null) {
        clearTimeout(scriptingStatusTimer);
        scriptingStatusTimer = null;
      }
      const mode = el("scripting-mode");
      mode.textContent = String(message || (scriptingSelectedPath ? "Ready" : "No file"));
      mode.dataset.state = state;
      mode.disabled = options.actionable !== true;
      mode.setAttribute("aria-haspopup", options.actionable === true ? "dialog" : "false");
      if (options.actionable !== true && scriptingRestartPath && !scriptingRestartBusy) {
        closeScriptingRestart();
      }
      if (state === "saved") {
        scriptingStatusTimer = window.setTimeout(() => {
          scriptingStatusTimer = null;
          if (!scriptingHasUnsavedChanges() && !scriptingSaving) {
            if (!renderScriptingApplyStatus()) setScriptingStatus("Ready");
          }
        }, 3000);
      }
    }

    function scriptingUsageSessionLabel(session) {
      return `${session.exchange} ${session.symbol} ${session.timeframe}`;
    }

    function scriptingWarmupTarget(session) {
      const target = Number(
        session && (session.next_warmup_at || session.next_prerun_at),
      );
      return Number.isFinite(target) && target > 0 ? target : null;
    }

    function formatScriptingRemaining(milliseconds) {
      const totalSeconds = Math.max(1, Math.ceil(milliseconds / 1000));
      if (totalSeconds < 60) return `${totalSeconds}s`;
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      if (minutes < 60) return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
      const hours = Math.floor(minutes / 60);
      const remainingMinutes = minutes % 60;
      return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
    }

    function renderScriptingApplyStatus(now = Date.now()) {
      const state = scriptingPendingApplies.get(scriptingSelectedPath);
      if (
        !state
        || !state.sessions.size
        || scriptingHasUnsavedChanges()
        || scriptingSaving
        || scriptingConflict
      ) return false;
      const sessions = Array.from(state.sessions.values());
      const applying = sessions.some((session) => session.observedWarmup);
      const futureTargets = sessions
        .map((session) => session.target)
        .filter((target) => Number.isFinite(target) && target > now)
        .sort((left, right) => left - right);
      let label = "Applies at next warm-up";
      if (applying) label = "Applying at warm-up";
      else if (futureTargets.length) {
        label += ` (${formatScriptingRemaining(futureTargets[0] - now)})`;
      }
      setScriptingStatus(label, "pending", { actionable: true });
      return true;
    }

    function stopScriptingApplyTimerIfIdle() {
      if (scriptingPendingApplies.size || scriptingApplyTimer === null) return;
      clearInterval(scriptingApplyTimer);
      scriptingApplyTimer = null;
    }

    async function refreshScriptingApplyState(path) {
      const state = scriptingPendingApplies.get(path);
      if (!state || scriptingApplyRefreshes.has(path)) return;
      scriptingApplyRefreshes.add(path);
      state.lastRefreshAt = Date.now();
      try {
        const usage = await api(
          `/api/scripting/usage?path=${encodeURIComponent(path)}`,
          { cache: "no-store" },
        );
        if (scriptingPendingApplies.get(path) !== state) return;
        const now = Date.now();
        const currentSessions = new Map(
          (usage.sessions || []).filter((session) => session.active).map((session) => [session.id, session]),
        );
        state.sessions.forEach((tracked, id) => {
          const current = currentSessions.get(id);
          if (!current) {
            state.sessions.delete(id);
            return;
          }
          const currentTarget = scriptingWarmupTarget(current);
          const currentPhase = String(current.runner_phase || "");
          tracked.exchange = current.exchange;
          tracked.symbol = current.symbol;
          tracked.timeframe = current.timeframe;
          tracked.runner_phase = currentPhase;
          if (currentPhase === "prerun_active") {
            if (tracked.ignoreActiveUntilExit) return;
            tracked.observedWarmup = true;
            if (tracked.target === null) tracked.target = now;
            return;
          }
          if (tracked.ignoreActiveUntilExit) {
            tracked.ignoreActiveUntilExit = false;
            tracked.target = currentTarget;
            return;
          }
          if (tracked.observedWarmup) {
            state.sessions.delete(id);
            return;
          }
          if (tracked.target === null) {
            tracked.target = currentTarget;
            return;
          }
          if (now < tracked.target) {
            if (currentTarget !== null) tracked.target = currentTarget;
            return;
          }
          if (
            (currentTarget !== null && currentTarget > tracked.target)
            || (currentPhase === "running" && currentTarget === null)
          ) {
            state.sessions.delete(id);
          }
        });
        if (!state.sessions.size) {
          scriptingPendingApplies.delete(path);
          if (scriptingSelectedPath === path && !scriptingHasUnsavedChanges()) {
            setScriptingStatus("Ready");
          }
          stopScriptingApplyTimerIfIdle();
          return;
        }
        if (scriptingSelectedPath === path) renderScriptingApplyStatus(now);
      } catch (_error) {
        if (scriptingSelectedPath === path) renderScriptingApplyStatus();
      } finally {
        scriptingApplyRefreshes.delete(path);
      }
    }

    function tickScriptingApplyState() {
      const now = Date.now();
      scriptingPendingApplies.forEach((state, path) => {
        const sessions = Array.from(state.sessions.values());
        const due = sessions.some((session) => (
          session.observedWarmup
          || session.target === null
          || session.target <= now
        ));
        const refreshInterval = sessions.some((session) => (
          session.observedWarmup || (session.target !== null && session.target <= now)
        )) ? 1000 : 5000;
        if (due && now - state.lastRefreshAt >= refreshInterval) {
          void refreshScriptingApplyState(path);
        }
      });
      renderScriptingApplyStatus(now);
    }

    function ensureScriptingApplyTimer() {
      if (scriptingApplyTimer !== null) return;
      scriptingApplyTimer = window.setInterval(tickScriptingApplyState, 1000);
    }

    async function trackScriptingApply(path) {
      try {
        const usage = await api(
          `/api/scripting/usage?path=${encodeURIComponent(path)}`,
          { cache: "no-store" },
        );
        const activeSessions = (usage.sessions || []).filter((session) => session.active);
        if (!activeSessions.length) {
          scriptingPendingApplies.delete(path);
          stopScriptingApplyTimerIfIdle();
          if (scriptingSelectedPath === path) setScriptingStatus("Saved", "saved");
          return;
        }
        scriptingPendingApplies.set(path, {
          lastRefreshAt: Date.now(),
          sessions: new Map(activeSessions.map((session) => [session.id, {
            ...session,
            target: scriptingWarmupTarget(session),
            observedWarmup: false,
            ignoreActiveUntilExit: session.runner_phase === "prerun_active",
          }])),
        });
        ensureScriptingApplyTimer();
        if (scriptingSelectedPath === path) renderScriptingApplyStatus();
      } catch (_error) {
        if (scriptingSelectedPath === path) {
          setScriptingStatus("Applies at next warm-up", "pending");
        }
      }
    }

    function setScriptingNotice(message = "") {
      const notice = el("scripting-editor-notice");
      el("scripting-editor-notice-text").textContent = String(message || "");
      notice.classList.toggle("hidden", !message);
    }

    function setScriptingNote(value = "") {
      scriptingNote = String(value || "").slice(0, 240);
      const input = el("scripting-note-input");
      if (input.value !== scriptingNote) input.value = scriptingNote;
      el("scripting-note-toggle").classList.toggle(
        "note-active",
        Boolean(scriptingNote.trim()),
      );
      updateScriptingEditState();
    }

    function normalizeScriptingNote(value) {
      return String(value || "").trim().replace(/\s+/g, " ");
    }

    function scriptingNoteChanged() {
      return normalizeScriptingNote(scriptingNote) !== scriptingBaseNote;
    }

    function scriptingHasUnsavedChanges() {
      return scriptingDirty || scriptingNoteChanged();
    }

    function setScriptingNoteOpen(open) {
      const resolved = Boolean(open) && Boolean(scriptingSelectedPath && scriptingBaseRevision);
      el("scripting-note-editor").classList.toggle("hidden", !resolved);
      el("scripting-note-toggle").setAttribute("aria-expanded", String(resolved));
      if (resolved) {
        window.requestAnimationFrame(() => el("scripting-note-input").focus());
      }
    }

    function updateScriptingEditState() {
      const loaded = Boolean(scriptingSelectedPath && scriptingBaseRevision);
      const modified = scriptingHasUnsavedChanges();
      el("scripting-save").disabled = !loaded
        || !scriptingDirty
        || scriptingSaving
        || scriptingConflict;
      el("scripting-save").classList.toggle("dirty", scriptingDirty);
      el("scripting-undo").disabled = !scriptingDirty || scriptingUndoStack.length === 0;
      el("scripting-find-toggle").disabled = !loaded;
      el("scripting-note-toggle").disabled = !loaded || scriptingSaving;
      el("scripting-note-close").disabled = !loaded || scriptingSaving || !scriptingNote;
      el("scripting-note-save").disabled = !loaded
        || scriptingSaving
        || scriptingConflict
        || !scriptingNoteChanged();
      el("scripting-history").disabled = !loaded;
      if (scriptingSaving) setScriptingStatus("Saving...", "saving");
      else if (scriptingConflict) setScriptingStatus("Conflict", "conflict");
      else if (modified) setScriptingStatus("Modified", "dirty");
      else if (loaded && !renderScriptingApplyStatus()) setScriptingStatus("Ready");
    }

    function resetScriptingUndo() {
      scriptingUndoStack = [];
      scriptingUndoInputType = "";
      scriptingUndoCapturedAt = 0;
      updateScriptingEditState();
    }

    function captureScriptingUndo(inputType = "") {
      if (inputType === "historyUndo" || inputType === "historyRedo") {
        resetScriptingUndo();
        return;
      }
      if (scriptingApplyingUndo || scriptingComposing) return;
      const code = el("scripting-code");
      const now = Date.now();
      const groupedTypes = new Set([
        "insertText",
        "insertCompositionText",
        "deleteContentBackward",
        "deleteContentForward",
      ]);
      const shouldGroup = groupedTypes.has(inputType)
        && inputType === scriptingUndoInputType
        && now - scriptingUndoCapturedAt < 700;
      if (!shouldGroup) {
        scriptingUndoStack.push({
          value: code.value,
          selectionStart: code.selectionStart,
          selectionEnd: code.selectionEnd,
          scrollTop: code.scrollTop,
          scrollLeft: code.scrollLeft,
        });
        if (scriptingUndoStack.length > 100) scriptingUndoStack.shift();
      }
      scriptingUndoInputType = inputType;
      scriptingUndoCapturedAt = now;
    }

    function undoScriptingEdit({ focusEditor = true } = {}) {
      const snapshot = scriptingUndoStack.pop();
      if (!snapshot) return;
      const code = el("scripting-code");
      scriptingApplyingUndo = true;
      code.value = snapshot.value;
      code.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
      handleScriptingInput();
      code.scrollTop = snapshot.scrollTop;
      code.scrollLeft = snapshot.scrollLeft;
      el("scripting-highlight").scrollTop = code.scrollTop;
      el("scripting-highlight").scrollLeft = code.scrollLeft;
      el("scripting-diff-markers").style.transform = `translateY(${-code.scrollTop}px)`;
      scriptingApplyingUndo = false;
      scriptingUndoInputType = "";
      scriptingUndoCapturedAt = 0;
      updateScriptingEditState();
      if (focusEditor) code.focus({ preventScroll: true });
    }

    function openScriptingUnsaved(action) {
      scriptingPendingNavigation = action;
      el("scripting-unsaved-path").textContent = scriptingSelectedPath;
      el("scripting-unsaved-save").disabled = scriptingConflict;
      const modal = el("scripting-unsaved-modal");
      modal.classList.remove("hidden");
      modal.setAttribute("aria-hidden", "false");
      window.requestAnimationFrame(() => el("scripting-unsaved-save").focus());
    }

    function closeScriptingUnsaved() {
      scriptingPendingNavigation = null;
      const modal = el("scripting-unsaved-modal");
      modal.classList.add("hidden");
      modal.setAttribute("aria-hidden", "true");
    }

    function runScriptingNavigation(action) {
      if (scriptingHasUnsavedChanges()) {
        openScriptingUnsaved(action);
        return false;
      }
      action();
      return true;
    }

    function scriptingFileCount(entries) {
      return (Array.isArray(entries) ? entries : []).reduce((total, entry) => (
        total + (entry && entry.type === "file"
          ? 1
          : scriptingFileCount(entry && entry.children))
      ), 0);
    }

    function scriptingContainsPath(entries, path) {
      for (const entry of Array.isArray(entries) ? entries : []) {
        if (entry && entry.type === "file" && entry.path === path) return true;
        if (entry && entry.type === "directory" && scriptingContainsPath(entry.children, path)) {
          return true;
        }
      }
      return false;
    }

    function scriptingEntryForPath(entries, path) {
      for (const entry of Array.isArray(entries) ? entries : []) {
        if (entry && entry.path === path) return entry;
        if (entry && entry.type === "directory") {
          const nested = scriptingEntryForPath(entry.children, path);
          if (nested) return nested;
        }
      }
      return null;
    }

    function setScriptingTreeSelection(path = "", type = "") {
      scriptingTreeSelectedPath = String(path || "");
      scriptingTreeSelectedType = scriptingTreeSelectedPath ? String(type || "file") : "";
      scriptingTreeSelectionAnchorPath = scriptingTreeSelectedType === "file"
        ? scriptingTreeSelectedPath
        : "";
      scriptingTreeSelectedPaths.clear();
      if (scriptingTreeSelectedPath) scriptingTreeSelectedPaths.add(scriptingTreeSelectedPath);
      updateScriptingTreeSelectionView();
    }

    function updateScriptingTreeSelectionView() {
      const tree = el("scripting-tree");
      tree.querySelectorAll("[data-scripting-path]").forEach((item) => {
        const selected = scriptingTreeSelectedPaths.has(
          String(item.dataset.scriptingPath || ""),
        );
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      updateScriptingActionsToggle();
    }

    function updateScriptingActionsToggle() {
      const toggle = el("scripting-actions-menu-toggle");
      const selectedCount = scriptingTreeSelectedPaths.size;
      if (toggle) toggle.disabled = selectedCount === 0;
      const actionsMenu = el("scripting-actions-menu");
      if (!actionsMenu) return;
      const rename = actionsMenu.querySelector('[data-scripting-operation="rename"]');
      if (rename) {
        rename.disabled = selectedCount !== 1;
        rename.title = selectedCount > 1 ? "Rename is available for one path at a time" : "";
      }
    }

    function scriptingIconMarkup(type, language = "") {
      if (type === "directory") {
        return '<svg viewBox="0 0 24 24" aria-hidden="true">'
          + '<path d="M3 5h6l2 3h10v11H3Z" /></svg>';
      }
      if (language === "markdown") {
        return '<svg viewBox="0 0 24 24" aria-hidden="true">'
          + '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />'
          + '<polyline points="14 2 14 8 20 8" />'
          + '<path d="M8 13v4m0-4 2 2 2-2v4m2-4v4m0 0 2-2m-2 2-2-2" />'
          + '</svg>';
      }
      return '<svg viewBox="0 0 24 24" aria-hidden="true">'
        + '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />'
        + '<polyline points="14 2 14 8 20 8" />'
        + '<polyline points="10 13 8 15 10 17" />'
        + '<polyline points="14 13 16 15 14 17" /></svg>';
    }

    function appendScriptingTreeEntries(container, entries, depth = 0) {
      for (const entry of Array.isArray(entries) ? entries : []) {
        if (!entry || !entry.path || !entry.name) continue;
        const directory = entry.type === "directory";
        const expanded = directory && scriptingExpandedDirectories.has(entry.path);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "scripting-tree-item " + (directory ? "directory" : "file");
        button.classList.toggle("expanded", expanded);
        button.classList.toggle("selected", scriptingTreeSelectedPaths.has(entry.path));
        button.style.setProperty("--scripting-depth", String(depth));
        button.dataset.scriptingPath = entry.path;
        button.dataset.scriptingType = directory ? "directory" : "file";
        button.setAttribute("role", "treeitem");
        button.setAttribute("aria-level", String(depth + 1));
        button.setAttribute("aria-selected", String(scriptingTreeSelectedPaths.has(entry.path)));
        if (directory) button.setAttribute("aria-expanded", String(expanded));

        const chevron = document.createElement("span");
        chevron.className = `scripting-tree-chevron${directory ? "" : " file"}`;
        chevron.setAttribute("aria-hidden", "true");
        const icon = document.createElement("span");
        icon.className = "scripting-tree-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.innerHTML = scriptingIconMarkup(entry.type, entry.language);
        const label = document.createElement("span");
        label.className = "scripting-tree-label";
        label.textContent = entry.name;
        button.append(chevron, icon, label);
        const row = document.createElement("div");
        row.className = "scripting-tree-row";
        row.appendChild(button);
        const actions = document.createElement("button");
        actions.type = "button";
        actions.className = "scripting-tree-row-action";
        actions.dataset.scriptingActionPath = entry.path;
        actions.dataset.scriptingActionType = directory ? "directory" : "file";
        actions.setAttribute("aria-label", `Actions for ${entry.name}`);
        actions.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">'
          + '<circle cx="5" cy="12" r="1.5" />'
          + '<circle cx="12" cy="12" r="1.5" />'
          + '<circle cx="19" cy="12" r="1.5" /></svg>';
        row.appendChild(actions);
        container.appendChild(row);

        if (directory) {
          const children = document.createElement("div");
          children.className = "scripting-tree-children";
          children.classList.toggle("expanded", expanded);
          children.dataset.scriptingChildren = entry.path;
          children.setAttribute("role", "group");
          children.setAttribute("aria-hidden", String(!expanded));
          children.inert = !expanded;
          const childrenInner = document.createElement("div");
          childrenInner.className = "scripting-tree-children-inner";
          appendScriptingTreeEntries(childrenInner, entry.children, depth + 1);
          children.appendChild(childrenInner);
          container.appendChild(children);
        }
      }
    }

    function renderScriptingTree() {
      const tree = el("scripting-tree");
      const scrollTop = tree.scrollTop;
      tree.replaceChildren();
      appendScriptingTreeEntries(tree, scriptingEntries || []);
      tree.scrollTop = scrollTop;
      updateScriptingActionsToggle();
    }

    function setScriptingTreeState(state, message = "") {
      el("scripting-tree-loading").classList.toggle("hidden", state !== "loading");
      el("scripting-tree-error").classList.toggle("hidden", state !== "error");
      el("scripting-tree-empty").classList.toggle("hidden", state !== "empty");
      el("scripting-tree").classList.toggle("hidden", state !== "ready");
      el("scripting-tree-error").textContent = state === "error" ? message : "";
    }

    function closeScriptingTreeMenus() {
      ["new", "actions"].forEach((name) => {
        el(`scripting-${name}-menu`).classList.add("hidden");
        const toggle = el(`scripting-${name}-menu-toggle`);
        if (toggle) toggle.setAttribute("aria-expanded", "false");
      });
    }

    function toggleScriptingTreeMenu(name, anchor = null, point = null) {
      const menu = el(`scripting-${name}-menu`);
      const opening = menu.classList.contains("hidden");
      closeScriptingTreeMenus();
      if (!opening) return;
      menu.classList.remove("hidden");
      const toggle = el(`scripting-${name}-menu-toggle`);
      if (toggle) toggle.setAttribute("aria-expanded", "true");
      if (anchor || point) {
        const pane = anchor
          ? anchor.closest(".scripting-tree-pane")
          : menu.closest(".scripting-tree-pane");
        if (pane) {
          const paneRect = pane.getBoundingClientRect();
          const menuRect = menu.getBoundingClientRect();
          const maximumTop = Math.max(6, paneRect.height - menuRect.height - 6);
          if (point) {
            const maximumLeft = Math.max(6, paneRect.width - menuRect.width - 6);
            menu.style.top = `${Math.max(6, Math.min(maximumTop, point.clientY - paneRect.top))}px`;
            menu.style.left = `${Math.max(6, Math.min(maximumLeft, point.clientX - paneRect.left))}px`;
            menu.style.removeProperty("right");
          } else {
            const anchorRect = anchor.getBoundingClientRect();
            const preferredTop = anchorRect.bottom - paneRect.top + 2;
            const fallbackTop = anchorRect.top - paneRect.top - menuRect.height - 2;
            menu.style.top = `${Math.max(6, Math.min(maximumTop,
              preferredTop + menuRect.height <= paneRect.height - 6 ? preferredTop : fallbackTop))}px`;
            menu.style.right = `${Math.max(6, paneRect.right - anchorRect.right)}px`;
            menu.style.removeProperty("left");
          }
        }
      } else {
        menu.style.removeProperty("top");
        menu.style.removeProperty("right");
        menu.style.removeProperty("left");
      }
      const first = menu.querySelector('button:not(.hidden):not(:disabled)');
      if (first) window.requestAnimationFrame(() => first.focus({ preventScroll: true }));
    }

    function scriptingParentPath(path) {
      const normalized = String(path || "");
      const index = normalized.lastIndexOf("/");
      return index >= 0 ? normalized.slice(0, index) : "";
    }

    function scriptingBaseName(path) {
      return String(path || "").split("/").pop() || "";
    }

    function scriptingSelectedDirectory() {
      if (scriptingTreeSelectedType === "directory") return scriptingTreeSelectedPath;
      return scriptingParentPath(scriptingTreeSelectedPath || scriptingSelectedPath);
    }

    function scriptingJoinPath(parent, name) {
      const normalizedParent = String(parent || "").trim().replace(/^\/+|\/+$/g, "");
      const normalizedName = String(name || "").trim().replace(/^\/+|\/+$/g, "");
      return normalizedParent ? `${normalizedParent}/${normalizedName}` : normalizedName;
    }

    function scriptingCopyName(path, sequence = 1) {
      const name = scriptingBaseName(path);
      const dot = name.lastIndexOf(".");
      const suffix = sequence > 1 ? `_copy${sequence}` : "_copy";
      return dot > 0
        ? `${name.slice(0, dot)}${suffix}${name.slice(dot)}`
        : `${name}${suffix}`;
    }

    function scriptingWorkspacePaths(entries, paths = new Set()) {
      for (const entry of Array.isArray(entries) ? entries : []) {
        if (!entry || !entry.path) continue;
        paths.add(String(entry.path));
        if (entry.type === "directory") scriptingWorkspacePaths(entry.children, paths);
      }
      return paths;
    }

    function scriptingCopyTargets(paths) {
      const reserved = scriptingWorkspacePaths(scriptingEntries || []);
      return paths.map((sourcePath) => {
        const parent = scriptingParentPath(sourcePath);
        let sequence = 1;
        let targetPath = "";
        do {
          targetPath = scriptingJoinPath(parent, scriptingCopyName(sourcePath, sequence));
          sequence += 1;
        } while (reserved.has(targetPath));
        reserved.add(targetPath);
        return { sourcePath, targetPath };
      });
    }

    function appendScriptingOperationPathList(paths) {
      const list = document.createElement("div");
      list.className = "scripting-operation-path-list mono";
      list.textContent = paths.join("\n");
      el("scripting-operation-fields").appendChild(list);
    }

    function appendScriptingOperationField({
      id,
      label,
      value = "",
      placeholder = "",
      textarea = false,
      required = false,
      wrapperId = "",
    }) {
      const wrapper = document.createElement("label");
      wrapper.className = "scripting-operation-field";
      if (wrapperId) wrapper.id = wrapperId;
      const title = document.createElement("span");
      title.textContent = label;
      const input = document.createElement(textarea ? "textarea" : "input");
      input.id = id;
      input.value = value;
      input.placeholder = placeholder;
      input.required = required;
      input.autocomplete = "off";
      input.spellcheck = false;
      wrapper.append(title, input);
      el("scripting-operation-fields").appendChild(wrapper);
      return input;
    }

    function scriptingDirectoryOptions(entries, depth = 0, options = []) {
      for (const entry of Array.isArray(entries) ? entries : []) {
        if (!entry || entry.type !== "directory" || !entry.path) continue;
        options.push({ path: entry.path, depth });
        scriptingDirectoryOptions(entry.children, depth + 1, options);
      }
      return options;
    }

    function closeScriptingDirectoryMenus(except = null) {
      el("scripting-operation-fields")
        .querySelectorAll(".scripting-directory-select.open")
        .forEach((select) => {
          if (select === except) return;
          select.classList.remove("open");
          select.querySelector(".scripting-directory-trigger")
            ?.setAttribute("aria-expanded", "false");
          select.querySelector(".scripting-directory-menu")
            ?.setAttribute("aria-hidden", "true");
        });
    }

    function appendScriptingDirectoryField({ id, label, value = "" }) {
      const wrapper = document.createElement("div");
      wrapper.className = "scripting-operation-field";
      const title = document.createElement("span");
      title.textContent = label;
      const input = document.createElement("input");
      input.type = "hidden";
      input.id = id;
      const select = document.createElement("div");
      select.className = "scripting-directory-select";
      const trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "scripting-directory-trigger";
      trigger.setAttribute("aria-haspopup", "listbox");
      trigger.setAttribute("aria-expanded", "false");
      const icon = document.createElement("span");
      icon.className = "scripting-directory-icon";
      icon.innerHTML = scriptingIconMarkup("directory");
      const selectedLabel = document.createElement("span");
      selectedLabel.className = "scripting-directory-label";
      const chevron = document.createElement("span");
      chevron.className = "scripting-directory-chevron";
      chevron.setAttribute("aria-hidden", "true");
      trigger.append(icon, selectedLabel, chevron);
      const menu = document.createElement("div");
      menu.id = `${id}-menu`;
      menu.className = "scripting-directory-menu";
      menu.setAttribute("role", "listbox");
      menu.setAttribute("aria-hidden", "true");
      trigger.setAttribute("aria-controls", menu.id);
      const directories = [
        { path: "", depth: 0 },
        ...scriptingDirectoryOptions(scriptingEntries || [], 1),
      ];
      const availablePaths = new Set(directories.map((directory) => directory.path));
      let selectedPath = availablePaths.has(value) ? value : "";

      function setSelected(path) {
        selectedPath = availablePaths.has(path) ? path : "";
        input.value = selectedPath;
        selectedLabel.textContent = selectedPath
          ? `workdir/scripts/${selectedPath}`
          : "workdir/scripts";
        menu.querySelectorAll("[data-scripting-directory]").forEach((option) => {
          const selected = option.dataset.scriptingDirectory === selectedPath;
          option.classList.toggle("selected", selected);
          option.setAttribute("aria-selected", String(selected));
        });
      }

      directories.forEach((directory) => {
        const option = document.createElement("button");
        option.type = "button";
        option.dataset.scriptingDirectory = directory.path;
        option.style.setProperty("--scripting-directory-depth", String(directory.depth));
        option.setAttribute("role", "option");
        option.textContent = directory.path
          ? `workdir/scripts/${directory.path}`
          : "workdir/scripts";
        option.addEventListener("click", () => {
          setSelected(directory.path);
          closeScriptingDirectoryMenus();
          trigger.focus({ preventScroll: true });
        });
        menu.appendChild(option);
      });
      trigger.addEventListener("click", () => {
        const opening = !select.classList.contains("open");
        closeScriptingDirectoryMenus(select);
        select.classList.toggle("open", opening);
        trigger.setAttribute("aria-expanded", String(opening));
        menu.setAttribute("aria-hidden", String(!opening));
        if (opening) {
          window.requestAnimationFrame(() => {
            menu.querySelector(".selected")?.scrollIntoView({ block: "nearest" });
          });
        }
      });
      select.append(trigger, menu);
      wrapper.append(title, input, select);
      el("scripting-operation-fields").appendChild(wrapper);
      setSelected(selectedPath);
      return input;
    }

    function appendScriptingTemplateOptions() {
      const wrapper = document.createElement("div");
      wrapper.className = "scripting-operation-field";
      const title = document.createElement("span");
      title.textContent = "Template";
      const options = document.createElement("div");
      options.className = "scripting-template-options";
      [
        ["empty", "Empty strategy"],
        ["long_short", "Long / short"],
        ["indicator", "Indicator"],
        ["copy", "Copy existing"],
      ].forEach(([value, label]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "scripting-template-option";
        button.classList.toggle("selected", value === scriptingOperationTemplate);
        button.dataset.scriptingTemplate = value;
        button.textContent = label;
        button.setAttribute("aria-pressed", String(value === scriptingOperationTemplate));
        options.appendChild(button);
      });
      wrapper.append(title, options);
      el("scripting-operation-fields").appendChild(wrapper);
    }

    function setScriptingOperationTemplate(template) {
      scriptingOperationTemplate = String(template || "empty");
      el("scripting-operation-fields").querySelectorAll("[data-scripting-template]").forEach((button) => {
        const selected = button.dataset.scriptingTemplate === scriptingOperationTemplate;
        button.classList.toggle("selected", selected);
        button.setAttribute("aria-pressed", String(selected));
      });
      const source = el("scripting-operation-source-field");
      if (source) source.classList.toggle("hidden", scriptingOperationTemplate !== "copy");
      ["scripting-operation-title-field", "scripting-operation-description-field"].forEach((id) => {
        const field = el(id);
        if (field) field.classList.toggle("hidden", scriptingOperationTemplate === "copy");
      });
    }

    function setScriptingOperationError(message = "") {
      const error = el("scripting-operation-error");
      error.textContent = String(message || "");
      error.classList.toggle("hidden", !message);
    }

    function renderScriptingOperationUsage() {
      const box = el("scripting-operation-usage");
      box.replaceChildren();
      const usage = scriptingOperationUsage;
      if (!usage || !Number(usage.session_count)) {
        box.classList.add("hidden");
        box.classList.remove("blocked");
        return;
      }
      const blocked = Number(usage.active_count) > 0;
      box.classList.remove("hidden");
      box.classList.toggle("blocked", blocked);
      const message = document.createElement("div");
      message.textContent = blocked
        ? "Stop the active Runner before changing this path."
        : (
          scriptingOperationAction === "delete"
            ? "Stopped sessions will have their script selection cleared."
            : "Stopped sessions will keep the old script path and must be updated manually."
        );
      const sessions = document.createElement("div");
      sessions.className = "scripting-operation-session-list";
      sessions.textContent = (usage.sessions || []).map((item) => (
        `${item.exchange} ${item.symbol} ${item.timeframe} · ${item.active ? "running" : "stopped"}`
      )).join("\n");
      box.append(message, sessions);
      el("scripting-operation-submit").disabled = blocked || scriptingOperationBusy;
    }

    function renderScriptingOperation(action) {
      scriptingOperationAction = action;
      scriptingOperationUsage = null;
      scriptingOperationTemplate = "empty";
      scriptingOperationBusy = false;
      const fields = el("scripting-operation-fields");
      fields.replaceChildren();
      setScriptingOperationError();
      const title = el("scripting-operation-title");
      const message = el("scripting-operation-message");
      const submit = el("scripting-operation-submit");
      submit.className = "btn btn-primary";
      submit.disabled = false;
      const parent = scriptingSelectedDirectory();
      const selectedPath = scriptingTreeSelectedPath;
      const selectedPaths = scriptingOperationPaths.length
        ? scriptingOperationPaths
        : (selectedPath ? [selectedPath] : []);
      const multiple = selectedPaths.length > 1;

      if (action === "new_strategy") {
        title.textContent = "New strategy";
        message.textContent = "Create a PyneCore strategy draft in workdir/scripts.";
        submit.textContent = "Create";
        appendScriptingDirectoryField({ id: "scripting-operation-parent", label: "Directory", value: parent });
        appendScriptingOperationField({ id: "scripting-operation-name", label: "Filename", value: "strategy.py", required: true });
        appendScriptingOperationField({ id: "scripting-operation-title-input", label: "Strategy title", value: "New Strategy", required: true, wrapperId: "scripting-operation-title-field" });
        appendScriptingOperationField({ id: "scripting-operation-description", label: "Description", textarea: true, wrapperId: "scripting-operation-description-field" });
        appendScriptingTemplateOptions();
        appendScriptingOperationField({
          id: "scripting-operation-source",
          label: "Source strategy path",
          value: scriptingTreeSelectedType === "file" && selectedPath.endsWith(".py") ? selectedPath : "",
          placeholder: "example/strategy.py",
          wrapperId: "scripting-operation-source-field",
        });
        setScriptingOperationTemplate("empty");
      } else if (action === "new_markdown") {
        title.textContent = "New Markdown";
        message.textContent = "Create local strategy notes alongside source files.";
        submit.textContent = "Create";
        appendScriptingDirectoryField({ id: "scripting-operation-parent", label: "Directory", value: parent });
        appendScriptingOperationField({ id: "scripting-operation-name", label: "Filename", value: "notes.md", required: true });
        appendScriptingOperationField({ id: "scripting-operation-title-input", label: "Title", value: "Strategy Notes", required: true });
        appendScriptingOperationField({ id: "scripting-operation-description", label: "Description", textarea: true });
      } else if (action === "new_directory") {
        title.textContent = "New directory";
        message.textContent = "Create an empty directory in workdir/scripts.";
        submit.textContent = "Create";
        appendScriptingDirectoryField({ id: "scripting-operation-parent", label: "Parent directory", value: "" });
        appendScriptingOperationField({ id: "scripting-operation-name", label: "Directory name", value: "new_strategy", required: true });
      } else if (action === "duplicate") {
        if (multiple) {
          title.textContent = `Duplicate ${selectedPaths.length} files`;
          message.textContent = "Create one copy beside each selected file.";
          submit.textContent = "Duplicate all";
          appendScriptingOperationPathList(
            scriptingOperationCopyTargets.map((item) => `${item.sourcePath}  ->  ${item.targetPath}`),
          );
          renderScriptingOperationUsage();
          return;
        }
        const directory = scriptingTreeSelectedType === "directory";
        title.textContent = directory ? "Duplicate directory" : "Duplicate file";
        message.textContent = directory
          ? `Copy ${selectedPath} and its contents. Generated cache files are excluded.`
          : `Create a separate copy of ${selectedPath}.`;
        submit.textContent = "Duplicate";
        appendScriptingOperationField({
          id: "scripting-operation-path",
          label: "New path",
          value: scriptingJoinPath(scriptingParentPath(selectedPath), scriptingCopyName(selectedPath)),
          required: true,
        });
      } else if (action === "rename") {
        title.textContent = "Rename path";
        message.textContent = `Rename ${selectedPath}. Existing version history follows the new path.`;
        submit.textContent = "Rename";
        appendScriptingOperationField({ id: "scripting-operation-path", label: "New path", value: selectedPath, required: true });
      } else {
        if (multiple) {
          title.textContent = `Delete ${selectedPaths.length} files`;
          message.textContent = "Delete every selected file. Version history is retained internally.";
          submit.textContent = "Delete all";
          submit.className = "btn btn-danger-primary";
          appendScriptingOperationPathList(selectedPaths);
          renderScriptingOperationUsage();
          return;
        }
        const directory = scriptingTreeSelectedType === "directory";
        title.textContent = directory ? "Delete directory" : "Delete file";
        message.textContent = directory
          ? `Delete ${selectedPath} and every file and directory inside it. Script version history is retained internally.`
          : `Delete ${selectedPath}. Version history is retained internally.`;
        submit.textContent = "Delete";
        submit.className = "btn btn-danger-primary";
      }
      renderScriptingOperationUsage();
    }

    function finishScriptingOperationClose() {
      if (scriptingOperationCloseTimer !== null) {
        clearTimeout(scriptingOperationCloseTimer);
        scriptingOperationCloseTimer = null;
      }
      const modal = el("scripting-operation-modal");
      const box = modal.querySelector(".scripting-operation-box");
      modal.classList.remove(
        "scripting-operation-closing",
        "scripting-operation-dragging",
        "scripting-operation-swipe-closing",
      );
      box.style.transition = "";
      box.style.transform = "";
      modal.classList.add("hidden");
      modal.setAttribute("aria-hidden", "true");
      scriptingOperationAction = "";
      scriptingOperationUsage = null;
      scriptingOperationPaths = [];
      scriptingOperationCopyTargets = [];
    }

    function closeScriptingOperation(options = {}) {
      if (scriptingOperationBusy) return;
      const modal = el("scripting-operation-modal");
      if (modal.classList.contains("hidden")) return;
      closeScriptingDirectoryMenus();
      if (options.immediate === true || !mobileHubQuery.matches) {
        finishScriptingOperationClose();
        return;
      }
      if (modal.classList.contains("scripting-operation-closing")) return;
      const box = modal.querySelector(".scripting-operation-box");
      modal.classList.remove("scripting-operation-dragging");
      if (options.fromDrag === true) {
        modal.classList.add("scripting-operation-swipe-closing");
        box.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
        window.requestAnimationFrame(() => {
          box.style.transform = "translateY(100dvh)";
        });
      } else {
        box.style.transition = "";
        box.style.transform = "";
      }
      modal.classList.add("scripting-operation-closing");
      scriptingOperationCloseTimer = window.setTimeout(
        finishScriptingOperationClose,
        220,
      );
    }

    function setScriptingRestartError(message = "") {
      const error = el("scripting-restart-error");
      error.textContent = String(message || "");
      error.classList.toggle("hidden", !message);
    }

    function renderScriptingRestartSessions() {
      const state = scriptingPendingApplies.get(scriptingRestartPath);
      const sessions = state ? Array.from(state.sessions.values()) : [];
      el("scripting-restart-sessions").textContent = sessions
        .map(scriptingUsageSessionLabel)
        .join("\n");
      el("scripting-restart-submit").disabled = scriptingRestartBusy || !sessions.length;
    }

    function closeScriptingRestart() {
      if (scriptingRestartBusy) return;
      const modal = el("scripting-restart-modal");
      modal.classList.add("hidden");
      modal.setAttribute("aria-hidden", "true");
      el("scripting-mode").setAttribute("aria-expanded", "false");
      scriptingRestartPath = "";
      setScriptingRestartError();
    }

    function openScriptingRestart() {
      if (!el("scripting-restart-modal").classList.contains("hidden")) {
        closeScriptingRestart();
        return;
      }
      const state = scriptingPendingApplies.get(scriptingSelectedPath);
      if (!state || !state.sessions.size) return;
      scriptingRestartPath = scriptingSelectedPath;
      scriptingRestartBusy = false;
      setScriptingRestartError();
      renderScriptingRestartSessions();
      const modal = el("scripting-restart-modal");
      modal.classList.remove("hidden");
      modal.setAttribute("aria-hidden", "false");
      el("scripting-mode").setAttribute("aria-expanded", "true");
      window.requestAnimationFrame(() => el("scripting-restart-cancel").focus());
    }

    async function restartScriptingRunners() {
      const path = scriptingRestartPath;
      const state = scriptingPendingApplies.get(path);
      if (!path || !state || scriptingRestartBusy) return;
      scriptingRestartBusy = true;
      setScriptingRestartError();
      const submit = el("scripting-restart-submit");
      submit.disabled = true;
      submit.textContent = "Restarting...";
      try {
        const usage = await api(
          `/api/scripting/usage?path=${encodeURIComponent(path)}`,
          { cache: "no-store" },
        );
        const pendingIds = new Set(state.sessions.keys());
        const activeSessions = (usage.sessions || []).filter((session) => (
          session.active && pendingIds.has(session.id)
        ));
        state.sessions.clear();
        activeSessions.forEach((session) => {
          state.sessions.set(session.id, {
            ...session,
            target: scriptingWarmupTarget(session),
            observedWarmup: false,
            ignoreActiveUntilExit: false,
          });
        });
        for (const session of activeSessions) {
          await api(
            `/api/sessions/${encodeURIComponent(session.id)}/runner/restart`,
            { method: "POST" },
          );
          state.sessions.delete(session.id);
        }
        scriptingPendingApplies.delete(path);
        stopScriptingApplyTimerIfIdle();
        scriptingRestartBusy = false;
        closeScriptingRestart();
        if (scriptingSelectedPath === path) setScriptingStatus("Runners restarting", "saved");
      } catch (error) {
        scriptingRestartBusy = false;
        submit.textContent = "Restart";
        renderScriptingRestartSessions();
        setScriptingRestartError(
          error && error.message ? error.message : "Runners could not be restarted.",
        );
      }
    }

    async function openScriptingOperation(action) {
      closeScriptingTreeMenus();
      if (["duplicate", "rename", "delete"].includes(action) && !scriptingTreeSelectedPath) return;
      if (scriptingOperationCloseTimer !== null) finishScriptingOperationClose();
      const selectedPaths = Array.from(scriptingTreeSelectedPaths);
      if (action === "rename" && selectedPaths.length !== 1) return;
      scriptingOperationPaths = selectedPaths.length
        ? selectedPaths
        : (scriptingTreeSelectedPath ? [scriptingTreeSelectedPath] : []);
      scriptingOperationCopyTargets = action === "duplicate" && scriptingOperationPaths.length > 1
        ? scriptingCopyTargets(scriptingOperationPaths)
        : [];
      renderScriptingOperation(action);
      const modal = el("scripting-operation-modal");
      const box = modal.querySelector(".scripting-operation-box");
      box.style.transition = "";
      box.style.transform = "";
      modal.classList.remove("hidden");
      modal.setAttribute("aria-hidden", "false");
      const firstInput = el("scripting-operation-fields").querySelector(
        ".scripting-directory-trigger, input:not([type=hidden]), textarea",
      );
      if (firstInput) window.requestAnimationFrame(() => firstInput.focus({ preventScroll: true }));
      if (!["rename", "delete"].includes(action)) return;
      el("scripting-operation-submit").disabled = true;
      try {
        const usageRows = await Promise.all(scriptingOperationPaths.map((path) => api(
          `/api/scripting/usage?path=${encodeURIComponent(path)}`,
          { cache: "no-store" },
        )));
        if (scriptingOperationAction !== action) return;
        const sessions = new Map();
        usageRows.forEach((usage) => {
          (usage.sessions || []).forEach((session) => sessions.set(session.id, session));
        });
        const sessionRows = Array.from(sessions.values());
        scriptingOperationUsage = {
          session_count: sessionRows.length,
          active_count: sessionRows.filter((session) => session.active).length,
          sessions: sessionRows,
        };
        el("scripting-operation-submit").disabled = false;
        renderScriptingOperationUsage();
      } catch (error) {
        if (scriptingOperationAction !== action) return;
        setScriptingOperationError(error && error.message ? error.message : "Session usage could not be checked.");
      }
    }

    function remapScriptingPath(path, currentPath, nextPath) {
      if (path === currentPath) return nextPath;
      return path.startsWith(`${currentPath}/`)
        ? `${nextPath}${path.slice(currentPath.length)}`
        : path;
    }

    async function finishScriptingOperation(result, action, previousPath) {
      closeScriptingOperation();
      const nextPath = String(result.path || "");
      if (action === "rename") {
        const expanded = Array.from(scriptingExpandedDirectories);
        scriptingExpandedDirectories.clear();
        expanded.forEach((path) => scriptingExpandedDirectories.add(
          remapScriptingPath(path, previousPath, nextPath),
        ));
        scriptingTreeSelectedPath = remapScriptingPath(
          scriptingTreeSelectedPath,
          previousPath,
          nextPath,
        );
        const selectedPaths = Array.from(scriptingTreeSelectedPaths);
        scriptingTreeSelectedPaths.clear();
        selectedPaths.forEach((path) => scriptingTreeSelectedPaths.add(
          remapScriptingPath(path, previousPath, nextPath),
        ));
        scriptingTreeSelectionAnchorPath = remapScriptingPath(
          scriptingTreeSelectionAnchorPath,
          previousPath,
          nextPath,
        );
        scriptingSelectedPath = remapScriptingPath(
          scriptingSelectedPath,
          previousPath,
          nextPath,
        );
        await loadScriptingTree();
        setScriptingStatus("Renamed", "saved");
        return;
      }
      if (action === "delete") {
        const removedLoadedFile = scriptingSelectedPath === previousPath
          || scriptingSelectedPath.startsWith(`${previousPath}/`);
        Array.from(scriptingExpandedDirectories).forEach((path) => {
          if (path === previousPath || path.startsWith(`${previousPath}/`)) {
            scriptingExpandedDirectories.delete(path);
          }
        });
        setScriptingTreeSelection();
        if (removedLoadedFile) resetScriptingEditor();
        await loadScriptingTree();
        setScriptingStatus("Deleted", "saved");
        return;
      }
      const directory = action === "new_directory" || result.type === "directory";
      const parent = directory ? nextPath : scriptingParentPath(nextPath);
      if (parent) scriptingExpandedDirectories.add(parent);
      await loadScriptingTree();
      if (directory) {
        setScriptingTreeSelection(nextPath, "directory");
        scriptingExpandedDirectories.add(nextPath);
        renderScriptingTree();
        setScriptingStatus("Directory created", "saved");
      } else {
        setScriptingTreeSelection(nextPath, "file");
        await openScriptingFile(nextPath);
        setScriptingStatus(action === "duplicate" ? "Duplicated" : "Created", "saved");
      }
    }

    async function finishScriptingBatchOperation(results, action, previousPaths) {
      closeScriptingOperation();
      if (action === "delete") {
        const removedLoadedFile = previousPaths.includes(scriptingSelectedPath);
        setScriptingTreeSelection();
        if (removedLoadedFile) resetScriptingEditor();
        await loadScriptingTree();
        setScriptingStatus(`Deleted ${previousPaths.length} files`, "saved");
        return;
      }

      const nextPaths = results.map((result) => String(result.path || "")).filter(Boolean);
      await loadScriptingTree();
      scriptingTreeSelectedPaths.clear();
      nextPaths.forEach((path) => scriptingTreeSelectedPaths.add(path));
      scriptingTreeSelectedPath = nextPaths[nextPaths.length - 1] || "";
      scriptingTreeSelectedType = scriptingTreeSelectedPath ? "file" : "";
      scriptingTreeSelectionAnchorPath = nextPaths[0] || "";
      updateScriptingTreeSelectionView();
      setScriptingStatus(`Duplicated ${nextPaths.length} files`, "saved");
    }

    async function submitScriptingOperation(event) {
      event.preventDefault();
      if (!scriptingOperationAction || scriptingOperationBusy) return;
      const action = scriptingOperationAction;
      const previousPath = scriptingTreeSelectedPath;
      const previousPaths = scriptingOperationPaths.length
        ? Array.from(scriptingOperationPaths)
        : (previousPath ? [previousPath] : []);
      const batch = previousPaths.length > 1 && ["duplicate", "delete"].includes(action);
      const copyTargets = Array.from(scriptingOperationCopyTargets);
      const value = (id) => {
        const input = el(id);
        return input ? String(input.value || "").trim() : "";
      };
      scriptingOperationBusy = true;
      setScriptingOperationError();
      const submit = el("scripting-operation-submit");
      submit.disabled = true;
      const originalLabel = submit.textContent;
      submit.textContent = action === "delete" ? "Deleting..." : "Working...";
      let completedCount = 0;
      try {
        let result;
        if (action === "new_directory") {
          result = await api("/api/scripting/directories", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              path: scriptingJoinPath(value("scripting-operation-parent"), value("scripting-operation-name")),
            }),
          });
        } else if (action === "new_strategy") {
          let name = value("scripting-operation-name");
          if (name && !name.toLowerCase().endsWith(".py")) name += ".py";
          const path = scriptingJoinPath(value("scripting-operation-parent"), name);
          result = await api("/api/scripting/files", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(scriptingOperationTemplate === "copy" ? {
              path,
              kind: "copy",
              source_path: value("scripting-operation-source"),
            } : {
              path,
              kind: "strategy",
              template: scriptingOperationTemplate,
              title: value("scripting-operation-title-input"),
              description: value("scripting-operation-description"),
            }),
          });
        } else if (action === "new_markdown") {
          let name = value("scripting-operation-name");
          if (name && !name.toLowerCase().endsWith(".md")) name += ".md";
          result = await api("/api/scripting/files", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              path: scriptingJoinPath(value("scripting-operation-parent"), name),
              kind: "markdown",
              title: value("scripting-operation-title-input"),
              description: value("scripting-operation-description"),
            }),
          });
        } else if (action === "duplicate") {
          if (batch) {
            result = [];
            for (const item of copyTargets) {
              result.push(await api("/api/scripting/files", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  path: item.targetPath,
                  kind: "copy",
                  source_path: item.sourcePath,
                }),
              }));
              completedCount += 1;
            }
          } else {
            result = await api("/api/scripting/files", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                path: value("scripting-operation-path"),
                kind: "copy",
                source_path: previousPath,
              }),
            });
          }
        } else if (action === "rename") {
          result = await api("/api/scripting/rename", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              path: previousPath,
              next_path: value("scripting-operation-path"),
              acknowledge_stopped_sessions: true,
            }),
          });
        } else {
          if (batch) {
            result = [];
            for (const path of previousPaths) {
              result.push(await api(
                `/api/scripting/file?path=${encodeURIComponent(path)}&acknowledge_stopped_sessions=true`,
                { method: "DELETE" },
              ));
              completedCount += 1;
            }
          } else {
            result = await api(
              `/api/scripting/file?path=${encodeURIComponent(previousPath)}&acknowledge_stopped_sessions=true`,
              { method: "DELETE" },
            );
          }
        }
        scriptingOperationBusy = false;
        if (batch) await finishScriptingBatchOperation(result, action, previousPaths);
        else await finishScriptingOperation(result, action, previousPath);
      } catch (error) {
        scriptingOperationBusy = false;
        submit.disabled = completedCount > 0
          || Boolean(scriptingOperationUsage && Number(scriptingOperationUsage.active_count));
        submit.textContent = originalLabel;
        if (completedCount > 0) await loadScriptingTree();
        const message = error && error.message ? error.message : "File operation failed.";
        setScriptingOperationError(completedCount > 0
          ? `${completedCount} of ${previousPaths.length} files completed before the error: ${message}`
          : message);
      }
    }

    function requestScriptingOperation(action) {
      const affectsLoadedFile = ["duplicate", "rename", "delete"].includes(action)
        && scriptingSelectedPath
        && Array.from(scriptingTreeSelectedPaths).some((path) => (
          scriptingSelectedPath === path || scriptingSelectedPath.startsWith(`${path}/`)
        ));
      if (affectsLoadedFile) {
        runScriptingNavigation(() => openScriptingOperation(action));
      } else {
        openScriptingOperation(action);
      }
    }

    function setScriptingMobileView(view) {
      const showEditor = view === "editor" && mobileHubQuery.matches;
      el("scripting-modal").classList.toggle("scripting-show-editor", showEditor);
      el("scripting-back").classList.toggle("hidden", !showEditor);
    }

    function scriptingTreeMaxWidth() {
      const width = el("scripting-workspace").clientWidth;
      if (!width) return SCRIPTING_TREE_MAX_WIDTH;
      return Math.max(
        SCRIPTING_TREE_MIN_WIDTH,
        Math.min(SCRIPTING_TREE_MAX_WIDTH, width - 360),
      );
    }

    function setScriptingTreeWidth(value, { persist = true } = {}) {
      const maxWidth = scriptingTreeMaxWidth();
      scriptingTreeWidth = Math.round(Math.max(
        SCRIPTING_TREE_MIN_WIDTH,
        Math.min(Number(value) || SCRIPTING_TREE_DEFAULT_WIDTH, maxWidth),
      ));
      el("scripting-workspace").style.setProperty(
        "--scripting-tree-width",
        `${scriptingTreeWidth}px`,
      );
      const resizer = el("scripting-tree-resizer");
      resizer.setAttribute("aria-valuemax", String(maxWidth));
      resizer.setAttribute("aria-valuenow", String(scriptingTreeWidth));
      if (persist) {
        try { localStorage.setItem(SCRIPTING_TREE_WIDTH_KEY, String(scriptingTreeWidth)); } catch {}
      }
    }

    function renderScriptingTreeLayout() {
      const collapsed = scriptingTreeCollapsed && !mobileHubQuery.matches;
      const workspace = el("scripting-workspace");
      const toggle = el("scripting-tree-toggle");
      workspace.classList.toggle("scripting-tree-collapsed", collapsed);
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.title = collapsed ? "Expand project tree" : "Collapse project tree";
      toggle.setAttribute("aria-label", toggle.title);
      el("scripting-tree-resizer").tabIndex = collapsed || mobileHubQuery.matches ? -1 : 0;
      setScriptingTreeWidth(scriptingTreeWidth, { persist: false });
    }

    function setScriptingTreeCollapsed(collapsed, { persist = true } = {}) {
      scriptingTreeCollapsed = Boolean(collapsed);
      renderScriptingTreeLayout();
      if (persist) {
        try {
          localStorage.setItem(SCRIPTING_TREE_COLLAPSED_KEY, String(scriptingTreeCollapsed));
        } catch {}
      }
    }

    function loadScriptingTreeLayout() {
      try {
        const width = Number(localStorage.getItem(SCRIPTING_TREE_WIDTH_KEY));
        if (Number.isFinite(width) && width > 0) scriptingTreeWidth = width;
        scriptingTreeCollapsed = localStorage.getItem(SCRIPTING_TREE_COLLAPSED_KEY) === "true";
      } catch {}
      renderScriptingTreeLayout();
    }

    function resetScriptingEditor() {
      scriptingSelectedPath = "";
      scriptingBaseContent = "";
      scriptingBaseRevision = "";
      scriptingBaseNote = "";
      scriptingLanguage = "";
      scriptingDirty = false;
      scriptingSaving = false;
      scriptingConflict = false;
      if (scriptingEditorController) scriptingEditorController.close({ focusEditor: false });
      setScriptingNote("");
      setScriptingNoteOpen(false);
      el("scripting-file-name").textContent = "No file selected";
      el("scripting-file-path").textContent = "";
      el("scripting-file-meta").textContent = "";
      el("scripting-code").value = "";
      el("scripting-code").dataset.revision = "";
      el("scripting-highlight").textContent = "";
      el("scripting-diff-markers").replaceChildren();
      setScriptingNotice();
      closeScriptingHistory();
      el("scripting-editor-empty").classList.remove("hidden");
      el("scripting-editor-loading").classList.add("hidden");
      el("scripting-editor-error").classList.add("hidden");
      el("scripting-editor-wrap").classList.add("hidden");
      resetScriptingUndo();
      setScriptingStatus("No file");
      updateScriptingTreeSelectionView();
    }

    function setScriptingEditorState(state, message = "") {
      el("scripting-editor-empty").classList.toggle("hidden", state !== "empty");
      el("scripting-editor-loading").classList.toggle("hidden", state !== "loading");
      el("scripting-editor-error").classList.toggle("hidden", state !== "error");
      el("scripting-editor-wrap").classList.toggle("hidden", state !== "ready");
      el("scripting-editor-error").textContent = state === "error" ? message : "";
    }

    function scriptingFileSize(size) {
      const bytes = Math.max(0, Number(size) || 0);
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    function wrapScriptingPythonToken(className, value) {
      return `<span class="${className}">${esc(value)}</span>`;
    }

    function highlightScriptingPython(source) {
      if (!source) return "";
      const keywords = new Set([
        "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
        "del", "elif", "else", "except", "finally", "for", "from", "global", "if",
        "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise",
        "return", "try", "while", "with", "yield",
      ]);
      const builtins = new Set([
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
        "len", "list", "map", "max", "min", "open", "print", "range", "reversed",
        "round", "set", "sorted", "str", "sum", "super", "tuple", "type", "zip",
      ]);
      const constants = new Set(["False", "None", "True", "Ellipsis", "NotImplemented"]);
      const isIdentStart = (char) => /[A-Za-z_]/.test(char);
      const isIdent = (char) => /[A-Za-z0-9_]/.test(char);
      const stringPrefixMatch = (offset) => {
        const match = /^(?:[rRuUbBfF]|[rR][fF]|[fF][rR]|[bB][rR]|[rR][bB])(?=['"])/
          .exec(source.slice(offset, offset + 3));
        return match ? match[0] : "";
      };
      let html = "";
      let index = 0;

      while (index < source.length) {
        const char = source[index];
        const prefix = stringPrefixMatch(index);
        const quoteOffset = index + prefix.length;
        const quote = source[quoteOffset];
        if ((prefix || char === "\"" || char === "'") && (quote === "\"" || quote === "'")) {
          const triple = source.slice(quoteOffset, quoteOffset + 3) === quote.repeat(3);
          let end = quoteOffset + (triple ? 3 : 1);
          while (end < source.length) {
            if (triple && source.slice(end, end + 3) === quote.repeat(3)) {
              end += 3;
              break;
            }
            if (!triple && source[end] === "\n") break;
            if (!triple && source[end] === "\\") end += 2;
            else if (!triple && source[end] === quote) {
              end += 1;
              break;
            } else end += 1;
          }
          html += wrapScriptingPythonToken("py-string", source.slice(index, end));
          index = end;
          continue;
        }
        if (char === "#") {
          let end = index;
          while (end < source.length && source[end] !== "\n") end += 1;
          html += wrapScriptingPythonToken("py-comment", source.slice(index, end));
          index = end;
          continue;
        }
        if (char === "@" && (index === 0 || source[index - 1] === "\n")) {
          let end = index + 1;
          while (end < source.length && /[A-Za-z0-9_.]/.test(source[end])) end += 1;
          html += wrapScriptingPythonToken("py-decorator", source.slice(index, end));
          index = end;
          continue;
        }
        if (/[0-9]/.test(char)) {
          const match = /^(?:0[xX][0-9A-Fa-f_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?j?)/
            .exec(source.slice(index));
          if (match) {
            html += wrapScriptingPythonToken("py-number", match[0]);
            index += match[0].length;
            continue;
          }
        }
        if (isIdentStart(char)) {
          let end = index + 1;
          while (end < source.length && isIdent(source[end])) end += 1;
          const word = source.slice(index, end);
          if (keywords.has(word)) html += wrapScriptingPythonToken("py-keyword", word);
          else if (builtins.has(word)) html += wrapScriptingPythonToken("py-builtin", word);
          else if (constants.has(word)) html += wrapScriptingPythonToken("py-constant", word);
          else html += esc(word);
          index = end;
          continue;
        }
        html += esc(char);
        index += 1;
      }
      return html;
    }

    function renderScriptingHighlight() {
      const code = el("scripting-code");
      const highlight = el("scripting-highlight");
      const source = code.value;
      const syntax = scriptingLanguage === "python"
        ? highlightScriptingPython(source)
        : esc(source);
      let lineIndex = 0;
      const withAnchors = syntax.replace(/\n/g, () => {
        lineIndex += 1;
        return `\n<span class="scripting-line-anchor" data-line="${lineIndex}"></span>`;
      });
      highlight.innerHTML = `<span class="scripting-line-anchor" data-line="0"></span>${withAnchors}`;
      highlight.scrollTop = code.scrollTop;
      highlight.scrollLeft = code.scrollLeft;
      scheduleScriptingDiff();
    }

    function clearScriptingDiff() {
      if (scriptingDiffTimer !== null) {
        clearTimeout(scriptingDiffTimer);
        scriptingDiffTimer = null;
      }
      el("scripting-diff-markers").replaceChildren();
    }

    function scheduleScriptingDiff() {
      if (!scriptingDirty) {
        clearScriptingDiff();
        return;
      }
      if (scriptingDiffTimer !== null) clearTimeout(scriptingDiffTimer);
      scriptingDiffTimer = window.setTimeout(() => {
        scriptingDiffTimer = null;
        window.requestAnimationFrame(renderScriptingDiff);
      }, 90);
    }

    function scriptingLineDiffOperations(before, after) {
      const oldLines = String(before || "").replace(/\r\n?/g, "\n").split("\n");
      const newLines = String(after || "").replace(/\r\n?/g, "\n").split("\n");
      let prefix = 0;
      while (
        prefix < oldLines.length
        && prefix < newLines.length
        && oldLines[prefix] === newLines[prefix]
      ) prefix += 1;
      let suffix = 0;
      while (
        suffix < oldLines.length - prefix
        && suffix < newLines.length - prefix
        && oldLines[oldLines.length - suffix - 1] === newLines[newLines.length - suffix - 1]
      ) suffix += 1;
      const operations = Array(prefix).fill("equal");
      operations.push(...scriptingLineMyersDiff(
        oldLines.slice(prefix, oldLines.length - suffix),
        newLines.slice(prefix, newLines.length - suffix),
      ));
      operations.push(...Array(suffix).fill("equal"));
      return { operations, lineCount: newLines.length };
    }

    function scriptingLineMyersDiff(oldLines, newLines) {
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
            oldIndex < oldCount
            && newIndex < newCount
            && oldLines[oldIndex] === newLines[newIndex]
          ) {
            oldIndex += 1;
            newIndex += 1;
          }
          frontier.set(diagonal, oldIndex);
          if (oldIndex >= oldCount && newIndex >= newCount) {
            return backtrackScriptingLineDiff(trace, oldLines, newLines);
          }
        }
      }
      return [
        ...Array(oldCount).fill("delete"),
        ...Array(newCount).fill("insert"),
      ];
    }

    function backtrackScriptingLineDiff(trace, oldLines, newLines) {
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
    }

    function scriptingChangedLines(before, after) {
      const { operations, lineCount } = scriptingLineDiffOperations(before, after);
      const lines = [];
      const deletionLines = [];
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
          lines.push({ line, type: insertedIndex < modifiedCount ? "modified" : "added" });
        });
        if (deletedCount > modifiedCount) {
          deletionLines.push(Math.max(0, Math.min(currentLine, lineCount - 1)));
        }
      }
      return {
        lines,
        deletionLines,
      };
    }

    function renderScriptingDiff() {
      if (!scriptingDirty) {
        clearScriptingDiff();
        return;
      }
      const highlight = el("scripting-highlight");
      const markers = el("scripting-diff-markers");
      const anchors = highlight.querySelectorAll(".scripting-line-anchor");
      if (!anchors.length) return;
      const changes = scriptingChangedLines(scriptingBaseContent, el("scripting-code").value);
      const highlightRect = highlight.getBoundingClientRect();
      const lineHeight = parseFloat(getComputedStyle(highlight).lineHeight) || 18;
      const lineTop = (line) => {
        const anchor = anchors[Math.max(0, Math.min(line, anchors.length - 1))];
        return anchor
          ? anchor.getBoundingClientRect().top - highlightRect.top + highlight.scrollTop
          : 0;
      };
      const fragment = document.createDocumentFragment();
      changes.lines.forEach(({ line, type }) => {
        const marker = document.createElement("span");
        const top = lineTop(line);
        const nextTop = line + 1 < anchors.length ? lineTop(line + 1) : top + lineHeight;
        marker.className = `scripting-diff-marker ${type}`;
        marker.style.top = `${top}px`;
        marker.style.height = `${Math.max(2, nextTop - top)}px`;
        fragment.appendChild(marker);
      });
      changes.deletionLines.forEach((line) => {
        const marker = document.createElement("span");
        marker.className = "scripting-diff-marker deleted";
        marker.style.top = `${lineTop(line)}px`;
        fragment.appendChild(marker);
      });
      markers.replaceChildren(fragment);
      markers.style.transform = `translateY(${-el("scripting-code").scrollTop}px)`;
    }

    function handleScriptingInput() {
      scriptingDirty = Boolean(scriptingBaseRevision)
        && el("scripting-code").value !== scriptingBaseContent;
      if (!scriptingConflict) setScriptingNotice();
      renderScriptingHighlight();
      if (scriptingEditorController && scriptingEditorController.isOpen()) {
        scriptingEditorController.refresh();
      }
      updateScriptingEditState();
    }

    function toggleScriptingComments() {
      if (scriptingLanguage !== "python") return;
      const editor = el("scripting-code");
      const source = editor.value;
      const selectionStart = editor.selectionStart;
      const selectionEnd = editor.selectionEnd;
      const lineStart = selectionStart === 0
        ? 0
        : source.lastIndexOf("\n", selectionStart - 1) + 1;
      const effectiveEnd = selectionEnd > selectionStart && source[selectionEnd - 1] === "\n"
        ? selectionEnd - 1
        : selectionEnd;
      const nextLineBreak = source.indexOf("\n", effectiveEnd);
      const lineEnd = nextLineBreak === -1 ? source.length : nextLineBreak;
      const lines = source.slice(lineStart, lineEnd).split("\n");
      const nonEmpty = lines.filter((line) => line.trim().length > 0);
      const remove = nonEmpty.length > 0 && nonEmpty.every((line) => /^\s*#/.test(line));
      captureScriptingUndo("insertText");
      const updated = lines.map((line) => {
        if (!line.trim()) return line;
        if (remove) return line.replace(/^(\s*)# ?/, "$1");
        return line.replace(/^(\s*)/, "$1# ");
      }).join("\n");
      editor.value = source.slice(0, lineStart) + updated + source.slice(lineEnd);
      editor.setSelectionRange(lineStart, lineStart + updated.length);
      handleScriptingInput();
    }

    function renderScriptingFile(payload, options = {}) {
      const path = String(payload.path || scriptingSelectedPath);
      const language = String(payload.language || "");
      const content = String(payload.content || "");
      scriptingSelectedPath = path;
      scriptingBaseContent = content;
      scriptingBaseRevision = String(payload.revision || "");
      scriptingBaseNote = normalizeScriptingNote(payload.note);
      scriptingLanguage = language;
      scriptingDirty = false;
      scriptingSaving = false;
      scriptingConflict = false;
      setScriptingNote(scriptingBaseNote);
      setScriptingNoteOpen(false);
      el("scripting-file-name").textContent = String(payload.name || path.split("/").pop() || "Source");
      el("scripting-file-path").textContent = path;
      el("scripting-file-meta").textContent = `${language === "markdown" ? "Markdown" : "Python"} · ${scriptingFileSize(payload.size)}`;
      const code = el("scripting-code");
      const highlight = el("scripting-highlight");
      code.value = content;
      code.dataset.revision = scriptingBaseRevision;
      code.scrollTop = 0;
      code.scrollLeft = 0;
      highlight.scrollTop = 0;
      highlight.scrollLeft = 0;
      setScriptingNotice();
      resetScriptingUndo();
      renderScriptingHighlight();
      setScriptingEditorState("ready");
      updateScriptingEditState();
      if (!options.preserveStatus && !renderScriptingApplyStatus()) setScriptingStatus("Ready");
      updateScriptingTreeSelectionView();
    }

    async function saveScriptingFile() {
      if (
        scriptingSaving
        || scriptingConflict
        || !scriptingHasUnsavedChanges()
        || !scriptingSelectedPath
      ) return !scriptingHasUnsavedChanges() && !scriptingConflict;
      scriptingSaving = true;
      updateScriptingEditState();
      try {
        const payload = await api("/api/scripting/file", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: scriptingSelectedPath,
            content: el("scripting-code").value,
            base_revision: scriptingBaseRevision,
            note: scriptingNote,
          }),
        });
        const savedPath = scriptingSelectedPath;
        renderScriptingFile(payload, { preserveStatus: true });
        if (payload.saved) await trackScriptingApply(savedPath);
        else setScriptingStatus("Note saved", "saved");
        if (isScriptingHistoryOpen()) await loadScriptingHistory();
        return true;
      } catch (error) {
        scriptingSaving = false;
        if (error && error.status === 409) {
          scriptingConflict = true;
          setScriptingNotice(
            "This file changed outside this editor. Reload the current file before saving again.",
          );
          setScriptingStatus("Conflict", "conflict");
        } else {
          scriptingConflict = false;
          setScriptingNotice(error && error.message ? error.message : "Script could not be saved.");
          setScriptingStatus("Save failed", "conflict");
        }
        updateScriptingEditState();
        return false;
      }
    }

    async function saveScriptingNote() {
      if (
        scriptingSaving
        || scriptingConflict
        || !scriptingNoteChanged()
        || !scriptingSelectedPath
      ) return !scriptingNoteChanged() && !scriptingConflict;
      scriptingSaving = true;
      updateScriptingEditState();
      try {
        const payload = await api("/api/scripting/file", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: scriptingSelectedPath,
            content: scriptingBaseContent,
            base_revision: scriptingBaseRevision,
            note: scriptingNote,
          }),
        });
        scriptingBaseRevision = String(payload.revision || scriptingBaseRevision);
        scriptingBaseNote = normalizeScriptingNote(payload.note);
        scriptingSaving = false;
        scriptingConflict = false;
        el("scripting-code").dataset.revision = scriptingBaseRevision;
        setScriptingNote(payload.note);
        setScriptingNotice();
        setScriptingStatus("Note saved", "saved");
        if (isScriptingHistoryOpen()) await loadScriptingHistory();
        return true;
      } catch (error) {
        scriptingSaving = false;
        if (error && error.status === 409) {
          scriptingConflict = true;
          setScriptingNotice(
            "This file changed outside this editor. Reload the current file before saving again.",
          );
          setScriptingStatus("Conflict", "conflict");
        } else {
          setScriptingNotice(error && error.message ? error.message : "Note could not be saved.");
          setScriptingStatus("Save failed", "conflict");
        }
        updateScriptingEditState();
        return false;
      }
    }

    function discardScriptingChanges() {
      const code = el("scripting-code");
      code.value = scriptingBaseContent;
      scriptingDirty = false;
      setScriptingNote(scriptingBaseNote);
      if (!scriptingConflict) setScriptingNotice();
      resetScriptingUndo();
      renderScriptingHighlight();
      updateScriptingEditState();
      setScriptingStatus("Ready");
    }

    function isScriptingHistoryOpen() {
      return !el("scripting-history-panel").classList.contains("hidden");
    }

    function setScriptingHistoryState(state, message = "") {
      el("scripting-history-loading").classList.toggle("hidden", state !== "loading");
      el("scripting-history-error").classList.toggle("hidden", state !== "error");
      el("scripting-history-empty").classList.toggle("hidden", state !== "empty");
      el("scripting-history-content").classList.toggle("hidden", state !== "ready");
      el("scripting-history-error").textContent = state === "error" ? message : "";
    }

    function scriptingRevisionLabel(source) {
      const labels = {
        baseline: "Baseline",
        manual: "Manual save",
        ai: "AI edit",
        external: "External edit",
        restore: "Restored version",
      };
      return labels[String(source || "")] || "Saved version";
    }

    function scriptingRevisionTime(value) {
      const date = new Date(value);
      if (!Number.isFinite(date.getTime())) return String(value || "");
      return date.toLocaleString([], {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
    }

    function renderScriptingHistoryList({ preserveScroll = true } = {}) {
      const list = el("scripting-history-list");
      const scrollTop = preserveScroll ? list.scrollTop : 0;
      const fragment = document.createDocumentFragment();
      scriptingHistoryRows.forEach((revision) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "scripting-history-item";
        button.classList.toggle("selected", revision.id === scriptingHistorySelectedId);
        button.dataset.revisionId = String(revision.id);
        const title = document.createElement("strong");
        title.textContent = scriptingRevisionLabel(revision.source);
        const time = document.createElement("time");
        time.textContent = scriptingRevisionTime(revision.created_at);
        const meta = document.createElement("span");
        meta.textContent = `${String(revision.revision || "").slice(0, 8)} · ${revision.line_count || 0} lines`;
        button.append(title, time, meta);
        const note = String(revision.note || "").trim();
        if (note) {
          const noteRow = document.createElement("small");
          noteRow.className = "scripting-history-item-note";
          noteRow.textContent = note;
          noteRow.title = note;
          button.appendChild(noteRow);
        }
        fragment.appendChild(button);
      });
      list.replaceChildren(fragment);
      list.scrollTop = scrollTop;
    }

    function renderScriptingHistoryDiff(diff) {
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
      el("scripting-history-diff").replaceChildren(fragment);
    }

    async function loadScriptingHistory() {
      if (!scriptingSelectedPath) return;
      const seq = ++scriptingHistoryRequestSeq;
      scriptingHistorySelectedId = null;
      el("scripting-history-path").textContent = scriptingSelectedPath;
      el("scripting-history-detail-title").textContent = "Select a version";
      el("scripting-history-detail-meta").textContent = "";
      el("scripting-history-diff").textContent = "Select a version to review its changes.";
      el("scripting-restore").disabled = true;
      setScriptingHistoryState("loading");
      try {
        const payload = await api(
          `/api/scripting/history?path=${encodeURIComponent(scriptingSelectedPath)}&limit=100`,
          { cache: "no-store" },
        );
        if (seq !== scriptingHistoryRequestSeq || !isScriptingHistoryOpen()) return;
        scriptingHistoryRows = Array.isArray(payload.revisions) ? payload.revisions : [];
        if (
          scriptingBaseRevision
          && payload.current_revision
          && payload.current_revision !== scriptingBaseRevision
        ) {
          scriptingConflict = true;
          setScriptingNotice(
            "This file changed outside this editor. Reload the current file before saving or restoring.",
          );
          setScriptingStatus("Conflict", "conflict");
        }
        renderScriptingHistoryList({ preserveScroll: false });
        setScriptingHistoryState(scriptingHistoryRows.length ? "ready" : "empty");
        if (scriptingHistoryRows.length) {
          await selectScriptingHistoryRevision(scriptingHistoryRows[0].id);
        }
      } catch (error) {
        if (seq !== scriptingHistoryRequestSeq || !isScriptingHistoryOpen()) return;
        setScriptingHistoryState(
          "error",
          error && error.message ? error.message : "Version history could not be loaded.",
        );
      }
    }

    function openScriptingHistory() {
      if (!scriptingSelectedPath) return;
      const panel = el("scripting-history-panel");
      panel.classList.remove("hidden");
      panel.setAttribute("aria-hidden", "false");
      el("scripting-modal").classList.add("scripting-history-open");
      loadScriptingHistory();
    }

    function closeScriptingHistory() {
      scriptingHistoryRequestSeq += 1;
      scriptingHistoryDiffRequestSeq += 1;
      scriptingHistorySelectedId = null;
      const panel = el("scripting-history-panel");
      panel.classList.add("hidden");
      panel.style.transform = "";
      panel.style.transition = "";
      panel.setAttribute("aria-hidden", "true");
      el("scripting-modal").classList.remove("scripting-history-open");
    }

    async function selectScriptingHistoryRevision(revisionId) {
      const resolvedId = Number(revisionId);
      const revision = scriptingHistoryRows.find((item) => item.id === resolvedId);
      if (!revision || !scriptingSelectedPath) return;
      scriptingHistorySelectedId = resolvedId;
      renderScriptingHistoryList();
      el("scripting-history-detail-title").textContent = scriptingRevisionLabel(revision.source);
      el("scripting-history-detail-meta").textContent = scriptingRevisionTime(revision.created_at);
      el("scripting-history-diff").textContent = "Loading diff...";
      el("scripting-restore").disabled = true;
      const seq = ++scriptingHistoryDiffRequestSeq;
      try {
        const payload = await api(
          `/api/scripting/diff?path=${encodeURIComponent(scriptingSelectedPath)}&revision_id=${resolvedId}`,
          { cache: "no-store" },
        );
        if (seq !== scriptingHistoryDiffRequestSeq || scriptingHistorySelectedId !== resolvedId) return;
        if (payload.changed) {
          renderScriptingHistoryDiff(payload.diff || "No textual changes.");
        } else {
          el("scripting-history-diff").textContent = "This is the current file content.";
        }
        el("scripting-restore").disabled = !payload.changed || scriptingHasUnsavedChanges() || scriptingConflict;
      } catch (error) {
        if (seq !== scriptingHistoryDiffRequestSeq || scriptingHistorySelectedId !== resolvedId) return;
        el("scripting-history-diff").textContent = error && error.message
          ? error.message
          : "Diff could not be loaded.";
      }
    }

    async function restoreScriptingRevision() {
      if (
        !scriptingHistorySelectedId
        || !scriptingSelectedPath
        || scriptingSaving
        || scriptingHasUnsavedChanges()
        || scriptingConflict
      ) return false;
      scriptingSaving = true;
      updateScriptingEditState();
      el("scripting-restore").disabled = true;
      try {
        const payload = await api("/api/scripting/restore", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: scriptingSelectedPath,
            revision_id: scriptingHistorySelectedId,
            base_revision: scriptingBaseRevision,
          }),
        });
        const restoredPath = scriptingSelectedPath;
        renderScriptingFile(payload, { preserveStatus: true });
        await trackScriptingApply(restoredPath);
        await loadScriptingHistory();
        return true;
      } catch (error) {
        scriptingSaving = false;
        if (error && error.status === 409) {
          scriptingConflict = true;
          setScriptingNotice(
            "This file changed before the restore was applied. Reload and review the version again.",
          );
          setScriptingStatus("Conflict", "conflict");
        } else {
          scriptingConflict = false;
          setScriptingNotice(error && error.message ? error.message : "Version could not be restored.");
          setScriptingStatus("Restore failed", "conflict");
        }
        updateScriptingEditState();
        return false;
      }
    }

    async function openScriptingFile(path, options = {}) {
      const normalizedPath = String(path || "");
      if (!normalizedPath) return;
      const seq = ++scriptingFileRequestSeq;
      scriptingSelectedPath = normalizedPath;
      setScriptingTreeSelection(normalizedPath, "file");
      scriptingBaseContent = "";
      scriptingBaseRevision = "";
      scriptingBaseNote = "";
      scriptingLanguage = "";
      scriptingDirty = false;
      scriptingSaving = false;
      scriptingConflict = false;
      if (scriptingEditorController) scriptingEditorController.close({ focusEditor: false });
      setScriptingNote("");
      setScriptingNoteOpen(false);
      resetScriptingUndo();
      setScriptingNotice();
      closeScriptingHistory();
      setScriptingStatus("Loading...");
      el("scripting-file-name").textContent = normalizedPath.split("/").pop() || "Source";
      el("scripting-file-path").textContent = normalizedPath;
      el("scripting-file-meta").textContent = "";
      setScriptingEditorState("loading");
      if (options.showEditor !== false) setScriptingMobileView("editor");
      try {
        const result = options.request
          ? await options.request
          : await requestScriptingFile(normalizedPath);
        if (result.error) throw result.error;
        if (seq !== scriptingFileRequestSeq || !isScriptingOpen()) return;
        renderScriptingFile(result.payload);
      } catch (error) {
        if (seq !== scriptingFileRequestSeq || !isScriptingOpen()) return;
        setScriptingEditorState(
          "error",
          error && error.message ? error.message : "Source could not be loaded.",
        );
        setScriptingStatus("Load failed", "conflict");
      }
    }

    function requestScriptingFile(path) {
      return api(
        `/api/scripting/file?path=${encodeURIComponent(path)}`,
        { cache: "no-store" },
      ).then(
        (payload) => ({ payload, error: null }),
        (error) => ({ payload: null, error }),
      );
    }

    async function loadScriptingTree(options = {}) {
      const seq = ++scriptingTreeRequestSeq;
      const refreshSelectedFile = options.refreshSelectedFile === true
        || !mobileHubQuery.matches
        || el("scripting-modal").classList.contains("scripting-show-editor");
      const selectedPathAtStart = refreshSelectedFile && !scriptingPendingAssignedPath
        ? String(scriptingSelectedPath || "")
        : "";
      const selectedFileRequest = selectedPathAtStart
        ? requestScriptingFile(selectedPathAtStart)
        : null;
      const refresh = el("scripting-refresh");
      refresh.classList.add("assets-refreshing");
      refresh.disabled = true;
      setScriptingTreeState("loading");
      try {
        const payload = await api("/api/scripting/tree", { cache: "no-store" });
        if (seq !== scriptingTreeRequestSeq || !isScriptingOpen()) return;
        scriptingEntries = Array.isArray(payload.entries) ? payload.entries : [];
        const count = scriptingFileCount(scriptingEntries);
        el("scripting-file-count").textContent = `${count} ${count === 1 ? "file" : "files"}`;
        if (
          scriptingTreeSelectedPath
          && !scriptingEntryForPath(scriptingEntries, scriptingTreeSelectedPath)
        ) {
          setScriptingTreeSelection();
        }
        if (scriptingSelectedPath && !scriptingContainsPath(scriptingEntries, scriptingSelectedPath)) {
          resetScriptingEditor();
        }
        renderScriptingTree();
        setScriptingTreeState(scriptingEntries.length ? "ready" : "empty");
        if (scriptingPendingAssignedPath) {
          const assignedPath = scriptingPendingAssignedPath;
          scriptingPendingAssignedPath = "";
          if (scriptingContainsPath(scriptingEntries, assignedPath)) {
            await openScriptingFile(assignedPath, { showEditor: false });
            return;
          }
        }
        if (
          refreshSelectedFile
          && scriptingSelectedPath
          && scriptingContainsPath(scriptingEntries, scriptingSelectedPath)
        ) {
          const request = selectedFileRequest && scriptingSelectedPath === selectedPathAtStart
            ? selectedFileRequest
            : null;
          await openScriptingFile(scriptingSelectedPath, { showEditor: false, request });
        }
      } catch (error) {
        if (seq !== scriptingTreeRequestSeq || !isScriptingOpen()) return;
        setScriptingTreeState(
          "error",
          error && error.message ? error.message : "Project tree could not be loaded.",
        );
      } finally {
        if (seq === scriptingTreeRequestSeq) {
          refresh.classList.remove("assets-refreshing");
          refresh.disabled = false;
        }
      }
    }

    function openScripting() {
      closeHubMenu();
      closeAiChat();
      if (scriptingCloseTimer !== null) {
        clearTimeout(scriptingCloseTimer);
        scriptingCloseTimer = null;
      }
      if (scriptingOpenTimer !== null) {
        clearTimeout(scriptingOpenTimer);
        scriptingOpenTimer = null;
      }
      const modal = el("scripting-modal");
      const box = modal.querySelector(".scripting-modal-box");
      modal.classList.remove(
        "scripting-closing",
        "scripting-dragging",
        "scripting-swipe-closing",
        "scripting-opening",
        "hidden",
      );
      modal.classList.add("scripting-opening");
      modal.style.background = "";
      box.style.transform = "";
      box.style.transition = "";
      modal.setAttribute("aria-hidden", "false");
      setScriptingMobileView("tree");
      window.requestAnimationFrame(renderScriptingTreeLayout);
      lockBodyScroll();
      scriptingOpenTimer = window.setTimeout(finishScriptingOpening, 230);
      loadScriptingTree({ refreshSelectedFile: true });
      window.requestAnimationFrame(() => el("scripting-close").focus());
    }

    function finishScriptingOpening() {
      if (scriptingOpenTimer !== null) clearTimeout(scriptingOpenTimer);
      scriptingOpenTimer = null;
      el("scripting-modal").classList.remove("scripting-opening");
    }

    function finishScriptingClose() {
      const modal = el("scripting-modal");
      const box = modal.querySelector(".scripting-modal-box");
      modal.classList.remove(
        "scripting-closing",
        "scripting-dragging",
        "scripting-swipe-closing",
        "scripting-opening",
        "scripting-show-editor",
      );
      modal.classList.add("hidden");
      modal.style.background = "";
      box.style.transform = "";
      box.style.transition = "";
      modal.setAttribute("aria-hidden", "true");
      el("scripting-back").classList.add("hidden");
      el("scripting-refresh").classList.remove("assets-refreshing");
      el("scripting-refresh").disabled = false;
      if (scriptingEditorController) scriptingEditorController.close({ focusEditor: false });
      closeScriptingHistory();
      closeScriptingTreeMenus();
      closeScriptingOperation({ immediate: true });
      closeScriptingRestart();
      scriptingOpenTimer = null;
      scriptingCloseTimer = null;
      unlockBodyScroll();
    }

    function closeScripting(options = {}) {
      if (!isScriptingOpen()) return;
      const modal = el("scripting-modal");
      if (modal.classList.contains("scripting-closing")) return;
      if (scriptingHasUnsavedChanges() && options.force !== true) {
        const box = modal.querySelector(".scripting-modal-box");
        modal.classList.remove("scripting-dragging", "scripting-swipe-closing");
        box.style.transition = "transform 180ms ease";
        box.style.transform = "translateY(0)";
        window.setTimeout(() => {
          if (isScriptingOpen()) {
            box.style.transition = "";
            box.style.transform = "";
          }
        }, 190);
        openScriptingUnsaved(() => closeScripting({ force: true }));
        return;
      }
      finishScriptingOpening();
      scriptingTreeRequestSeq += 1;
      scriptingFileRequestSeq += 1;
      if (!mobileHubQuery.matches) {
        finishScriptingClose();
        return;
      }
      const box = modal.querySelector(".scripting-modal-box");
      modal.classList.remove("scripting-dragging");
      if (options.fromDrag === true) {
        modal.classList.add("scripting-swipe-closing");
        box.style.transition = "transform 220ms cubic-bezier(0.55, 0, 1, 0.45)";
        window.requestAnimationFrame(() => {
          box.style.transform = "translateY(100dvh)";
        });
      } else {
        box.style.transform = "";
        box.style.transition = "";
      }
      modal.classList.add("scripting-closing");
      scriptingCloseTimer = window.setTimeout(finishScriptingClose, 220);
    }

    function initMobileScriptingGestures() {
      const modal = el("scripting-modal");
      const box = modal.querySelector(".scripting-modal-box");
      const header = modal.querySelector(".scripting-modal-header");
      let closeDrag = null;

      header.addEventListener("pointerdown", (event) => {
        if (!mobileHubQuery.matches || !event.isPrimary || !isScriptingOpen()) return;
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
          finishScriptingOpening();
          modal.classList.add("scripting-dragging");
        }
        event.preventDefault();
        closeDrag.dy = Math.max(0, dy);
        box.style.transform = `translateY(${closeDrag.dy}px)`;
      }, { passive: false });
      function endScriptingCloseDrag(event, cancelled = false) {
        if (!closeDrag || (event && event.pointerId !== closeDrag.id)) return;
        const current = closeDrag;
        closeDrag = null;
        try { header.releasePointerCapture(current.id); } catch {}
        modal.classList.remove("scripting-dragging");
        if (!cancelled && current.active && current.dy > 100) {
          closeScripting({ fromDrag: true });
          return;
        }
        if (!current.active) return;
        box.style.transition = "transform 180ms ease";
        box.style.transform = "translateY(0)";
        window.setTimeout(() => {
          if (!isScriptingOpen() || modal.classList.contains("scripting-closing")) return;
          box.style.transition = "";
          box.style.transform = "";
        }, 190);
      }
      header.addEventListener("pointerup", (event) => endScriptingCloseDrag(event));
      header.addEventListener("pointercancel", (event) => endScriptingCloseDrag(event, true));
    }

    function initMobileScriptingHistoryGestures() {
      const panel = el("scripting-history-panel");
      const header = panel.querySelector(".scripting-history-header");
      let drag = null;
      header.addEventListener("pointerdown", (event) => {
        if (!mobileHubQuery.matches || !event.isPrimary || !isScriptingHistoryOpen()) return;
        if (event.target && event.target.closest && event.target.closest("button")) return;
        drag = { id: event.pointerId, startX: event.clientX, startY: event.clientY, dy: 0, active: false };
        try { header.setPointerCapture(event.pointerId); } catch {}
      });
      header.addEventListener("pointermove", (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        const dx = event.clientX - drag.startX;
        const dy = event.clientY - drag.startY;
        if (!drag.active) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
          if (dy <= 0 || Math.abs(dy) <= Math.abs(dx)) {
            drag = null;
            return;
          }
          drag.active = true;
        }
        event.preventDefault();
        drag.dy = Math.max(0, dy);
        panel.style.transform = `translateY(${drag.dy}px)`;
      }, { passive: false });
      function finish(event, cancelled = false) {
        if (!drag || (event && event.pointerId !== drag.id)) return;
        const current = drag;
        drag = null;
        try { header.releasePointerCapture(current.id); } catch {}
        if (!cancelled && current.active && current.dy > 90) {
          panel.style.transition = "transform 180ms ease-in";
          panel.style.transform = "translateY(100%)";
          window.setTimeout(closeScriptingHistory, 180);
          return;
        }
        if (!current.active) return;
        panel.style.transition = "transform 180ms ease";
        panel.style.transform = "translateY(0)";
        window.setTimeout(() => {
          if (!isScriptingHistoryOpen()) return;
          panel.style.transition = "";
          panel.style.transform = "";
        }, 190);
      }
      header.addEventListener("pointerup", (event) => finish(event));
      header.addEventListener("pointercancel", (event) => finish(event, true));
    }

    function initMobileScriptingOperationGestures() {
      const modal = el("scripting-operation-modal");
      const box = modal.querySelector(".scripting-operation-box");
      const handles = [
        modal.querySelector(".scripting-operation-handle"),
        modal.querySelector(".modal-header"),
      ];
      let drag = null;

      function start(event, handle) {
        if (
          !mobileHubQuery.matches
          || !event.isPrimary
          || scriptingOperationBusy
          || modal.classList.contains("hidden")
        ) return;
        if (event.target && event.target.closest && event.target.closest("button")) return;
        closeScriptingDirectoryMenus();
        drag = {
          id: event.pointerId,
          handle,
          startX: event.clientX,
          startY: event.clientY,
          dy: 0,
          active: false,
        };
        try { handle.setPointerCapture(event.pointerId); } catch {}
      }

      function move(event) {
        if (!drag || event.pointerId !== drag.id) return;
        const dx = event.clientX - drag.startX;
        const dy = event.clientY - drag.startY;
        if (!drag.active) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
          if (dy <= 0 || Math.abs(dy) <= Math.abs(dx)) {
            drag = null;
            return;
          }
          drag.active = true;
          modal.classList.add("scripting-operation-dragging");
        }
        event.preventDefault();
        drag.dy = Math.max(0, dy);
        box.style.transform = `translateY(${drag.dy}px)`;
      }

      function finish(event, cancelled = false) {
        if (!drag || (event && event.pointerId !== drag.id)) return;
        const current = drag;
        drag = null;
        try { current.handle.releasePointerCapture(current.id); } catch {}
        modal.classList.remove("scripting-operation-dragging");
        if (!cancelled && current.active && current.dy > 90) {
          closeScriptingOperation({ fromDrag: true });
          return;
        }
        if (!current.active) return;
        box.style.transition = "transform 180ms ease";
        box.style.transform = "translateY(0)";
        window.setTimeout(() => {
          if (modal.classList.contains("hidden")) return;
          box.style.transition = "";
          box.style.transform = "";
        }, 190);
      }

      handles.forEach((handle) => {
        handle.addEventListener("pointerdown", (event) => start(event, handle));
        handle.addEventListener("pointermove", move, { passive: false });
        handle.addEventListener("pointerup", (event) => finish(event));
        handle.addEventListener("pointercancel", (event) => finish(event, true));
      });
    }

    function initScriptingTreeLayout() {
      const workspace = el("scripting-workspace");
      const pane = workspace.querySelector(".scripting-tree-pane");
      const resizer = el("scripting-tree-resizer");
      let resize = null;

      loadScriptingTreeLayout();
      el("scripting-tree-toggle").addEventListener("click", () => {
        if (mobileHubQuery.matches) return;
        setScriptingTreeCollapsed(!scriptingTreeCollapsed);
      });

      resizer.addEventListener("pointerdown", (event) => {
        if (
          mobileHubQuery.matches
          || scriptingTreeCollapsed
          || !event.isPrimary
          || event.button !== 0
        ) return;
        event.preventDefault();
        resize = {
          id: event.pointerId,
          startX: event.clientX,
          startWidth: pane.getBoundingClientRect().width,
        };
        workspace.classList.add("scripting-tree-resizing");
        try { resizer.setPointerCapture(event.pointerId); } catch {}
      });
      resizer.addEventListener("pointermove", (event) => {
        if (!resize || event.pointerId !== resize.id) return;
        event.preventDefault();
        setScriptingTreeWidth(resize.startWidth + event.clientX - resize.startX, {
          persist: false,
        });
      }, { passive: false });
      const finishResize = (event) => {
        if (!resize || (event && event.pointerId !== resize.id)) return;
        const pointerId = resize.id;
        resize = null;
        workspace.classList.remove("scripting-tree-resizing");
        try { resizer.releasePointerCapture(pointerId); } catch {}
        setScriptingTreeWidth(scriptingTreeWidth);
      };
      resizer.addEventListener("pointerup", finishResize);
      resizer.addEventListener("pointercancel", finishResize);
      resizer.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const direction = event.key === "ArrowLeft" ? -1 : 1;
        setScriptingTreeWidth(scriptingTreeWidth + direction * 16);
      });
    }

    function initScriptingWorkspace() {
      const modal = el("scripting-modal");
      const tree = el("scripting-tree");
      const code = el("scripting-code");
      const highlight = el("scripting-highlight");

      initScriptingTreeLayout();
      scriptingEditorController = window.PyneEditor.create({
        editor: code,
        panel: el("scripting-find-panel"),
        findInput: el("scripting-find-input"),
        replaceInput: el("scripting-replace-input"),
        replaceRow: el("scripting-replace-row"),
        replaceToggle: el("scripting-replace-toggle"),
        count: el("scripting-find-count"),
        previousButton: el("scripting-find-previous"),
        nextButton: el("scripting-find-next"),
        closeButton: el("scripting-find-close"),
        replaceButton: el("scripting-replace-one"),
        replaceAllButton: el("scripting-replace-all"),
        highlightRoot: highlight,
        beforeReplace: () => captureScriptingUndo("replaceText"),
        afterReplace: handleScriptingInput,
        onOpen: () => setScriptingNoteOpen(false),
      });
      modal.addEventListener("keydown", (event) => {
        if (!event.target.closest(".scripting-editor-pane")) return;
        scriptingEditorController.handleShortcut(event);
      });
      el("hub-scripting-open").addEventListener("click", openScripting);
      el("scripting-close").addEventListener("click", closeScripting);
      el("scripting-mode").addEventListener("click", openScriptingRestart);
      el("scripting-back").addEventListener("click", () => {
        runScriptingNavigation(() => setScriptingMobileView("tree"));
      });
      el("scripting-refresh").addEventListener("click", () => {
        runScriptingNavigation(loadScriptingTree);
      });
      el("scripting-new-menu-toggle").addEventListener("click", () => {
        toggleScriptingTreeMenu("new");
      });
      el("scripting-actions-menu-toggle").addEventListener("click", (event) => {
        if (!scriptingTreeSelectedPath) return;
        toggleScriptingTreeMenu("actions", event.currentTarget);
      });
      [el("scripting-new-menu"), el("scripting-actions-menu")].forEach((menu) => {
        menu.addEventListener("click", (event) => {
          const button = event.target && event.target.closest
            ? event.target.closest("[data-scripting-operation]")
            : null;
          if (!button || !menu.contains(button)) return;
          requestScriptingOperation(String(button.dataset.scriptingOperation || ""));
        });
      });
      el("scripting-save").addEventListener("click", saveScriptingFile);
      el("scripting-find-toggle").addEventListener("click", () => {
        scriptingEditorController.toggle();
      });
      el("scripting-note-toggle").addEventListener("click", () => {
        const opening = el("scripting-note-editor").classList.contains("hidden");
        if (opening) scriptingEditorController.close({ focusEditor: false });
        if (!opening) setScriptingNote(scriptingBaseNote);
        setScriptingNoteOpen(opening);
      });
      el("scripting-note-close").addEventListener("click", () => {
        setScriptingNote("");
        el("scripting-note-input").focus({ preventScroll: true });
      });
      el("scripting-note-save").addEventListener("click", saveScriptingNote);
      el("scripting-note-input").addEventListener("input", (event) => {
        setScriptingNote(event.target.value);
      });
      el("scripting-note-input").addEventListener("keydown", (event) => {
        if (event.key !== "Escape" && event.key !== "Enter") return;
        event.preventDefault();
        event.stopPropagation();
        setScriptingNoteOpen(false);
        el("scripting-code").focus({ preventScroll: true });
      });
      el("scripting-undo").addEventListener("pointerdown", (event) => {
        scriptingUndoPointerType = event.pointerType || "";
      });
      el("scripting-undo").addEventListener("pointercancel", () => {
        scriptingUndoPointerType = "";
      });
      el("scripting-undo").addEventListener("click", () => {
        const focusEditor = scriptingUndoPointerType !== "touch";
        scriptingUndoPointerType = "";
        undoScriptingEdit({ focusEditor });
      });
      el("scripting-history").addEventListener("click", openScriptingHistory);
      el("scripting-history-close").addEventListener("click", closeScriptingHistory);
      el("scripting-editor-reload").addEventListener("click", () => {
        const path = scriptingSelectedPath;
        discardScriptingChanges();
        openScriptingFile(path, { showEditor: false });
      });
      tree.addEventListener("click", (event) => {
        const rowAction = event.target && event.target.closest
          ? event.target.closest("[data-scripting-action-path]")
          : null;
        if (rowAction && tree.contains(rowAction)) {
          const path = String(rowAction.dataset.scriptingActionPath || "");
          const actionsMenu = el("scripting-actions-menu");
          if (
            !actionsMenu.classList.contains("hidden")
            && scriptingTreeSelectedPath === path
          ) {
            closeScriptingTreeMenus();
            return;
          }
          closeScriptingTreeMenus();
          setScriptingTreeSelection(
            path,
            String(rowAction.dataset.scriptingActionType || "file"),
          );
          const currentAnchor = Array.from(
            tree.querySelectorAll("[data-scripting-action-path]"),
          ).find((button) => button.dataset.scriptingActionPath === path);
          toggleScriptingTreeMenu("actions", currentAnchor || rowAction);
          return;
        }
        const item = event.target && event.target.closest
          ? event.target.closest("[data-scripting-path]")
          : null;
        if (!item || !tree.contains(item)) return;
        const path = String(item.dataset.scriptingPath || "");
        if (
          !mobileHubQuery.matches
          && event.shiftKey
          && item.dataset.scriptingType === "file"
          && scriptingTreeSelectionAnchorPath
        ) {
          const visibleFiles = Array.from(tree.querySelectorAll(".scripting-tree-item.file"))
            .filter((candidate) => {
              let parent = candidate.parentElement;
              while (parent && parent !== tree) {
                if (
                  parent.classList.contains("scripting-tree-children")
                  && !parent.classList.contains("expanded")
                ) return false;
                parent = parent.parentElement;
              }
              return true;
            });
          const paths = visibleFiles.map((candidate) => String(candidate.dataset.scriptingPath || ""));
          const anchorIndex = paths.indexOf(scriptingTreeSelectionAnchorPath);
          const targetIndex = paths.indexOf(path);
          if (anchorIndex >= 0 && targetIndex >= 0) {
            scriptingTreeSelectedPaths.clear();
            const start = Math.min(anchorIndex, targetIndex);
            const end = Math.max(anchorIndex, targetIndex);
            paths.slice(start, end + 1).forEach((selectedPath) => {
              scriptingTreeSelectedPaths.add(selectedPath);
            });
            scriptingTreeSelectedPath = path;
            scriptingTreeSelectedType = "file";
            updateScriptingTreeSelectionView();
            return;
          }
        }
        if (item.dataset.scriptingType === "directory") {
          scriptingTreeSelectedPath = path;
          scriptingTreeSelectedType = "directory";
          scriptingTreeSelectionAnchorPath = "";
          scriptingTreeSelectedPaths.clear();
          scriptingTreeSelectedPaths.add(path);
          updateScriptingTreeSelectionView();
          const expanded = !scriptingExpandedDirectories.has(path);
          if (expanded) scriptingExpandedDirectories.add(path);
          else scriptingExpandedDirectories.delete(path);
          item.classList.toggle("expanded", expanded);
          item.setAttribute("aria-expanded", String(expanded));
          const row = item.closest(".scripting-tree-row");
          const children = row ? row.nextElementSibling : item.nextElementSibling;
          if (children && children.dataset.scriptingChildren === path) {
            children.inert = !expanded;
            children.setAttribute("aria-hidden", String(!expanded));
            children.classList.toggle("expanded", expanded);
          }
          return;
        }
        setScriptingTreeSelection(path, "file");
        if (path === scriptingSelectedPath) {
          if (mobileHubQuery.matches) setScriptingMobileView("editor");
          return;
        }
        runScriptingNavigation(() => openScriptingFile(path));
      });
      tree.addEventListener("contextmenu", (event) => {
        if (mobileHubQuery.matches) return;
        const item = event.target && event.target.closest
          ? event.target.closest("[data-scripting-path]")
          : null;
        if (!item || !tree.contains(item)) return;
        event.preventDefault();
        event.stopPropagation();
        const path = String(item.dataset.scriptingPath || "");
        const type = String(item.dataset.scriptingType || "file");
        closeScriptingTreeMenus();
        if (scriptingTreeSelectedPaths.has(path)) {
          scriptingTreeSelectedPath = path;
          scriptingTreeSelectedType = type;
          updateScriptingTreeSelectionView();
        } else {
          setScriptingTreeSelection(path, type);
        }
        toggleScriptingTreeMenu("actions", null, {
          clientX: event.clientX,
          clientY: event.clientY,
        });
      });
      el("scripting-history-list").addEventListener("click", (event) => {
        const item = event.target && event.target.closest
          ? event.target.closest("[data-revision-id]")
          : null;
        if (!item) return;
        selectScriptingHistoryRevision(Number(item.dataset.revisionId));
      });
      el("scripting-restore").addEventListener("click", () => {
        runScriptingNavigation(restoreScriptingRevision);
      });
      code.addEventListener("beforeinput", (event) => captureScriptingUndo(event.inputType || ""));
      code.addEventListener("input", handleScriptingInput);
      code.addEventListener("compositionstart", () => { scriptingComposing = true; });
      code.addEventListener("compositionend", () => {
        scriptingComposing = false;
        handleScriptingInput();
      });
      code.addEventListener("keydown", (event) => {
        if (scriptingEditorController.handleShortcut(event)) return;
        const command = event.metaKey || event.ctrlKey;
        if (command && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "z") {
          event.preventDefault();
          undoScriptingEdit();
          return;
        }
        if (command && event.key.toLowerCase() === "s") {
          event.preventDefault();
          saveScriptingFile();
          return;
        }
        if (command && event.key === "/") {
          event.preventDefault();
          toggleScriptingComments();
          return;
        }
        if (event.key === "Tab" && !command && !event.altKey) {
          event.preventDefault();
          captureScriptingUndo("insertText");
          const start = code.selectionStart;
          const end = code.selectionEnd;
          code.value = code.value.slice(0, start) + "    " + code.value.slice(end);
          code.setSelectionRange(start + 4, start + 4);
          handleScriptingInput();
        }
      });
      code.addEventListener("scroll", () => {
        highlight.scrollTop = code.scrollTop;
        highlight.scrollLeft = code.scrollLeft;
        el("scripting-diff-markers").style.transform = `translateY(${-code.scrollTop}px)`;
      }, { passive: true });
      el("scripting-unsaved-close").addEventListener("click", closeScriptingUnsaved);
      el("scripting-unsaved-cancel").addEventListener("click", closeScriptingUnsaved);
      el("scripting-unsaved-discard").addEventListener("click", () => {
        const action = scriptingPendingNavigation;
        closeScriptingUnsaved();
        discardScriptingChanges();
        if (action) action();
      });
      el("scripting-unsaved-save").addEventListener("click", async () => {
        const action = scriptingPendingNavigation;
        closeScriptingUnsaved();
        if (await saveScriptingFile()) {
          if (action) action();
        }
      });
      el("scripting-unsaved-modal").addEventListener("click", (event) => {
        if (event.target === el("scripting-unsaved-modal")) closeScriptingUnsaved();
      });
      el("scripting-operation-close").addEventListener("click", closeScriptingOperation);
      el("scripting-operation-cancel").addEventListener("click", closeScriptingOperation);
      el("scripting-operation-form").addEventListener("submit", submitScriptingOperation);
      el("scripting-operation-fields").addEventListener("click", (event) => {
        const button = event.target && event.target.closest
          ? event.target.closest("[data-scripting-template]")
          : null;
        if (!button) return;
        setScriptingOperationTemplate(button.dataset.scriptingTemplate);
      });
      el("scripting-operation-modal").addEventListener("click", (event) => {
        if (event.target === el("scripting-operation-modal")) closeScriptingOperation();
      });
      el("scripting-restart-cancel").addEventListener("click", closeScriptingRestart);
      el("scripting-restart-submit").addEventListener("click", restartScriptingRunners);
      document.addEventListener("pointerdown", (event) => {
        if (!event.target.closest?.(".scripting-directory-select")) {
          closeScriptingDirectoryMenus();
        }
        const restartPopover = el("scripting-restart-modal");
        if (
          !restartPopover.classList.contains("hidden")
          && !restartPopover.contains(event.target)
          && !el("scripting-mode").contains(event.target)
        ) closeScriptingRestart();
        const inTreeMenu = [
          el("scripting-new-menu"),
          el("scripting-actions-menu"),
          el("scripting-new-menu-toggle"),
          el("scripting-actions-menu-toggle"),
        ].some((node) => node && node.contains(event.target))
          || Boolean(event.target.closest && event.target.closest(".scripting-tree-row-action"));
        if (!inTreeMenu) closeScriptingTreeMenus();
        if (!isScriptingHistoryOpen()) return;
        const panel = el("scripting-history-panel");
        if (panel.contains(event.target) || el("scripting-history").contains(event.target)) return;
        closeScriptingHistory();
      });
      window.addEventListener("resize", () => {
        if (!isScriptingOpen()) return;
        renderScriptingTreeLayout();
        if (!mobileHubQuery.matches) setScriptingMobileView("desktop");
      }, { passive: true });
      document.addEventListener("keydown", (event) => {
        const unsavedOpen = !el("scripting-unsaved-modal").classList.contains("hidden");
        const operationOpen = !el("scripting-operation-modal").classList.contains("hidden");
        const restartOpen = !el("scripting-restart-modal").classList.contains("hidden");
        const eventTarget = event.target instanceof Element ? event.target : null;
        const editableTarget = Boolean(eventTarget && (
          eventTarget.matches("input, textarea, select")
          || eventTarget.isContentEditable
          || eventTarget.closest('[contenteditable="true"]')
        ));
        if (
          event.key === "Delete"
          && !mobileHubQuery.matches
          && isScriptingOpen()
          && !unsavedOpen
          && !operationOpen
          && !restartOpen
          && !isScriptingHistoryOpen()
          && !editableTarget
          && scriptingTreeSelectedPaths.size > 0
        ) {
          event.preventDefault();
          event.stopPropagation();
          requestScriptingOperation("delete");
          return;
        }
        if (
          isScriptingOpen()
          && !unsavedOpen
          && !operationOpen
          && !restartOpen
          && !isScriptingHistoryOpen()
          && scriptingEditorController.handleShortcut(event)
        ) return;
        if (event.key !== "Escape") return;
        const directoryMenuOpen = Boolean(
          el("scripting-operation-fields").querySelector(".scripting-directory-select.open"),
        );
        if (directoryMenuOpen) {
          closeScriptingDirectoryMenus();
          return;
        }
        if (restartOpen) {
          closeScriptingRestart();
        } else if (operationOpen) {
          closeScriptingOperation();
        } else if (unsavedOpen) {
          closeScriptingUnsaved();
        } else if (isScriptingHistoryOpen()) {
          closeScriptingHistory();
        } else if (isScriptingOpen()) {
          closeScripting();
        }
      });
      initMobileScriptingGestures();
      initMobileScriptingHistoryGestures();
      initMobileScriptingOperationGestures();
    }

    initScriptingWorkspace();
    initialized = true;
  }

  window.PyneScripting = {
    init,
    selectAssignedFile(path) {
      if (assignedFileHandler) assignedFileHandler(path);
    },
  };
})();
