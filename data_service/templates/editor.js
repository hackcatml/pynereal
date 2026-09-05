(function () {
  function create(options) {
    const editor = options.editor;
    const codeEditor = editor && editor._pyneCodeEditor;
    if (!codeEditor) throw new Error("CodeMirror editor is required");
    const panel = options.panel;
    const findInput = options.findInput;
    const replaceInput = options.replaceInput;
    const replaceRow = options.replaceRow;
    const replaceToggle = options.replaceToggle;
    const count = options.count;
    const previousButton = options.previousButton;
    const nextButton = options.nextButton;
    const closeButton = options.closeButton;
    const replaceButton = options.replaceButton;
    const replaceAllButton = options.replaceAllButton;
    const mobileQuery = window.matchMedia("(max-width: 720px)");
    let matches = [];
    let currentIndex = -1;
    let replaceAllEnabled = false;
    let highlightFrame = null;

    function isOpen() {
      return !panel.classList.contains("hidden");
    }

    function collectMatches() {
      const query = findInput.value;
      if (!query) return [];
      const source = codeEditor.getValue().toLowerCase();
      const needle = query.toLowerCase();
      const found = [];
      let offset = 0;
      let line = 0;
      let nextLineBreak = source.indexOf("\n");
      while (offset <= source.length - needle.length) {
        const start = source.indexOf(needle, offset);
        if (start < 0) break;
        while (nextLineBreak >= 0 && nextLineBreak < start) {
          line += 1;
          nextLineBreak = source.indexOf("\n", nextLineBreak + 1);
        }
        found.push({ start, end: start + query.length, line });
        offset = start + Math.max(1, query.length);
      }
      return found;
    }

    function clearSearchHighlights() {
      codeEditor.clearSearchMatches();
    }

    function renderSearchHighlights() {
      highlightFrame = null;
      if (!isOpen() || !matches.length) codeEditor.clearSearchMatches();
      else codeEditor.setSearchMatches(matches, currentIndex);
    }

    function scheduleSearchHighlights() {
      if (highlightFrame !== null) cancelAnimationFrame(highlightFrame);
      highlightFrame = requestAnimationFrame(renderSearchHighlights);
    }

    function updateControls() {
      const available = matches.length > 0;
      count.textContent = available ? `${currentIndex + 1}/${matches.length}` : "0/0";
      count.classList.toggle("empty", !available && Boolean(findInput.value));
      previousButton.disabled = !available;
      nextButton.disabled = !available;
      replaceButton.disabled = !available;
      replaceButton.title = replaceAllEnabled ? "Replace all matches" : "Replace current match";
      scheduleSearchHighlights();
    }

    function revealMatch(index, { focusEditor = false } = {}) {
      if (!matches.length) {
        currentIndex = -1;
        updateControls();
        return false;
      }
      currentIndex = (index + matches.length) % matches.length;
      const match = matches[currentIndex];
      codeEditor.revealRange(match.start, match.end, { focus: focusEditor });
      updateControls();
      return true;
    }

    function refresh({ select = false, anchor = codeEditor.getSelection().from } = {}) {
      matches = collectMatches();
      if (!matches.length) {
        currentIndex = -1;
        updateControls();
        return;
      }
      const selection = codeEditor.getSelection();
      const selectedIndex = matches.findIndex((match) => (
        match.start === selection.from && match.end === selection.to
      ));
      if (selectedIndex >= 0) currentIndex = selectedIndex;
      else {
        const nextIndex = matches.findIndex((match) => match.start >= anchor);
        currentIndex = nextIndex >= 0 ? nextIndex : 0;
      }
      if (select) revealMatch(currentIndex);
      else updateControls();
    }

    function setReplaceExpanded(expanded) {
      const resolved = Boolean(expanded);
      replaceRow.classList.toggle("hidden", !resolved);
      replaceToggle.classList.toggle("expanded", resolved);
      replaceToggle.setAttribute("aria-expanded", String(resolved));
    }

    function open({ replace = false } = {}) {
      if (!isOpen() && typeof options.onOpen === "function") options.onOpen();
      const range = codeEditor.getSelection();
      const selection = codeEditor.getValue().slice(range.from, range.to);
      if (selection && !selection.includes("\n") && selection.length <= 120) {
        findInput.value = selection;
      }
      panel.classList.remove("hidden");
      panel.setAttribute("aria-hidden", "false");
      setReplaceExpanded(replace);
      refresh({ select: Boolean(findInput.value) });
      window.requestAnimationFrame(() => {
        findInput.focus({ preventScroll: true });
        findInput.select();
      });
    }

    function close({ focusEditor = true } = {}) {
      panel.classList.add("hidden");
      panel.setAttribute("aria-hidden", "true");
      if (focusEditor && !mobileQuery.matches) codeEditor.focus({ preventScroll: true });
      else if (document.activeElement && panel.contains(document.activeElement)) {
        document.activeElement.blur();
      }
      scheduleSearchHighlights();
    }

    function toggle() {
      if (isOpen()) close();
      else open();
    }

    function move(direction) {
      if (!matches.length) refresh();
      if (matches.length) revealMatch(currentIndex + direction);
    }

    function replaceCurrent() {
      if (!matches.length || currentIndex < 0) return;
      const match = matches[currentIndex];
      if (typeof options.beforeReplace === "function") options.beforeReplace();
      codeEditor.replaceRange(match.start, match.end, replaceInput.value, { emitChange: false });
      if (typeof options.afterReplace === "function") options.afterReplace();
      refresh({ select: true, anchor: match.start + replaceInput.value.length });
    }

    function replaceAll() {
      if (!matches.length) return;
      if (typeof options.beforeReplace === "function") options.beforeReplace();
      const source = codeEditor.getValue();
      const replacement = replaceInput.value;
      const parts = [];
      let offset = 0;
      matches.forEach((match) => {
        parts.push(source.slice(offset, match.start), replacement);
        offset = match.end;
      });
      parts.push(source.slice(offset));
      codeEditor.setValue(parts.join(""), {
        addToHistory: true,
        selection: { anchor: 0, head: 0 },
      });
      if (typeof options.afterReplace === "function") options.afterReplace();
      refresh({ select: false, anchor: 0 });
    }

    function replace() {
      if (replaceAllEnabled) replaceAll();
      else replaceCurrent();
    }

    function setReplaceAllEnabled(enabled) {
      replaceAllEnabled = Boolean(enabled);
      replaceAllButton.setAttribute("aria-pressed", String(replaceAllEnabled));
      replaceAllButton.title = replaceAllEnabled ? "Replace all is on" : "Replace all is off";
      updateControls();
    }

    function handleShortcut(event) {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return false;
      const key = event.key.toLowerCase();
      if (key !== "f" && key !== "r") return false;
      event.preventDefault();
      event.stopPropagation();
      open({ replace: key === "r" });
      return true;
    }

    findInput.addEventListener("input", () => refresh({ select: true }));
    findInput.addEventListener("keydown", (event) => {
      if (handleShortcut(event)) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === "Enter") {
        event.preventDefault();
        move(event.shiftKey ? -1 : 1);
      }
    });
    replaceInput.addEventListener("keydown", (event) => {
      if (handleShortcut(event)) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === "Enter") {
        event.preventDefault();
        replace();
      }
    });
    replaceToggle.addEventListener("click", () => {
      const expanded = replaceToggle.getAttribute("aria-expanded") === "true";
      setReplaceExpanded(!expanded);
      if (!expanded) replaceInput.focus({ preventScroll: true });
    });
    previousButton.addEventListener("click", () => move(-1));
    nextButton.addEventListener("click", () => move(1));
    closeButton.addEventListener("click", () => close());
    replaceButton.addEventListener("click", replace);
    replaceAllButton.addEventListener("click", () => {
      setReplaceAllEnabled(!replaceAllEnabled);
    });
    editor.addEventListener("input", () => {
      if (isOpen()) refresh({ select: false });
    });
    editor.addEventListener("scroll", () => {
      if (isOpen()) scheduleSearchHighlights();
    }, { passive: true });

    setReplaceExpanded(false);
    setReplaceAllEnabled(false);
    updateControls();
    return {
      close,
      handleShortcut,
      isOpen,
      open,
      refresh,
      toggle,
    };
  }

  window.PyneEditor = { create };
})();
