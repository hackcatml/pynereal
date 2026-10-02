import { IndicatorEngine } from "./engine.mjs";

const engine = new IndicatorEngine();
self.onmessage = ({ data }) => {
  const { revision, kind, configs, bars } = data;
  try {
    const series = kind === "reset" ? engine.reset(configs, bars) : engine.update(bars);
    self.postMessage({ revision, kind, series });
  } catch (error) {
    self.postMessage({ revision, error: String(error.message || error) });
  }
};
