import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// The cockpit calls the FastAPI backend at /api/*. In dev, Vite proxies
// that to the backend (start it with `python -m my20q.api`), so the
// browser sees one origin and no CORS is involved.
//
// SSE notes: the explicit object form (target + changeOrigin) ensures
// http-proxy forwards the EventSource connection cleanly to the backend.
// The backend itself sets `Cache-Control: no-cache` and `X-Accel-Buffering:
// no` on the stream so nothing buffers it in transit.
export default defineConfig({
  plugins: [preact()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/assets": "http://localhost:8000",
    },
  },
});
