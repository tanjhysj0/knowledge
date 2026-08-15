import { test, expect, type Page } from '@playwright/test';

/**
 * ChatPage 左侧会话栏交互 E2E（#35）。
 *
 * #52 新形态：会话只能由首页小说卡片创建（每会话绑定且仅绑定一本小说），
 * 页面不再提供「新建会话」按钮与文档选择器；无会话时不自动创建。
 *
 * 覆盖 acceptance criteria：
 *  1. 左侧 280px 会话栏可见，且无「新建」按钮。
 *  2. 无会话时不自动创建，显示引导空态、输入区禁用。
 *  3. 删除会话 → 列表移除；删空后回到引导空态（不自动建新会话）。
 *  4. 切换会话 → 消息区清空 + 切回原会话恢复历史。
 *
 * 用例串行 + 固定 client key（X-Client-Id）；会话经 API 直建/清理。
 * 涉及真实 chat 流时使用 page.route 拦截 /api/v1/chat/stream 返回受控 SSE，
 * 绕开 bge-m3 embedding 与真实 LLM 调用，确保测试在本地稳定运行。
 */

// 固定 client key：浏览器侧会话空间与 API 直建/清理保持一致。
const CLIENT_KEY = 'e2e-conv-sidebar-client';

test.describe('ChatPage 会话侧栏 - E2E (#35)', () => {
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(
      (key) => localStorage.setItem('docqa_client_id', key),
      CLIENT_KEY
    );
    await cleanupConversations(page);
    await page.goto('/chat');
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForSelector('[data-testid="conversation-sidebar"]');
  });

  /** 通过 API 清理该 client 的全部会话（404 视为 OK）。 */
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

  /** 经 API 为该 client 直建一条通用会话（绕过前端仅卡片建会话的限制）。 */
  async function createConversationViaApi(page: Page): Promise<void> {
    const res = await page.request.post('/api/conversations', {
      headers: { 'X-Client-Id': CLIENT_KEY },
      data: {},
    });
    expect(res.ok()).toBeTruthy();
  }

  /** 当前激活会话的 id（data-testid 后缀）。 */
  async function getActiveConvId(page: Page): Promise<string> {
    const testId = await page
      .locator('[data-testid^="conversation-item-"][data-active="true"]')
      .getAttribute('data-testid');
    expect(testId).toBeTruthy();
    return testId!.replace('conversation-item-', '');
  }

  test('应展示左侧 280px 会话栏且无新建按钮', async ({ page }) => {
    const sidebar = page.locator('[data-testid="conversation-sidebar"]');
    await expect(sidebar).toBeVisible();

    const width = await sidebar.evaluate((el) => el.getBoundingClientRect().width);
    // 280px 设计宽度；允许 1px 测量误差
    expect(Math.round(width)).toBe(280);

    // #52 新形态：不再提供「新建」按钮（会话只能从首页小说卡片创建）
    await expect(page.locator('[data-testid="conversation-new"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="conversation-list"]')).toBeVisible();
  });

  test('无会话时不自动创建，显示引导空态且输入区禁用', async ({ page }) => {
    // 清理后没有任何会话：不自动创建，展示引导空态
    await expect(page.locator('.empty-state')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('.empty-state')).toContainText('还没有会话');
    await expect(page.locator('[data-testid="empty-shelf-link"]')).toBeVisible();

    // 输入区禁用，需从首页选小说进入
    await expect(page.locator('textarea')).toBeDisabled();
    await expect(page.locator('button:has-text("发送")')).toBeDisabled();
  });

  test('删除会话应从列表移除；删空后回到引导空态', async ({ page }) => {
    await createConversationViaApi(page);
    await createConversationViaApi(page);
    await page.reload();
    await page.waitForSelector('[data-testid="conversation-sidebar"]');

    const items = page.locator('[data-testid^="conversation-item-"]');
    await expect(items).toHaveCount(2, { timeout: 5_000 });

    // 拦截 confirm 默认返回 true（删除确认）
    page.on('dialog', (dialog) => dialog.accept());

    // 删除当前激活的会话 → 自动切到剩余一条
    const firstId = await getActiveConvId(page);
    await page.locator(`[data-testid="conversation-delete-${firstId}"]`).click();
    await expect(items).toHaveCount(1, { timeout: 3_000 });

    // 再删仅剩的一条 → 回到引导空态（不自动建新会话）
    const lastId = await getActiveConvId(page);
    await page.locator(`[data-testid="conversation-delete-${lastId}"]`).click();
    await expect(items).toHaveCount(0, { timeout: 3_000 });
    await expect(page.locator('.empty-state')).toBeVisible();
    await expect(page.locator('textarea')).toBeDisabled();
  });

  test('切换会话应清空消息区并恢复目标会话历史', async ({ page }) => {
    await createConversationViaApi(page);
    await createConversationViaApi(page);
    await page.reload();
    await page.waitForSelector('[data-testid="conversation-sidebar"]');

    const items = page.locator('[data-testid^="conversation-item-"]');
    await expect(items).toHaveCount(2, { timeout: 5_000 });

    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 在激活会话 A 发一条带唯一标记的消息（真实后端 MockLLMProvider 应答并落库）
    const tagA = 'convA-' + Date.now();
    await textarea.fill(tagA);
    await sendButton.click();
    await expect(
      page.locator('.message.user .content').filter({ hasText: tagA })
    ).toBeVisible({ timeout: 3_000 });
    // 流结束：按钮文案回到 "发送"（此时后端已落库）
    await expect(sendButton).toHaveText('发送', { timeout: 5_000 });

    const convAId = await getActiveConvId(page);

    // 切到另一条会话 B（非激活项）
    const otherItem = page
      .locator(
        `[data-testid^="conversation-item-"]:not([data-testid="conversation-item-${convAId}"])`
      )
      .first();
    await otherItem.locator('.conversation-item-main').click();

    // 消息区应清空（不含 tagA）；A 不再激活
    await expect(
      page.locator('.message.user .content').filter({ hasText: tagA })
    ).toHaveCount(0, { timeout: 3_000 });
    await expect(page.locator('.empty-state')).toBeVisible({ timeout: 3_000 });
    await expect(
      page.locator(`[data-testid="conversation-item-${convAId}"]`)
    ).toHaveAttribute('data-active', 'false');

    // 切回 A：历史恢复
    await page
      .locator(`[data-testid="conversation-item-${convAId}"] .conversation-item-main`)
      .click();
    await expect(
      page.locator('.message.user .content').filter({ hasText: tagA })
    ).toBeVisible({ timeout: 3_000 });
    await expect(
      page.locator(`[data-testid="conversation-item-${convAId}"]`)
    ).toHaveAttribute('data-active', 'true');
  });
});
