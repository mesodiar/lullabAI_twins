import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxy = {
  "/push": "http://127.0.0.1:8095",
  "/edge/": {
    target: "http://127.0.0.1:8095",
    rewrite: (path: string) => path.replace(/^\/edge/, ""),
  },
  // Expose the player endpoints, not the go2rtc admin API.
  "^/go2rtc/api/ws(?:\\?|$)": {
    target: "http://127.0.0.1:1984",
    ws: true,
    rewrite: (path: string) => path.replace(/^\/go2rtc/, ""),
    configure: (server) => {
      server.on("proxyReqWs", (request, incoming, socket) => {
        const { host, origin } = incoming.headers;
        if (origin && origin !== `https://${host}` && origin !== `http://${host}`) {
          socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
          request.destroy();
        }
      });
    },
  },
  "^/go2rtc/api/stream\\.mjpeg(?:\\?|$)": {
    target: "http://127.0.0.1:1984",
    rewrite: (path: string) => path.replace(/^\/go2rtc/, ""),
  },
  "/mock/": {
    target: "http://127.0.0.1:8090",
    rewrite: (path: string) => path.replace(/^\/mock/, ""),
  },
} satisfies NonNullable<import("vite").ServerOptions["proxy"]>;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy,
  },
  preview: { proxy, allowedHosts: ["lullabai.phawin.net"] },
});

