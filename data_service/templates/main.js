var App = window.App || (window.App = {});

App.history.attach();
App.timeframes.init();
App.sessionPicker.init();
App.indicators.init();
App.ws.connect();
App.ws.startKeepalive();
const chartInfoReady = App.data.loadChartInfo();
const scriptSourceReady = App.data.loadScriptSource();
App.data.loadWebhookConfig();
App.ui.loadManualAlertTemplates({ migrateLocal: true });
const manualAlertsReady = App.ui.loadManualAlertTrigger();
if (App.chart.captureMode) {
  App.chart.captureInputsReady = Promise.all([
    chartInfoReady, scriptSourceReady, manualAlertsReady, App.indicators.settingsLoaded,
  ]);
}
App.measure.init();
App.trendline.init();
App.chart.startJankMonitor();
App.chart.attachResizeHandler();
App.chart.attachNavButtons();
App.chart.startPriceLineTimer();
