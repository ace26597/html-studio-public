import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Studio 2 web UI. Dev server on 127.0.0.1:5200, `/api` proxied to the studio
// server on 8200. The proxy must stream server-sent events untouched.
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5200,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8200",
        changeOrigin: false,
        // Long-lived event streams: never time out, never buffer.
        timeout: 0,
        proxyTimeout: 0,
        configure(proxy) {
          proxy.on("proxyRes", (proxyRes, _req, res) => {
            const type = String(proxyRes.headers["content-type"] ?? "");
            if (type.includes("text/event-stream")) {
              res.setHeader("Cache-Control", "no-cache, no-transform");
              res.setHeader("X-Accel-Buffering", "no");
              res.flushHeaders();
            }
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
