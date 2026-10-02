import { SMA, EMA, RSI, BollingerBands, MACD, SMI, VWAP } from "trading-signals";

class DailyVWAP {
  day = null;
  candles = [];
  indicator = new VWAP();

  add(candle) { return this.update(candle, false); }
  replace(candle) { return this.update(candle, true); }
  get isStable() { return this.indicator.isStable; }
  getResult() { return this.indicator.getResult(); }

  update(candle, replace) {
    const day = Math.floor(candle.time / 86400);
    if (day !== this.day) {
      this.day = day;
      this.candles = [];
      this.indicator = new VWAP();
    }
    const previous = replace && this.candles.at(-1)?.time === candle.time ? this.candles.at(-1) : null;
    if (previous) this.candles[this.candles.length - 1] = candle;
    else this.candles.push(candle);
    if (candle.volume > 0) {
      if (previous?.volume > 0) this.indicator.replace(candle);
      else this.indicator.add(candle);
    } else if (previous?.volume > 0) {
      // The library ignores zero-volume replacements. Replay only this day's
      // positive-volume inputs when a correction removes the current candle.
      this.indicator = new VWAP();
      for (const bar of this.candles) if (bar.volume > 0) this.indicator.add(bar);
    }
    return this.getResult();
  }
}

const factories = {
  sma: config => new SMA(config.period),
  ema: config => new EMA(config.period),
  bb: config => new BollingerBands(config.period, config.multiplier),
  rsi: config => new RSI(config.period),
  macd: config => new MACD(new EMA(config.fastPeriod), new EMA(config.slowPeriod), new EMA(config.signalPeriod)),
  smi: config => new SMI({ interval: config.period, smooth1: config.smooth1, smooth2: config.smooth2 }),
  vwap: () => new DailyVWAP(),
};

const lineCount = id => id === "bb" || id === "macd" ? 3 : id === "smi" ? 2 : 1;
const stateFor = config => ({
  indicator: factories[config.id](config),
  signal: config.id === "smi" ? new EMA(config.signalPeriod) : null,
});

// One state per displayed timeframe. Unread history gaps start a new warm-up;
// they must never be treated as zero-price candles or joined across the gap.
export class IndicatorEngine {
  reset(configs, bars) {
    this.configs = configs.filter(config => factories[config.id]);
    this.lastTime = null;
    this.instances = this.configs.map(config => ({ ...stateFor(config), valid: false, beforeGap: null }));
    const series = this.emptySeries();
    for (const bar of bars) this.append(series, this.step(bar));
    return series;
  }

  emptySeries() {
    return this.configs.map(config => ({
      id: config.id,
      lines: Array.from({ length: lineCount(config.id) }, () => []),
    }));
  }

  step(bar) {
    const sameTime = bar.time === this.lastTime;
    const values = this.instances.map((state, index) => {
      const config = this.configs[index];
      const candleInput = config.id === "smi" || config.id === "vwap";
      const valid = Number.isFinite(bar.close) &&
        (!candleInput || (Number.isFinite(bar.high) && Number.isFinite(bar.low))) &&
        (config.id !== "vwap" || (Number.isFinite(bar.volume) && bar.volume >= 0));
      const replace = sameTime && state.valid;
      if (!valid) {
        if (!sameTime) state.beforeGap = { indicator: state.indicator, signal: state.signal };
        Object.assign(state, stateFor(config));
      } else if (sameTime && !state.valid && state.beforeGap) {
        // A provisional whitespace tail may become a real candle on the next tick.
        Object.assign(state, state.beforeGap);
      }
      const instance = state.indicator;
      if (valid) {
        const input = candleInput ? bar : bar.close;
        if (replace) instance.replace(input);
        else instance.add(input);
      }
      const result = valid && instance.isStable ? instance.getResult() : null;
      let numbers = [result];
      if (config.id === "bb") numbers = [result?.upper, result?.middle, result?.lower];
      else if (config.id === "macd") {
        numbers = [result?.macd, instance.signal.isStable ? result?.signal : null, instance.signal.isStable ? result?.histogram : null];
      } else if (config.id === "smi") {
        if (Number.isFinite(result)) {
          if (replace) state.signal.replace(result);
          else state.signal.add(result);
        }
        numbers = [result, valid && state.signal.isStable ? state.signal.getResult() : null];
      }
      state.valid = valid;
      if (valid) state.beforeGap = null;
      return numbers.map(value => Number.isFinite(value)
        ? { time: bar.time, value } : { time: bar.time });
    });
    this.lastTime = bar.time;
    return values;
  }

  append(series, values) {
    series.forEach((item, index) => item.lines.forEach((line, i) => line.push(values[index][i])));
  }

  update(bars) {
    const series = this.emptySeries();
    for (const bar of bars) {
      if (this.lastTime !== null && bar.time < this.lastTime) {
        throw new Error("Historical corrections require an indicator reset");
      }
      this.append(series, this.step(bar));
    }
    return series;
  }
}
