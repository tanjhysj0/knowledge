import { test, expect } from '@playwright/test';

// 直接对后端 API 做契约断言。LLM 慢路径用 30s 显式超时避免与 actionTimeout 冲突。
test.describe('Backend API 契约 - E2E', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  test('POST /api/chat 应返回 ChatResponse 结构', async ({ page }) => {
    // 非流式接口应返回 message + sources 字段
    const res = await page.request.post('/api/chat', {
      data: { message: 'Hello API', document_ids: [] },
      timeout: 30_000,
    });

    expect(res.ok()).toBeTruthy();
    const body = await res.json();

    expect(body).toHaveProperty('message');
    expect(typeof body.message).toBe('string');
    expect(body).toHaveProperty('sources');
    expect(Array.isArray(body.sources)).toBeTruthy();
  });

  test('GET /api/chat/history 应返回消息数组', async ({ page }) => {
    // 先发一条非流式消息
    await page.request.post('/api/chat', {
      data: { message: 'history seed ' + Date.now() },
      timeout: 30_000,
    });

    const res = await page.request.get('/api/chat/history');
    expect(res.ok()).toBeTruthy();
    const history = await res.json();
    expect(Array.isArray(history)).toBeTruthy();

    // 至少含 user 与 assistant
    const hasUser = history.some((m: any) => m.role === 'user');
    const hasAssistant = history.some((m: any) => m.role === 'assistant');
    expect(hasUser && hasAssistant).toBeTruthy();

    // 每条消息都应有 id/role/content/created_at 字段
    if (history.length > 0) {
      const m = history[0];
      expect(m).toHaveProperty('id');
      expect(m).toHaveProperty('role');
      expect(m).toHaveProperty('content');
      expect(m).toHaveProperty('created_at');
    }
  });

  test('DELETE /api/chat/history 应清空所有历史', async ({ page }) => {
    // 先写入一条
    await page.request.post('/api/chat', {
      data: { message: 'to be cleared ' + Date.now() },
      timeout: 30_000,
    });

    // 清空
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