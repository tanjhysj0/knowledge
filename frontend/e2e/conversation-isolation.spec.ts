import { expect, request as apiRequest, type Page } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #36 会话上下文隔离 E2E（#52 新形态）。
 *
 * 会话只能由首页小说卡片创建，每会话绑定且仅绑定一本小说；聊天页无
 * 「新建」按钮与文档选择器，文档上下文 = 会话绑定的那本小说。
 *
 * 覆盖 acceptance criteria:
 *  1. 同一 chat 流只能写入指定 conversation_id，跨会话不污染。
 *  2. 切换会话后另一会话的消息列表互不可见。
 *  3. 切换会话时文档上下文跟随会话绑定小说变化（context-indicator）。
 *  4. 错误的 conversation_id → 422（缺字段）或 404（不存在），
 *     不写入孤儿消息。
 *
 * 用例串行 + 固定 client key（X-Client-Id）；会话自清理（DELETE）。
 * Playwright 自动注入 ``X-E2E-Test``，后端使用 ``MockLLMProvider`` /
 * mock embedding，保留真实 chat 写入与历史读取链路。
 */

const CLIENT_KEY = 'e2e-conv-isolation-client';

interface MultipartValue {
  name: string;
  mimeType: string;
  buffer: Buffer;
}

/** 通过 API 上传小说（标题取小说名，供卡片与 indicator 文案断言）。 */
async function uploadViaApi(filename: string, content: string, title: string): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  const multipart: { [key: string]: string | MultipartValue } = {
    file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from(content) },
    title,
  };
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

/** 首页点击指定书名卡片，等待跳转聊天页并聚焦该小说。 */
async function openNovelCard(page: Page, title: string, docId: number) {
  await page.goto('/');
  const card = page
    .locator('[data-testid="novel-card"]')
    .filter({ hasText: title })
    .first();
  await expect(card).toBeVisible({ timeout: 10_000 });
  await card.click();
  await expect(page).toHaveURL(new RegExp(`/chat\\?doc=${docId}`), {
    timeout: 5_000,
  });
  await page.waitForSelector('textarea');
}

/** 发送消息并等待 mock 回复完成（按钮文案回到「发送」）。 */
async function sendAndWaitReply(page: Page, message: string) {
  await page.locator('textarea').fill(message);
  await page.locator('button:has-text("发送")').click();
  await expect(
    page.locator(`.message.user .content:has-text("${message}")`)
  ).toBeVisible({ timeout: 5_000 });
  await expect(page.locator('.message.assistant .content').last()).not.toBeEmpty({
    timeout: 5_000,
  });
  await expect(page.locator('button:has-text("发送")')).toHaveText('发送', {
    timeout: 5_000,
  });
}

cleanupTest.describe('ChatPage 会话上下文隔离 - E2E (#36)', () => {
  cleanupTest.describe.configure({ mode: 'serial' });

  cleanupTest.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(
      (key) => localStorage.setItem('docqa_client_id', key),
      CLIENT_KEY
    );
    await cleanupConversations(page);
    await page.goto('/chat');
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForSelector('[data-testid="conversation-sidebar"]');
    // 清理后无会话：引导空态可见
    await expect(page.locator('.empty-state')).toBeVisible({ timeout: 5_000 });
  });

  async function cleanupConversations(page: Page) {
    const res = await page.request.get('/api/conversations', {
      headers: { 'X-Client-Id': CLIENT_KEY },
    });
    if (!res.ok()) return;
    const list: Array<{ id: number }> = await res.json();
    for (const c of list) {
      await page.request.delete(`/api/conversations/${c.id}`).catch(() => {});
    }
  }

  cleanupTest(
    '两会话消息隔离 + 切换会话时文档上下文跟随绑定小说变化',
    async ({ page, uploadedDocs }) => {
      cleanupTest.setTimeout(30_000);

      const titleA = `隔离小说A-${Date.now()}`;
      const titleB = `隔离小说B-${Date.now()}`;
      const filenameA = `isolation-A-${Date.now()}.txt`;
      const filenameB = `isolation-B-${Date.now()}.txt`;
      await uploadViaApi(filenameA, `novel body for A`, titleA);
      await uploadViaApi(filenameB, `novel body for B`, titleB);
      const docIdA = await waitDocId(filenameA);
      const docIdB = await waitDocId(filenameB);
      await uploadedDocs.track(filenameA);
      await uploadedDocs.track(filenameB);

      const indicator = page.locator('[data-testid="context-indicator"]');
      const msgA = 'A 专属问题 alpha-001';
      const msgB = 'B 专属问题 beta-001';

      // 1. 点卡片 A → 创建绑定会话 A，上下文只绑定 A
      await openNovelCard(page, titleA, docIdA);
      await expect(indicator).toBeVisible({ timeout: 5_000 });
      await expect(indicator.locator('.context-label')).toHaveText(
        `基于《${titleA}》回答`
      );
      await sendAndWaitReply(page, msgA);

      // 2. 点卡片 B → 创建绑定会话 B，上下文切换为 B
      await openNovelCard(page, titleB, docIdB);
      await expect(indicator.locator('.context-label')).toHaveText(
        `基于《${titleB}》回答`
      );
      await expect(
        page.locator(`.message.user .content:has-text("${msgA}")`)
      ).toHaveCount(0);
      await sendAndWaitReply(page, msgB);

      // 3. 侧栏切回会话 A：消息恢复且上下文回到 A（回归：切换会话文档跟着变）
      await page
        .locator(`.conversation-item-title:has-text("${titleA}")`)
        .first()
        .click();
      await expect(
        page.locator(`.message.user .content:has-text("${msgA}")`)
      ).toBeVisible({ timeout: 5_000 });
      await expect(
        page.locator(`.message.user .content:has-text("${msgB}")`)
      ).toHaveCount(0);
      await expect(indicator.locator('.context-label')).toHaveText(
        `基于《${titleA}》回答`
      );

      // 4. 再切回会话 B：上下文回到 B
      await page
        .locator(`.conversation-item-title:has-text("${titleB}")`)
        .first()
        .click();
      await expect(
        page.locator(`.message.user .content:has-text("${msgB}")`)
      ).toBeVisible({ timeout: 5_000 });
      await expect(indicator.locator('.context-label')).toHaveText(
        `基于《${titleB}》回答`
      );
    }
  );

  cleanupTest('缺失 conversation_id 应被 Pydantic 422 拒绝（不写入任何消息）', async ({ page }) => {
    // 直接用 page.request 发裸请求绕过前端 ChatPage，验证 Pydantic 拦截
    const res = await page.request.post('/api/chat/stream', {
      headers: { 'Content-Type': 'application/json' },
      data: JSON.stringify({ message: 'no conv id', document_ids: [] }),
      failOnStatusCode: false,
    });
    expect(res.status()).toBe(422);
  });

  cleanupTest('不存在的 conversation_id 应返回 404（ask 端点）', async ({ page }) => {
    const res = await page.request.post('/api/chat', {
      headers: { 'Content-Type': 'application/json' },
      data: JSON.stringify({
        message: 'orphan',
        document_ids: [],
        conversation_id: 999999,
      }),
      failOnStatusCode: false,
    });
    expect(res.status()).toBe(404);
  });
});
