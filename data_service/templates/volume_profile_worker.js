import { VolumeProfile, loadRange } from "./volume_profile_core.mjs?v=1";

let generation = 0, controller, profile, request, socket, reconnect, heartbeat, renderTimer;
let pending = new Map();

function stop() {
  controller?.abort();
  controller = null;
  clearTimeout(reconnect);
  clearTimeout(heartbeat);
  clearTimeout(renderTimer);
  if (socket) {
    socket.onclose = socket.onerror = socket.onmessage = null;
    socket.close();
    socket = null;
  }
  profile = null;
  pending.clear();
}

function emit() {
  clearTimeout(renderTimer);
  renderTimer = null;
  if (profile) postMessage({ type: "result", id: request.id, ...profile.result() });
}

function update(bars) {
  const selected = bars.filter(bar => bar.time >= request.from && bar.time < request.to);
  if (!profile) {
    for (const bar of selected) pending.set(bar.time, bar);
  } else if (profile.update(selected) && !renderTimer) renderTimer = setTimeout(emit, 250);
}

function connect(id) {
  if (id !== generation || !request.socketUrl || Date.now() / 1000 > request.to + 720) return;
  socket = new WebSocket(request.socketUrl);
  const connection = socket;
  const retry = () => {
    if (socket !== connection || id !== generation) return;
    socket = null;
    connection.onclose = connection.onerror = null;
    connection.close();
    clearTimeout(heartbeat);
    reconnect = setTimeout(() => connect(id), 2000);
  };
  const alive = () => {
    clearTimeout(heartbeat);
    heartbeat = setTimeout(retry, 15000);
  };
  alive();
  socket.onerror = socket.onclose = retry;
  socket.onmessage = event => {
    if (id !== generation) return;
    alive();
    if (Date.now() / 1000 > request.to + 720) {
      connection.onclose = connection.onerror = null;
      connection.close();
      socket = null;
      clearTimeout(heartbeat);
      return;
    }
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "minute_window") update(payload.bars);
    } catch { /* A reconnect will deliver a fresh minute window. */ }
  };
}

self.onmessage = async ({ data }) => {
  if (data.type === "bars") { if (request?.id === data.id) update(data.bars); return; }
  if (data.type === "settings") {
    if (request && data.id === request.id) {
      Object.assign(request, { rows: data.rows, valueArea: data.valueArea });
      if (profile) { profile.configure(data.rows, data.valueArea); emit(); }
    }
    return;
  }
  const id = ++generation;
  stop();
  if (data.type !== "load") return;
  request = data;
  controller = new AbortController();
  const loadingController = controller;
  const signal = loadingController.signal;
  const timeout = setTimeout(() => loadingController.abort(), 120000);
  connect(id);
  try {
    const bars = await loadRange(data.url, data.from, data.to, signal, candles => {
      if (id === generation) postMessage({ type: "loading", id: data.id, candles });
    });
    if (id !== generation) return;
    profile = new VolumeProfile(bars, request.rows, request.valueArea, request.tick);
    profile.update([...pending.values()]);
    pending.clear();
    emit();
  } catch (error) {
    if (id === generation) {
      stop();
      postMessage({ type: "error", id: data.id,
        message: signal.aborted ? "1m data request timed out. Retry to load this range." : String(error.message) });
    }
  } finally { clearTimeout(timeout); }
};
