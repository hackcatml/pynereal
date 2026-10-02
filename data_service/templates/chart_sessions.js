var App = window.App || (window.App = {});

App.sessionPicker = {
  controller: null,
  isOpen() { return !!this.menu && !this.menu.classList.contains("hidden"); },
  button() { return document.getElementById("chart-session-toggle"); },
  available(sessions) {
    return Array.isArray(sessions) ? sessions.filter(session =>
      session && typeof session.id === "string" && session.id && session.history_ready === true) : [];
  },
  close(focus = false) {
    this.controller?.abort();
    this.controller = null;
    this.menu?.classList.add("hidden");
    this.button()?.setAttribute("aria-expanded", "false");
    if (focus) this.button()?.focus({ preventScroll: true });
  },
  position() {
    const button = this.button();
    if (!button || !this.menu) return;
    const anchor = button.getBoundingClientRect(), viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
    this.menu.style.maxHeight = `${Math.max(60, Math.min(420, height - 24))}px`;
    const rect = this.menu.getBoundingClientRect();
    this.menu.style.left = `${Math.max(left + 12, Math.min(anchor.left, left + width - rect.width - 12))}px`;
    this.menu.style.top = `${Math.max(top + 12, Math.min(anchor.bottom + 6, top + height - rect.height - 12))}px`;
  },
  status(text, retry = false) {
    const status = document.createElement("div");
    status.className = "chart-session-status";
    status.setAttribute("role", "status");
    status.textContent = text;
    this.menu.replaceChildren(status);
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.retry = "true";
      button.textContent = "Retry";
      this.menu.append(button);
    }
    this.position();
  },
  render(sessions) {
    const available = this.available(sessions);
    if (!available.length) { this.status("No available sessions."); return; }
    this.menu.replaceChildren(...available.map(session => {
      const link = document.createElement("a");
      link.href = `/s/${encodeURIComponent(session.id)}`;
      link.dataset.sessionId = session.id;
      link.setAttribute("role", "menuitem");
      if (session.id === App.config.runtimeId) link.setAttribute("aria-current", "page");
      const name = document.createElement("span"), detail = document.createElement("span");
      name.className = "chart-session-name";
      name.textContent = `${session.symbol || session.id} | ${session.timeframe || ""}`;
      detail.className = "chart-session-detail";
      detail.textContent = [String(session.exchange || "").toUpperCase(), session.script_name || ""].filter(Boolean).join(" | ");
      link.append(name, detail);
      return link;
    }));
    this.position();
    (this.menu.querySelector('[aria-current="page"]') || this.menu.querySelector("a"))?.focus({ preventScroll: true });
  },
  async load() {
    this.controller?.abort();
    const controller = this.controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    this.status("Loading...");
    try {
      const response = await fetch("/api/sessions", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("Sessions unavailable");
      const data = await response.json();
      if (this.controller === controller && this.isOpen()) this.render(data.sessions);
    } catch {
      if (this.controller === controller && this.isOpen()) this.status("Sessions unavailable.", true);
    } finally {
      clearTimeout(timeout);
      if (this.controller === controller) this.controller = null;
    }
  },
  toggle() {
    if (this.isOpen()) { this.close(); return; }
    App.timeframes?.closeMenu();
    App.indicators?.close();
    this.menu.classList.remove("hidden");
    this.button()?.setAttribute("aria-expanded", "true");
    void this.load();
  },
  init() {
    this.menu = document.getElementById("chart-session-menu");
    if (!this.menu || !App.config.runtimeId) return;
    App.ui.elements.chartInfoBase.addEventListener("click", event => {
      if (!event.target.closest("#chart-session-toggle")) return;
      event.stopPropagation();
      this.toggle();
    });
    this.menu.addEventListener("click", event => {
      if (event.target.closest("[data-retry]")) { void this.load(); return; }
      const link = event.target.closest("a[data-session-id]");
      if (!link) return;
      if (link.dataset.sessionId === App.config.runtimeId) event.preventDefault();
      this.close();
      // Keep normal link navigation, including the editor's beforeunload guard.
    });
    this.menu.addEventListener("dblclick", event => event.preventDefault());
    document.addEventListener("pointerdown", event => {
      if (!this.menu.contains(event.target) && !event.target.closest("#chart-session-toggle")) this.close();
    }, true);
    document.addEventListener("keydown", event => {
      if (!this.isOpen()) {
        if (event.key === "ArrowDown" && event.target.closest("#chart-session-toggle")) {
          event.preventDefault();
          this.toggle();
        }
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        this.close(true);
      } else if (event.key === "Tab") this.close();
      else if ((this.menu.contains(event.target) || event.target.closest("#chart-session-toggle")) &&
          ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const items = [...this.menu.querySelectorAll("a, button")];
        if (!items.length) return;
        let index = items.indexOf(document.activeElement);
        if (event.key === "Home") index = 0;
        else if (event.key === "End") index = items.length - 1;
        else index = (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[index]?.focus();
      }
    });
    window.addEventListener("resize", () => this.close());
    window.visualViewport?.addEventListener("resize", () => this.close());
    window.addEventListener("pagehide", () => this.close());
  },
};
