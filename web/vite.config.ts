import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const settingsProxy = {
  target: "http://127.0.0.1:8095",
  rewrite: (path: string) => path.replace(/^\/edge/, ""),
};
const edgeProxy = {
  "/push": "http://127.0.0.1:8095",
  "/edge/cloud-review": settingsProxy,
  "/edge/video-retention": settingsProxy,
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: edgeProxy,
  },
  preview: { proxy: edgeProxy },
});

