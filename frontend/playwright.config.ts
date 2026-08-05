import { defineConfig, devices } from '@playwright/test';

const allProjects = [
  {
    name: 'chromium',
    use: { ...devices['Desktop Chrome'] },
  },
  {
    name: 'firefox',
    use: { ...devices['Desktop Firefox'] },
  },
  {
    name: 'webkit',
    use: { ...devices['Desktop Safari'] },
  },
  {
    name: 'mobile-chrome',
    use: { ...devices['Pixel 5'] },
  },
];

// By default run only chromium (fast, no extra browser downloads).
// Set E2E_ALL_BROWSERS=1 to run the full multi-browser matrix.
const projects = process.env.E2E_ALL_BROWSERS
  ? allProjects
  : allProjects.filter((p) => p.name === 'chromium');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1, // Run tests serially to avoid state pollution
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    // 单个请求超时 5 秒
    actionTimeout: 5_000,
    navigationTimeout: 5_000,
  },
  // 单个测试超时 10 秒
  timeout: 10_000,
  projects,
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    timeout: 120 * 1000,
    reuseExistingServer: !process.env.CI,
  },
});
