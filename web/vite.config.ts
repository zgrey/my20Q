import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// The cockpit calls the FastAPI backend at /api/*. In dev, Vite proxies
// that to the backend (start it with `python -m my20q.api`), so the
// browser sees one origin and no CORS is involved.
export default defineConfig({
  plugins: [preact()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/assets": "http://localhost:8000",
    },
  },
});
