// Candle-based profile: distribute each minute's volume by price overlap.
// This is not tick-level buy/sell volume; candle direction defines up/down.
export function validBar(bar) {
  return bar && ["time", "open", "high", "low", "close", "volume"].every(key =>
    typeof bar[key] === "number" && Number.isFinite(bar[key])) &&
    bar.volume >= 0 && bar.low <= Math.min(bar.open, bar.close) &&
    bar.high >= Math.max(bar.open, bar.close);
}

export function accepts(old, bar) {
  if (!validBar(bar)) return false;
  if (!old) return true;
  const authoritative = value => value.source && !["live", "trades"].includes(value.source);
  if (authoritative(old) && !authoritative(bar)) return false;
  if (!authoritative(old) && authoritative(bar)) return true;
  if (bar.source === "live" || old.source === "live") {
    if (bar.volume !== old.volume) return bar.volume > old.volume;
  }
  return !(old.updated_at > bar.updated_at);
}

export class VolumeProfile {
  constructor(bars = [], rows = 48, valueArea = 70, tick = null) {
    this.bars = new Map();
    for (const bar of bars) if (accepts(this.bars.get(bar.time), bar)) this.bars.set(bar.time, bar);
    this.configure(rows, valueArea, tick);
  }

  configure(rows, valueArea, tick = this.tick) {
    this.rows = Math.max(1, Math.min(200, Math.round(Number(rows) || 48)));
    this.valueArea = Math.max(1, Math.min(100, Number(valueArea) || 70));
    this.tick = Number.isFinite(tick) && tick > 0 ? tick : null;
    this.rebuild();
  }

  rebuild() {
    this.min = Infinity;
    this.max = -Infinity;
    this.first = Infinity;
    this.last = -Infinity;
    for (const bar of this.bars.values()) {
      this.first = Math.min(this.first, bar.time);
      this.last = Math.max(this.last, bar.time);
      if (bar.volume === 0) continue;
      this.min = Math.min(this.min, bar.low);
      this.max = Math.max(this.max, bar.high);
    }
    this.up = new Float64Array(this.rows);
    this.down = new Float64Array(this.rows);
    if (!Number.isFinite(this.min)) return;
    // The chart's display precision is not necessarily the exchange tick size.
    // Without a known tick, divide the range directly, including sub-cent assets.
    this.base = this.tick ? Math.floor(this.min / this.tick) * this.tick : this.min;
    this.step = this.tick ? Math.max(this.tick, Math.ceil((this.max - this.base) / this.rows / this.tick - 1e-10) * this.tick) :
      (this.max - this.min) / this.rows || Math.max(Math.abs(this.min) * 0.0001, 1e-12);
    this.count = Math.max(1, Math.min(this.rows, Math.ceil((this.max - this.base) / this.step - 1e-10)));
    for (const bar of this.bars.values()) this.distribute(bar, 1);
  }

  index(price) {
    return Math.max(0, Math.min(this.count - 1, Math.floor((price - this.base) / this.step)));
  }

  distribute(bar, sign) {
    if (!bar.volume || !Number.isFinite(this.min)) return;
    const values = bar.close >= bar.open ? this.up : this.down;
    const first = this.index(bar.low), last = this.index(bar.high);
    if (bar.high === bar.low || first === last) {
      values[first] += sign * bar.volume;
      return;
    }
    let remaining = bar.volume;
    for (let index = first; index <= last; index++) {
      const bottom = this.base + index * this.step;
      const overlap = Math.max(0, Math.min(bar.high, bottom + this.step) - Math.max(bar.low, bottom));
      const amount = index === last ? remaining : Math.min(remaining, bar.volume * overlap / (bar.high - bar.low));
      values[index] += sign * amount;
      remaining -= amount;
    }
  }

  update(bars) {
    let changed = false, rebuild = false;
    const changes = [];
    for (const bar of bars) {
      const old = this.bars.get(bar.time);
      if (!accepts(old, bar)) continue;
      this.bars.set(bar.time, bar);
      this.first = Math.min(this.first, bar.time);
      this.last = Math.max(this.last, bar.time);
      if (old && ["open", "high", "low", "close", "volume"].every(key => old[key] === bar[key])) continue;
      changed = true;
      if (!Number.isFinite(this.min) || bar.low < this.min || bar.high > this.max ||
          (old && (old.low === this.min || old.high === this.max) &&
            (old.low !== bar.low || old.high !== bar.high || !bar.volume))) rebuild = true;
      changes.push([old, bar]);
    }
    if (rebuild) this.rebuild();
    else for (const [old, bar] of changes) {
      if (old) this.distribute(old, -1);
      this.distribute(bar, 1);
    }
    return changed;
  }

  result() {
    if (!Number.isFinite(this.min)) return { bins: [], total: 0, candles: this.bars.size };
    const bins = Array.from({ length: this.count }, (_, index) => ({
      low: this.base + index * this.step, high: this.base + (index + 1) * this.step,
      up: Math.max(0, this.up[index]), down: Math.max(0, this.down[index]),
    }));
    const volumes = bins.map(bin => bin.up + bin.down);
    const total = volumes.reduce((sum, volume) => sum + volume, 0);
    const maximum = Math.max(...volumes);
    let poc = volumes.indexOf(maximum), lower = poc, upper = poc, included = volumes[poc];
    // Grow a contiguous value area from POC, preferring the larger neighbor.
    while (included < total * this.valueArea / 100 && (lower > 0 || upper < bins.length - 1)) {
      const below = lower > 0 ? volumes[lower - 1] : -1;
      const above = upper < bins.length - 1 ? volumes[upper + 1] : -1;
      if (above > below || (above === below && upper - poc <= poc - lower)) included += volumes[++upper];
      else included += volumes[--lower];
    }
    return { bins, total, maximum, candles: this.bars.size, first: this.first, last: this.last, lower, upper,
      poc: (bins[poc].low + bins[poc].high) / 2, vah: bins[upper].high, val: bins[lower].low };
  }
}

// Kept independent from chart pagination: never change its viewport or cache.
export async function loadRange(url, from, to, signal, onProgress = () => {}, fetcher = fetch) {
  let before = to;
  const bars = new Map();
  while (before > from) {
    const pageUrl = new URL(url);
    pageUrl.searchParams.set("limit", "5000");
    pageUrl.searchParams.set("before", String(before));
    const response = await fetcher(pageUrl, { signal, cache: "no-store" });
    if (!response.ok) throw new Error(`1m data: HTTP ${response.status}`);
    const page = await response.json();
    if (page.interval !== 60) throw new Error("1m data is not available for this session.");
    if (!Array.isArray(page.bars)) throw new Error("Invalid 1m data response.");
    if (!page.bars.length) break;
    let first = before;
    for (const bar of page.bars) {
      if (!validBar(bar)) throw new Error("Invalid 1m candle.");
      first = Math.min(first, bar.time);
      if (bar.time >= from && bar.time < to && accepts(bars.get(bar.time), bar)) bars.set(bar.time, bar);
    }
    onProgress(bars.size);
    if (first <= from || !page.has_before) break;
    if (first >= before) throw new Error("1m pagination did not advance.");
    before = first;
  }
  return [...bars.values()];
}
