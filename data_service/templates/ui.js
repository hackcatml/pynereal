var App = window.App || (window.App = {});

App.ui = {
  elements: {
    chartInfo: document.getElementById("chart-info"),
    chartInfoLine: document.getElementById("chart-info-line"),
    chartInfoTitleRow: document.getElementById("chart-info-title-row"),
    chartInfoTitle: document.getElementById("chart-info-title"),
    alertsToggle: document.getElementById("alerts-toggle"),
    alertsMenu: document.getElementById("alerts-menu")
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
  },
  formatNumber(value, decimals) {
    if (value == null || Number.isNaN(value)) return "-";
    return Number(value).toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    });
  },
  init() {
    const { alertsToggle, alertsMenu } = this.elements;
    alertsToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleAlertsMenu();
    });

    document.addEventListener("click", (e) => {
      if (!alertsMenu.contains(e.target) && e.target !== alertsToggle) {
        this.toggleAlertsMenu(false);
      }
    });
  }
};

App.ui.init();
