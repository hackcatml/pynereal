import { build } from "esbuild";

await build({
  entryPoints: ["frontend/codemirror.js"],
  bundle: true,
  format: "iife",
  target: ["es2020"],
  outfile: "data_service/templates/codemirror.js",
  minify: true,
  legalComments: "eof",
  banner: {
    js: "/*! CodeMirror 6 and Lezer packages are distributed under the MIT license. */",
  },
});
