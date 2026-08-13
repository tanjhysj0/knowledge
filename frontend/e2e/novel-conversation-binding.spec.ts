import { expect, request as apiRequest, type Page } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #52: 小说与会话绑定——每本小说一个持续讨论区。
 *
 * 覆盖 acceptance criteria：
 *  1. 重复点击同一小说卡片 → 恢复该小说的既有绑定会话及其历史消息，
 *     不另开新会话。
 *  2. 两个独立浏览器上下文（不同 client key）点同一小说 → 各自独立
 *     会话空间，历史消息互不可见。
 *  3. 删除小说后其绑定会话保留（不级联删除），历史消息仍在。
 *
 * 前端首次访问生成 client key 存 localStorage 并经 axios 拦截器统一
 * 携带 X-Client-Id；后端 GET /conversations 按 client_id 过滤，
 * POST 带 document_id 时按 (client_id, document_id) 幂等绑定。
 * 聊天请求由 playwright.config 注入的 X-E2E-Test 头部切到 MockLLMProvider。
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

/** 删除标题为指定小说名的会话（#52 绑定会话测试后清理）。 */
async function deleteConversationsByTitle(page: Page, title: string): Promise<void> {
  const res = await page.request.get('/api/conversations');
  if (!res.ok()) return;
  const list = (await res.json()) as Array<{ id: number; title: string | null }>;
  for (const c of list) {
    if (c.title === title) {
      await page.request.delete(`/api/conversations/${c.id}`).catch(() => {});
    }
  }
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

/** 当前激活会话的标题 locator 断言（激活项为 data-active="true"）。 */
async function expectActiveConversationTitle(page: Page, title: string) {
  const activeItem = page.locator(
    '[data-testid^="conversation-item-"][data-active="true"]'
  );
  await expect(activeItem).toHaveCount(1, { timeout: 5_000 });
  await expect(activeItem.locator('.conversation-item-title')).toHaveText(title, {
    timeout: 5_000,
  });
}

cleanupTest.describe('小说与会话绑定 - E2E (#52)', () => {
  cleanupTest.beforeEach(async () => {
    // 上传含后端分块/embedding 处理，单个用例放宽到 30s。
    cleanupTest.setTimeout(30_000);
  });

  cleanupTest(
    '重复点击同一小说卡片 → 恢复既有会话及其历史消息',
    async ({ page, uploadedDocs }) => {
      const title = `绑定恢复-${Date.now()}`;
      const filename = `binding-restore-${Date.now()}.txt`;
      await uploadViaApi(filename, `novel body for ${filename}`, title);
      const docId = await waitDocId(filename);
      await uploadedDocs.track(filename);

      // 首次点卡片：新建绑定会话并发送一条消息
      await openNovelCard(page, title, docId);
      await expectActiveConversationTitle(page, title);
      const marker = `恢复测试消息-${Date.now()}`;
      await sendAndWaitReply(page, marker);

      // 回首页再次点击同一卡片
      await openNovelCard(page, title, docId);

      // 恢复同一绑定会话：标题不变、历史消息仍在、不新建第二条会话
      await expectActiveConversationTitle(page, title);
      await expect(
        page.locator(`.message.user .content:has-text("${marker}")`)
      ).toBeVisible({ timeout: 5_000 });
      await expect(
        page.locator('[data-testid^="conversation-item-"]')
      ).toHaveCount(1, { timeout: 5_000 });

      // 清理本条测试新建的会话
      await deleteConversationsByTitle(page, title);
    }
  );

  cleanupTest(
    '两个独立浏览器上下文点同一小说 → 会话空间隔离',
    async ({ browser, uploadedDocs }) => {
      const title = `绑定隔离-${Date.now()}`;
      const filename = `binding-isolation-${Date.now()}.txt`;
      await uploadViaApi(filename, `novel body for ${filename}`, title);
      const docId = await waitDocId(filename);
      await uploadedDocs.track(filename);

      const clientA = `e2e-iso-a-${Date.now()}`;
      const clientB = `e2e-iso-b-${Date.now()}`;
      const msgA = 'A 客户端专属问题';
      const msgB = 'B 客户端专属问题';

      // 两个独立浏览器上下文，各自注入固定 client key（#52 会话空间按客户端隔离）。
      // 手动创建的 context 不继承 playwright.config 的 use.baseURL，需显式传入。
      const ctxA = await browser.newContext({
        baseURL: 'http://127.0.0.1:5173',
        extraHTTPHeaders: { 'X-E2E-Test': 'true' },
      });
      const ctxB = await browser.newContext({
        baseURL: 'http://127.0.0.1:5173',
        extraHTTPHeaders: { 'X-E2E-Test': 'true' },
      });
      const setClientKey = (key: string) => {
        try {
          localStorage.setItem('docqa_client_id', key);
        } catch {
          // about:blank 等无 origin 文档上 localStorage 不可用，忽略
        }
      };
      await ctxA.addInitScript(setClientKey, clientA);
      await ctxB.addInitScript(setClientKey, clientB);
      const pageA = await ctxA.newPage();
      const pageB = await ctxB.newPage();

      // A 点卡片 → 新建绑定会话 → 发消息
      await openNovelCard(pageA, title, docId);
      await expectActiveConversationTitle(pageA, title);
      await sendAndWaitReply(pageA, msgA);

      // B 点同一卡片 → 独立会话空间：无 A 历史，只有自己的 1 条会话
      await openNovelCard(pageB, title, docId);
      await expectActiveConversationTitle(pageB, title);
      await expect(
        pageB.locator(`.message.user .content:has-text("${msgA}")`)
      ).toHaveCount(0);
      await expect(pageB.locator('.empty-state')).toBeVisible();
      await sendAndWaitReply(pageB, msgB);

      // A 再次点卡片：恢复 A 的会话（含历史），看不到 B 的消息
      await openNovelCard(pageA, title, docId);
      await expectActiveConversationTitle(pageA, title);
      await expect(
        pageA.locator(`.message.user .content:has-text("${msgA}")`)
      ).toBeVisible({ timeout: 5_000 });
      await expect(
        pageA.locator(`.message.user .content:has-text("${msgB}")`)
      ).toHaveCount(0);
      await expect(
        pageA.locator('[data-testid^="conversation-item-"]')
      ).toHaveCount(1, { timeout: 5_000 });

      // 清理两个 client 各自空间内的会话
      for (const [p, clientKey] of [
        [pageA, clientA],
        [pageB, clientB],
      ] as const) {
        const res = await p.request.get('/api/conversations', {
          headers: { 'X-Client-Id': clientKey },
        });
        if (!res.ok()) continue;
        const list = (await res.json()) as Array<{ id: number }>;
        for (const c of list) {
          await p.request.delete(`/api/conversations/${c.id}`).catch(() => {});
        }
      }
      await ctxA.close();
      await ctxB.close();
    }
  );

  cleanupTest(
    '删除小说后其绑定会话保留、历史消息仍在',
    async ({ page, uploadedDocs }) => {
      const title = `删书保留-${Date.now()}`;
      const filename = `binding-delete-${Date.now()}.txt`;
      await uploadViaApi(filename, `novel body for ${filename}`, title);
      const docId = await waitDocId(filename);
      await uploadedDocs.track(filename);

      // 点卡片建绑定会话并发送消息
      await openNovelCard(page, title, docId);
      await expectActiveConversationTitle(page, title);
      const marker = `删书前的问题-${Date.now()}`;
      await sendAndWaitReply(page, marker);

      // 记录绑定会话 id
      const activeTestId = await page
        .locator('[data-testid^="conversation-item-"][data-active="true"]')
        .getAttribute('data-testid');
      const convId = Number(activeTestId?.replace('conversation-item-', ''));
      expect(Number.isInteger(convId) && convId > 0).toBeTruthy();

      // 通过 API 删除小说（不级联删除绑定会话）
      const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
      await ctx.delete(`/api/documents/${docId}`);
      await ctx.dispose();

      // 绑定会话保留：消息历史仍可读
      const res = await page.request.get(`/api/conversations/${convId}/messages`);
      expect(res.ok()).toBeTruthy();
      const msgs = (await res.json()) as Array<{ content: string }>;
      expect(msgs.some((m) => m.content.includes(marker))).toBeTruthy();

      // 清理会话
      await page.request.delete(`/api/conversations/${convId}`).catch(() => {});
    }
  );
});
