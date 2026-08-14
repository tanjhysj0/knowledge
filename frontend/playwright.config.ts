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
const mainProjects = (process.env.E2E_ALL_BROWSERS
  ? allProjects
  : allProjects.filter((p) => p.name === 'chromium')
).map((p) => ({
  ...p,
  testIgnore: /preflight-unconfigured\.spec\.ts/,
  dependencies: ['preflight-setup'],
}));

// #66 后续：preflight 拒绝路径（preflight-unconfigured.spec）需要短暂清空
// 默认模型 api_key，与主套件并行共享后端 DB 时该窗口会随机打挂聊天测试。
// 故拆为前置 project：先串行跑完（收尾配回 dummy key），主套件才并行启动。
const preflightProject = {
  name: 'preflight-setup',
  testMatch: /preflight-unconfigured\.spec\.ts/,
  use: { ...devices['Desktop Chrome'] },
};

const projects = [preflightProject, ...mainProjects];

export default defineConfig({
  testDir: './e2e',
  // 全部 E2E 请求统一携带 X-E2E-Test 头部，后端据此返回 MockLLMProvider，
  // 避免在测试环境调用真实 LLM。
  globalSetup: './e2e/helpers/globalSetup.ts',
  // 测试并行执行。状态隔离由各 spec 的 beforeEach / afterEach 负责（清 history、清文档、复原 settings）。
  // preflight-unconfigured.spec 例外：它是前置 project（dependencies），先于主套件串行跑完。
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
    // 所有 browser context 自动附带 X-E2E-Test，让后端切到 MockLLMProvider。
    extraHTTPHeaders: { 'X-E2E-Test': 'true' },
  },
  // 单个测试用例超时 5 秒（mock 后所有请求都应很快完成；--timeout CLI 可覆盖）
  timeout: 5_000,
  // 期望串行执行的测试通过 test.describe.configure({ mode: 'serial' }) 启用。
  projects,
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    timeout: 5_000,
    reuseExistingServer: !process.env.CI,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
