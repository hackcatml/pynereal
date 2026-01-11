window.App = window.App || {};

window.App.state = {
  ws: null,
  runnerConnected: false,
  initialLoadInProgress: false,
  initialLoadDone: false,
  firstBarTime: null,
  timeframeInterval: 60,
  lastBarTime: 0,
  lastOpenPrice: { time: 0, value: 0 },
  lastPrice: 0,
  lastOhlcv: null,
  scriptTitle: "No title",
  scriptTitleVisible: false,
  baseInfoTop: "Loading...",
  baseInfoText: "Loading...",
  jankFrames: [],
  jankReloaded: false,
  lastFrameTs: null
};

window.App.collections = {
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
