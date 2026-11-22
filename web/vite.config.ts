import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const pushProxy = { "/push": "http://127.0.0.1:8095" };

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: pushProxy,
  },
  preview: { proxy: pushProxy },
});

