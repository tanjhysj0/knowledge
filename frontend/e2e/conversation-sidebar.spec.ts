import { test, expect, type Page } from '@playwright/test';

/**
 * ChatPage 左侧会话栏交互 E2E（#35）。
 *
 * 覆盖 acceptance criteria：
 *  1. 左侧 280px 会话栏可见。
 *  2. 新建会话 → 列表新增项 + 自动激活。
 *  3. 删除会话 → 列表移除。
 *  4. 切换会话 → 新建会话消息区清空 + 切回原会话恢复历史。
 *  5. 首条用户消息发送成功后该会话标题自动改为消息前 20 字摘要。
 *
 * 用例串行 + 用例间自清理（DELETE /api/conversations/*）。
 * 涉及真实 chat 流时使用 page.route 拦截 /api/chat/stream 返回受控 SSE，
 * 绕开 bge-m3 embedding 与真实 LLM 调用，确保测试在本地稳定运行。
 */
test.describe('ChatPage 会话侧栏 - E2E (#35)', () => {
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForSelector('[data-testid="conversation-sidebar"]');
  });

  /**
   * 通过 API 自清理：删除所有会话（404 视为 OK）。
   * 必须在 page.goto 之后调用，因为需要同一个 origin 的 request 上下文。
   */
  async function cleanupConversations(page: Page) {
    const res = await page.request.get('/api/conversations');
    if (!res.ok()) return;
    const list: Array<{ id: number }> = await res.json();
    for (const c of list) {
      await page.request.delete(`/api/conversations/${c.id}`).catch(() => {});
    }
  }

  /**
   * 用 page.route 拦截 /api/chat/stream，立即返回受控 SSE done 帧，
   * 绕开 bge-m3 embedding 加载（本地首次加载 > 10s）。
   */
  async function mockChatStream(page: Page, assistantContent = 'mocked reply') {
    await page.route('**/api/chat/stream', async (route) => {
      const body =
        `event: message\ndata: ${JSON.stringify({ content: assistantContent })}\n\n` +
        `event: done\ndata: ${JSON.stringify({ sources: [] })}\n\n`;
      await route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
        },
        body,
      });
    });
  }

  test.afterAll(async ({ browser }) => {
    // 用一次性 context 调 API 清空，避免污染下一个 spec
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    try {
      const res = await page.request.get('http://127.0.0.1:5173/api/conversations');
      if (res.ok()) {
        const list: Array<{ id: number }> = await res.json();
        for (const c of list) {
          await page.request
            .delete(`http://127.0.0.1:5173/api/conversations/${c.id}`)
            .catch(() => {});
        }
      }
    } finally {
      await ctx.close();
    }
  });

  test('应展示左侧 280px 会话栏', async ({ page }) => {
    const sidebar = page.locator('[data-testid="conversation-sidebar"]');
    await expect(sidebar).toBeVisible();

    const width = await sidebar.evaluate((el) => el.getBoundingClientRect().width);
    // 280px 设计宽度；允许 1px 测量误差
    expect(Math.round(width)).toBe(280);

    // 新建按钮与列表可见
    await expect(page.locator('[data-testid="conversation-new"]')).toBeVisible();
    await expect(page.locator('[data-testid="conversation-list"]')).toBeVisible();
  });

  test('新建会话应在列表新增项并自动激活', async ({ page }) => {
    // 进入页面前先清空，保证 count 断言可控
    await cleanupConversations(page);
    await page.reload();
    await page.waitForSelector('[data-testid="conversation-sidebar"]');

    const newBtn = page.locator('[data-testid="conversation-new"]');
    const items = page.locator('[data-testid^="conversation-item-"]');

    // 等自动建出来的初始会话出现（useEffect 异步）
    await expect(items).toHaveCount(1, { timeout: 5_000 });

    await newBtn.click();

    // 应新增一条并自动激活
    await expect(items).toHaveCount(2, { timeout: 3_000 });
  });

  test('删除会话应从列表移除', async ({ page }) => {
    await cleanupConversations(page);
    await page.reload();
    await page.waitForSelector('[data-testid="conversation-sidebar"]');

    const newBtn = page.locator('[data-testid="conversation-new"]');
    const items = page.locator('[data-testid^="conversation-item-"]');

    // 等初始会话出现
    await expect(items).toHaveCount(1, { timeout: 5_000 });
    await newBtn.click();
    await newBtn.click();
    // 等到出现第 3 条（初始 1 + 新建 2）
    await expect(items).toHaveCount(3, { timeout: 5_000 });

    // 拦截 confirm 默认返回 true（删除确认）
    page.on('dialog', (dialog) => dialog.accept());

    const lastItem = items.last();
    const lastId = (await lastItem.getAttribute('data-testid'))?.replace(
      'conversation-item-',
      ''
    );
    expect(lastId).toBeTruthy();

    // 点击最后一项的删除按钮
    await page.locator(`[data-testid="conversation-delete-${lastId}"]`).click();

    // 列表减 1
    await expect(items).toHaveCount(2, { timeout: 3_000 });

    // 被删的那条不再存在
    await expect(
      page.locator(`[data-testid="conversation-item-${lastId}"]`)
    ).toHaveCount(0);
  });

  test('切换会话应清空消息区并恢复空状态', async ({ page }) => {
    await cleanupConversations(page);
    await page.reload();
    await page.waitForSelector('[data-testid="conversation-sidebar"]');

    // 拦截 SSE：让前端立即拿到回复，避免 bge-m3 加载
    await mockChatStream(page, 'reply-A');

    const newBtn = page.locator('[data-testid="conversation-new"]');
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 在初始会话 A 发一条带唯一标记的消息（mock 流立即 done）
    const tagA = 'convA-' + Date.now();
    await textarea.fill(tagA);
    await sendButton.click();
    await expect(
      page.locator('.message.user .content').filter({ hasText: tagA })
    ).toBeVisible({ timeout: 3_000 });
    // 流结束：按钮文案回到 "发送"
    await expect(sendButton).toHaveText('发送', { timeout: 5_000 });

    // 记录 A 的 id（A 是激活态的）
    const activeId = await page
      .locator('[data-testid^="conversation-item-"][data-active="true"]')
      .getAttribute('data-testid');
    const convAId = activeId?.replace('conversation-item-', '');
    expect(convAId).toBeTruthy();

    // 新建会话 B
    await newBtn.click();
    // 消息区应清空（不含 tagA）；empty-state 应出现
    await expect(
      page.locator('.message.user .content').filter({ hasText: tagA })
    ).toHaveCount(0, { timeout: 3_000 });
    await expect(page.locator('.empty-state')).toBeVisible({ timeout: 3_000 });
    // 激活态应切换到 B（不是 A）
    await expect(
      page.locator(`[data-testid="conversation-item-${convAId}"]`)
    ).toHaveAttribute('data-active', 'false');
  });

  test('首条用户消息成功后该会话标题自动改为消息前 20 字摘要', async ({ page }) => {
    await cleanupConversations(page);
    await page.reload();
    await page.waitForSelector('[data-testid="conversation-sidebar"]');

    // 拦截 SSE：让前端立即拿到回复
    await mockChatStream(page, 'summary reply');

    // 取当前激活会话的初始 id（默认 "新对话"）
    const activeId = await page
      .locator('[data-testid^="conversation-item-"][data-active="true"]')
      .getAttribute('data-testid');
    const convId = activeId?.replace('conversation-item-', '');
    expect(convId).toBeTruthy();

    const item = page.locator(`[data-testid="conversation-item-${convId}"]`);
    await expect(item.locator('.conversation-item-title')).toHaveText('新对话', {
      timeout: 3_000,
    });

    // 发一条超过 20 字的消息
    const longText = '关于分布式系统下的幂等性设计模式的深度讨论与实践';
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');
    await textarea.fill(longText);
    await sendButton.click();
    await expect(
      page.locator('.message.user .content').filter({ hasText: longText })
    ).toBeVisible({ timeout: 3_000 });
    // 流结束
    await expect(sendButton).toHaveText('发送', { timeout: 5_000 });

    // 标题应自动更新为前 20 字 + …
    const expectedTitle = longText.slice(0, 20) + '…';
    await expect(item.locator('.conversation-item-title')).toHaveText(expectedTitle, {
      timeout: 3_000,
    });
  });
});
