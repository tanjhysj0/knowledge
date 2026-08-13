/**
 * #45：LLM 不可用 / 运行时失败 → 聊天页输入区上方红字 banner E2E。
 *
 * 三个场景：
 *  1. LLM 未配置（preflight 拒绝）→ banner + "去设置" 链接
 *  2. 运行时 LLM 失败（SSE 中间 error 事件）→ banner 无 "去设置" 链接
 *  3. 关闭 banner 按钮可隐藏 banner
 *
 * 全部使用 page.route 拦截 HTTP 响应，不修改真实 LLM 配置（settingsGuard
 * 不恢复 api_key，如果改真实设置会污染后续测试）。
 */
import { test, expect } from '@playwright/test';

test.describe('Chat Page - LLM 异常 Banner (Issue #45)', () => {
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    await page.goto('/chat');
    await page.waitForSelector('textarea');
    await expect(
      page.locator('[data-testid^="conversation-item-"][data-active="true"]')
    ).toHaveCount(1, { timeout: 5_000 });
  });

  test('LLM 未配置时应展示红字 banner 与去设置链接', async ({ page }) => {
    // 1. 拦截 /api/llm/status → 返回未配置
    await page.route('**/api/llm/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          provider: 'openai',
          configured: false,
          reason: 'OpenAI API Key 未配置',
        }),
      });
    });

    // 2. 拦截 /api/chat/stream → 返回 503 (preflight 拒绝)
    await page.route('**/api/chat/stream', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          reason: 'OpenAI API Key 未配置',
          error: 'LLM not configured',
        }),
      });
    });

    // 3. 重新加载让 ChatPage 拉取 /api/llm/status
    await page.reload();
    await page.waitForSelector('textarea');

    // 4. banner 应展示，含 "去设置" 链接
    const banner = page.locator('[data-testid="llm-error-banner"]');
    await expect(banner).toBeVisible({ timeout: 5_000 });
    await expect(banner).toContainText('OpenAI API Key 未配置');
    await expect(page.locator('[data-testid="llm-error-banner-link"]')).toBeVisible();
    await expect(page.locator('[data-testid="llm-error-banner-link"]')).toContainText('去设置');
    await expect(page.locator('[data-testid="llm-error-banner-close"]')).toBeVisible();

    // 5. 主动 send 让后端 503，验证用户消息被回滚（catch 块清理）
    const uniqueTag = 'unconfigured-' + Date.now();
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');
    await textarea.fill(uniqueTag);
    await sendButton.click();
    await expect(
      page.locator('.message.user').filter({ hasText: uniqueTag })
    ).toHaveCount(0, { timeout: 3_000 });
  });

  test('点击关闭按钮应隐藏 banner', async ({ page }) => {
    // 同样拦截 /api/llm/status 让 banner 出现
    await page.route('**/api/llm/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          provider: 'openai',
          configured: false,
          reason: 'OpenAI API Key 未配置',
        }),
      });
    });

    await page.reload();
    await page.waitForSelector('textarea');

    const banner = page.locator('[data-testid="llm-error-banner"]');
    await expect(banner).toBeVisible({ timeout: 5_000 });

    await page.locator('[data-testid="llm-error-banner-close"]').click();
    await expect(banner).not.toBeVisible();
  });

  test('运行时 LLM 失败（中间 SSE error 事件）应触发 banner（无去设置链接）', async ({ page }) => {
    // 中间 SSE 失败：先发 message 让前端不显示 banner（preflight 通过），
    // 然后 error 事件让前端触发 banner。
    await page.route('**/api/chat/stream', async (route) => {
      const body =
        `event: error\ndata: {"error":"Mock LLM unavailable"}\n\n` +
        `event: done\ndata: {"sources":[]}\n\n`;
      await route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
        },
        body,
      });
    });

    const uniqueTag = 'llm-error-' + Date.now();
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');
    await textarea.fill(uniqueTag);
    await sendButton.click();

    const banner = page.locator('[data-testid="llm-error-banner"]');
    await expect(banner).toBeVisible({ timeout: 5_000 });
    await expect(banner).toContainText('Mock LLM unavailable');
    // 运行时失败：无 "去设置" 链接
    await expect(page.locator('[data-testid="llm-error-banner-link"]')).toHaveCount(0);

    // B2 / #45 catch 块需清掉本轮 user + assistant 占位，聊天区不应残留任何气泡。
    await expect(
      page.locator('.message.user').filter({ hasText: uniqueTag })
    ).toHaveCount(0, { timeout: 2_000 });
    await expect(page.locator('.message.assistant')).toHaveCount(0, { timeout: 2_000 });
  });
});
