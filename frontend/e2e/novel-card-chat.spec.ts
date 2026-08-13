import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #51: 点击小说卡片开始讨论。
 *
 * 完整链路：首页点卡片 → /chat?doc=<id> 聚焦该小说（文档上下文 = 会话
 * 绑定的这本小说，有且只有一本）→ 自动新建并激活会话、标题默认取小说名
 * → 发送消息请求体 document_ids 仅含该小说 id；聊天页左上角 DocQA Logo
 * 点击返回首页书架；无 doc 参数直接访问 /chat 无会话时显示引导空态。
 *
 * 测试数据通过后端 API 上传/删除（同 homepage-novel.spec），聊天请求
 * 由 playwright.config 注入的 X-E2E-Test 头部切到 MockLLMProvider。
 */

interface MultipartValue {
  name: string;
  mimeType: string;
  buffer: Buffer;
}

async function uploadViaApi(
  filename: string,
  content: string,
  title?: string
): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  const multipart: { [key: string]: string | MultipartValue } = {
    file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from(content) },
  };
  if (title) multipart.title = title;
  const res = await ctx.post('/api/documents/upload', { multipart });
  expect(res.ok()).toBeTruthy();
  await ctx.dispose();
}

/** 按文件名轮询获取后端持久化后的文档 id（上传含异步分块处理）。 */
async function waitDocId(filename: string): Promise<number> {
  for (let i = 0; i < 20; i++) {
    const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
    try {
      const res = await ctx.get('/api/documents?page=1&page_size=1000');
      expect(res.ok()).toBeTruthy();
      const body = (await res.json()) as {
        items: Array<{ id: number; filename: string }>;
      };
      const found = body.items.find((d) => d.filename === filename);
      if (found) return found.id;
    } finally {
      await ctx.dispose();
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`文档 ${filename} 上传后未出现`);
}

/** 删除标题为指定小说名的会话（#51 每次点卡片新建一条，测试后清理）。 */
async function deleteConversationsByTitle(
  page: import('@playwright/test').Page,
  title: string
): Promise<void> {
  const res = await page.request.get('/api/conversations');
  if (!res.ok()) return;
  const list = (await res.json()) as Array<{ id: number; title: string | null }>;
  for (const c of list) {
    if (c.title === title) {
      await page.request.delete(`/api/conversations/${c.id}`).catch(() => {});
    }
  }
}

cleanupTest.describe('点击小说卡片开始讨论 - E2E (#51)', () => {
  cleanupTest.beforeEach(async () => {
    // 上传含后端分块/embedding 处理，单个用例放宽到 30s。
    cleanupTest.setTimeout(30_000);
  });

  cleanupTest(
    '点卡片 → 聚焦单小说 → 请求体仅含该小说 id → 会话标题取小说名',
    async ({ page, uploadedDocs }) => {
      const title = `卡片小说-${Date.now()}`;
      const filename = `card-novel-${Date.now()}.txt`;
      await uploadViaApi(filename, `novel body for ${filename}`, title);
      const docId = await waitDocId(filename);
      await uploadedDocs.track(filename);

      // 首页书架出现该卡片后点击
      await page.goto('/');
      await page.waitForLoadState('networkidle').catch(() => {});
      const card = page
        .locator('[data-testid="novel-card"]')
        .filter({ hasText: title })
        .first();
      await expect(card).toBeVisible({ timeout: 10_000 });
      await card.click();

      // 跳转 /chat 并携带小说标识
      await expect(page).toHaveURL(new RegExp(`/chat\\?doc=${docId}`), {
        timeout: 5_000,
      });
      await page.waitForSelector('textarea');

      // 聊天页文档上下文 = 会话绑定的这本小说：context-indicator 显示书名
      const indicator = page.locator('[data-testid="context-indicator"]');
      await expect(indicator).toBeVisible({ timeout: 5_000 });
      await expect(indicator.locator('.context-label')).toHaveText(
        `基于《${title}》回答`
      );

      // 自动新建并激活的会话标题默认取小说名
      const activeItem = page.locator(
        '[data-testid^="conversation-item-"][data-active="true"]'
      );
      await expect(activeItem.locator('.conversation-item-title')).toHaveText(
        title,
        { timeout: 5_000 }
      );

      // 发送消息：请求体 document_ids 仅含该小说 id
      const reqPromise = page.waitForRequest(
        (req) => req.url().includes('/api/chat/stream'),
        { timeout: 5_000 }
      );
      await page.locator('textarea').fill('这本小说的主角是谁');
      await page.locator('button:has-text("发送")').click();
      const req = await reqPromise;
      const body = req.postDataJSON() as { document_ids: number[] };
      expect(body.document_ids).toEqual([docId]);

      // mock 回复出现且流结束（避免清理会话时后端仍在写消息）
      await expect(page.locator('.message.assistant .content').last()).not.toBeEmpty(
        { timeout: 5_000 }
      );
      await expect(page.locator('button:has-text("发送")')).toHaveText('发送', {
        timeout: 5_000,
      });

      // 清理本条测试新建的会话
      await deleteConversationsByTitle(page, title);
    }
  );

  cleanupTest('聊天页 DocQA Logo 点击返回首页书架', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForSelector('textarea');

    const logo = page.locator('[data-testid="chat-logo"]');
    await expect(logo).toBeVisible();
    await expect(logo).toContainText('DocQA');

    await logo.click();

    // 返回首页：书架（卡片网格或空状态）可见
    await expect(page).toHaveURL(/\/$/, { timeout: 5_000 });
    await expect(
      page
        .locator('[data-testid="shelf-grid"], [data-testid="shelf-empty"]')
        .first()
    ).toBeVisible({ timeout: 10_000 });
  });

  cleanupTest('无 doc 参数直接访问 /chat：无会话不自动创建，显示引导空态', async ({ page, uploadedDocs }) => {
    const filename = `direct-chat-${Date.now()}.txt`;
    await uploadViaApi(filename, `novel body for ${filename}`);
    await uploadedDocs.track(filename);

    // 新浏览器上下文没有任何会话：不自动创建，展示引导空态
    await page.goto('/chat');
    await page.waitForSelector('textarea');
    await expect(page.locator('.empty-state')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('.empty-state')).toContainText('还没有会话');
    await expect(page.locator('[data-testid="empty-shelf-link"]')).toBeVisible();

    // 输入区禁用，需从首页选小说进入
    await expect(page.locator('textarea')).toBeDisabled();
    await expect(page.locator('button:has-text("发送")')).toBeDisabled();
  });
});
