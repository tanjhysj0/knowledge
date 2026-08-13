import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #64: 管理端小说列表展示索引进度与状态。
 *
 * 覆盖验收：
 * - 每行展示状态徽标，processing 展示进度条与百分比；
 * - 上传后管理列表自动轮询：排队中/处理中（进度展示）→ 就绪，无需手动刷新；
 * - 全部小说到达终态后轮询停止（观察窗口内不再产生列表请求）。
 * - failed 徽标与错误信息可见性由 #63 的 document-indexing.spec 覆盖。
 */

interface DocStatus {
  id: number;
  filename: string;
  status: string;
  progress: number;
}

async function uploadViaApi(
  filename: string,
  content: string,
  mimeType = 'text/plain'
): Promise<DocStatus> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.post('/api/documents/upload', {
      multipart: {
        file: { name: filename, mimeType, buffer: Buffer.from(content) },
      },
    });
    expect(res.ok()).toBeTruthy();
    return (await res.json()) as DocStatus;
  } finally {
    await ctx.dispose();
  }
}

/** 轮询后端全量列表直到目标文档 ready。 */
async function pollReady(filename: string, timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
    let items: DocStatus[] = [];
    try {
      const res = await ctx.get('/api/documents?page=1&page_size=100&all_statuses=true');
      expect(res.ok()).toBeTruthy();
      const body = await res.json();
      items = body.items || [];
    } finally {
      await ctx.dispose();
    }
    const doc = items.find((d) => d.filename === filename);
    if (doc && doc.status === 'ready') return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`文档 ${filename} 在 ${timeoutMs}ms 内未达到 ready`);
}

const rowOf = (page: import('@playwright/test').Page, title: string) =>
  page.locator('[data-testid="novel-list-item"]').filter({ hasText: title });

cleanupTest.describe('管理端索引进度与状态展示 - E2E (#64)', () => {
  cleanupTest.beforeEach(async () => {
    // 后台索引含真实 bge-m3 embedding 推理（CPU），放宽单个用例超时。
    cleanupTest.setTimeout(120_000);
  });

  cleanupTest('上传后列表自动轮询：进度展示 → 就绪', async ({ page, uploadedDocs }) => {
    const filename = `progress-${Date.now()}.txt`;
    const title = filename.replace(/\.txt$/, '');
    // 足够长的内容让后台索引持续数秒（约 4 个 chunk 的 CPU embedding），
    // 为 UI 轮询留出观察处理中 + 进度条的窗口。
    const content = `进度展示测试。${'蓝色独角兽漫游星海。'.repeat(150)}`;
    const uploaded = await uploadViaApi(filename, content);
    expect(uploaded.status).toBe('pending');
    expect(uploaded.progress).toBe(0);

    // 上传后打开管理列表：无需手动刷新即可看到状态与进度。
    await page.goto('/admin');
    await page.waitForLoadState('networkidle').catch(() => {});
    const row = rowOf(page, title);
    await expect(row).toBeVisible({ timeout: 15_000 });

    // 非终态阶段：排队中或处理中徽标可见，处理中附进度百分比。
    await expect(row.locator('[data-testid="doc-status-badge"]')).toContainText(
      /排队中|处理中/,
      { timeout: 15_000 }
    );

    // processing 阶段展示进度条与百分比。
    await expect(row.locator('[data-testid="doc-progress-bar"]')).toBeVisible({
      timeout: 15_000,
    });
    await expect(row.locator('[data-testid="doc-status-badge"]')).toContainText('%');

    // 自动轮询直至就绪：进度条消失、徽标变为就绪。
    await expect(row.locator('[data-testid="doc-status-badge"]')).toContainText(
      '就绪',
      { timeout: 90_000 }
    );
    await expect(row.locator('[data-testid="doc-progress-bar"]')).toHaveCount(0);

    await uploadedDocs.track(filename);
  });

  cleanupTest('全部小说到达终态后轮询停止', async ({ page, uploadedDocs }) => {
    const filename = `poll-stop-${Date.now()}.txt`;
    const title = filename.replace(/\.txt$/, '');
    await uploadViaApi(filename, '轮询停止测试内容');
    // 先让后端索引完成，页面首屏即全终态。
    await pollReady(filename);
    await uploadedDocs.track(filename);

    await page.goto('/admin');
    await page.waitForLoadState('networkidle').catch(() => {});

    // 统计浏览器发起的列表请求（轮询走 GET /api/documents）。
    let listRequests = 0;
    page.on('request', (req) => {
      if (req.url().includes('/api/documents')) listRequests += 1;
    });

    // 目标行就绪，且页面上所有行均为终态（就绪/失败）。
    const row = rowOf(page, title);
    await expect(row.locator('[data-testid="doc-status-badge"]')).toContainText(
      '就绪',
      { timeout: 15_000 }
    );
    const badges = page.locator('[data-testid="doc-status-badge"]');
    const badgeCount = await badges.count();
    for (let i = 0; i < badgeCount; i += 1) {
      await expect(badges.nth(i)).not.toContainText(/排队中|处理中/);
    }

    // 等一轮可能的在途请求落定后取快照，观察 5s（≥2 个轮询周期）无新请求。
    await page.waitForTimeout(500);
    const snapshot = listRequests;
    await page.waitForTimeout(5_000);
    expect(listRequests).toBe(snapshot);
  });
});
