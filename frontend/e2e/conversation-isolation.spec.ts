import { test, expect, type Page, type BrowserContext } from '@playwright/test';

/**
 * 会话上下文隔离 E2E（#36）。
 *
 * 验证 acceptance criteria:
 *  1. 同一 chat 流只能在指定 conversation_id 下写入消息，跨会话不污染。
 *  2. 切换会话后另一会话的消息列表与其它上下文互不可见。
 *  3. 错误的 conversation_id 请求后端应直接 422（缺字段）或 404（不存在），
 *     不写入孤儿消息。
 *
 * 用例串行 + 用例间自清理（DELETE /api/conversations/*）。
 * Playwright 自动注入 ``X-E2E-Test``，后端使用 ``MockLLMProvider`` / mock embedding，
 * 保留真实 chat 写入与历史读取链路。*/

test.describe('ChatPage 会话上下文隔离 - E2E (#36)', () => {
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForSelector('[data-testid="conversation-sidebar"]');
    await cleanupConversations(page);
    await page.reload();
    await page.waitForSelector('[data-testid="conversation-sidebar"]');
    await expect(
      page.locator('[data-testid="conversation-list"] .conversation-item')
    ).toHaveCount(1, { timeout: 5_000 });
  });

  test.afterAll(async ({ browser }) => {
    const ctx: BrowserContext = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto('http://localhost:8000/chat').catch(() => {});
    await cleanupConversations(page);
    await ctx.close();
  });

  async function cleanupConversations(page: Page) {
    const res = await page.request.get('/api/conversations');
    if (!res.ok()) return;
    const list: Array<{ id: number }> = await res.json();
    for (const c of list) {
      await page.request.delete(`/api/conversations/${c.id}`).catch(() => {});
    }
  }

  async function sendAndWait(page: Page, message: string) {
    // 等 React 完成 useEffect（list → create → setActiveConvId）后 fill。
    // 否则 click 发送时 activeConvId 仍为 null，handleSend 会早退。
    await expect(
      page.locator('[data-testid^="conversation-item-"][data-active="true"]')
    ).toHaveCount(1, { timeout: 5_000 });
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');
    await textarea.fill(message);
    await sendButton.click();
    await expect(
      page.locator(`.message.user .content:has-text("${message}")`)
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('.message.assistant .content').last()).not.toBeEmpty({
      timeout: 5_000,
    });
    await expect(sendButton).toHaveText('发送', { timeout: 5_000 });
  }

  test('在 A 会话发送的消息不会出现在 B 会话', async ({ page }) => {
    test.setTimeout(15_000);

    // 1. 在 conv A（首次自动创建）发消息
    await sendAndWait(page, 'A 专属问题 alpha-001');

    // 2. 新建 conv B
    await page.locator('[data-testid="conversation-new"]').click();
    // 新建后会话列表有 2 项；激活的是新建的 B
    await expect(
      page.locator('[data-testid="conversation-list"] .conversation-item')
    ).toHaveCount(2, { timeout: 5_000 });

    // 3. conv B 当前应为空（不应看到 A 的消息）
    await expect(
      page.locator('.message.user .content:has-text("alpha-001")')
    ).toHaveCount(0);
    await expect(page.locator('.empty-state')).toBeVisible();

    // 4. 在 conv B 发消息
    await sendAndWait(page, 'B 专属问题 beta-001');

    // 5. 切回 conv A
    const convAButton = page
      .locator('.conversation-item-title:has-text("A 专属问题")')
      .first();
    await convAButton.click();
    // A 仍只有自己的消息；B 的消息不污染
    await expect(
      page.locator('.message.user .content:has-text("alpha-001")')
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.locator('.message.user .content:has-text("beta-001")')
    ).toHaveCount(0);
    await expect(
      page.locator('.message.assistant .content:has-text("reply-for-B")')
    ).toHaveCount(0);

    // 6. 再切回 conv B
    const convBButton = page
      .locator('.conversation-item-title:has-text("B 专属问题")')
      .first();
    await convBButton.click();
    await expect(
      page.locator('.message.user .content:has-text("beta-001")')
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.locator('.message.user .content:has-text("alpha-001")')
    ).toHaveCount(0);
  });

  test('缺失 conversation_id 应被 Pydantic 422 拒绝（不写入任何消息）', async ({ page }) => {
    // 直接用 page.request 发裸请求绕过前端 ChatPage，验证 Pydantic 拦截
    const res = await page.request.post('/api/chat/stream', {
      headers: { 'Content-Type': 'application/json' },
      data: JSON.stringify({ message: 'no conv id', document_ids: [] }),
      failOnStatusCode: false,
    });
    expect(res.status()).toBe(422);
  });

  test('不存在的 conversation_id 应返回 404（ask 端点）', async ({ page }) => {
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