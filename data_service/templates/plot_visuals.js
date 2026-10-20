var App = window.App || (window.App = {});

// Visual columns share the plot timeline but never participate in price scaling.
App.PlotVisuals = class {
  static isVisual(kind) { return ['plot_color', 'barcolor', 'fill'].includes(kind); }

  static color(value) {
    if (value == null || value === '') return null;
    const number = Number(value);
    if (!Number.isInteger(number) || number < 0 || number > 0xFFFFFFFF) return null;
    return `#${number.toString(16).padStart(8, '0')}`;
  }

  constructor(owner, collections) {
    this.owner = owner;
    this.collections = collections;
    this.layers = new Map();
    this.lines = new Map();
    this.offsets = new Map();
    this.colors = new Map();
    this.bars = new Map();
    this.fills = [];
    this.historyGaps = new Set();
    this.dirty = false;
    this.views = [{ renderer: () => this, zOrder: () => 'bottom' }];
    owner.chart.panes()[0].attachPrimitive(this);
  }

  attached({ requestUpdate }) { this.requestUpdate = requestUpdate; }
  detached() { this.requestUpdate = null; }
  paneViews() { return this.views; }
  updateAllViews() {}
  draw() {}

  clear() {
    this.layers.clear();
    this.lines.clear();
    this.offsets.clear();
    this.timeline = [];
    this.timelineIndex = new Map();
    this.colors.clear();
    this.bars.clear();
    this.fills = [];
    this.historyGaps.clear();
    this.requestUpdate?.();
  }

  load(plots) {
    this.clear();
    for (const plot of plots) {
      for (const time of plot.historyGaps || []) this.historyGaps.add(time);
      const points = new Map((plot.data || []).map(p => [Number(p.time), p.value]));
      if (App.PlotVisuals.isVisual(plot.kind)) {
        const layer = { ...plot, points };
        this.layers.set(plot.title, layer);
        if (plot.kind === 'plot_color') this.colors.set(plot.target, layer);
      } else if (!plot.kind || plot.kind === 'line') {
        this.lines.set(plot.title, points);
        const offset = Math.trunc(Number(plot.offset) || 0);
        if (offset) this.offsets.set(plot.title, offset);
      }
    }
    if (this.offsets.size) this.rebuildTimeline();
    this.rebuildBarColors();
    this.dirty = true;
    this.requestUpdate?.();
  }

  rebuildTimeline() {
    this.timeline = [];
    this.timelineIndex = new Map();
    let segment = 0;
    for (const bar of this.collections.ohlcvData || []) {
      // Unread history is a barrier, not a candle to count in plot offsets.
      if (bar.close == null) { segment++; continue; }
      this.timelineIndex.set(Number(bar.time), this.timeline.length);
      this.timeline.push({ time: Number(bar.time), segment });
    }
    const data = this.timeline;
    this.interval = Number(App.state?.configuredTimeframeSec || App.state?.timeframeInterval)
      || (data.length > 1 ? data[1].time - data[0].time : 60);
  }

  onBarAdded(bar) {
    if (!this.offsets.size) return false;
    const last = this.timeline.at(-1);
    if (!last || Number(bar.time) <= last.time) return false;
    this.timelineIndex.set(Number(bar.time), this.timeline.length);
    this.timeline.push({ time: Number(bar.time), segment: last.segment });
    this.dirty = true;
    this.requestUpdate?.();
    // A real candle after a trading gap replaces projected future timestamps.
    return Number(bar.time) !== last.time + this.interval;
  }

  shiftedTime(time, offset) {
    if (!offset) return Number(time);
    const index = this.timelineIndex.get(Number(time));
    if (index == null) return null;
    const source = this.timeline[index], targetIndex = index + offset;
    const target = this.timeline[targetIndex];
    if (target) return target.segment === source.segment ? target.time : null;
    const last = this.timeline.at(-1);
    if (targetIndex >= this.timeline.length && source.segment === last.segment) {
      return last.time + (targetIndex - this.timeline.length + 1) * this.interval;
    }
    return null;
  }

  projectPoint(title, point) {
    const time = this.shiftedTime(point.time, this.offsets.get(title));
    return time == null ? null : time === point.time ? point : { ...point, time };
  }

  rebuildBarColors() {
    this.bars.clear();
    const data = this.collections.ohlcvData || [];
    const index = this.collections.ohlcvIndexByTime;
    for (const layer of [...this.layers.values()].sort((a, b) => (a.order || 0) - (b.order || 0))) {
      if (layer.kind !== 'barcolor') continue;
      const points = [...layer.points].sort((a, b) => a[0] - b[0]);
      const lastIndex = index?.get(points.at(-1)?.[0]);
      for (const [time, value] of points) {
        const sourceIndex = index?.get(time);
        if (layer.show_last != null && sourceIndex < lastIndex - layer.show_last + 1) continue;
        const target = layer.offset ? data[sourceIndex + layer.offset]?.time : time;
        const color = App.PlotVisuals.color(value);
        if (target != null && color) this.bars.set(target, color);
      }
    }
  }

  colorBar(bar) {
    const color = this.bars.get(Number(bar.time));
    return color && bar.close != null ? { ...bar, color, wickColor: color, borderColor: color } : bar;
  }

  colorPoint(title, point) {
    const layer = this.colors.get(title);
    if (!layer || point.value == null) return point;
    // A missing color hides this segment without removing the numerical value.
    return { ...point, color: App.PlotVisuals.color(layer.points.get(point.time)) || '#00000000' };
  }

  update(title, time, value) {
    const layer = this.layers.get(title);
    if (layer) {
      layer.points.set(time, value);
      if (layer.kind === 'barcolor') {
        if (!layer.offset && layer.show_last == null) {
          this.bars.delete(time);
          for (const item of this.layers.values()) {
            if (item.kind !== 'barcolor') continue;
            const color = App.PlotVisuals.color(item.points.get(time));
            if (color) this.bars.set(time, color);
          }
        } else this.rebuildBarColors();
        const sourceIndex = this.collections.ohlcvIndexByTime.get(time);
        const bar = this.collections.ohlcvData[sourceIndex + (layer.offset || 0)];
        if (bar?.close != null) this.owner.candleSeries.update(this.colorBar(bar), true);
      }
    } else this.lines.get(title)?.set(time, value);
    this.dirty = true;
    this.requestUpdate?.();
    return layer;
  }

  rebuildFills() {
    this.fills = [];
    for (const layer of this.layers.values()) {
      if (layer.kind !== 'fill') continue;
      const upper = this.lines.get(layer.plot1), lower = this.lines.get(layer.plot2);
      if (!upper || !lower) continue;
      const upperOffset = this.offsets.get(layer.plot1) || 0;
      const lowerOffset = this.offsets.get(layer.plot2) || 0;
      const offset = upperOffset === lowerOffset ? upperOffset : 0;
      const project = (line, displacement) => !displacement ? line : new Map(
        [...line].map(([time, value]) => [this.shiftedTime(time, displacement), value])
          .filter(([time]) => time != null),
      );
      const shownUpper = project(upper, upperOffset), shownLower = project(lower, lowerOffset);
      const source = new Map(layer.points);
      for (const time of this.historyGaps) source.set(time, null);
      let points = [...source].sort((a, b) => a[0] - b[0]).map(([time, color]) => {
        const shownTime = this.historyGaps.has(time) ? time : this.shiftedTime(time, offset);
        return { time: shownTime, upper: shownUpper.get(shownTime), lower: shownLower.get(shownTime),
          color: App.PlotVisuals.color(color) };
      }).filter(point => point.time != null).sort((a, b) => a.time - b.time);
      if (layer.show_last != null) points = points.slice(Math.max(0, points.length - layer.show_last));
      if (layer.fillgaps) points = points.filter(p => this.historyGaps.has(p.time) || (p.upper != null && p.lower != null));
      this.fills.push(points);
    }
    this.dirty = false;
  }

  drawBackground(target) {
    if (this.dirty) this.rebuildFills();
    const scale = this.owner.chart.timeScale(), range = scale.getVisibleRange();
    if (!range) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: rx, verticalPixelRatio: ry }) => {
      ctx.save();
      for (const points of this.fills) {
        let low = 0, high = points.length;
        while (low < high) {
          const mid = (low + high) >>> 1;
          if (points[mid].time < range.from) low = mid + 1;
          else high = mid;
        }
        for (let i = Math.max(1, low); i < points.length; i++) {
          const a = points[i - 1], b = points[i];
          if (a.time > range.to) break;
          if (!b.color || a.upper == null || a.lower == null || b.upper == null || b.lower == null) continue;
          const x1 = scale.timeToCoordinate(a.time), x2 = scale.timeToCoordinate(b.time);
          const y = [a.upper, b.upper, b.lower, a.lower].map(v => this.owner.candleSeries.priceToCoordinate(v));
          if (x1 == null || x2 == null || y.some(v => v == null)) continue;
          ctx.fillStyle = b.color;
          ctx.beginPath();
          ctx.moveTo(x1 * rx, y[0] * ry);
          ctx.lineTo(x2 * rx, y[1] * ry);
          ctx.lineTo(x2 * rx, y[2] * ry);
          ctx.lineTo(x1 * rx, y[3] * ry);
          ctx.closePath();
          ctx.fill();
        }
      }
      ctx.restore();
    });
  }
};
