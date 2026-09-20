(() => {
  const query = new URLSearchParams(location.search);
  if (window.parent === window || query.get("embedded") !== "1") return;
  const root = document.documentElement;
  root.classList.add("backtest-embedded");
  root.classList.toggle("backtest-compact", query.get("compact") === "1");
  let api = null;
  let enabled = false;
  let leader = false;
  let queued = null;
  let applying = false;
  let revision = 0;
  let scheduled = false;
  let lastSent = "";
  const post = (type, extra = {}) => window.parent.postMessage({ type, ...extra }, location.origin);

  function cancel() {
    revision++;
    queued = null;
    if (applying) api?.cancel();
    lastSent = "";
  }

  function notify() {
    if (!api || !enabled || !leader || scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      if (!enabled || !leader) return;
      const view = api.read();
      if (!view) return;
      const signature = `${view.from}:${view.to}:${view.time}`;
      if (signature === lastSent) return;
      lastSent = signature;
      post("pynereal:backtest-view", { view });
    });
  }

  async function applyQueued() {
    if (!api || applying || !enabled || leader || !queued) return;
    const view = queued;
    const request = revision;
    queued = null;
    applying = true;
    const current = () => enabled && !leader && revision === request;
    try {
      await api.apply(view, current);
    } catch (error) {
      if (current()) api.error(error.message || "Chart synchronization failed.");
    } finally {
      applying = false;
      void applyQueued();
    }
  }

  window.BacktestChartLink = {
    attach(controller) {
      api = controller;
      api.subscribe(notify);
      post("pynereal:backtest-ready");
      notify();
      void applyQueued();
    },
    notify,
  };
  window.addEventListener("message", event => {
    if (event.origin !== location.origin || event.source !== window.parent) return;
    const data = event.data;
    if (data?.type === "pynereal:backtest-layout") {
      const compact = data.compact === true;
      if (root.classList.contains("backtest-compact") === compact) return;
      root.classList.toggle("backtest-compact", compact);
      window.dispatchEvent(new Event("resize"));
    } else if (data?.type === "pynereal:backtest-sync-state") {
      if (enabled !== (data.enabled === true) || leader !== (data.leader === true)) cancel();
      enabled = data.enabled === true;
      leader = data.leader === true;
      notify();
    } else if (data?.type === "pynereal:backtest-sync-view" && enabled && !leader) {
      const view = data.view;
      if (!view || !Number.isFinite(view.from) || !Number.isFinite(view.to)
        || view.from >= view.to || !Number.isFinite(view.time)) return;
      revision++;
      queued = view;
      void applyQueued();
    }
  });
  const select = () => {
    if (enabled && !leader) { cancel(); leader = true; }
    post("pynereal:backtest-select");
  };
  document.addEventListener("pointerdown", select, { capture: true, passive: true });
  document.addEventListener("focusin", select);
  document.addEventListener("wheel", select, { capture: true, passive: true });
})();
