import { test, expect } from '@playwright/test';

// 直接对后端 API 做契约断言。/api/chat 与 /api/chat/stream 走真实后端，
// 后端依靠 X-E2E-Test Header 自动返回 MockLLMProvider，不再额外 mock。
//
// 由于 `page.route` 只能拦截浏览器发出的请求（不能拦截 page.request 从 Node.js
// 发出的请求），所以 LLM 对话类契约测试需要从浏览器端发起 fetch，才能被 mock 命中。
test.describe('Backend API 契约 - E2E', () => {
  test.beforeEach(async ({ page }) => {
    // chat/stream 等流式契约用例需读完整 SSE 响应，高负载下可能超过默认 5s。
    test.setTimeout(30_000);
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  // 从浏览器端发起请求，从而能让后端 MockLLMProvider 命中（X-E2E-Test Header
  // 由 playwright.config 的 extraHTTPHeaders 注入）。
  // #36：ChatRequest.conversation_id 必填；创建临时会话作为隔离载体。
  async function createConversation(page: any): Promise<number> {
    const result = await page.evaluate(async () => {
      // 必须发送 Content-Type + 非空 body，否则 FastAPI 会以 422 拒绝
      // （“Field required”，即使 ConversationCreate 所有字段均可选）。
      const res = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      return { status: res.status, body: await res.json() };
    });
    if (result.status !== 200) {
      throw new Error(`createConversation failed: ${result.status} ${JSON.stringify(result.body)}`);
    }
    return result.body.id as number;
  }

  async function deleteConversation(page: any, id: number): Promise<void> {
    await page.request.delete(`/api/conversations/${id}`).catch(() => {});
  }

  async function postChatFromBrowser(page: any, message: string, conversationId?: number): Promise<any> {
    const convId = conversationId ?? (await createConversation(page));
    try {
      return await page.evaluate(
        async ({ msg, convId }: { msg: string; convId: number }) => {
          const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg, document_ids: [], conversation_id: convId }),
          });
          return { status: res.status, body: await res.json() };
        },
        { msg: message, convId }
      );
    } finally {
      if (conversationId === undefined) await deleteConversation(page, convId);
    }
  }

  async function postStreamFromBrowser(
    page: any,
    message: string,
    headers: Record<string, string> = {},
    conversationId?: number
  ): Promise<{ status: number; body: string }> {
    const convId = conversationId ?? (await createConversation(page));
    try {
      return await page.evaluate(
        async ({ msg, requestHeaders, convId }: { msg: string; requestHeaders: Record<string, string>; convId: number }) => {
          const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...requestHeaders },
            body: JSON.stringify({ message: msg, document_ids: [], conversation_id: convId }),
          });
          return { status: res.status, body: await res.text() };
        },
        { msg: message, requestHeaders: headers, convId }
      );
    } finally {
      if (conversationId === undefined) await deleteConversation(page, convId);
    }
  }

  function parseSseEvents(body: string): { event: string; data: unknown }[] {
    return body.trim().split(/\r?\n\r?\n/).map((block) => {
      const lines = block.split(/\r?\n/);
      const event = lines.find((line) => line.startsWith('event: '))?.slice(7);
      const data = lines.find((line) => line.startsWith('data: '))?.slice(6);
      expect(event).toBeTruthy();
      expect(data).toBeTruthy();
      return { event: event!, data: JSON.parse(data!) };
    });
  }

  test('POST /api/chat/stream 应遵守 message 与 done SSE 契约', async ({ page }) => {
    const response = await postStreamFromBrowser(page, 'stream contract ' + Date.now());
    expect(response.status).toBe(200);

    const events = parseSseEvents(response.body);
    const messageEvents = events.filter((item) => item.event === 'message');
    expect(messageEvents.length).toBeGreaterThan(0);
    for (const item of messageEvents) {
      expect(item.data).toEqual(expect.objectContaining({ content: expect.any(String) }));
    }

    const doneIndex = events.findIndex((item) => item.event === 'done');
    expect(doneIndex).toBe(events.length - 1);
    expect(events[doneIndex].data).toEqual(expect.objectContaining({ sources: expect.any(Array) }));
    expect(messageEvents.map((item) => (item.data as { content: string }).content).join('')).toBe(
      'Hello! I am a mocked DocQA assistant. (no real LLM was called)',
    );
  });

  test('POST /api/chat/stream 错误路径应返回 event: error 且 data 含 error 字符串', async ({ page }) => {
    // 删除 X-E2E-Test header，强制后端走真实 LLM preflight（真实环境无 key →
    // #45 preflight 拒绝 → error + done SSE）。这是 SSE 错误路径的契约测试。
    await page.route('**/api/chat/stream', async (route) => {
      const headers = { ...route.request().headers() };
      delete headers['x-e2e-test'];
      delete headers['X-E2E-Test'];
      await route.continue({ headers });
    });

    const response = await postStreamFromBrowser(page, 'error contract ' + Date.now());
    expect(response.status).toBe(200);

    const events = parseSseEvents(response.body);
    const errorEvents = events.filter((item) => item.event === 'error');
    expect(errorEvents.length).toBe(1);
    const errorData = errorEvents[0].data as { error: string };
    expect(typeof errorData.error).toBe('string');
    expect(errorData.error.length).toBeGreaterThan(0);
    // 错误路径不应该有 message 事件（#45 preflight 会以 done 收尾）
    expect(events.filter((item) => item.event === 'message').length).toBe(0);
  });

  test('POST /api/chat 应返回 ChatResponse 结构', async ({ page }) => {
    const convId = await createConversation(page);
    const { status, body } = await postChatFromBrowser(page, 'Hello API', convId);
    expect(status).toBe(200);
    expect(body).toHaveProperty('message');
    expect(typeof body.message).toBe('string');
    expect(body).toHaveProperty('sources');
    expect(Array.isArray(body.sources)).toBeTruthy();
  });

  test('GET /api/conversations/{id}/messages 应返回该会话消息数组', async ({ page }) => {
    const convId = await createConversation(page);
    try {
      await postChatFromBrowser(page, 'messages seed ' + Date.now(), convId);
      const res = await page.request.get(`/api/conversations/${convId}/messages`);
      expect(res.ok()).toBeTruthy();
      const messages = await res.json();
      expect(Array.isArray(messages)).toBeTruthy();
      expect(messages.length).toBeGreaterThan(0);
      for (const m of messages) {
        expect(m).toHaveProperty('id');
        expect(m).toHaveProperty('role');
        expect(m).toHaveProperty('content');
        expect(m).toHaveProperty('created_at');
      }
    } finally {
      await deleteConversation(page, convId);
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