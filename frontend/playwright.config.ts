import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e", fullyParallel: false, workers: 1, timeout: 120_000,
  expect: { timeout: 30_000 }, reporter: "list",
  use: { baseURL: "http://127.0.0.1:3000", browserName: "chromium", channel: "msedge", headless: true,
    viewport: { width: 1440, height: 1000 }, trace: "off", screenshot: "only-on-failure" },
  webServer: { command: "npm run start", url: "http://127.0.0.1:3000", reuseExistingServer: true, timeout: 60_000 },
});
