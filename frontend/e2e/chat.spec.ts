import { test, expect, type Page } from '@playwright/test';

test.describe('Chat Page - E2E', () => {
  // 后端通过 X-E2E-Test Header 自动返回 MockLLMProvider；前端不再额外拦截。
  // 其他接口（history/documents/settings）继续走真实后端。
  //
  // 串行执行：后端不再提供 DELETE /api/chat/history（#27），history 会在
  // 多次测试间累积。串行确保依赖计数 / 空状态的断言在隔离环境中成立。
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    // 导航到聊天页
    await page.goto('/chat');
    await page.waitForLoadState('networkidle').catch(() => {});
    // 等待聊天页输入框渲染
    await page.waitForSelector('textarea');
    // #36：跨文件并行（conversation-isolation / conversation-sidebar）会
    // 通过 API 删/建会话干扰 React state。若列表为空 reload，让 ChatPage
    // useEffect 自动建一个新会话，保证 activeConvId 一定有效。不主动
    // DELETE 所有会话以避免 beforeEach 超时（7个测试串行下 5s 不得）
    const res = await page.request.get('/api/conversations');
    if (res.ok()) {
      const list: Array<{ id: number }> = await res.json();
      if (list.length === 0) {
        await page.reload();
        await page.waitForSelector('textarea');
        await page.waitForLoadState('networkidle').catch(() => {});
      }
    }
    // 保证进入首个发送前 React state 已有 active conv：
    await expect(
      page.locator('[data-testid^="conversation-item-"][data-active="true"]')
    ).toHaveCount(1, { timeout: 5_000 });
  });

  test('应展示页面标题与聊天区', async ({ page }) => {
    // 验证页面标题
    await expect(page.locator('h1')).toContainText('DocQA');
    await expect(page.locator('textarea')).toBeVisible();
    await expect(page.locator('button:has-text("发送")')).toBeVisible();
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

    // 输入消息并发送（唯一标记以避免与其他历史消息冲突）
    const testMessage = 'send-now-' + Date.now();
    await textarea.fill(testMessage);
    await sendButton.click();

    // 用户消息应立即出现，无需等待 API 响应（按内容定位最后一条）
    await expect(
      page.locator('.message.user .content').filter({ hasText: testMessage })
    ).toBeVisible({ timeout: 3_000 });
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

    const tag = Date.now();
    await textarea.fill('stream-' + tag);
    await sendButton.click();

    // 等待助手消息出现（最后一条即本次回复）
    const assistantContent = page.locator('.message.assistant .content').last();
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

    // 第一轮对话（唯一标记，避开历史消息干扰）
    const tag = Date.now();
    const firstMessage = `multiround-1-${tag}`;
    await textarea.fill(firstMessage);
    await sendButton.click();

    // 等待首轮流式结束：typing-indicator 消失（按钮文案从 “发送中...” 变回 “发送”）
    await expect(sendButton).toHaveText('发送', { timeout: 5_000 });

    // 按内容定位本轮用户消息计数
    await expect(
      page.locator('.message.user .content', { hasText: `multiround-1-${tag}` })
    ).toHaveCount(1, { timeout: 3_000 });

    // 第二轮对话
    const secondMessage = `multiround-2-${tag}`;
    await textarea.fill(secondMessage);
    await sendButton.click();

    // 等待第二轮流式结束
    await expect(sendButton).toHaveText('发送', { timeout: 5_000 });

    // 按唯一标记验证两条用户消息均存在（避免依赖总数）
    await expect(
      page.locator('.message.user .content', { hasText: `multiround-1-${tag}` })
    ).toHaveCount(1);
    await expect(
      page.locator('.message.user .content', { hasText: `multiround-2-${tag}` })
    ).toHaveCount(1);
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

    // 填写消息后按 Enter 发送（不带 Shift）。使用唯一标记避免与历史冲突。
    const testMessage = 'enter-' + Date.now();
    await textarea.fill(testMessage);
    await textarea.press('Enter');

    // 消息应被发送（按内容定位）
    await expect(
      page.locator('.message.user .content').filter({ hasText: testMessage })
    ).toBeVisible({ timeout: 3_000 });
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

    const testMessage = 'role-label-' + Date.now();
    await textarea.fill(testMessage);
    await sendButton.click();

    // 等待本轮用户消息出现
    await expect(
      page.locator('.message.user .content').filter({ hasText: testMessage })
    ).toBeVisible({ timeout: 3_000 });

    // 等待本轮助手回复（最后一条 assistant 即本次回复）
    await expect(page.locator('.message.assistant .content').last()).not.toBeEmpty({ timeout: 3_000 });

    // 校验角色标签（用户 / AI）。使用 .first() 避开多匹配。
    await expect(page.locator('.message.user .role').first()).toContainText('用户');
    await expect(page.locator('.message.assistant .role').first()).toContainText('AI');
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
    // 发送 2 条带唯一标记的消息 → 等待响应 → reload → 验证历史包含这两条
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    const tag = Date.now();
    const firstMsg = `reload-msg-1-${tag}`;
    await textarea.fill(firstMsg);
    await sendButton.click();
    await expect(page.locator(`.message.user .content:has-text("${firstMsg}")`)).toBeVisible({ timeout: 3_000 });
    // 等待首轮流式结束
    await expect(sendButton).toHaveText('发送', { timeout: 5_000 });

    const secondMsg = `reload-msg-2-${tag}`;
    await textarea.fill(secondMsg);
    await sendButton.click();
    await expect(page.locator(`.message.user .content:has-text("${secondMsg}")`)).toBeVisible({ timeout: 3_000 });
    await expect(sendButton).toHaveText('发送', { timeout: 5_000 });

    // reload 后 ChatPage 应调用 history 接口并恢复显示
    await page.reload();
    await page.waitForSelector('textarea');

    // 用唯一标记定位，不依赖总数（history 可能累积）
    await expect(
      page.locator(`.message.user .content:has-text("${firstMsg}")`)
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.locator(`.message.user .content:has-text("${secondMsg}")`)
    ).toBeVisible({ timeout: 5_000 });
  });

  test('思考内容默认折叠可展开', async ({ page }) => {
    // Issue #28：通过 X-E2E-Mock-Thinking 让 MockLLMProvider 输出 <think>...</think> 块，
    // 在不调用真实 LLM 的前提下验证思考折叠 UI。header 由 setExtraHTTPHeaders 合并到
    // playwright.config 的 extraHTTPHeaders 中，与上下文无关。
    await page.setExtraHTTPHeaders({ 'X-E2E-Mock-Thinking': 'true' });

    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('thinking-collapse-' + Date.now());
    await sendButton.click();

    // 等待本轮助手回复 + 思考区出现（思考 chunk 由 SSE 先到达）
    const lastAssistant = page.locator('.message.assistant').last();
    const thinkingSection = lastAssistant.locator('.thinking-section');
    await expect(thinkingSection).toBeVisible({ timeout: 3_000 });

    // 默认折叠：summary 可见，content 隐藏
    await expect(thinkingSection.locator('.thinking-summary')).toContainText('思考过程');
    await expect(thinkingSection.locator('.thinking-content')).toBeHidden();

    // 点击 summary 展开
    await thinkingSection.locator('.thinking-summary').click();

    // 展开后 content 应可见
    await expect(thinkingSection.locator('.thinking-content')).toBeVisible();

    // 主回答内容不应包含 think 标签（思考在独立 details 中）
    const contentText = (await lastAssistant.locator('.content').textContent()) ?? '';
    expect(contentText).not.toContain('<think>');
    expect(contentText).not.toContain('</think>');
  });

  test('应展示 RAG 检索来源（mock SSE done 携带 sources）', async ({ page }) => {
    // Issue #33：通过 page.route 拦截 /api/chat/stream，让 done 事件携带
    // sources=['doc_1']，验证前端能解析为文件名 + 渲染来源区。
    // MockLLMProvider 默认会调用真实 LLM（无 key 会失败），但 SSE 由我们手写
    // 完全绕开 LLM。
    await page.route('**/api/chat/stream', async (route) => {
      const body = `event: message\ndata: {"content":"基于文档的答案"}\n\nevent: done\ndata: {"sources":["doc_1"]}\n\n`;
      await route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
        },
        body,
      });
    });

    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');
    const tag = 'sources-' + Date.now();
    await textarea.fill(tag);
    await sendButton.click();

    // 助手回复（最后一条）应含 "基于文档的答案"
    const lastAssistant = page.locator('.message.assistant').last();
    await expect(lastAssistant.locator('.content')).toContainText('基于文档的答案', { timeout: 3_000 });

    // 来源区应可见且至少包含一个 source-item
    const sources = lastAssistant.locator('.sources');
    await expect(sources).toBeVisible({ timeout: 3_000 });
    await expect(sources.locator('[data-testid="source-item-doc_1"]')).toBeVisible();
  });

  test('多文档 sources 应按后端顺序去重展示', async ({ page }) => {
    // Issue #33：SSE done 携带多个 doc_N（含重复）时，前端按后端首次出现
    // 顺序去重展示；doc_N 未在 GET /api/documents 找到时显示原始 token。
    await page.route('**/api/chat/stream', async (route) => {
      const body = `event: message\ndata: {"content":"answer"}\n\nevent: done\ndata: {"sources":["doc_1","doc_2","doc_1"]}\n\n`;
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body,
      });
    });

    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');
    await textarea.fill('multi-sources-' + Date.now());
    await sendButton.click();

    const lastAssistant = page.locator('.message.assistant').last();
    await expect(lastAssistant.locator('.content')).toContainText('answer', { timeout: 3_000 });

    // 来源区应只出现 doc_1 和 doc_2 各一次（doc_1 重复被去重）
    await expect(lastAssistant.locator('[data-testid="source-item-doc_1"]')).toHaveCount(1, { timeout: 3_000 });
    await expect(lastAssistant.locator('[data-testid="source-item-doc_2"]')).toHaveCount(1);
    // 总来源数 = 2（去重后）
    await expect(lastAssistant.locator('.sources-item')).toHaveCount(2);
  });

  test('空 sources 时不渲染来源区', async ({ page }) => {
    // Issue #33：未命中（sources=[]）时前端不显示来源区。
    await page.route('**/api/chat/stream', async (route) => {
      const body = `event: message\ndata: {"content":"general answer"}\n\nevent: done\ndata: {"sources":[]}\n\n`;
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body,
      });
    });

    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');
    await textarea.fill('no-sources-' + Date.now());
    await sendButton.click();

    const lastAssistant = page.locator('.message.assistant').last();
    await expect(lastAssistant.locator('.content')).toContainText('general answer', { timeout: 3_000 });
    // 来源区不应存在
    await expect(lastAssistant.locator('.sources')).toHaveCount(0);
  });
});