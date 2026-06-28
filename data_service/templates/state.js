window.App = window.App || {};

// Per-session runtime config injected by the hub at /s/{id} (see ui.py chart_page).
// The chart page is always served with these injected, so the fallbacks below are
// only a safety default. NOTE: the hub serves data under /api/{id}/... and /ws/{id}
// (plus a /ws default-session alias); it does NOT serve un-namespaced /api/* data
// routes, so the apiBase "/api" fallback is inert unless RUNTIME_ID is injected.
window.App.config = {
  runtimeId: window.RUNTIME_ID || null,
  apiBase: window.API_BASE || "/api",
  wsPath: window.WS_PATH || "/ws",
  storageKey(name) {
    return this.runtimeId ? `${name}:${this.runtimeId}` : name;
  }
};

window.App.state = {
  ws: null,
  runnerConnected: false,
  initialLoadInProgress: false,
  initialLoadDone: false,
  firstBarTime: null,
  timeframeInterval: 60,
  // Timeframe from /api/info (seconds). Source of truth for the countdown:
  // bar spacing cannot be trusted because some exchange charts hide zero-volume bars.
  configuredTimeframeSec: null,
  lastBarTime: 0,
  lastOpenPrice: { time: 0, value: 0 },
  lastPrice: 0,
  lastOhlcv: null,
  exchange: "",
  symbol: "",
  timeframe: "",
  scriptTitle: "No title",
  scriptTitleVisible: false,
  scriptSourceName: "",
  scriptSource: "",
  scriptSourceLoaded: false,
  sourceDirty: false,
  sourceSaving: false,
  sourceSaveStatus: "",
  sourcePanelOpen: false,
  baseInfoTop: "Loading...",
  baseInfoText: "Loading...",
  jankFrames: [],
  jankReloaded: false,
  lastFrameTs: null,
  webhookEnabled: false,
  telegramEnabled: false,
  webhookUrl: "",
  manualAlertTemplates: [],
  manualAlertContext: null,
  manualAlertMenuOpen: false,
  manualAlertTemplateOpen: false,
  manualAlertConfirmOpen: false,
  manualAlertSelectedTemplateIndex: -1,
  manualAlertSuppressClickUntil: 0,
  measureToolActive: false,
  measureDraft: null,
  measureResult: null
};

window.App.collections = {
  ohlcvData: [],
  ohlcvIndexByTime: new Map(),
  ohlcvVolumePrefix: [],
  plotSeriesMap: new Map(),
  plotSeriesList: [],
  markers: [],
  markerKeys: new Set(),
  seriesMarkers: null,
  plotcharMarkers: [],
  plotcharMarkerKeys: new Set(),
  plotcharSeriesMarkers: null,
  entryMarkerData: [],
  closeMarkerData: [],
  entryPriceKeys: new Set(),
  closePriceKeys: new Set()
};

window.App.util = {
  sleep: (ms) => new Promise(resolve => setTimeout(resolve, ms))
};
