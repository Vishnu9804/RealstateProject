import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // Pinned so this app never drifts onto a port already owned by Frontend/
    // (5173) or LandingPage/ (5174) — a silently-picked next-free port would
    // land outside the backend's CORS allow-list (Backend/main.py) and every
    // request would fail in the browser.
    port: 5175,
    strictPort: true,
    // Bind all interfaces, matching Frontend/ and LandingPage/.
    host: true,
  },
});
