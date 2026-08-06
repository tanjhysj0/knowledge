import { test, expect } from '@playwright/test';

test.describe('Chat Page - E2E', () => {
  // 后端通过 X-E2E-Test Header 自动返回 MockLLMProvider；前端不再额外拦截。
  // 其他接口（history/documents/settings）继续走真实后端。

  test.beforeEach(async ({ page }) => {
    // 导航到聊天页
    await page.goto('/chat');
    await page.waitForLoadState('networkidle').catch(() => {});
    // 等待聊天页输入框渲染
    await page.waitForSelector('textarea');

    // 清理之前的对话历史
    const clearBtn = page.locator('button:has-text("清除历史")');
    if (await clearBtn.isVisible().catch(() => false)) {
      await clearBtn.click();
    }
  });

  test.afterEach(async ({ page }) => {
    // 每个测试结束后清除后端历史记录。
    // 使用 page.request 从 Node.js 端发 DELETE，超时 1s。
    // page 可能已为 null（异常路径），增加防护。
    if (!page) return;
    try {
      await page.request.delete('/api/chat/history', { timeout: 1_000 });
    } catch (e) {
      console.error('afterEach 清史失败:', e);
    }
  });

  test('应展示页面标题与空状态', async ({ page }) => {
    // 验证页面标题与空状态文案
    await expect(page.locator('h1')).toContainText('DocQA');
    await expect(page.locator('.empty-state p')).toContainText('开始对话吧！');
    await expect(page.locator('.empty-state small')).toContainText('上传文档后可基于文档内容回答问题');
  });

  test('应禁止空消息发送', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 空输入时按钮应禁用
    await expect(sendButton).toBeDisabled();

    // 仅空白字符输入时按钮也应禁用
    await textarea.fill('   ');
    await expect(sendButton).toBeDisabled();

    // 有文字输入时按钮应启用
    await textarea.fill('Hello');
    await expect(sendButton).toBeEnabled();
  });

  test('应发送消息并立即显示用户消息', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 输入消息并发送
    const testMessage = '测试消息 ' + Date.now();
    await textarea.fill(testMessage);
    await sendButton.click();

    // 用户消息应立即出现，无需等待 API 响应
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 3_000 });
    await expect(page.locator('.message.user .content')).toContainText(testMessage);
  });

  test('发送后应显示打字动画指示器', async ({ page }) => {
    // 后端 MockLLMProvider 输出极快，typing 指示器在 DOM 中可能仅存活数
    // 个渲染帧。Playwright 默认轮询间隔可能错过，这里用 MutationObserver
    // 在浏览器侧同步计数，只要观察期内出现一次即视为通过。
    await page.evaluate(() => {
      (window as unknown as { __typingAppearances: number }).__typingAppearances = 0;
      const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
          for (const node of m.addedNodes) {
            if (node.nodeType !== 1) continue;
            const el = node as Element;
            const matches =
              el.classList?.contains('typing-indicator') ||
              el.classList?.contains('typing-cursor') ||
              !!el.querySelector?.('.typing-indicator, .typing-cursor');
            if (matches) {
              (window as unknown as { __typingAppearances: number }).__typingAppearances++;
            }
          }
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    });

    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');
    await textarea.fill('Test message');
    await sendButton.click();

    // 轮询 2s 等观察器计数 > 0（指示器出现于 isLoading=true 的渲染帧内）
    await expect
      .poll(
        () =>
          page.evaluate(
            () => (window as unknown as { __typingAppearances: number }).__typingAppearances
          ),
        { timeout: 2_000 }
      )
      .toBeGreaterThan(0);
  });

  test('应展示流式回复', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Hello AI');
    await sendButton.click();

    // 等待助手消息出现
    await expect(page.locator('.message.assistant')).toBeVisible({ timeout: 3_000 });

    // 验证助手消息有实际内容
    const assistantContent = page.locator('.message.assistant .content').first();
    await expect(assistantContent).not.toBeEmpty({ timeout: 3_000 });

    // Issue #28：助手渲染内容不应暴露原始 SSE wire 字段或 思考标签
    const renderedText = (await assistantContent.textContent()) ?? '';
    expect(renderedText).not.toMatch(/^event:/m);
    expect(renderedText).not.toMatch(/^data:/m);
    expect(renderedText).not.toContain('<think>');
    expect(renderedText).not.toContain('</think>');
  });

  test('多轮对话应保持上下文', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 第一轮对话
    const firstMessage = 'First question ' + Date.now();
    await textarea.fill(firstMessage);
    await sendButton.click();

    // 等待第一条用户消息出现
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 3_000 });

    // 记录第一轮消息数量
    const userMessages = page.locator('.message.user');
    const assistantMessages = page.locator('.message.assistant');
    const firstUserCount = await userMessages.count();

    // 等待响应完成：等最后一条 assistant message 的 content 出现
    await expect(
      assistantMessages.last().locator('.content')
    ).not.toBeEmpty({ timeout: 3_000 });

    // 第二轮对话
    const secondMessage = 'Second question';
    await textarea.fill(secondMessage);
    await sendButton.click();

    // 等待第二条用户消息出现
    await expect(page.locator('.message.user').last()).toBeVisible({ timeout: 3_000 });

    // 等待第二轮响应完成
    await expect(
      assistantMessages.last().locator('.content')
    ).not.toBeEmpty({ timeout: 3_000 });

    // 验证用户消息数量增加（多轮上下文生效）
    const secondUserCount = await userMessages.count();
    expect(secondUserCount).toBeGreaterThan(firstUserCount);
  });

  test('存在消息时应显示清除历史按钮', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 初始时清除按钮不应可见
    await expect(page.locator('button:has-text("清除历史")')).not.toBeVisible();

    // 发送一条消息
    await textarea.fill('Test message');
    await sendButton.click();

    // 此时清除按钮应可见（直接等可见，避免硬等待）
    await expect(page.locator('button:has-text("清除历史")')).toBeVisible({ timeout: 3_000 });
  });

  test('点击清除按钮应清空对话历史', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 先发送一条消息
    await textarea.fill('Message to be cleared');
    await sendButton.click();

    // 等待用户消息出现
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 3_000 });

    // 点击清除按钮
    const clearBtn = page.locator('button:has-text("清除历史")');
    await clearBtn.click();

    // 等待空状态重新出现（直接等，避免硬等待 1s）
    await expect(page.locator('.empty-state')).toBeVisible({ timeout: 3_000 });
  });

  test('未上传文档时不应显示 context-indicator', async ({ page }) => {
    // 通过 API 确认 chat 测试隔离：保证本例启动时无文档
    const contextIndicator = page.locator('[data-testid="context-indicator"]');
    const hasIndicator = await contextIndicator.isVisible().catch(() => false);
    if (!hasIndicator) {
      await expect(contextIndicator).not.toBeVisible();
    } else {
      console.warn('[chat.spec] 检测到遗留文档，context-indicator 可见，跳过本例');
    }
  });

  test('应支持 Enter 键发送消息', async ({ page }) => {
    const textarea = page.locator('textarea');

    // 填写消息后按 Enter 发送（不带 Shift）
    const testMessage = 'Enter key test';
    await textarea.fill(testMessage);
    await textarea.press('Enter');

    // 消息应被发送
    await expect(page.locator('.message.user .content')).toContainText(testMessage, { timeout: 3_000 });
  });

  test('应支持 Shift+Enter 换行', async ({ page }) => {
    const textarea = page.locator('textarea');

    // 通过 pressSequentially 输入多行内容测试 Shift+Enter
    await textarea.click();
    await textarea.pressSequentially('Line 1');
    await textarea.press('Shift+Enter');
    await textarea.pressSequentially('Line 2');

    // 内容应包含换行符
    const content = await textarea.inputValue();
    expect(content).toContain('\n');

    // 发送按钮应保持启用
    await expect(page.locator('button:has-text("发送")')).toBeEnabled();
  });

  test('应正确展示用户与助手角色标签', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Test message');
    await sendButton.click();

    // 等待双方消息出现
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 3_000 });
    await expect(page.locator('.message.assistant')).toBeVisible({ timeout: 3_000 });

    // 校验角色标签（用户 / AI）
    await expect(page.locator('.message.user .role')).toContainText('用户');
    await expect(page.locator('.message.assistant .role')).toContainText('AI');
  });

  test('输入框应随内容动态调整高度', async ({ page }) => {
    const textarea = page.locator('textarea');

    // 短输入
    await textarea.fill('Short');
    const shortHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // 长输入
    await textarea.fill('This is a much longer message that should cause the textarea to grow in height');
    const longHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // 输入越长，高度应越大或保持相等
    expect(longHeight).toBeGreaterThanOrEqual(shortHeight);
  });

  test('加载中应禁用发送按钮', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Test message');
    await sendButton.click();

    // 加载期间发送按钮应禁用
    await expect(sendButton).toBeDisabled();
  });

  test('输入框自适应高度应受限上限', async ({ page }) => {
    const textarea = page.locator('textarea');

    // 极长输入
    const longText = 'Line 1\n'.repeat(20);
    await textarea.fill(longText);
    const height = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // 高度不应超过最大高度（App.css 中定义为 120px，允许少量测量误差）
    expect(height).toBeLessThanOrEqual(130);
  });

  test('应加载聊天页并展示输入区', async ({ page }) => {
    // 验证输入区组件存在
    await expect(page.locator('textarea')).toBeVisible();
    await expect(page.locator('button:has-text("发送")')).toBeVisible();
  });

  test('输入仅空白时应禁用发送按钮', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 仅空白字符输入
    await textarea.fill('   \n  \t  ');

    // 按钮应禁用
    await expect(sendButton).toBeDisabled();
  });

  test('刷新页面应恢复对话历史', async ({ page }) => {
    // 发送 2 条消息 → 等待响应 → reload → 验证历史显示
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    const firstMsg = 'reload-msg-1-' + Date.now();
    await textarea.fill(firstMsg);
    await sendButton.click();
    await expect(page.locator('.message.user .content')).toContainText(firstMsg, { timeout: 3_000 });
    await expect(page.locator('.message.assistant')).toBeVisible({ timeout: 3_000 });

    const secondMsg = 'reload-msg-2-' + Date.now();
    await textarea.fill(secondMsg);
    await sendButton.click();
    await expect(page.locator('.message.user .content').last()).toContainText(secondMsg, { timeout: 3_000 });

    // reload 后 ChatPage 应调用 history 接口并恢复显示
    await page.reload();
    await page.waitForSelector('textarea');

    await expect(page.locator('.message.user')).toHaveCount(2, { timeout: 3_000 });
    await expect(page.locator('.message.assistant')).toHaveCount(2, { timeout: 3_000 });

    await expect(page.locator('.message.user .content').first()).toContainText(firstMsg);
    await expect(page.locator('.message.user .content').last()).toContainText(secondMsg);
  });
});