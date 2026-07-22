import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const staticOutput = decodeURIComponent(
  new URL("../../src/agent/web/static/benchmark-review-next", import.meta.url).pathname,
).replace(/^\/([A-Za-z]:)/, "$1");

export default defineConfig({
  plugins: [react()],
  base: "/benchmark-review/",
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
