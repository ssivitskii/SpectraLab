import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.SPECTRALAB_API_TARGET ?? "http://127.0.0.1:8000",
      "/docs": process.env.SPECTRALAB_API_TARGET ?? "http://127.0.0.1:8000",
      "/openapi.json":
        process.env.SPECTRALAB_API_TARGET ?? "http://127.0.0.1:8000",
    },
  },
  test: {
    include: ["src/test/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
