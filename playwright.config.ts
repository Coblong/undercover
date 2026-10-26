import { randomUUID } from "node:crypto";
import { defineConfig } from "@playwright/test";
import { databasePath } from "./e2e/database";

// The tests run on their own backend and a new database, apart from `npm run dev`.
process.env.E2E_RUN ??= randomUUID();

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  workers: 1,
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command: "uv run -m uvicorn backend.main:app --port 8001",
      env: {
        UNDERCOVER_DB: databasePath(),
        BACKEND_URL: "http://127.0.0.1:8001",
      },
      url: "http://127.0.0.1:8001/api/health",
      reuseExistingServer: false,
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      env: { BACKEND_URL: "http://127.0.0.1:8001" },
      url: "http://localhost:5174",
      reuseExistingServer: false,
    },
  ],
});
