var App = window.App || (window.App = {});

class BgColorPaneRenderer {
  constructor(source) {
    this.source = source;
  }

  draw() {}

  drawBackground(target) {
    const segments = this.source.visibleSegments();
    if (!segments.length) return;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const ratio = scope.horizontalPixelRatio;
      const maxWidth = scope.bitmapSize.width;
      const timeScale = this.source.chart.timeScale();
      const halfBarSpacing = Math.max(0, Number(timeScale.options().barSpacing) || 0) / 2;
      for (const segment of segments) {
        const leftCenter = timeScale.logicalToCoordinate(segment.start);
        const rightCenter = timeScale.logicalToCoordinate(segment.end);
        const left = leftCenter == null ? null : leftCenter - halfBarSpacing;
        const right = rightCenter == null ? null : rightCenter + halfBarSpacing;
        if (left == null || right == null) continue;

        const x1 = Math.max(0, Math.floor(Math.min(left, right) * ratio));
        const x2 = Math.min(maxWidth, Math.ceil(Math.max(left, right) * ratio));
        if (x2 <= x1) continue;
        ctx.fillStyle = segment.color;
        ctx.fillRect(x1, 0, x2 - x1, scope.bitmapSize.height);
      }
    });
  }
}

class BgColorPaneView {
  constructor(source) {
    this.rendererInstance = new BgColorPaneRenderer(source);
  }

  update() {}

  renderer() {
    return this.rendererInstance;
  }

  zOrder() {
    return "bottom";
  }
}

class BgColorPanePrimitive {
  constructor(chart, collections) {
    this.chart = chart;
    this.collections = collections;
    this.layers = new Map();
    this.segmentGroups = [];
    this.dirty = true;
    this.requestUpdate = null;
    this.views = [new BgColorPaneView(this)];
  }

  attached({ requestUpdate }) {
    this.requestUpdate = requestUpdate;
    this.invalidate();
  }

  detached() {
    this.requestUpdate = null;
  }

  updateAllViews() {
    this.views.forEach(view => view.update());
  }

  paneViews() {
    return this.views;
  }

  setLayer(plot, data) {
    const title = String(plot.title || "");
    if (!title) return;
    const points = new Map();
    for (const point of Array.isArray(data) ? data : []) {
      const time = Number(point && point.time);
      if (!Number.isFinite(time)) continue;
      points.set(time, this.normalizeColorValue(point.value));
    }
    this.layers.set(title, {
      title,
      offset: this.normalizeInteger(plot.offset, 0),
      showLast: plot.show_last == null ? null : Math.max(0, this.normalizeInteger(plot.show_last, 0)),
      order: this.normalizeInteger(plot.order, this.layers.size),
      points
    });
    this.invalidate();
  }

  updatePoint(title, time, value) {
    const layer = this.layers.get(String(title || ""));
    const pointTime = Number(time);
    if (!layer || !Number.isFinite(pointTime)) return;
    layer.points.set(pointTime, this.normalizeColorValue(value));
    this.invalidate();
  }

  onOhlcvChanged() {
    this.invalidate();
  }

  clear() {
    this.layers.clear();
    this.segmentGroups = [];
    this.invalidate();
  }

  invalidate() {
    this.dirty = true;
    if (this.requestUpdate) this.requestUpdate();
  }

  normalizeInteger(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.trunc(number) : fallback;
  }

  normalizeColorValue(value) {
    if (value == null || value === "") return null;
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0 || number > 0xFFFFFFFF) return null;
    return Math.trunc(number);
  }

  colorToCss(value) {
    const rgba = Number(value) >>> 0;
    const red = (rgba >>> 24) & 0xFF;
    const green = (rgba >>> 16) & 0xFF;
    const blue = (rgba >>> 8) & 0xFF;
    const alpha = rgba & 0xFF;
    return alpha === 0 ? null : `rgba(${red}, ${green}, ${blue}, ${alpha / 255})`;
  }

  rebuildSegments() {
    const indexByTime = this.collections.ohlcvIndexByTime;
    const layers = Array.from(this.layers.values()).sort((a, b) => (
      a.order - b.order || a.title.localeCompare(b.title)
    ));
    const segmentGroups = [];

    for (const layer of layers) {
      const layerSegments = [];
      const points = [];
      for (const [time, value] of layer.points.entries()) {
        const sourceIndex = indexByTime.get(Number(time));
        if (sourceIndex == null) continue;
        points.push({ sourceIndex, value });
      }
      points.sort((a, b) => a.sourceIndex - b.sourceIndex);
      const lastSourceIndex = points.length ? points[points.length - 1].sourceIndex : -1;
      const minimumSourceIndex = layer.showLast == null
        ? -Infinity
        : lastSourceIndex - layer.showLast + 1;
      let active = null;

      for (const point of points) {
        if (point.sourceIndex < minimumSourceIndex) continue;
        const color = this.colorToCss(point.value);
        if (!color) {
          active = null;
          continue;
        }
        const targetIndex = point.sourceIndex + layer.offset;
        if (active && active.color === color && targetIndex === active.end + 1) {
          active.end = targetIndex;
          continue;
        }
        active = { start: targetIndex, end: targetIndex, color };
        layerSegments.push(active);
      }
      segmentGroups.push(layerSegments);
    }

    this.segmentGroups = segmentGroups;
    this.dirty = false;
  }

  visibleSegments() {
    if (this.dirty) this.rebuildSegments();
    const range = this.chart.timeScale().getVisibleLogicalRange();
    if (!range) return [];
    const minimum = range.from - 1;
    const maximum = range.to + 1;
    const visible = [];
    for (const segments of this.segmentGroups) {
      let low = 0;
      let high = segments.length;
      while (low < high) {
        const middle = (low + high) >> 1;
        if (segments[middle].end < minimum) low = middle + 1;
        else high = middle;
      }
      for (let index = low; index < segments.length; index++) {
        const segment = segments[index];
        if (segment.start > maximum) break;
        visible.push(segment);
      }
    }
    return visible;
  }
}

App.BgColorPanePrimitive = BgColorPanePrimitive;
