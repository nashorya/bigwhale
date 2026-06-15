import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// NoneBot2 默认监听 127.0.0.1:8080
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
});
