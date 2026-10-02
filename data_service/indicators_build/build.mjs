import { build } from "esbuild";
import { readFileSync } from "node:fs";

const license = readFileSync("node_modules/trading-signals/LICENSE", "utf8");
await build({
  entryPoints: ["data_service/indicators_build/worker.mjs"],
  outfile: "data_service/templates/chart_indicators_worker.js",
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  minify: true,
  legalComments: "eof",
  banner: { js: `/*! trading-signals 8.3.0\n${license}\n*/` },
});
