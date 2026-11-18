import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The studio runs this with --port $PORT and shows it at the project URL. Leave server.allowedHosts
// unset: Vite then answers only to localhost and addresses, so a page elsewhere cannot point its own
// name at this machine and read the app.
export default defineConfig({ plugins: [react()], server: { host: "127.0.0.1" }, clearScreen: false });
