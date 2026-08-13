import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #50: 首页重构为纯展示书架。
 * 上传/删除/分页等管理能力已迁至 /admin（见 admin.spec.ts），本 spec 仅
 * 断言展示：卡片结构（封面大图 + 底部居中书名）、默认封面图/封面图、
 * title 优先、hover 反馈、响应式列数、空状态。测试数据通过后端 API 上传。
 */

// 通过后端 API 上传小说正文（可选封面），返回持久化后的文档。
interface MultipartValue {
  name: string;
  mimeType: string;
  buffer: Buffer;
}

async function uploadViaApi(
  filename: string,
  content: string,
  title?: string,
  cover?: Buffer
): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  const multipart: { [key: string]: string | MultipartValue } = {
    file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from(content) },
  };
  if (title) multipart.title = title;
  if (cover) multipart.cover = { name: 'cover.png', mimeType: 'image/png', buffer: cover };
  const res = await ctx.post('/api/documents/upload', { multipart });
  expect(res.ok()).toBeTruthy();
  await ctx.dispose();
}

/**
 * #63：轮询默认（ready-only）列表直到该文件出现。
 * 上传只落库（pending），后台索引完成后才在默认列表可见。
 */
async function waitForReadyViaApi(filename: string, timeoutMs = 30_000): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await ctx.get('/api/documents?page=1&page_size=100');
    const body = await res.json();
    const items = (body.items || []) as { filename: string }[];
    if (items.some((d) => d.filename === filename)) {
      await ctx.dispose();
      return;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  await ctx.dispose();
}

// #48：后端只校验封面扩展名与大小，fake PNG 头即可通过。
const FAKE_PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

type Page = import('@playwright/test').Page;

cleanupTest.describe('首页书架（纯展示）- E2E (#50)', () => {
  cleanupTest.beforeEach(async ({ page }) => {
    // 上传含后端分块/embedding 处理，单个用例放宽到 30s。
    cleanupTest.setTimeout(30_000);
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  /** 上传小说并等待后台索引完成（ready）后刷新首页书架。 */
  async function seedAndReload(
    page: Page,
    filename: string,
    title?: string,
    cover?: Buffer
  ): Promise<void> {
    await uploadViaApi(filename, `novel body for ${filename}`, title, cover);
    // #63：上传只落库（pending），索引完成后才出现在默认书架列表。
    await waitForReadyViaApi(filename);
    await page.reload();
    await page.waitForLoadState('networkidle').catch(() => {});
  }

  cleanupTest('卡片结构：封面大图（2:3）+ 底部居中书名', async ({ page, uploadedDocs }) => {
    const filename = `shelf-${Date.now()}.txt`;
    await seedAndReload(page, filename);
    await uploadedDocs.track(filename);

    const card = page.locator('[data-testid="novel-card"]').filter({ hasText: 'shelf-' }).first();
    await expect(card).toBeVisible({ timeout: 10_000 });

    // 封面区 2:3 竖版
    const coverBox = await card.locator('.novel-card-cover').boundingBox();
    expect(coverBox).toBeTruthy();
    expect(coverBox!.height / coverBox!.width).toBeCloseTo(1.5, 1);

    // 书名底部居中
    await expect(card.locator('.novel-card-title')).toHaveCSS('text-align', 'center');
  });

  cleanupTest('无封面小说展示默认封面图', async ({ page, uploadedDocs }) => {
    const filename = `shelf-default-${Date.now()}.txt`;
    await seedAndReload(page, filename);
    await uploadedDocs.track(filename);

    const card = page.locator('[data-testid="novel-card"]').filter({ hasText: 'shelf-default-' }).first();
    await expect(card).toBeVisible({ timeout: 10_000 });
    // 默认封面图（#53 同一内置 SVG 资源），非 img
    await expect(card.locator('svg[aria-label="默认封面"]')).toBeVisible();
    await expect(card.locator('img')).toHaveCount(0);
  });

  cleanupTest('有封面小说展示封面大图', async ({ page, uploadedDocs }) => {
    const filename = `shelf-cover-${Date.now()}.txt`;
    await seedAndReload(page, filename, undefined, FAKE_PNG);
    await uploadedDocs.track(filename);

    const card = page.locator('[data-testid="novel-card"]').filter({ hasText: 'shelf-cover-' }).first();
    await expect(card).toBeVisible({ timeout: 10_000 });
    const img = card.locator('img.novel-card-cover-img');
    await expect(img).toBeVisible();
    await expect(img).toHaveAttribute('src', /\/api\/covers\//);
  });

  cleanupTest('书名优先展示 title，超长省略', async ({ page, uploadedDocs }) => {
    const title = `超长书名-${'长'.repeat(40)}-${Date.now()}`;
    const filename = `shelf-title-${Date.now()}.txt`;
    await seedAndReload(page, filename, title);
    await uploadedDocs.track(filename);

    const card = page.locator('[data-testid="novel-card"]').filter({ hasText: '超长书名-' }).first();
    await expect(card).toBeVisible({ timeout: 10_000 });

    // 书名展示 title 而非文件名
    const titleEl = card.locator('.novel-card-title');
    await expect(titleEl).toHaveText(title);
    // 超长省略：单行 + ellipsis
    await expect(titleEl).toHaveCSS('text-overflow', 'ellipsis');
    await expect(titleEl).toHaveCSS('white-space', 'nowrap');
  });

  cleanupTest('卡片 hover 有视觉反馈（放大）且可点击', async ({ page, uploadedDocs }) => {
    const filename = `shelf-hover-${Date.now()}.txt`;
    await seedAndReload(page, filename);
    await uploadedDocs.track(filename);

    const card = page.locator('[data-testid="novel-card"]').filter({ hasText: 'shelf-hover-' }).first();
    await expect(card).toBeVisible({ timeout: 10_000 });
    // 整卡可点击（点击链路由 #51 实现）
    await expect(card).toHaveCSS('cursor', 'pointer');

    // hover 后 transform 从 none 变为缩放矩阵
    const before = await card.evaluate((el) => getComputedStyle(el).transform);
    await card.hover();
    await expect
      .poll(async () => card.evaluate((el) => getComputedStyle(el).transform))
      .not.toBe(before);
  });

  cleanupTest('响应式：桌面多列、窄屏单列', async ({ page, uploadedDocs }) => {
    const filename = `shelf-resp-${Date.now()}.txt`;
    await seedAndReload(page, filename);
    await uploadedDocs.track(filename);

    const grid = page.locator('[data-testid="shelf-grid"]');
    await expect(grid).toBeVisible({ timeout: 10_000 });

    const columnCount = () =>
      grid.evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length);

    // 桌面视口：多列
    await page.setViewportSize({ width: 1280, height: 720 });
    await expect.poll(columnCount).toBeGreaterThan(1);

    // 窄屏视口：单列
    await page.setViewportSize({ width: 375, height: 720 });
    await expect.poll(columnCount).toBe(1);
  });

  cleanupTest('无小说时展示居中空状态', async ({ page }) => {
    // 有小说时断言卡片网格；无小说时断言居中空状态
    const hasCards = (await page.locator('[data-testid="novel-card"]').count()) > 0;
    if (!hasCards) {
      const empty = page.locator('[data-testid="shelf-empty"]');
      await expect(empty).toBeVisible();
      await expect(empty).toContainText('暂无小说');
    }
  });
});
