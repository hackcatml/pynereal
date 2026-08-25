(function () {
  const SCRIPTING_TREE_WIDTH_KEY = "pynereal.scripting.tree.width";
  const SCRIPTING_TREE_COLLAPSED_KEY = "pynereal.scripting.tree.collapsed";
  const SCRIPTING_TREE_MIN_WIDTH = 210;
  const SCRIPTING_TREE_MAX_WIDTH = 520;
  const SCRIPTING_TREE_DEFAULT_WIDTH = 290;
  let initialized = false;

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
    const scriptingExpandedDirectories = new Set();
    const el = (id) => document.getElementById(id);

    function isScriptingOpen() {
      return !el("scripting-modal").classList.contains("hidden");
    }

    function setScriptingStatus(message, state = "") {
      if (scriptingStatusTimer !== null) {
        clearTimeout(scriptingStatusTimer);
        scriptingStatusTimer = null;
      }
      const mode = el("scripting-mode");
      mode.textContent = String(message || (scriptingSelectedPath ? "Ready" : "No file"));
      mode.dataset.state = state;
      if (state === "saved") {
        scriptingStatusTimer = window.setTimeout(() => {
          scriptingStatusTimer = null;
          if (!scriptingHasUnsavedChanges() && !scriptingSaving) setScriptingStatus("Ready");
        }, 3000);
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
      else if (loaded) setScriptingStatus("Ready");
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
        button.classList.toggle("selected", !directory && entry.path === scriptingSelectedPath);
        button.style.setProperty("--scripting-depth", String(depth));
        button.dataset.scriptingPath = entry.path;
        button.dataset.scriptingType = directory ? "directory" : "file";
        button.setAttribute("role", "treeitem");
        button.setAttribute("aria-level", String(depth + 1));
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
        container.appendChild(button);

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
    }

    function setScriptingTreeState(state, message = "") {
      el("scripting-tree-loading").classList.toggle("hidden", state !== "loading");
      el("scripting-tree-error").classList.toggle("hidden", state !== "error");
      el("scripting-tree-empty").classList.toggle("hidden", state !== "empty");
      el("scripting-tree").classList.toggle("hidden", state !== "ready");
      el("scripting-tree-error").textContent = state === "error" ? message : "";
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
      renderScriptingTree();
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
      if (!options.preserveStatus) setScriptingStatus("Ready");
      renderScriptingTree();
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
        renderScriptingFile(payload, { preserveStatus: true });
        setScriptingStatus(payload.saved ? "Applies at next warm-up" : "Note saved", "saved");
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
        renderScriptingFile(payload, { preserveStatus: true });
        setScriptingStatus("Applies at next warm-up", "saved");
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
      renderScriptingTree();
      try {
        const payload = await api(
          `/api/scripting/file?path=${encodeURIComponent(normalizedPath)}`,
          { cache: "no-store" },
        );
        if (seq !== scriptingFileRequestSeq || !isScriptingOpen()) return;
        renderScriptingFile(payload);
      } catch (error) {
        if (seq !== scriptingFileRequestSeq || !isScriptingOpen()) return;
        setScriptingEditorState(
          "error",
          error && error.message ? error.message : "Source could not be loaded.",
        );
        setScriptingStatus("Load failed", "conflict");
      }
    }

    async function loadScriptingTree() {
      const seq = ++scriptingTreeRequestSeq;
      const refreshSelectedFile = !mobileHubQuery.matches
        || el("scripting-modal").classList.contains("scripting-show-editor");
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
        if (scriptingSelectedPath && !scriptingContainsPath(scriptingEntries, scriptingSelectedPath)) {
          resetScriptingEditor();
        }
        renderScriptingTree();
        setScriptingTreeState(count ? "ready" : "empty");
        if (
          refreshSelectedFile
          && scriptingSelectedPath
          && scriptingContainsPath(scriptingEntries, scriptingSelectedPath)
        ) {
          await openScriptingFile(scriptingSelectedPath, { showEditor: false });
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
      if (scriptingEntries === null) loadScriptingTree();
      else {
        renderScriptingTree();
        setScriptingTreeState(scriptingFileCount(scriptingEntries) ? "ready" : "empty");
        if (scriptingSelectedPath && !scriptingHasUnsavedChanges()) {
          openScriptingFile(scriptingSelectedPath, { showEditor: false });
        }
      }
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
      el("scripting-back").addEventListener("click", () => {
        runScriptingNavigation(() => setScriptingMobileView("tree"));
      });
      el("scripting-refresh").addEventListener("click", () => {
        runScriptingNavigation(loadScriptingTree);
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
      modal.addEventListener("click", (event) => {
        if (event.target === modal) closeScripting();
      });
      tree.addEventListener("click", (event) => {
        const item = event.target && event.target.closest
          ? event.target.closest("[data-scripting-path]")
          : null;
        if (!item || !tree.contains(item)) return;
        const path = String(item.dataset.scriptingPath || "");
        if (item.dataset.scriptingType === "directory") {
          const expanded = !scriptingExpandedDirectories.has(path);
          if (expanded) scriptingExpandedDirectories.add(path);
          else scriptingExpandedDirectories.delete(path);
          item.classList.toggle("expanded", expanded);
          item.setAttribute("aria-expanded", String(expanded));
          const children = item.nextElementSibling;
          if (children && children.dataset.scriptingChildren === path) {
            children.inert = !expanded;
            children.setAttribute("aria-hidden", String(!expanded));
            children.classList.toggle("expanded", expanded);
          }
          return;
        }
        if (path === scriptingSelectedPath) {
          if (mobileHubQuery.matches) setScriptingMobileView("editor");
          return;
        }
        runScriptingNavigation(() => openScriptingFile(path));
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
      document.addEventListener("pointerdown", (event) => {
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
        if (
          isScriptingOpen()
          && !unsavedOpen
          && !isScriptingHistoryOpen()
          && scriptingEditorController.handleShortcut(event)
        ) return;
        if (event.key !== "Escape") return;
        if (unsavedOpen) {
          closeScriptingUnsaved();
        } else if (isScriptingHistoryOpen()) {
          closeScriptingHistory();
        } else if (isScriptingOpen()) {
          closeScripting();
        }
      });
      initMobileScriptingGestures();
      initMobileScriptingHistoryGestures();
    }

    initScriptingWorkspace();
    initialized = true;
  }

  window.PyneScripting = { init };
})();
