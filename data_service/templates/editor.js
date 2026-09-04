(function () {
  const MAX_VISIBLE_SEARCH_HIGHLIGHTS = 80;
  const SEARCH_HIGHLIGHT_OVERSCAN_LINES = 2;

  function create(options) {
    const editor = options.editor;
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
    const highlightRoot = options.highlightRoot || null;
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
      const source = editor.value.toLowerCase();
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
      if (!highlightRoot) return;
      const parents = new Set();
      highlightRoot.querySelectorAll("mark.editor-search-match").forEach((mark) => {
        if (mark.parentNode) parents.add(mark.parentNode);
        mark.replaceWith(...mark.childNodes);
      });
      parents.forEach((parent) => parent.normalize());
    }

    function textSegments() {
      const walker = document.createTreeWalker(highlightRoot, NodeFilter.SHOW_TEXT);
      const segments = [];
      let start = 0;
      let node = walker.nextNode();
      while (node) {
        const end = start + node.data.length;
        segments.push({ node, start, end });
        start = end;
        node = walker.nextNode();
      }
      return segments;
    }

    function textBoundary(segments, offset) {
      if (!segments.length) return null;
      const target = Math.max(0, Number(offset) || 0);
      let low = 0;
      let high = segments.length - 1;
      while (low <= high) {
        const middle = Math.floor((low + high) / 2);
        const segment = segments[middle];
        if (target < segment.start) high = middle - 1;
        else if (target > segment.end) low = middle + 1;
        else return { node: segment.node, offset: target - segment.start };
      }
      const last = segments[segments.length - 1];
      return { node: last.node, offset: last.node.data.length };
    }

    function highlightedMatchIndexes() {
      const style = getComputedStyle(editor);
      const lineHeight = parseFloat(style.lineHeight) || 18;
      const paddingTop = parseFloat(style.paddingTop) || 0;
      const firstLine = Math.max(
        0,
        Math.floor((editor.scrollTop - paddingTop) / lineHeight)
          - SEARCH_HIGHLIGHT_OVERSCAN_LINES,
      );
      const lastLine = Math.ceil(
        (editor.scrollTop + editor.clientHeight - paddingTop) / lineHeight,
      ) + SEARCH_HIGHLIGHT_OVERSCAN_LINES;
      const indexes = [];
      for (let index = 0; index < matches.length; index += 1) {
        const line = matches[index].line;
        if (line < firstLine) continue;
        if (line > lastLine || indexes.length >= MAX_VISIBLE_SEARCH_HIGHLIGHTS) break;
        indexes.push(index);
      }
      if (currentIndex >= 0 && !indexes.includes(currentIndex)) indexes.push(currentIndex);
      indexes.sort((left, right) => left - right);
      return indexes;
    }

    function renderSearchHighlights() {
      highlightFrame = null;
      if (!highlightRoot) return;
      clearSearchHighlights();
      if (!isOpen() || !matches.length) return;
      const segments = textSegments();
      const indexes = highlightedMatchIndexes();
      for (let position = indexes.length - 1; position >= 0; position -= 1) {
        const index = indexes[position];
        const match = matches[index];
        const start = textBoundary(segments, match.start);
        const end = textBoundary(segments, match.end);
        if (!start || !end) continue;
        const range = document.createRange();
        range.setStart(start.node, start.offset);
        range.setEnd(end.node, end.offset);
        const mark = document.createElement("mark");
        mark.className = `editor-search-match${index === currentIndex ? " current" : ""}`;
        mark.appendChild(range.extractContents());
        range.insertNode(mark);
      }
    }

    function scheduleSearchHighlights() {
      if (!highlightRoot) return;
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
      editor.setSelectionRange(match.start, match.end);

      const before = editor.value.slice(0, match.start);
      const line = before.split("\n").length - 1;
      const style = getComputedStyle(editor);
      const lineHeight = parseFloat(style.lineHeight) || 18;
      const paddingTop = parseFloat(style.paddingTop) || 0;
      const matchTop = paddingTop + line * lineHeight;
      const visibleTop = editor.scrollTop;
      const visibleBottom = visibleTop + editor.clientHeight - lineHeight;
      if (matchTop < visibleTop || matchTop > visibleBottom) {
        editor.scrollTop = Math.max(0, matchTop - editor.clientHeight / 2 + lineHeight);
      }
      editor.dispatchEvent(new Event("scroll"));
      if (focusEditor) editor.focus({ preventScroll: true });
      updateControls();
      return true;
    }

    function refresh({ select = false, anchor = editor.selectionStart } = {}) {
      matches = collectMatches();
      if (!matches.length) {
        currentIndex = -1;
        updateControls();
        return;
      }
      const selectedIndex = matches.findIndex((match) => (
        match.start === editor.selectionStart && match.end === editor.selectionEnd
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
      const selection = editor.value.slice(editor.selectionStart, editor.selectionEnd);
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
      if (focusEditor && !mobileQuery.matches) editor.focus({ preventScroll: true });
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
      editor.setRangeText(replaceInput.value, match.start, match.end, "end");
      if (typeof options.afterReplace === "function") options.afterReplace();
      refresh({ select: true, anchor: match.start + replaceInput.value.length });
    }

    function replaceAll() {
      if (!matches.length) return;
      if (typeof options.beforeReplace === "function") options.beforeReplace();
      const source = editor.value;
      const replacement = replaceInput.value;
      const parts = [];
      let offset = 0;
      matches.forEach((match) => {
        parts.push(source.slice(offset, match.start), replacement);
        offset = match.end;
      });
      parts.push(source.slice(offset));
      editor.value = parts.join("");
      editor.setSelectionRange(0, 0);
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
