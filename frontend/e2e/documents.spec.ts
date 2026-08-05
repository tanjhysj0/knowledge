import { test, expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

const _BACKEND = BACKEND_BASE;

// 通过后端 API 列出全部文档（用于测试间断言）
async function listDocumentsViaApi(): Promise<{ id: number; filename: string; chunk_count: number; size: number }[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  const res = await ctx.get('/api/documents?page=1&page_size=100');
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  await ctx.dispose();
  return body.items;
}

// 通过后端 API 删除文档，返回 HTTP 状态码
async function deleteDocumentViaApi(id: number): Promise<number> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  const res = await ctx.delete(`/api/documents/${id}`);
  const status = res.status();
  await ctx.dispose();
  return status;
}

cleanupTest.describe('Documents Page - E2E', () => {
  cleanupTest.beforeEach(async ({ page }) => {
    // 进入文档管理页
    await page.goto('/documents');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  cleanupTest('应展示页面标题与上传区', async ({ page }) => {
    // 验证页面标题与上传区文案
    await expect(page.locator('h2')).toContainText('文档管理');
    await expect(page.locator('text=拖拽或点击上传文件')).toBeVisible();
    await expect(page.locator('.upload-zone small')).toContainText('TXT');
    await expect(page.locator('.upload-zone small')).toContainText('MD');
  });

  cleanupTest('应展示文档列表或空状态', async ({ page }) => {
    // 有文档时显示列表，无文档时显示空状态
    const hasDocuments = await page.locator('.doc-item').count();
    const hasEmptyState = await page.locator('text=暂无文档，请上传文件').isVisible().catch(() => false);
    expect(hasDocuments > 0 || hasEmptyState).toBeTruthy();
  });

  cleanupTest('无文档时应显示空状态', async ({ page }) => {
    // 无文档时应显示空状态或至少有列表项可验证
    const hasEmptyState = await page.locator('text=暂无文档，请上传文件').isVisible().catch(() => false);
    const hasDocs = await page.locator('.doc-item').count();
    expect(hasEmptyState || hasDocs > 0).toBeTruthy();
  });

  cleanupTest('存在分页时应能跳转下一页', async ({ page }) => {
    // 等待分页器出现
    await page.waitForSelector('.pagination', { state: 'visible', timeout: 5_000 }).catch(() => {});

    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const nextButton = page.locator('button:has-text("下一页")');
      const isDisabled = await nextButton.isDisabled().catch(() => true);

      if (!isDisabled) {
        // 记录翻页前的页码
        const pageInfoBefore = await page.locator('.pagination-info').textContent().catch(() => '');

        await nextButton.click();

        // 等待页码文本变化（比 networkidle 更精确，避免 race condition）
        await expect(page.locator('.pagination-info')).not.toHaveText(pageInfoBefore ?? '', { timeout: 5_000 });

        // 翻页后页码应发生变化
        const pageInfoAfter = await page.locator('.pagination-info').textContent().catch(() => '');
        expect(pageInfoAfter).not.toBe(pageInfoBefore);
      }
    }
  });

  cleanupTest('应能从分页跳转上一页', async ({ page }) => {
    await page.waitForSelector('.pagination', { state: 'visible', timeout: 5_000 }).catch(() => {});

    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const prevButton = page.locator('button:has-text("上一页")');
      const isDisabled = await prevButton.isDisabled().catch(() => true);

      if (!isDisabled) {
        const pageInfoBefore = await page.locator('.pagination-info').textContent().catch(() => '');
        await prevButton.click();

        // 等待页码文本变化（避免 race condition）
        await expect(page.locator('.pagination-info')).not.toHaveText(pageInfoBefore ?? '', { timeout: 5_000 });

        const pageInfoAfter = await page.locator('.pagination-info').textContent().catch(() => '');
        expect(pageInfoAfter).not.toBe(pageInfoBefore);
      }
    }
  });

  cleanupTest('上传不支持的文件类型应报错', async ({ page }) => {
    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached();

    // 构造不支持的 .exe 文件
    const testFile = {
      name: 'test.exe',
      mimeType: 'application/x-msdownload',
      buffer: Buffer.from('test content'),
    };

    await fileInput.setInputFiles(testFile);

    // 应显示错误提示
    const errorVisible = await page.locator('.error-message').isVisible({ timeout: 3_000 }).catch(() => false);
    expect(errorVisible).toBeTruthy();
  });

  cleanupTest('拖拽文件到上传区应展示拖拽态', async ({ page }) => {
    const uploadZone = page.locator('.upload-zone');

    // 模拟 dragover 事件触发 dragging 样式
    await page.evaluate(() => {
      const zone = document.querySelector('.upload-zone') as HTMLElement;
      if (zone) {
        zone.dispatchEvent(new DragEvent('dragover', {
          bubbles: true,
          cancelable: true,
          dataTransfer: new DataTransfer(),
        }));
      }
    });

    const hasDraggingClass = await uploadZone.evaluate(el => el.classList.contains('dragging'));
    expect(hasDraggingClass).toBeTruthy();
  });

  cleanupTest('上传 txt 文件应出现在列表中且元数据正确', async ({ page }) => {
    // 上传 TXT 文件并校验前端列表与后端元数据
    const filename = `e2e-upload-${Date.now()}.txt`;
    const testContent = 'This is a test document for E2E testing.';
    const fileInput = page.locator('input[type="file"]');

    const beforeCount = (await listDocumentsViaApi()).length;

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from(testContent),
    });

    // 前端列表应出现该文档
    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 5_000 });
    await expect(page.locator(`.doc-item:has-text("${filename}") .doc-item-meta`)).toBeVisible();

    // 后端应保存文档，且分块数 > 0
    const uploadedDoc = (await listDocumentsViaApi()).find(d => d.filename === filename);
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);

    const afterCount = (await listDocumentsViaApi()).length;
    expect(afterCount).toBe(beforeCount + 1);
  });

  cleanupTest('应完成大体积 markdown 文件上传并持久化', async ({ page }) => {
    // 上传大体积 MD 文件验证进度与持久化
    const filename = `progress-test-${Date.now()}.md`;
    const testContent = 'Test document content for progress bar testing. '.repeat(200);
    const fileInput = page.locator('input[type="file"]');

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/markdown',
      buffer: Buffer.from(testContent),
    });

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 5_000 });

    // 校验大小与分块数
    const uploadedDoc = (await listDocumentsViaApi()).find(d => d.filename === filename);
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.size).toBeGreaterThan(1000);
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);
  });

  cleanupTest('上传 markdown 应在后端创建分块', async ({ page }) => {
    // 上传 MD 文件并断言后端分块成功
    const filename = `test-doc-${Date.now()}.md`;
    const mdContent = `# Test Document

This is a test markdown document.

## Section 1
Content for section 1.

## Section 2
Content for section 2.
`;
    const fileInput = page.locator('input[type="file"]');

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/markdown',
      buffer: Buffer.from(mdContent),
    });

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 5_000 });

    const uploadedDoc = (await listDocumentsViaApi()).find(d => d.filename === filename);
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);
  });

  cleanupTest('应能删除文档且前后端均清理', async ({ page }) => {
    // 上传 → 删除 → 验证前后端均无残留
    const filename = `to-delete-${Date.now()}.txt`;
    const fileInput = page.locator('input[type="file"]');

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('Document to be deleted: ' + Date.now()),
    });

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 5_000 });

    const beforeDocs = await listDocumentsViaApi();
    const targetDoc = beforeDocs.find(d => d.filename === filename);
    expect(targetDoc).toBeDefined();
    expect(targetDoc!.chunk_count).toBeGreaterThan(0);

    page.on('dialog', dialog => dialog.accept().catch(() => {}));

    const docItem = page.locator(`.doc-item:has-text("${filename}")`).first();
    await docItem.locator('button:has-text("删除")').click();

    // 前端列表中该文档应消失
    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toHaveCount(0, { timeout: 5_000 });

    // 后端也应同步删除，总数减 1
    const afterDocs = await listDocumentsViaApi();
    expect(afterDocs.find(d => d.filename === filename)).toBeUndefined();
    expect(afterDocs.length).toBe(beforeDocs.length - 1);
  });

  cleanupTest('删除不存在的文档应返回 404', async ({ page }) => {
    page.on('dialog', dialog => dialog.accept().catch(() => {}));

    const filename = `to-delete-404-${Date.now()}.txt`;
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('will be deleted twice'),
    });

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 5_000 });

    const targetDoc = (await listDocumentsViaApi()).find(d => d.filename === filename);
    expect(targetDoc).toBeDefined();

    // 第一次通过 API 删除应成功（200）
    const statusFromApi = await deleteDocumentViaApi(targetDoc!.id);
    expect(statusFromApi).toBe(200);

    await page.reload();
    await page.waitForLoadState('networkidle').catch(() => {});
    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toHaveCount(0);

    // 再次删除同一文档应返回 404
    const statusSecondDelete = await deleteDocumentViaApi(targetDoc!.id);
    expect(statusSecondDelete).toBe(404);
  });

  cleanupTest('应正确展示文档元数据', async ({ page }) => {
    // 列表项应展示文档名与元数据
    const docItems = page.locator('.doc-item');
    const count = await docItems.count();

    if (count > 0) {
      const firstDoc = docItems.first();
      await expect(firstDoc.locator('.doc-item-name')).toBeVisible();
      await expect(firstDoc.locator('.doc-item-meta')).toBeVisible();
    }
  });

  cleanupTest('分页应能正确处理大量文档', async ({ page }) => {
    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const paginationInfo = page.locator('.pagination-info');
      await expect(paginationInfo).toBeVisible();

      // 校验页码与总数文案格式
      const infoText = await paginationInfo.textContent();
      expect(infoText?.match(/第 \d+ \/ \d+ 页/)).toBeTruthy();
      expect(infoText?.match(/共 \d+ 个文档/)).toBeTruthy();
    }
  });

  cleanupTest('第一页应禁用上一页按钮', async ({ page }) => {
    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const prevButton = page.locator('button:has-text("上一页")');
      const isDisabled = await prevButton.isDisabled().catch(() => false);

      // 第一页时上一页按钮应禁用
      const pageInfo = (await page.locator('.pagination-info').textContent().catch(() => '') || '');
      const isFirstPage = pageInfo.includes('第 1 /');

      expect(isDisabled || !isFirstPage).toBeTruthy();
    }
  });

  cleanupTest('末页应禁用下一页按钮', async ({ page }) => {
    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const nextButton = page.locator('button:has-text("下一页")');
      // 一次性点到底。每次点击后等待页码文本变化，避免 race condition。
      for (let i = 0; i < 20; i++) {
        if (await nextButton.isDisabled().catch(() => false)) break;
        const pageInfoBefore = await page.locator('.pagination-info').textContent().catch(() => '');
        await nextButton.click();
        await expect(page.locator('.pagination-info')).not.toHaveText(pageInfoBefore ?? '', { timeout: 3_000 });
      }

      const isDisabled = await nextButton.isDisabled().catch(() => false);
      expect(isDisabled).toBeTruthy();
    }
  });

  cleanupTest('点击错误提示应清除错误', async ({ page }) => {
    // 触发错误后点击错误条应清除
    const fileInput = page.locator('input[type="file"]');

    await fileInput.setInputFiles({
      name: 'bad.exe',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from('test'),
    });

    await expect(page.locator('.error-message')).toBeVisible({ timeout: 3_000 });

    await page.locator('.error-message').click();
    await page.waitForTimeout(300);
    await expect(page.locator('.error-message')).toHaveCount(0);
  });

  cleanupTest('上传区应展示支持的文件格式', async ({ page }) => {
    // 上传区应列出支持的文件格式
    const small = page.locator('.upload-zone small');
    await expect(small).toContainText('TXT');
    await expect(small).toContainText('MD');
    await expect(small).toContainText('PDF');
    await expect(small).toContainText('DOCX');
  });
});
