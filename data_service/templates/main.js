var App = window.App || (window.App = {});

App.ws.connect();
App.ws.startKeepalive();
App.data.loadChartInfo();
App.chart.startJankMonitor();
App.chart.attachResizeHandler();
App.chart.startPriceLineTimer();
