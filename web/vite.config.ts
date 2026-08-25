import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../dist", emptyOutDir: true },
  server: {
    // The API and the SPA are served from one origin in production; proxy in dev
    // so the session header behaves identically in both.
    proxy: {
      "/api": "http://localhost:5000",
      "/dicomweb": "http://localhost:5000",
    },
  },
});
