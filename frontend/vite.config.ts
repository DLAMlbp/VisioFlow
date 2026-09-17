import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5174,
      strictPort: true,
      proxy: {
        "/api": {
          target: env.LOCAL_API_PROXY_TARGET ?? "http://127.0.0.1:18000",
          changeOrigin: true,
          headers: { "X-VisioFlow-Web-Access": "1" }
        }
      }
    },
    test: {
      environment: "jsdom"
    }
  };
});
