/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API = process.env.MDE_API ?? "http://localhost:8000";

// Proxied to the backend in dev, so the browser sees one origin and no CORS is needed: the REST API under
// /api, and the MCP server with its OAuth endpoints at the root. Everything else is the SPA.
const API_PREFIXES = ["/api", "/mcp", "/.well-known", "/authorize", "/token", "/register", "/revoke"];

export default defineConfig({
  base: "/",
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(API_PREFIXES.map((p) => [p, { target: API, changeOrigin: false }])),
  },
  build: { outDir: "dist", sourcemap: true, chunkSizeWarningLimit: 2500 },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: { modules: { classNameStrategy: "non-scoped" } },
  },
});
