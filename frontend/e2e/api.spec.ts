import { test, expect } from '@playwright/test';
import { installNonStreamChatMock, DEFAULT_CHAT_ANSWER } from './helpers/chatMock';

// 直接对后端 API 做契约断言。/api/chat 这个 LLM 对话接口用 mock 提速。
// 其他接口走真实后端。
//
// 由于 `page.route` 只能拦截浏览器发出的请求（不能拦截 page.request 从 Node.js
// 发出的请求），所以 LLM 对话类契约测试需要从浏览器端发起 fetch，才能被 mock 拦截。
test.describe('Backend API 契约 - E2E', () => {
  test.beforeEach(async ({ page }) => {
    // 仅拦截真正与 LLM 对话的接口；/api/chat/history 与
    // /api/chat 删除动作仍走真实后端。
    await installNonStreamChatMock(page, { answer: DEFAULT_CHAT_ANSWER });
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  // 从浏览器端发起请求，从而能被 page.route 拦截的 mock 命中。
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
    // 非流式接口应返回 message + sources 字段
    const { status, body } = await postChatFromBrowser(page, 'Hello API');
    expect(status).toBe(200);
    expect(body).toHaveProperty('message');
    expect(typeof body.message).toBe('string');
    expect(body).toHaveProperty('sources');
    expect(Array.isArray(body.sources)).toBeTruthy();
  });

  test('GET /api/chat/history 应返回消息数组', async ({ page }) => {
    // 通过 mock 写一条消息 → 断言 history 接口返回数组结构
    await postChatFromBrowser(page, 'history seed ' + Date.now());

    const res = await page.request.get('/api/chat/history');
    expect(res.ok()).toBeTruthy();
    const history = await res.json();
    expect(Array.isArray(history)).toBeTruthy();

    // 每条消息都应有 id/role/content/created_at 字段（mock 下可能为空数组）
    for (const m of history) {
      expect(m).toHaveProperty('id');
      expect(m).toHaveProperty('role');
      expect(m).toHaveProperty('content');
      expect(m).toHaveProperty('created_at');
    }
  });

  test('DELETE /api/chat/history 应清空所有历史', async ({ page }) => {
    // 清空（mock 下数据库为空，但接口仍应返回成功）
    const delRes = await page.request.delete('/api/chat/history');
    expect(delRes.ok()).toBeTruthy();

    // 再次查询应为空数组
    const getRes = await page.request.get('/api/chat/history');
    const history = await getRes.json();
    expect(history.length).toBe(0);
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

    // SettingsResponse 实际结构为 { llm: { provider, base_url, model } }
    expect(body).toHaveProperty('llm');
    expect(body.llm).toHaveProperty('provider');
    expect(body.llm).toHaveProperty('base_url');
    expect(body.llm).toHaveProperty('model');
  });

  test('PUT /api/settings 应持久化新配置', async ({ page }) => {
    // 修改并回滚
    const original = await (await page.request.get('/api/settings')).json();
    const newModel = original.llm.model + '-apitest-' + Date.now();

    const putRes = await page.request.put('/api/settings', {
      data: { llm_model: newModel },
    });
    expect(putRes.ok()).toBeTruthy();

    const reloaded = await (await page.request.get('/api/settings')).json();
    expect(reloaded.llm.model).toBe(newModel);

    // 回滚以避免污染后续测试
    await page.request.put('/api/settings', { data: { llm_model: original.llm.model } });
  });

  test('DELETE /api/documents/{id} 不存在时应返回 404', async ({ page }) => {
    const res = await page.request.delete('/api/documents/999999');
    expect(res.status()).toBe(404);
  });
});