import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  expect: {timeout: 20_000},
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", {open: "never"}], ["json", {outputFile: "artifacts/evaluation/e2e-results.json"}]],
  use: {baseURL: "http://127.0.0.1:3000", channel: "chrome", trace: "retain-on-failure", screenshot: "only-on-failure"},
  projects: [
    {name: "desktop", use: {...devices["Desktop Chrome"], viewport: {width: 1440, height: 1000}}},
    {name: "mobile", use: {...devices["Pixel 7"]}},
  ],
});
