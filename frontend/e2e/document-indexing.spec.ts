import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #63: 上传与索引分离。
 *
 * 覆盖验收：
 * - 上传秒回：响应只等落库，返回 pending/0（含小说 id）；
 * - 后台索引完成后小说转为 ready/100；
 * - 未 ready 的小说不出现在默认书架列表，ready 后出现；
 * - 解析失败标记 failed 并记录错误信息，管理端可见，且不影响其他小说；
 * - ready 后的小说参与聊天检索（SSE done 事件 sources 命中）。
 */

interface DocStatus {
  id: number;
  filename: string;
  status: string;
  progress: number;
  error_message: string | null;
}

async function uploadViaApi(
  filename: string,
  content: string,
  mimeType = 'text/plain'
): Promise<DocStatus> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.post('/api/documents/upload', {
      multipart: {
        file: { name: filename, mimeType, buffer: Buffer.from(content) },
      },
    });
    expect(res.ok()).toBeTruthy();
    return (await res.json()) as DocStatus;
  } finally {
    await ctx.dispose();
  }
}

async function listViaApi(allStatuses: boolean): Promise<DocStatus[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const query = allStatuses
      ? '?page=1&page_size=100&all_statuses=true'
      : '?page=1&page_size=100';
    const res = await ctx.get(`/api/documents${query}`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    return (body.items || []) as DocStatus[];
  } finally {
    await ctx.dispose();
  }
}

/** 轮询全量列表直到目标文档满足条件。 */
async function pollStatus(
  filename: string,
  predicate: (doc: DocStatus) => boolean,
  timeoutMs = 60_000
): Promise<DocStatus> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const items = await listViaApi(true);
    const doc = items.find((d) => d.filename === filename);
    if (doc && predicate(doc)) return doc;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`文档 ${filename} 在 ${timeoutMs}ms 内未达到预期状态`);
}

cleanupTest.describe('上传与索引分离 - E2E (#63)', () => {
  cleanupTest.beforeEach(async () => {
    // 后台索引含真实 bge-m3 embedding 推理（CPU），放宽单个用例超时。
    cleanupTest.setTimeout(90_000);
  });

  cleanupTest('上传秒回为 pending/0，后台索引最终 ready/100', async ({ uploadedDocs }) => {
    const filename = `indexing-${Date.now()}.txt`;
    const started = Date.now();
    const uploaded = await uploadViaApi(filename, `索引分离测试内容 ${filename}`);
    const elapsed = Date.now() - started;

    // 秒回：响应只等落库，不含解析/分块/embedding 耗时
    expect(elapsed).toBeLessThan(10_000);
    expect(uploaded.id).toBeGreaterThan(0);
    expect(uploaded.status).toBe('pending');
    expect(uploaded.progress).toBe(0);

    const doc = await pollStatus(
      filename,
      (d) => d.status === 'ready' && d.progress === 100
    );
    expect(doc.error_message).toBeNull();

    await uploadedDocs.track(filename);
  });

  cleanupTest('未 ready 的小说不进默认书架，ready 后出现', async ({ uploadedDocs }) => {
    const filename = `shelf-filter-${Date.now()}.txt`;
    // 稍长的内容确保后台索引需要数秒，留出 pending 观察窗口
    const content = `书架过滤测试。${'蓝色独角兽漫游星海。'.repeat(30)}`;
    await uploadViaApi(filename, content);

    // 上传刚返回时默认（ready-only）列表不应包含该小说
    const immediately = await listViaApi(false);
    expect(immediately.some((d) => d.filename === filename)).toBeFalsy();

    // 索引完成后出现在默认书架列表
    await pollStatus(filename, (d) => d.status === 'ready');
    const after = await listViaApi(false);
    expect(after.some((d) => d.filename === filename)).toBeTruthy();

    await uploadedDocs.track(filename);
  });

  cleanupTest('解析失败标记 failed 且管理端可见，不影响其他小说', async ({ page, uploadedDocs }) => {
    // .pdf 扩展名的纯文本内容无法被 pdfplumber 解析 → failed
    const badFilename = `bad-${Date.now()}.pdf`;
    await uploadViaApi(badFilename, 'this is not a real pdf', 'application/pdf');

    const doc = await pollStatus(badFilename, (d) => d.status === 'failed');
    expect(doc.error_message).toBeTruthy();
    expect(doc.progress).toBeLessThan(100);

    // 失败的小说不进入默认书架
    const defaultItems = await listViaApi(false);
    expect(defaultItems.some((d) => d.filename === badFilename)).toBeFalsy();

    // 管理端全量视图展示失败徽标与错误信息
    await page.goto('/admin');
    await page.waitForLoadState('networkidle').catch(() => {});
    // 列表行展示的小说名为文件名去扩展名（title 回退），不含 .pdf 后缀
    const badTitle = badFilename.replace(/\.pdf$/, '');
    const row = page
      .locator('[data-testid="novel-list-item"]')
      .filter({ hasText: badTitle });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row.locator('[data-testid="doc-status-badge"]')).toContainText('失败');
    await expect(row.locator('[data-testid="doc-error-message"]')).toBeVisible();

    // 失败不影响其他小说：新传一本正常小说照常 ready
    const goodFilename = `good-${Date.now()}.txt`;
    await uploadViaApi(goodFilename, 'normal novel content for indexing');
    await pollStatus(goodFilename, (d) => d.status === 'ready');

    await uploadedDocs.track(badFilename);
    await uploadedDocs.track(goodFilename);
  });

  cleanupTest('ready 后的小说参与聊天检索（sources 命中）', async ({ page, uploadedDocs }) => {
    const filename = `chat-ready-${Date.now()}.txt`;
    // 用 content 中的完整子句提问：短查询 + 数字串会拉低 bge-m3 相似度
    // 至阈值附近，完整子句的 distance 稳定低于 RETRIEVAL_SCORE_THRESHOLD。
    const question = '星海蓝色独角兽喜欢在银河里散步';
    await uploadViaApi(
      filename,
      `这本小说讲的是星海蓝色独角兽的故事。${question}。`
    );
    await pollStatus(filename, (d) => d.status === 'ready');
    await uploadedDocs.track(filename);

    // 先打开首页让 fetch 有 origin（vite 代理 /api 到后端）。
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});

    // 浏览器端发起：X-E2E-Test 头由 playwright 注入（LLM 走 mock），
    // 检索走真实 embedding + Milvus。
    const result = await page.evaluate(
      async ({ question, filename }: { question: string; filename: string }) => {
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
        if (!doc) throw new Error(`ready 的小说未出现在默认列表: ${filename}`);

        const streamRes = await fetch('/api/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: question,
            document_ids: [doc.id],
            conversation_id: conv.id,
          }),
        });
        const text = await streamRes.text();
        await fetch(`/api/conversations/${conv.id}`, { method: 'DELETE' }).catch(() => {});
        return { docId: doc.id, text };
      },
      { question, filename }
    );

    // 解析 SSE：done 事件的 sources 应包含该小说
    const doneBlock = result.text
      .trim()
      .split(/\r?\n\r?\n/)
      .map((block) => {
        const lines = block.split(/\r?\n/);
        const event = lines.find((l) => l.startsWith('event: '))?.slice(7);
        const dataLine = lines.find((l) => l.startsWith('data: '))?.slice(6);
        let data: unknown = null;
        if (dataLine) {
          try {
            data = JSON.parse(dataLine);
          } catch {
            data = null;
          }
        }
        return { event, data };
      })
      .find((e) => e.event === 'done');
    expect(doneBlock).toBeTruthy();
    expect((doneBlock!.data as { sources: string[] }).sources).toContain(
      `doc_${result.docId}`
    );
  });
});
