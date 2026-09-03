import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Bind to all interfaces, not just localhost: the WhatsApp inquiry form
  // link (Backend/Config/settings.py's inquiry_form_base_url) is built with
  // this machine's LAN IP so a phone can open it — that link is dead on
  // arrival if this dev server only listens on localhost.
  server: {
    host: true,
  },
});
