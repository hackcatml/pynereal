var App = window.App || (window.App = {});

App.ws.connect();
App.ws.startKeepalive();
App.data.loadChartInfo();
App.data.loadScriptSource();
App.data.loadWebhookConfig();
App.ui.loadManualAlertTemplates({ migrateLocal: true });
App.measure.init();
App.chart.startJankMonitor();
App.chart.attachResizeHandler();
App.chart.attachNavButtons();
App.chart.startPriceLineTimer();
