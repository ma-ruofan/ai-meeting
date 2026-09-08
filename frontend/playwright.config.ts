import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 45000,
  use: {
    baseURL: "http://127.0.0.1:8765",
    viewport: { width: 1440, height: 1000 },
    headless: true,
    launchOptions: {
      ...(process.env.CHROME_PATH
        ? { executablePath: process.env.CHROME_PATH }
        : {}),
    },
  },
  reporter: "list",
});
