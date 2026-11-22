import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const edgeProxy = {
  "/push": "http://127.0.0.1:8095",
  "/edge/cloud-review": {
    target: "http://127.0.0.1:8095",
    rewrite: (path: string) => path.replace(/^\/edge/, ""),
  },
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

