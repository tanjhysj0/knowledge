import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #66: 混合检索 + Evidence Agent 的 SSE 契约。
 *
 * 覆盖验收：
 * - /api/chat/stream 在 message 之前发出可选 evidence 事件（证据包摘要）；
 * - done 事件扩展结构化 evidence 字段（向下兼容：sources 仍是数组）；
 * - x-e2e-mock-judge 头控制证据判定：默认 sufficient 直接作答；
 *   insufficient 触发补充检索循环，达上限后强制作答（note 非空）。
 */

interface DocStatus {
  id: number;
  filename: string;
  status: string;
  progress: number;
}

async function uploadViaApi(filename: string, content: string): Promise<DocStatus> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.post('/api/documents/upload', {
      multipart: {
        file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from(content) },
      },
    });
    expect(res.ok()).toBeTruthy();
    return (await res.json()) as DocStatus;
  } finally {
    await ctx.dispose();
  }
}

async function pollReady(filename: string, timeoutMs = 90_000): Promise<DocStatus> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
    try {
      const res = await ctx.get('/api/documents?page=1&page_size=100&all_statuses=true');
      const body = await res.json();
      const doc = (body.items || []).find((d: DocStatus) => d.filename === filename);
      if (doc && doc.status === 'ready') return doc;
    } finally {
      await ctx.dispose();
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`文档 ${filename} 未在 ${timeoutMs}ms 内 ready`);
}

function parseSseEvents(body: string): { event: string; data: any }[] {
  return body
    .trim()
    .split(/\r?\n\r?\n/)
    .map((block) => {
      const lines = block.split(/\r?\n/);
      const event = lines.find((l) => l.startsWith('event: '))?.slice(7);
      const data = lines.find((l) => l.startsWith('data: '))?.slice(6);
      expect(event).toBeTruthy();
      expect(data).toBeTruthy();
      return { event: event!, data: JSON.parse(data!) };
    });
}

cleanupTest.describe('Evidence 事件契约 - E2E (#66)', () => {
  cleanupTest.beforeEach(async ({ page }) => {
    // 上传 + 真实 bge-m3 索引（CPU）耗时较长。
    cleanupTest.setTimeout(150_000);
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  cleanupTest('evidence 事件与 done.evidence 契约 + judge 头控制循环', async ({ page, uploadedDocs }) => {
    // 问题与文档内容同源，保证真实 bge-m3 embedding + Milvus 检索命中。
    const question = '青藤学院院长在紫雾森林里发现了会说话的石头';
    const filename = `evidence-${Date.now()}.txt`;
    await uploadViaApi(filename, `这本小说讲的是青藤学院的故事。${question}。`);
    await pollReady(filename);
    await uploadedDocs.track(filename);

    // 浏览器端发起：X-E2E-Test 由 playwright extraHTTPHeaders 注入，
    // LLM 走 mock；检索走真实 embedding + Milvus。
    const streamText = await page.evaluate(
      async ({ question, filename, judgeHeader }: { question: string; filename: string; judgeHeader: string | null }) => {
        const convRes = await fetch('/api/conversations', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const conv = (await convRes.json()) as { id: number };

        const docsRes = await fetch('/api/documents?page=1&page_size=100');
        const docsBody = (await docsRes.json()) as {
          items: Array<{ id: number; filename: string }>;
        };
        const doc = docsBody.items.find((d) => d.filename === filename);
        if (!doc) throw new Error(`ready 小说未出现在默认列表: ${filename}`);

        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (judgeHeader) headers['x-e2e-mock-judge'] = judgeHeader;
        const res = await fetch('/api/chat/stream', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            message: question,
            document_ids: [doc.id],
            conversation_id: conv.id,
          }),
        });
        const text = await res.text();
        await fetch(`/api/conversations/${conv.id}`, { method: 'DELETE' }).catch(() => {});
        return text;
      },
      { question, filename, judgeHeader: null }
    );

    const events = parseSseEvents(streamText);

    // 1. evidence 事件在 message 之前发出，负载为证据包摘要。
    const evidenceIndex = events.findIndex((e) => e.event === 'evidence');
    expect(evidenceIndex).toBeGreaterThanOrEqual(0);
    const firstMessageIndex = events.findIndex((e) => e.event === 'message');
    if (firstMessageIndex >= 0) expect(evidenceIndex).toBeLessThan(firstMessageIndex);

    const evidence = events[evidenceIndex].data;
    expect(Array.isArray(evidence.hits)).toBeTruthy();
    expect(typeof evidence.sufficient).toBe('boolean');
    expect(typeof evidence.iterations).toBe('number');
    expect(typeof evidence.note).toBe('string');
    // 默认 judge 头 → 证据足够 → 直接作答（无补充检索）。
    expect(evidence.sufficient).toBe(true);
    expect(evidence.iterations).toBe(0);

    // 2. done 事件扩展结构化 evidence 字段（向下兼容：sources 仍是数组）。
    const done = events[events.length - 1];
    expect(done.event).toBe('done');
    expect(Array.isArray(done.data.sources)).toBeTruthy();
    expect(Array.isArray(done.data.evidence)).toBeTruthy();
    for (const item of done.data.evidence) {
      expect(item).toHaveProperty('document_id');
      expect(item).toHaveProperty('chunk_index');
      expect(item).toHaveProperty('strategy');
    }

    // 3. judge 头 insufficient → 证据循环 → 达上限强制作答（sufficient=false + note 非空）。
    const insufficientText = await page.evaluate(
      async ({ question, filename, judgeHeader }: { question: string; filename: string; judgeHeader: string }) => {
        const convRes = await fetch('/api/conversations', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const conv = (await convRes.json()) as { id: number };

        const docsRes = await fetch('/api/documents?page=1&page_size=100');
        const docsBody = (await docsRes.json()) as {
          items: Array<{ id: number; filename: string }>;
        };
        const doc = docsBody.items.find((d) => d.filename === filename);
        if (!doc) throw new Error(`ready 小说未出现在默认列表: ${filename}`);

        const res = await fetch('/api/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'x-e2e-mock-judge': judgeHeader },
          body: JSON.stringify({
            message: question,
            document_ids: [doc.id],
            conversation_id: conv.id,
          }),
        });
        const text = await res.text();
        await fetch(`/api/conversations/${conv.id}`, { method: 'DELETE' }).catch(() => {});
        return text;
      },
      { question, filename, judgeHeader: 'insufficient' }
    );

    const insufficientEvents = parseSseEvents(insufficientText);
    const insufficientEvidence = insufficientEvents.find((e) => e.event === 'evidence');
    expect(insufficientEvidence).toBeTruthy();
    expect(insufficientEvidence!.data.sufficient).toBe(false);
    expect(insufficientEvidence!.data.note.length).toBeGreaterThan(0);
  });
});
