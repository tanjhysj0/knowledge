/**
 * #45 preflight 拒绝路径契约测试（前置 project：先于主套件跑完）。
 *
 * 该用例短暂清空默认模型 api_key 触发 preflight 拒绝（删除 X-E2E-Test
 * 头后走真实 preflight），并在 finally 配回 dummy key。之所以拆成独立
 * project（playwright.config 的 dependencies）：主套件 fullyParallel
 * 全并行且共享后端 DB，任何「清空 key」的窗口都会随机打挂并行聊天
 * 测试；前置 project 串行先跑完并恢复 key，主套件期间 key 恒非空。
 */
import { test, expect } from './helpers/cleanup';
import { clearDefaultApiKey, ensureDefaultModelKey } from './helpers/cleanup';

async function createConversation(page: any): Promise<number> {
  const result = await page.evaluate(async () => {
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

async function postStreamFromBrowser(page: any, message: string): Promise<{ status: number; body: string }> {
  const convId = await createConversation(page);
  try {
    return await page.evaluate(
      async ({ msg, convId }: { msg: string; convId: number }) => {
        const res = await fetch('/api/v1/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: msg, document_ids: [], conversation_id: convId }),
        });
        return { status: res.status, body: await res.text() };
      },
      { msg: message, convId }
    );
  } finally {
    await page.request.delete(`/api/conversations/${convId}`).catch(() => {});
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

test('POST /api/v1/chat/stream 错误路径应返回 event: error 且 data 含 error 字符串', async ({ page }) => {
  test.setTimeout(30_000);

  // 清空默认模型 api_key（本机配置了真实 key 时 preflight 会放行，需主动
  // 清空才能稳定触发拒绝）。finally 配回 dummy key，主套件并行期间恒非空。
  await clearDefaultApiKey();
  try {
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});

    // 删除 X-E2E-Test header，强制后端走真实 LLM preflight（api_key 已清空 →
    // #45 preflight 拒绝 → error + done SSE）。这是 SSE 错误路径的契约测试。
    await page.route('**/api/v1/chat/stream', async (route) => {
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
  } finally {
    // 无论断言成败都配回 dummy key，保证主套件启动时 preflight 可通过。
    await ensureDefaultModelKey();
  }
});
