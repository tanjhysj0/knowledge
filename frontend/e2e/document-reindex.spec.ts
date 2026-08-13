import fs from 'node:fs';
import path from 'node:path';
import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #65: 索引失败手动重试。
 *
 * 覆盖验收：
 * - 管理列表 failed 行有「重新索引」操作，其余状态（就绪）不可重试；
 * - 重试端点把 failed 小说重置 pending 并重新入队，进度照常更新至 ready；
 * - 非 failed 小说调用重试端点被 409 拒绝；
 * - 失败 → 重试 → 就绪 全链路（重试前修复磁盘文件内容模拟故障恢复）。
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

/** 轮询全量列表直到目标文档满足条件。 */
async function pollStatus(
  filename: string,
  predicate: (doc: DocStatus) => boolean,
  timeoutMs = 60_000
): Promise<DocStatus> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
    try {
      const res = await ctx.get(
        '/api/documents?page=1&page_size=100&all_statuses=true'
      );
      expect(res.ok()).toBeTruthy();
      const body = await res.json();
      const doc = ((body.items || []) as DocStatus[]).find(
        (d) => d.filename === filename
      );
      if (doc && predicate(doc)) return doc;
    } finally {
      await ctx.dispose();
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`文档 ${filename} 在 ${timeoutMs}ms 内未达到预期状态`);
}

/** 后端上传根目录下的文件绝对路径（backend 以 ./uploads 为上传根目录）。 */
function backendUploadPath(filename: string): string {
  return path.resolve(process.cwd(), '..', 'backend', 'uploads', filename);
}

const rowOf = (page: import('@playwright/test').Page, title: string) =>
  page.locator('[data-testid="novel-list-item"]').filter({ hasText: title });

cleanupTest.describe('索引失败手动重试 - E2E (#65)', () => {
  cleanupTest.beforeEach(async () => {
    // 后台索引含真实 bge-m3 embedding 推理（CPU），放宽单个用例超时。
    cleanupTest.setTimeout(120_000);
  });

  cleanupTest('failed 行重新索引：失败 → 重试 → 就绪，其余状态不可重试', async ({
    page,
    uploadedDocs,
  }) => {
    // 空内容上传 → 后台索引失败（DocumentEmptyError → failed）。
    const badFilename = `retry-${Date.now()}.txt`;
    const badTitle = badFilename.replace(/\.txt$/, '');
    await uploadViaApi(badFilename, '');
    const failedDoc = await pollStatus(
      badFilename,
      (d) => d.status === 'failed'
    );
    expect(failedDoc.error_message).toBeTruthy();

    // 对照组：正常小说索引至就绪（非 failed 不可重试）。
    const goodFilename = `retry-good-${Date.now()}.txt`;
    const goodTitle = goodFilename.replace(/\.txt$/, '');
    await uploadViaApi(goodFilename, '对照组小说内容');
    const goodDoc = await pollStatus(
      goodFilename,
      (d) => d.status === 'ready'
    );

    // API 契约：非 failed 小说调用重试端点被 409 拒绝。
    const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
    try {
      const res = await ctx.post(`/api/documents/${goodDoc.id}/reindex`);
      expect(res.status()).toBe(409);
    } finally {
      await ctx.dispose();
    }

    // 修复磁盘文件内容，模拟失败原因被修正后重试。
    fs.writeFileSync(
      backendUploadPath(badFilename),
      `重试成功的小说内容。${'蓝色独角兽漫游星海。'.repeat(30)}`,
      'utf-8'
    );

    await page.goto('/admin');
    await page.waitForLoadState('networkidle').catch(() => {});

    // failed 行：失败徽标、错误信息与「重新索引」按钮可见。
    const badRow = rowOf(page, badTitle);
    await expect(badRow).toBeVisible({ timeout: 15_000 });
    await expect(badRow.locator('[data-testid="doc-status-badge"]')).toContainText('失败');
    await expect(badRow.locator('[data-testid="doc-error-message"]')).toBeVisible();
    await expect(badRow.locator('[data-testid="novel-reindex-btn"]')).toBeVisible();

    // 就绪行：状态徽标就绪，且没有「重新索引」按钮（非 failed 不可重试）。
    const goodRow = rowOf(page, goodTitle);
    await expect(goodRow.locator('[data-testid="doc-status-badge"]')).toContainText(
      '就绪',
      { timeout: 15_000 }
    );
    await expect(goodRow.locator('[data-testid="novel-reindex-btn"]')).toHaveCount(0);

    // 点击重新索引：重置 pending 重新入队，列表轮询自动跟进直至就绪。
    await badRow.locator('[data-testid="novel-reindex-btn"]').click();
    await expect(badRow.locator('[data-testid="doc-status-badge"]')).toContainText(
      '就绪',
      { timeout: 90_000 }
    );
    // 就绪后失败原因消失、按钮隐藏。
    await expect(badRow.locator('[data-testid="doc-error-message"]')).toHaveCount(0);
    await expect(badRow.locator('[data-testid="novel-reindex-btn"]')).toHaveCount(0);

    await uploadedDocs.track(badFilename);
    await uploadedDocs.track(goodFilename);
  });
});
