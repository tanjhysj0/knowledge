import { test, expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';
import { installChatStreamMock } from './helpers/chatMock';

/**
 * Issue #25: 跨页 RAG 集成流 - 上传文档 → ChatPage 按文档问答
 *
 * 完整 tracer bullet: DocumentsPage 上传 .md → ChatPage 自动加载 → 看到
 * 上下文指示器 → 选中/取消选中文档 → 提问 → 验证 mock SSE 收到正确的
 * document_ids，且 mock 根据选中状态返回含/不含关键词的内容。
 */

const KEYWORD = '公司年营收 1 亿元';
const ANSWER_WITH_DOCS = `根据提供的文档内容，${KEYWORD}。`;
const ANSWER_WITHOUT_DOCS = '这是一段通用知识回答，未引用任何文档。';

// 通过 API 端删除文档，便于测试间清理
async function listDocuments(): Promise<{ id: number; filename: string }[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.get('/api/documents?page=1&page_size=100');
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    return body.items || [];
  } finally {
    await ctx.dispose();
  }
}

// 本 spec 专属清理：每个 spec 前删除遗留的 cross-page-rag-* 文档。
// cleanup fixture 的默认前缀不含此集合，因此 spec 内部主动清理。
async function cleanupCrossPageDocs(): Promise<void> {
  const docs = await listDocuments();
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    for (const d of docs) {
      if (d.filename.startsWith('cross-page-rag-')) {
        await ctx.delete(`/api/documents/${d.id}`).catch(() => {});
      }
    }
  } finally {
    await ctx.dispose();
  }
}

// 进入 ChatPage 并等待 documents list 接口返回（避免 React StrictMode 双调用竞态）
async function goToChatAndWaitDocs(page: import('@playwright/test').Page): Promise<void> {
  const listPromise = page.waitForResponse(
    (res) =>
      res.url().includes('/api/documents') && res.url().includes('page=1'),
    { timeout: 10_000 }
  );
  await page.goto('/chat');
  await listPromise;
  await page.waitForSelector('textarea');
}

cleanupTest.describe('Cross-page RAG integration - E2E (#25)', () => {
  cleanupTest.beforeEach(async ({ page }) => {
    // 先清理遗留文档，避免与历史 spec 残留文档混淆
    await cleanupCrossPageDocs();

    // mock /api/chat/stream：根据 document_ids 返回含/不含关键词内容
    await installChatStreamMock(page, {
      answer: ANSWER_WITHOUT_DOCS,
      answersByDocs: {
        withDocs: ANSWER_WITH_DOCS,
        withoutDocs: ANSWER_WITHOUT_DOCS,
      },
      chunkDelayMs: 0,
      initialDelayMs: 0,
    });
  });

  cleanupTest(
    '上传含关键词 .md 后 ChatPage 应显示 context-indicator 并默认选中',
    async ({ page, uploadedDocs }) => {
      // 1. DocumentsPage 上传含已知关键词的 .md
      const filename = `cross-page-rag-${Date.now()}.md`;
      await page.goto('/documents');
      await page.waitForSelector('.upload-zone');

      const fileInput = page.locator('input[type="file"]');
      await fileInput.setInputFiles({
        name: filename,
        mimeType: 'text/markdown',
        buffer: Buffer.from(
          `# 公司财务报告\n\n本年度${KEYWORD}，同比增长 15%。`
        ),
      });

      // 前端列表应出现该文档
      await expect(
        page.locator(`.doc-item-name:has-text("${filename}")`)
      ).toBeVisible({ timeout: 5_000 });

      // 注册 cleanup（retry 拿 ID，避免并发上传竞态）
      let docId: number | undefined;
      for (let i = 0; i < 20; i++) {
        const docs = await listDocuments();
        const found = docs.find((d) => d.filename === filename);
        if (found) {
          docId = found.id;
          break;
        }
        await page.waitForTimeout(250);
      }
      expect(docId).toBeDefined();
      await uploadedDocs.track(filename);

      // 进入 ChatPage，并等待 documents list 接口返回（避免 React StrictMode 双调用竞态）
      const listPromise = page.waitForResponse(
        (res) =>
          res.url().includes('/api/documents') &&
          res.url().includes('page=1'),
        { timeout: 10_000 }
      );
      await page.goto('/chat');
      await listPromise;
      await page.waitForSelector('textarea');
      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toBeVisible();
      // 等选择器中包含上传的文档
      await page.locator('[data-testid="context-toggle"]').click();
      const checkbox = page.locator(`[data-testid="document-checkbox-${docId}"]`);
      await expect(checkbox).toBeVisible({ timeout: 15_000 });
      await expect(checkbox).toBeChecked();

      // 5. 文件名应出现在选择器列表中
      await expect(
        page.locator(`.document-selector-name:has-text("${filename}")`)
      ).toBeVisible();
    }
  );

  cleanupTest(
    '选中后提问 → 响应内容应包含上传文档关键词',
    async ({ page, uploadedDocs }) => {
      const filename = `cross-page-rag-qa-${Date.now()}.md`;
      await page.goto('/documents');
      await page.waitForSelector('.upload-zone');
      await page.locator('input[type="file"]').setInputFiles({
        name: filename,
        mimeType: 'text/markdown',
        buffer: Buffer.from(`# 公司财务报告\n\n本年度${KEYWORD}。`),
      });
      await expect(
        page.locator(`.doc-item-name:has-text("${filename}")`)
      ).toBeVisible({ timeout: 5_000 });
      // 用 retry 拿 ID，并注册清理
      let docId: number | undefined;
      for (let i = 0; i < 20; i++) {
        const docs = await listDocuments();
        const found = docs.find((d) => d.filename === filename);
        if (found) {
          docId = found.id;
          break;
        }
        await page.waitForTimeout(250);
      }
      expect(docId).toBeDefined();
      await uploadedDocs.track(filename);

      // 进入 ChatPage（等 list 接口完成）
      await goToChatAndWaitDocs(page);
      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toBeVisible();

      // 选中状态（默认全选）下提问
      const textarea = page.locator('textarea');
      const sendButton = page.locator('button:has-text("发送")');
      await textarea.fill('公司年营收是多少');
      await sendButton.click();

      // 助手回答应包含上传文档的关键词
      const assistant = page.locator('.message.assistant .content').last();
      await expect(assistant).toContainText(KEYWORD, { timeout: 5_000 });
    }
  );

  cleanupTest(
    '取消选中后提问 → 响应退化为通用知识（不含关键词）',
    async ({ page, uploadedDocs }) => {
      const filename = `cross-page-rag-empty-${Date.now()}.md`;
      await page.goto('/documents');
      await page.waitForSelector('.upload-zone');
      await page.locator('input[type="file"]').setInputFiles({
        name: filename,
        mimeType: 'text/markdown',
        buffer: Buffer.from(`# 报告\n\n本年度${KEYWORD}。`),
      });
      await expect(
        page.locator(`.doc-item-name:has-text("${filename}")`)
      ).toBeVisible({ timeout: 5_000 });
      await uploadedDocs.track(filename);

      // 进入 ChatPage
      await goToChatAndWaitDocs(page);
      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toBeVisible();

      // 展开选择器
      await page.locator('[data-testid="context-toggle"]').click();
      // 取消全部选择
      await page.locator('[data-testid="document-selector-all"]').click();

      // indicator 应变为 "未选择文档" 状态
      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toContainText('未选择');

      // 提问
      const textarea = page.locator('textarea');
      const sendButton = page.locator('button:has-text("发送")');
      await textarea.fill('公司年营收是多少');
      await sendButton.click();

      const assistant = page.locator('.message.assistant .content').last();
      await expect(assistant).not.toBeEmpty({ timeout: 5_000 });
      await expect(assistant).not.toContainText(KEYWORD);
    }
  );

  cleanupTest(
    '切换选中不同文档 → mock 收到的 document_ids 应随之变化',
    async ({ page, uploadedDocs }) => {
      // 上传两个文档
      const filenameA = `cross-page-rag-A-${Date.now()}.md`;
      const filenameB = `cross-page-rag-B-${Date.now()}.md`;
      await page.goto('/documents');
      await page.waitForSelector('.upload-zone');
      const fileInput = page.locator('input[type="file"]');
      await fileInput.setInputFiles({
        name: filenameA,
        mimeType: 'text/markdown',
        buffer: Buffer.from(`# Doc A\n\n${KEYWORD} from A`),
      });
      await expect(
        page.locator(`.doc-item-name:has-text("${filenameA}")`)
      ).toBeVisible({ timeout: 5_000 });
      await fileInput.setInputFiles({
        name: filenameB,
        mimeType: 'text/markdown',
        buffer: Buffer.from(`# Doc B\n\n${KEYWORD} from B`),
      });
      await expect(
        page.locator(`.doc-item-name:has-text("${filenameB}")`)
      ).toBeVisible({ timeout: 5_000 });

      // 注册 cleanup（通过 API 端拿 ID）
      async function findDocIdByName(name: string): Promise<number> {
        for (let i = 0; i < 20; i++) {
          const docs = await listDocuments();
          const found = docs.find((d) => d.filename === name);
          if (found) return found.id;
          await page.waitForTimeout(250);
        }
        throw new Error(`未找到刚上传的文档：${name}`);
      }
      const docAId = await findDocIdByName(filenameA);
      const docBId = await findDocIdByName(filenameB);
      await uploadedDocs.track(filenameA);
      await uploadedDocs.track(filenameB);

      // 在 ChatPage 中通过 Playwright route 注入断言：记录最近一次请求 body
      await page.route('**/api/chat/stream', async (route) => {
        const raw = route.request().postData() ?? '{}';
        try {
          const body = JSON.parse(raw);
          await page.evaluate((b) => {
            (window as any).__lastChatBody = b;
          }, body);
        } catch {
          /* ignore */
        }
        // 转给原 mock（已被 beforeEach 装好的 chatStreamMock）继续 fulfill
        await route.fallback();
      });

      // 进入 ChatPage
      await goToChatAndWaitDocs(page);
      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toBeVisible();

      // 展开选择器 → 先全部取消，再只勾选 A
      await page.locator('[data-testid="context-toggle"]').click();
      // 等选择器面板与所有 checkbox 渲染完成
      await expect(
        page.locator('[data-testid="document-selector"]')
      ).toBeVisible();
      // 等 docA 与 docB 的 checkbox 渲染出现
      await Promise.all([
        expect(
          page.locator(`[data-testid="document-checkbox-${docAId}"]`)
        ).toBeVisible({ timeout: 15_000 }),
        expect(
          page.locator(`[data-testid="document-checkbox-${docBId}"]`)
        ).toBeVisible({ timeout: 15_000 }),
      ]);
      await page.locator('[data-testid="document-selector-all"]').click(); // 全部取消
      await page
        .locator(`[data-testid="document-checkbox-${docAId}"]`)
        .click(); // 只勾选 A

      // 提问
      const textarea = page.locator('textarea');
      const sendButton = page.locator('button:has-text("发送")');
      await textarea.fill('营收');
      await sendButton.click();

      // 等响应完成
      await expect(
        page.locator('.message.assistant .content').last()
      ).not.toBeEmpty({ timeout: 5_000 });

      // 第一次请求应只包含 docA 的 id
      const firstBody = await page.evaluate(() => (window as any).__lastChatBody);
      expect(firstBody.document_ids).toEqual([docAId]);

      // 切换为仅选中 B
      await page
        .locator(`[data-testid="document-checkbox-${docAId}"]`)
        .click(); // 取消 A
      await page
        .locator(`[data-testid="document-checkbox-${docBId}"]`)
        .click(); // 勾选 B

      // 等第二次请求完成后再读取 body（避免硬等待时序脆弱）
      const secondReqPromise = page.waitForRequest(
        (req) => req.url().includes('/api/chat/stream'),
        { timeout: 5_000 }
      );
      await textarea.fill('营收 2');
      await sendButton.click();
      await secondReqPromise;
      const secondBody = await page.evaluate(() => (window as any).__lastChatBody);
      expect(secondBody.document_ids).toEqual([docBId]);
    }
  );

  cleanupTest(
    '删除已上传文档 → ChatPage selector 中应移除该文档且请求不再携带其 id',
    async ({ page, uploadedDocs }) => {
      // 上传 docA → 进入 ChatPage → 删除 → 验证 selector 已剔除
      const filename = `cross-page-rag-delete-${Date.now()}.md`;
      await page.goto('/documents');
      await page.waitForSelector('.upload-zone');
      await page.locator('input[type="file"]').setInputFiles({
        name: filename,
        mimeType: 'text/markdown',
        buffer: Buffer.from(`# Delete Me\n\n${KEYWORD}`),
      });
      await expect(
        page.locator(`.doc-item-name:has-text("${filename}")`)
      ).toBeVisible({ timeout: 5_000 });
      let docId: number | undefined;
      for (let i = 0; i < 20; i++) {
        const docs = await listDocuments();
        const found = docs.find((d) => d.filename === filename);
        if (found) {
          docId = found.id;
          break;
        }
        await page.waitForTimeout(250);
      }
      expect(docId).toBeDefined();
      await uploadedDocs.track(filename);

      // 进入 ChatPage，selector 中应包含该文档
      await goToChatAndWaitDocs(page);
      await page.locator('[data-testid="context-toggle"]').click();
      const checkbox = page.locator(`[data-testid="document-checkbox-${docId}"]`);
      await expect(checkbox).toBeVisible({ timeout: 15_000 });

      // 记录 /api/chat/stream 请求 body
      await page.route('**/api/chat/stream', async (route) => {
        const raw = route.request().postData() ?? '{}';
        try {
          const body = JSON.parse(raw);
          await page.evaluate((b) => {
            (window as any).__lastChatBody = b;
          }, body);
        } catch {
          /* ignore */
        }
        await route.fallback();
      });

      // 通过 API 删除文档
      const delCtx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
      const delRes = await delCtx.delete(`/api/documents/${docId}`);
      expect(delRes.ok()).toBeTruthy();
      await delCtx.dispose();

      // 刷新 ChatPage，使 documents 列表重新加载
      await page.reload();
      const listPromise = page.waitForResponse(
        (res) =>
          res.url().includes('/api/documents') && res.url().includes('page=1'),
        { timeout: 10_000 }
      );
      await listPromise;
      await page.locator('[data-testid="context-toggle"]').click();

      // selector 中应不再包含被删除的 checkbox
      await expect(
        page.locator(`[data-testid="document-checkbox-${docId}"]`)
      ).toHaveCount(0, { timeout: 5_000 });

      // 发送请求，验证 document_ids 中不含已删除 id
      const reqPromise = page.waitForRequest(
        (req) => req.url().includes('/api/chat/stream'),
        { timeout: 5_000 }
      );
      await page.locator('textarea').fill('删除后再问');
      await page.locator('button:has-text("发送")').click();
      await reqPromise;

      const body = await page.evaluate(() => (window as any).__lastChatBody);
      expect(body.document_ids).not.toContain(docId);
    }
  );

});
