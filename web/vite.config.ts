/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API = process.env.MDE_API ?? "http://localhost:8000";

// API path prefixes proxied to FastAPI in dev, so the browser sees one origin and no CORS is needed.
// "/app" is the SPA itself and is never proxied.
const API_PREFIXES = [
  "/auth/magic-link",
  "/auth/sso",
  "/auth/sign-out",
  "/me",
  "/orgs",
  "/invitations/accept",
  "/agreements",
  "/milestones",
  "/evidence",
  "/criteria",
  "/health",
  "/mock",
  "/paypal",
  "/pay/",
  "/webhooks",
  "/openapi.json",
  "/docs",
];

export default defineConfig({
  base: "/app/",
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
