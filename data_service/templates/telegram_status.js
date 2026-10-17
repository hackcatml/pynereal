(() => {
  const root = document.getElementById("telegram-service");
  if (!root) return;
  const toggle = document.getElementById("telegram-service-toggle");
  const panel = document.getElementById("telegram-service-panel");
  const restart = document.getElementById("telegram-service-restart");
  const label = document.getElementById("telegram-service-state");
  const reason = document.getElementById("telegram-service-reason");
  const retry = document.getElementById("telegram-service-retry");
  const labels = { unknown: "Connecting", disabled: "Disabled", starting: "Starting",
    restarting: "Restarting", running: "Running", retrying: "Reconnecting", stopped: "Stopped" };
  let state = { state: "unknown", enabled: false };
  let busy = false;
  let connected = false;
  let generation = 0;
  let latestUpdate = 0;

  function render() {
    const value = busy ? "restarting" : connected ? state.state : "unknown";
    root.dataset.state = value;
    label.textContent = labels[value] || "Unknown";
    toggle.setAttribute("aria-label", `Telegram commands: ${label.textContent}`);
    restart.disabled = busy || !connected || !state.enabled || value === "starting";
    restart.setAttribute("aria-busy", String(busy));
    reason.textContent = connected ? state.reason || "" : "";
    reason.hidden = !reason.textContent;
    retry.hidden = !connected || !state.retry_at || value !== "retrying";
    retry.textContent = retry.hidden ? "" : `Retry at ${new Date(state.retry_at * 1000).toLocaleTimeString("en-GB")}`;
  }

  function update(value) {
    if (!value || !Object.hasOwn(labels, value.state)) return;
    const updatedAt = Number(value.updated_at) || 0;
    if (updatedAt < latestUpdate) return;
    latestUpdate = updatedAt;
    state = value;
    connected = true;
    render();
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { cache: "no-store", ...options });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || "Telegram service request failed");
    return value;
  }

  async function sync() {
    const current = generation;
    try {
      const value = await request("/api/telegram/status");
      if (current === generation) update(value);
    } catch {
      if (current === generation) {
        connected = false;
        render();
      }
    }
  }

  function disconnect() {
    generation += 1;
    latestUpdate = 0;
    connected = false;
    render();
  }

  function close() {
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  toggle.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden) sync();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!root.contains(event.target)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      close();
      toggle.focus({ preventScroll: true });
    }
  });
  restart.addEventListener("click", async () => {
    if (restart.disabled || busy) return;
    busy = true;
    render();
    const current = generation;
    try {
      const value = await request("/api/telegram/restart", { method: "POST" });
      if (current === generation) update(value);
    } catch (error) {
      if (current === generation) state = { ...state, reason: error.message };
    } finally {
      busy = false;
      render();
    }
  });

  window.PyneTelegramStatus = { update, sync, disconnect };
  render();
})();
