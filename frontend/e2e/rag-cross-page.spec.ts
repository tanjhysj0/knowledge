import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #25: 跨页 RAG 集成流 - 上传文档 → ChatPage 按文档问答
 *
 * 完整 tracer bullet: DocumentsPage 上传 .md → ChatPage 自动加载 → 看到
 * 上下文指示器 → 选中/取消选中文档 → 提问 → 验证后端 SSE 收到正确的
 * document_ids，并根据选中状态走 RAG 检索（无关键词命中时返回通用知识）。
 */

const KEYWORD = '公司年营收 1 亿元';
// 后端 MockLLMProvider 始终返回固定 mock 文本，因此本 spec 改用「是否有
// 真实 RAG 检索结果」作为成功信号：当 document_ids 为空时 mock 回答被
// 当作通用知识返回；当 document_ids 命中已上传文档时，后端照常走 mock。
// 这里仅断言前端按 document_ids 把请求体正确发到后端。

// 通过 API 端列出文档
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

// 本 spec 串行执行，避免清理与上传操作互相竞争。
cleanupTest.describe.configure({ mode: 'serial' });

// 清理本 spec 运行留下的文档（只清除本 spec 产生的 cross-page-rag-*）。
async function cleanupCrossPageDocs(): Promise<void> {
  const docs = await listDocuments();
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    for (const document of docs) {
      if (document.filename.startsWith('cross-page-rag-')) {
        await ctx.delete(`/api/documents/${document.id}`).catch(() => {});
      }
    }
  } finally {
    await ctx.dispose();
  }
}


// 通过后端 API 上传文档（#50：首页已移除上传区，改用 API 上传测试数据）
async function uploadDocumentViaApi(filename: string, content: string): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.post('/api/documents/upload', {
      multipart: {
        file: { name: filename, mimeType: 'text/markdown', buffer: Buffer.from(content) },
      },
    });
    expect(res.ok()).toBeTruthy();
  } finally {
    await ctx.dispose();
  }
}


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

// 后端 MockLLMProvider 返回的固定文本
const MOCK_REPLY = 'Hello! I am a mocked DocQA assistant. (no real LLM was called)';

cleanupTest.describe('Cross-page RAG integration - E2E (#25)', () => {
  cleanupTest.beforeEach(async () => {
    await cleanupCrossPageDocs();
  });

  cleanupTest(
    '上传含关键词 .md 后 ChatPage 应显示 context-indicator 并默认选中',
    async ({ page, uploadedDocs }) => {
      const filename = `cross-page-rag-${Date.now()}.md`;
      await uploadDocumentViaApi(
        filename,
        `# 公司财务报告\n\n本年度${KEYWORD}，同比增长 15%。`
      );

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
      await page.locator('[data-testid="context-toggle"]').click();
      const checkbox = page.locator(`[data-testid="document-checkbox-${docId}"]`);
      await expect(checkbox).toBeVisible({ timeout: 15_000 });
      await expect(checkbox).toBeChecked();

      await expect(
        page.locator(`.document-selector-name:has-text("${filename}")`)
      ).toBeVisible();
    }
  );

  cleanupTest(
    '选中后提问 → 后端应收到包含文档 id 的请求并返回 mock 回复',
    async ({ page, uploadedDocs }) => {
      const filename = `cross-page-rag-qa-${Date.now()}.md`;
      await uploadDocumentViaApi(filename, `# 公司财务报告\n\n本年度${KEYWORD}。`);
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

      await goToChatAndWaitDocs(page);
      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toBeVisible();

      const textarea = page.locator('textarea');
      const sendButton = page.locator('button:has-text("发送")');
      const reqPromise = page.waitForRequest(
        (req) => req.url().includes('/api/chat/stream'),
        { timeout: 5_000 }
      );
      await textarea.fill('公司年营收是多少');
      await sendButton.click();

      // 助手应展示 mock 文本（说明后端 MockLLMProvider 命中）
      const assistant = page.locator('.message.assistant .content').last();
      await expect(assistant).toContainText(MOCK_REPLY, { timeout: 5_000 });

      // 请求 body 应包含已上传文档 id
      const req = await reqPromise;
      const lastBody = req.postDataJSON() as { message: string; document_ids: number[] };
      expect(lastBody.document_ids).toContain(docId);
    }
  );

  cleanupTest(
    '取消选中后提问 → 请求体不应携带任何文档 id',
    async ({ page, uploadedDocs }) => {
      const filename = `cross-page-rag-empty-${Date.now()}.md`;
      await uploadDocumentViaApi(filename, `# 报告\n\n本年度${KEYWORD}。`);
      await uploadedDocs.track(filename);

      await goToChatAndWaitDocs(page);
      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toBeVisible();

      await page.locator('[data-testid="context-toggle"]').click();
      await page.locator('[data-testid="document-selector-all"]').click();

      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toContainText('未选择');

      const textarea = page.locator('textarea');
      const sendButton = page.locator('button:has-text("发送")');
      const reqPromise = page.waitForRequest(
        (req) => req.url().includes('/api/chat/stream'),
        { timeout: 5_000 }
      );
      await textarea.fill('公司年营收是多少');
      await sendButton.click();

      const assistant = page.locator('.message.assistant .content').last();
      await expect(assistant).not.toBeEmpty({ timeout: 5_000 });

      const req = await reqPromise;
      const lastBody = req.postDataJSON() as { message: string; document_ids: number[] };
      expect(lastBody.document_ids).toEqual([]);
    }
  );

  cleanupTest(
    '切换选中不同文档 → mock 收到的 document_ids 应随之变化',
    async ({ page, uploadedDocs }) => {
      const filenameA = `cross-page-rag-A-${Date.now()}.md`;
      const filenameB = `cross-page-rag-B-${Date.now()}.md`;
      await uploadDocumentViaApi(filenameA, `# Doc A\n\n${KEYWORD} from A`);
      await uploadDocumentViaApi(filenameB, `# Doc B\n\n${KEYWORD} from B`);

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

      await goToChatAndWaitDocs(page);
      await expect(
        page.locator('[data-testid="context-indicator"]')
      ).toBeVisible();

      await page.locator('[data-testid="context-toggle"]').click();
      await expect(
        page.locator('[data-testid="document-selector"]')
      ).toBeVisible();
      await Promise.all([
        expect(
          page.locator(`[data-testid="document-checkbox-${docAId}"]`)
        ).toBeVisible({ timeout: 15_000 }),
        expect(
          page.locator(`[data-testid="document-checkbox-${docBId}"]`)
        ).toBeVisible({ timeout: 15_000 }),
      ]);
      await page.locator('[data-testid="document-selector-all"]').click();
      await page
        .locator(`[data-testid="document-checkbox-${docAId}"]`)
        .click();

      const textarea = page.locator('textarea');
      const sendButton = page.locator('button:has-text("发送")');
      const firstReqPromise = page.waitForRequest(
        (req) => req.url().includes('/api/chat/stream'),
        { timeout: 5_000 }
      );
      await textarea.fill('营收');
      await sendButton.click();

      await expect(
        page.locator('.message.assistant .content').last()
      ).not.toBeEmpty({ timeout: 5_000 });

      const firstReq = await firstReqPromise;
      const firstBody = firstReq.postDataJSON() as { message: string; document_ids: number[] };
      expect(firstBody.document_ids).toEqual([docAId]);

      await page
        .locator(`[data-testid="document-checkbox-${docAId}"]`)
        .click();
      await page
        .locator(`[data-testid="document-checkbox-${docBId}"]`)
        .click();

      const secondReqPromise = page.waitForRequest(
        (req) => req.url().includes('/api/chat/stream'),
        { timeout: 5_000 }
      );
      await textarea.fill('营收 2');
      await sendButton.click();
      const secondReq = await secondReqPromise;
      const secondBody = secondReq.postDataJSON() as { message: string; document_ids: number[] };
      expect(secondBody.document_ids).toEqual([docBId]);
    }
  );

  cleanupTest(
    '删除已上传文档 → ChatPage selector 中应移除该文档且请求不再携带其 id',
    async ({ page, uploadedDocs }) => {
      const filename = `cross-page-rag-delete-${Date.now()}.md`;
      // 额外上传一个"诱饵"文档，避免删除目标后 totalCount=0 导致
      // ChatPage 隐藏 context-toggle（页面只在有文档时才渲染选择入口）。
      const decoyFilename = `cross-page-rag-decoy-${Date.now()}.md`;
      await uploadDocumentViaApi(filename, `# Delete Me\n\n${KEYWORD}`);
      await uploadDocumentViaApi(decoyFilename, `# Decoy\n\n${KEYWORD}`);
      let docId: number | undefined;
      let decoyDocId: number | undefined;
      for (let i = 0; i < 20; i++) {
        const docs = await listDocuments();
        const found = docs.find((d) => d.filename === filename);
        const decoy = docs.find((d) => d.filename === decoyFilename);
        if (found && decoy) {
          docId = found.id;
          decoyDocId = decoy.id;
          break;
        }
        await page.waitForTimeout(250);
      }
      expect(docId).toBeDefined();
      expect(decoyDocId).toBeDefined();
      await uploadedDocs.track(filename);
      await uploadedDocs.track(decoyFilename);

      await goToChatAndWaitDocs(page);
      await page.locator('[data-testid="context-toggle"]').click();
      const checkbox = page.locator(`[data-testid="document-checkbox-${docId}"]`);
      await expect(checkbox).toBeVisible({ timeout: 15_000 });
      // 诱饵文档的 checkbox 也应可见（确保 totalCount >= 2）
      await expect(
        page.locator(`[data-testid="document-checkbox-${decoyDocId}"]`)
      ).toBeVisible({ timeout: 15_000 });

      const delCtx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
      const delRes = await delCtx.delete(`/api/documents/${docId}`);
      expect(delRes.ok()).toBeTruthy();
      await delCtx.dispose();

      await page.reload();
      const listPromise = page.waitForResponse(
        (res) =>
          res.url().includes('/api/documents') && res.url().includes('page=1'),
        { timeout: 10_000 }
      );
      await listPromise;
      const toggle = page.locator('[data-testid="context-toggle"]');
      await expect(toggle).toBeVisible({ timeout: 10_000 });
      await toggle.click();

      // 被删的 checkbox 必须不存在；诱饵文档仍存在以保证 toggle 可见
      await expect(
        page.locator(`[data-testid="document-checkbox-${docId}"]`)
      ).toHaveCount(0, { timeout: 5_000 });
      await expect(
        page.locator(`[data-testid="document-checkbox-${decoyDocId}"]`)
      ).toBeVisible({ timeout: 5_000 });

      const reqPromise = page.waitForRequest(
        (req) => req.url().includes('/api/chat/stream'),
        { timeout: 5_000 }
      );
      await page.locator('textarea').fill('删除后再问');
      await page.locator('button:has-text("发送")').click();
      const req = await reqPromise;
      const body = req.postDataJSON() as { message: string; document_ids: number[] };
      expect(body.document_ids).not.toContain(docId);
    }
  );

});