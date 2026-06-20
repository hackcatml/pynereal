var App = window.App || (window.App = {});

App.ws.connect();
App.ws.startKeepalive();
App.data.loadChartInfo();
App.data.loadScriptSource();
App.data.loadWebhookConfig();
App.chart.startJankMonitor();
App.chart.attachResizeHandler();
App.chart.attachNavButtons();
App.chart.startPriceLineTimer();
