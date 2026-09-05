import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 5174, pinned. The internal tool (Frontend/) owns 5173, and Vite's
    // default behaviour when a port is taken is to quietly pick the next
    // free one — which would mean whichever app started second lands on an
    // unpredictable port that the backend's CORS allow-list (Backend/main.py)
    // does not include, and every request from it fails in the browser.
    // strictPort turns that silent drift into an immediate, obvious error.
    port: 5174,
    strictPort: true,
    // Same reason as Frontend/vite.config.ts: bind all interfaces so the
    // site can be opened from a phone on the same network, not just from
    // this machine.
    host: true,
  },
});
