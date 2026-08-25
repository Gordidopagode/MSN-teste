import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: path.resolve(projectRoot, "client"),
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(projectRoot, "client/src"),
      "@shared": path.resolve(projectRoot, "shared"),
    },
  },
  publicDir: false,
  build: {
    outDir: path.resolve(projectRoot, "client/public"),
    emptyOutDir: false,
  },
  server: {
    host: true,
  },
});
