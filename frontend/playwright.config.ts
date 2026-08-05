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
  // 测试并行执行。状态隔离由各 spec 的 beforeEach / afterEach 负责（清 history、清文档、复原 settings）。
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // 并行 worker 数：默认上限 4（CI 下限为 2）。可以 E2E_WORKERS=N 环境变量覆盖。
  workers: Number(process.env.E2E_WORKERS) || (process.env.CI ? 2 : 4),
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    // 单个请求超时 5 秒（保留宽松值，防止 vite 冷启动误杀 click/fill）
    actionTimeout: 5_000,
    navigationTimeout: 5_000,
  },
  // 单个测试用例超时 5 秒（mock 后所有请求都应很快完成；--timeout CLI 可覆盖）
  timeout: 5_000,
  // 期望串行执行的测试通过 test.describe.configure({ mode: 'serial' }) 启用。
  projects,
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    timeout: 120 * 1000,
    reuseExistingServer: !process.env.CI,
  },
});
