import { test, expect } from '@playwright/test';

// 直接对后端 API 做契约断言。/api/chat 与 /api/chat/stream 走真实后端，
// 后端依靠 X-E2E-Test Header 自动返回 MockLLMProvider，不再额外 mock。
//
// 由于 `page.route` 只能拦截浏览器发出的请求（不能拦截 page.request 从 Node.js
// 发出的请求），所以 LLM 对话类契约测试需要从浏览器端发起 fetch，才能被 mock 命中。
test.describe('Backend API 契约 - E2E', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  // 从浏览器端发起请求，从而能让后端 MockLLMProvider 命中（X-E2E-Test Header
  // 由 playwright.config 的 extraHTTPHeaders 注入）。
  async function postChatFromBrowser(page: any, message: string): Promise<any> {
    return await page.evaluate(async (msg: string) => {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, document_ids: [] }),
      });
      return { status: res.status, body: await res.json() };
    }, message);
  }

  test('POST /api/chat 应返回 ChatResponse 结构', async ({ page }) => {
    const { status, body } = await postChatFromBrowser(page, 'Hello API');
    expect(status).toBe(200);
    expect(body).toHaveProperty('message');
    expect(typeof body.message).toBe('string');
    expect(body).toHaveProperty('sources');
    expect(Array.isArray(body.sources)).toBeTruthy();
  });

  test('GET /api/chat/history 应返回消息数组', async ({ page }) => {
    await postChatFromBrowser(page, 'history seed ' + Date.now());

    const res = await page.request.get('/api/chat/history');
    expect(res.ok()).toBeTruthy();
    const history = await res.json();
    expect(Array.isArray(history)).toBeTruthy();

    for (const m of history) {
      expect(m).toHaveProperty('id');
      expect(m).toHaveProperty('role');
      expect(m).toHaveProperty('content');
      expect(m).toHaveProperty('created_at');
    }
  });

  test('GET /api/documents 应返回分页结构', async ({ page }) => {
    const res = await page.request.get('/api/documents');
    expect(res.ok()).toBeTruthy();
    const body = await res.json();

    expect(body).toHaveProperty('items');
    expect(Array.isArray(body.items)).toBeTruthy();
    expect(body).toHaveProperty('total');
    expect(body).toHaveProperty('page');
    expect(body).toHaveProperty('page_size');
  });

  test('GET /api/settings 应返回嵌套的 llm 配置', async ({ page }) => {
    const res = await page.request.get('/api/settings');
    expect(res.ok()).toBeTruthy();
    const body = await res.json();

    expect(body).toHaveProperty('llm');
    expect(body.llm).toHaveProperty('provider');
    expect(body.llm).toHaveProperty('base_url');
    expect(body.llm).toHaveProperty('model');
  });

  test('PUT /api/settings 应持久化新配置', async ({ page }) => {
    const original = await (await page.request.get('/api/settings')).json();
    const newModel = original.llm.model + '-apitest-' + Date.now();

    const putRes = await page.request.put('/api/settings', {
      data: { llm_model: newModel },
    });
    expect(putRes.ok()).toBeTruthy();

    const reloaded = await (await page.request.get('/api/settings')).json();
    expect(reloaded.llm.model).toBe(newModel);

    await page.request.put('/api/settings', { data: { llm_model: original.llm.model } });
  });

  test('DELETE /api/documents/{id} 不存在时应返回 404', async ({ page }) => {
    const res = await page.request.delete('/api/documents/999999');
    expect(res.status()).toBe(404);
  });
});