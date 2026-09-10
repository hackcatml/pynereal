(() => {
  const standalone = window.matchMedia("(display-mode: standalone)");
  const mobile = window.matchMedia("(max-width: 720px)");
  const root = document.documentElement;
  const viewport = document.querySelector('meta[name="viewport"]');
  const browserViewport = viewport?.content;
  const appViewport = browserViewport && browserViewport.split(",")
    .map(part => part.trim())
    .filter(part => !part.startsWith("viewport-fit="))
    .concat("viewport-fit=cover")
    .join(", ");
  const updateMode = () => {
    const enabled = mobile.matches && (standalone.matches || window.navigator.standalone === true);
    root.classList.toggle("pwa-mobile", enabled);
    // The installed app handles safe areas once in CSS, on every page.
    if (viewport && browserViewport) {
      const content = enabled ? appViewport : browserViewport;
      if (viewport.content !== content) viewport.content = content;
    }
  };
  updateMode();
  standalone.addEventListener("change", updateMode);
  mobile.addEventListener("change", updateMode);

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("click", (event) => {
      if (!root.classList.contains("pwa-mobile") || event.defaultPrevented
          || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const chart = event.target instanceof Element
        ? event.target.closest('a[data-field="chart-link"]') : null;
      if (!chart) return;
      event.preventDefault();
      // Keep the originating dashboard in this window's history in standalone mode.
      window.location.assign(chart.href);
    });
    const home = document.querySelector(".pwa-chart-home");
    let navigatingHome = false;
    if (home) {
      window.addEventListener("pageshow", () => { navigatingHome = false; });
    }
    home?.addEventListener("click", (event) => {
      if (!root.classList.contains("pwa-mobile") || event.defaultPrevented
          || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (navigatingHome) return;
      navigatingHome = true;

      let returnToPrevious = false;
      if (window.history.length > 1 && document.referrer) {
        try {
          const previous = new URL(document.referrer);
          const dashboard = new URL(home.href);
          returnToPrevious = previous.origin === dashboard.origin
            && (previous.pathname === dashboard.pathname
              || previous.pathname.startsWith("/s/") || previous.pathname.startsWith("/backtests/"));
        } catch {}
      }
      // Restore the originating page, including its backtest view when applicable.
      if (returnToPrevious) window.history.back();
      else window.location.replace(home.href);
    });

    document.addEventListener("dblclick", (event) => {
      if (root.classList.contains("pwa-mobile")
          && event.target instanceof Element
          && event.target.closest("#chart, #backtest-price-chart")) {
        // Leave chart handlers intact, including double-tap price autoscale.
        event.preventDefault();
      }
    });
  }, { once: true });
})();
