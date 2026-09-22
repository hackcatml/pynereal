import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

const python = process.env.PYTHON || (existsSync("venv/bin/python") ? "venv/bin/python" : "python3");
const api = execFileSync(python, ["data_service/codemirror_build/generate_pyne_api.py"], {
  encoding: "utf8",
});

await build({
  entryPoints: ["data_service/codemirror_build/codemirror.js"],
  bundle: true,
  format: "iife",
  target: ["es2020"],
  outfile: "data_service/templates/codemirror.js",
  minify: true,
  legalComments: "eof",
  plugins: [{
    name: "pynecore-api",
    setup(build) {
      build.onResolve({ filter: /^pynecore-api$/ }, () => ({ path: "pynecore-api", namespace: "pynecore-api" }));
      build.onLoad({ filter: /.*/, namespace: "pynecore-api" }, () => ({ contents: `export default ${api}`, loader: "js" }));
    },
  }],
  banner: {
    js: "/*! CodeMirror 6 and Lezer packages are distributed under the MIT license. */",
  },
});
