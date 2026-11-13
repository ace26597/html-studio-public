import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The studio runs this with --port $PORT and shows it at the project URL.
export default defineConfig({ plugins: [react()], server: { host: "127.0.0.1", allowedHosts: true }, clearScreen: false });
