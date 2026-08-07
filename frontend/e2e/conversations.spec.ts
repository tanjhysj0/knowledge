import { test, expect } from '@playwright/test';

/**
 * 会话基础 API 契约 E2E（#34）。
 *
 * 直接对后端 4 个端点做契约断言：
 *   - GET  /api/conversations
 *   - POST /api/conversations
 *   - DELETE /api/conversations/{id}
 *   - GET  /api/conversations/{id}/messages
 *
 * 使用串行模式 + 每用例自清理（创建后立刻删除），避免不同用例间相互影响。
 */
test.describe('Conversation API 契约 - E2E (#34)', () => {
  test.describe.configure({ mode: 'serial' });

  test('GET /api/conversations 应返回数组', async ({ page }) => {
    const res = await page.request.get('/api/conversations');
    expect(res.ok()).toBeTruthy();
    const list = await res.json();
    expect(Array.isArray(list)).toBeTruthy();
  });

  test('POST /api/conversations 应返回带 id 的会话（无 title 默认"新对话"）', async ({ page }) => {
    const createRes = await page.request.post('/api/conversations', {
      data: {},
    });
    expect(createRes.ok()).toBeTruthy();
    const conv = await createRes.json();
    expect(conv).toHaveProperty('id');
    expect(conv.id).toBeGreaterThan(0);
    expect(conv.title).toBe('新对话');
    expect(conv.message_count).toBe(0);

    // 自清理
    await page.request.delete(`/api/conversations/${conv.id}`);
  });

  test('POST /api/conversations 显式 title 应被持久化', async ({ page }) => {
    const title = '关于 DDD 的讨论 - ' + Date.now();
    const createRes = await page.request.post('/api/conversations', { data: { title } });
    expect(createRes.ok()).toBeTruthy();
    const conv = await createRes.json();
    expect(conv.title).toBe(title);

    // GET 列表中能查到
    const listRes = await page.request.get('/api/conversations');
    const list = await listRes.json();
    expect(list.find((c: { id: number; title: string }) => c.id === conv.id)).toBeTruthy();

    await page.request.delete(`/api/conversations/${conv.id}`);
  });

  test('DELETE /api/conversations/{id} 已存在 → 200，不存在 → 404', async ({ page }) => {
    // 创建一条
    const createRes = await page.request.post('/api/conversations', { data: {} });
    const conv = await createRes.json();

    // 删除应成功
    const delRes = await page.request.delete(`/api/conversations/${conv.id}`);
    expect(delRes.ok()).toBeTruthy();

    // 二次删除应 404
    const second = await page.request.delete(`/api/conversations/${conv.id}`);
    expect(second.status()).toBe(404);
  });

  test('GET /api/conversations/{id}/messages 不存在会话应 404', async ({ page }) => {
    const res = await page.request.get('/api/conversations/999999/messages');
    expect(res.status()).toBe(404);
  });

  test('GET /api/conversations/{id}/messages 存在会话应返回数组', async ({ page }) => {
    const createRes = await page.request.post('/api/conversations', { data: {} });
    const conv = await createRes.json();
    try {
      const res = await page.request.get(`/api/conversations/${conv.id}/messages`);
      expect(res.ok()).toBeTruthy();
      const msgs = await res.json();
      expect(Array.isArray(msgs)).toBeTruthy();
    } finally {
      await page.request.delete(`/api/conversations/${conv.id}`);
    }
  });
});
