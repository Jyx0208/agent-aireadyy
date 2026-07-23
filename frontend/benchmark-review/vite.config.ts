import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import packageMetadata from "./package.json";

const nodeEnv = (
  globalThis as typeof globalThis & {
    process?: { env?: Record<string, string | undefined> };
  }
).process?.env || {};
const buildTime = nodeEnv.VITE_BUILD_TIME?.trim() || new Date().toISOString();
const buildRevision =
  nodeEnv.VITE_BUILD_REVISION?.trim() ||
  nodeEnv.GITHUB_SHA?.trim().slice(0, 7) ||
  "local";

const staticOutput = decodeURIComponent(
  new URL("../../src/agent/web/static/benchmark-review-next", import.meta.url).pathname,
).replace(/^\/([A-Za-z]:)/, "$1");

export default defineConfig({
  plugins: [react()],
  base: "/benchmark-review/",
  define: {
    __APP_BUILD_INFO__: JSON.stringify({
      version: packageMetadata.version,
      revision: buildRevision,
      builtAt: buildTime,
    }),
  },
  build: {
    // Vite 8/Rolldown on Windows requires an absolute path when the output
    // directory lives outside the frontend root.
    outDir: staticOutput,
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    proxy: {
      "/api": "http://127.0.0.1:8001",
    },
  },
});
