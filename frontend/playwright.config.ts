import { defineConfig } from "@playwright/test";
const baseURL=process.env.E2E_BASE_URL ?? 'http://127.0.0.1:3000';

export default defineConfig({
  testDir: "./e2e", fullyParallel: false, workers: 1, timeout: 120_000,
  expect: { timeout: 30_000 }, reporter: "list",
  use: { baseURL, browserName: "chromium", channel: "msedge", headless: true,
    actionTimeout:30_000,navigationTimeout:45_000,
    viewport: { width: 1440, height: 1000 }, trace: "off", screenshot: "only-on-failure" },
  webServer: { command: `npm run start -- --port ${new URL(baseURL).port || '3000'}`, url: baseURL, reuseExistingServer: true, timeout: 60_000 },
});
