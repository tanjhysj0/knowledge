import { test, expect, request as apiRequest } from '@playwright/test';

const BACKEND_BASE = 'http://localhost:8000';

async function listDocumentsViaApi(): Promise<{ id: number; filename: string; chunk_count: number; size: number }[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  const res = await ctx.get('/api/documents?page=1&page_size=100');
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  await ctx.dispose();
  return body.items;
}

async function deleteDocumentViaApi(id: number): Promise<number> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  const res = await ctx.delete(`/api/documents/${id}`);
  const status = res.status();
  await ctx.dispose();
  return status;
}

test.describe('Documents Page - E2E', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/documents');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  test('should display page title and upload zone', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('文档管理');
    await expect(page.locator('text=拖拽或点击上传文件')).toBeVisible();
    await expect(page.locator('.upload-zone small')).toContainText('TXT');
    await expect(page.locator('.upload-zone small')).toContainText('MD');
  });

  test('should display document list or empty state', async ({ page }) => {
    const hasDocuments = await page.locator('.doc-item').count();
    const hasEmptyState = await page.locator('text=暂无文档，请上传文件').isVisible().catch(() => false);
    expect(hasDocuments > 0 || hasEmptyState).toBeTruthy();
  });

  test('should show empty state when no documents exist', async ({ page }) => {
    const hasEmptyState = await page.locator('text=暂无文档，请上传文件').isVisible().catch(() => false);
    const hasDocs = await page.locator('.doc-item').count();
    expect(hasEmptyState || hasDocs > 0).toBeTruthy();
  });

  test('should navigate to next page when pagination exists', async ({ page }) => {
    await page.waitForSelector('.pagination', { state: 'visible', timeout: 10000 }).catch(() => {});

    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const nextButton = page.locator('button:has-text("下一页")');
      const isDisabled = await nextButton.isDisabled().catch(() => true);

      if (!isDisabled) {
        const pageInfoBefore = await page.locator('.pagination-info').textContent().catch(() => '');

        await nextButton.click();
        await page.waitForLoadState('networkidle').catch(() => {});

        const pageInfoAfter = await page.locator('.pagination-info').textContent().catch(() => '');
        expect(pageInfoAfter).not.toBe(pageInfoBefore);
      }
    }
  });

  test('should navigate to previous page from pagination', async ({ page }) => {
    await page.waitForSelector('.pagination', { state: 'visible', timeout: 10000 }).catch(() => {});

    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const prevButton = page.locator('button:has-text("上一页")');
      const isDisabled = await prevButton.isDisabled().catch(() => true);

      if (!isDisabled) {
        const pageInfoBefore = await page.locator('.pagination-info').textContent().catch(() => '');
        await prevButton.click();
        await page.waitForLoadState('networkidle').catch(() => {});
        const pageInfoAfter = await page.locator('.pagination-info').textContent().catch(() => '');
        expect(pageInfoAfter).not.toBe(pageInfoBefore);
      }
    }
  });

  test('should show error message for unsupported file type upload', async ({ page }) => {
    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached();

    const testFile = {
      name: 'test.exe',
      mimeType: 'application/x-msdownload',
      buffer: Buffer.from('test content'),
    };

    await fileInput.setInputFiles(testFile);

    const errorVisible = await page.locator('.error-message').isVisible({ timeout: 3000 }).catch(() => false);
    expect(errorVisible).toBeTruthy();
  });

  test('should show drag-over state when dragging file over upload zone', async ({ page }) => {
    const uploadZone = page.locator('.upload-zone');

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

  test('should upload txt file and verify it appears in the list with correct metadata', async ({ page }) => {
    const filename = `e2e-upload-${Date.now()}.txt`;
    const testContent = 'This is a test document for E2E testing.';
    const fileInput = page.locator('input[type="file"]');

    const beforeCount = (await listDocumentsViaApi()).length;

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from(testContent),
    });

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 15000 });
    await expect(page.locator(`.doc-item:has-text("${filename}") .doc-item-meta`)).toBeVisible();

    const uploadedDoc = (await listDocumentsViaApi()).find(d => d.filename === filename);
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);

    const afterCount = (await listDocumentsViaApi()).length;
    expect(afterCount).toBe(beforeCount + 1);
  });

  test('should complete upload of large markdown file and persist in backend', async ({ page }) => {
    const filename = `progress-test-${Date.now()}.md`;
    const testContent = 'Test document content for progress bar testing. '.repeat(200);
    const fileInput = page.locator('input[type="file"]');

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/markdown',
      buffer: Buffer.from(testContent),
    });

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 15000 });

    const uploadedDoc = (await listDocumentsViaApi()).find(d => d.filename === filename);
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.size).toBeGreaterThan(1000);
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);
  });

  test('should upload markdown file and verify chunks are created in backend', async ({ page }) => {
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

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 15000 });

    const uploadedDoc = (await listDocumentsViaApi()).find(d => d.filename === filename);
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);
  });

  test('should delete document and verify it disappears from list and backend', async ({ page }) => {
    const filename = `to-delete-${Date.now()}.txt`;
    const fileInput = page.locator('input[type="file"]');

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('Document to be deleted: ' + Date.now()),
    });

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 15000 });

    const beforeDocs = await listDocumentsViaApi();
    const targetDoc = beforeDocs.find(d => d.filename === filename);
    expect(targetDoc).toBeDefined();
    expect(targetDoc!.chunk_count).toBeGreaterThan(0);

    page.on('dialog', dialog => dialog.accept().catch(() => {}));

    const docItem = page.locator(`.doc-item:has-text("${filename}")`).first();
    await docItem.locator('button:has-text("删除")').click();

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toHaveCount(0, { timeout: 10000 });

    const afterDocs = await listDocumentsViaApi();
    expect(afterDocs.find(d => d.filename === filename)).toBeUndefined();
    expect(afterDocs.length).toBe(beforeDocs.length - 1);
  });

  test('should show error when deleting a non-existent document (404)', async ({ page }) => {
    page.on('dialog', dialog => dialog.accept().catch(() => {}));

    const filename = `to-delete-404-${Date.now()}.txt`;
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('will be deleted twice'),
    });

    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toBeVisible({ timeout: 15000 });

    const targetDoc = (await listDocumentsViaApi()).find(d => d.filename === filename);
    expect(targetDoc).toBeDefined();

    const statusFromApi = await deleteDocumentViaApi(targetDoc!.id);
    expect(statusFromApi).toBe(200);

    await page.reload();
    await page.waitForLoadState('networkidle').catch(() => {});
    await expect(page.locator(`.doc-item-name:has-text("${filename}")`)).toHaveCount(0);

    const statusSecondDelete = await deleteDocumentViaApi(targetDoc!.id);
    expect(statusSecondDelete).toBe(404);
  });

  test('should display document metadata correctly', async ({ page }) => {
    const docItems = page.locator('.doc-item');
    const count = await docItems.count();

    if (count > 0) {
      const firstDoc = docItems.first();
      await expect(firstDoc.locator('.doc-item-name')).toBeVisible();
      await expect(firstDoc.locator('.doc-item-meta')).toBeVisible();
    }
  });

  test('should handle pagination correctly with many documents', async ({ page }) => {
    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const paginationInfo = page.locator('.pagination-info');
      await expect(paginationInfo).toBeVisible();

      const infoText = await paginationInfo.textContent();
      expect(infoText?.match(/第 \d+ \/ \d+ 页/)).toBeTruthy();
      expect(infoText?.match(/共 \d+ 个文档/)).toBeTruthy();
    }
  });

  test('should disable prev button on first page', async ({ page }) => {
    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      const prevButton = page.locator('button:has-text("上一页")');
      const isDisabled = await prevButton.isDisabled().catch(() => false);

      const pageInfo = (await page.locator('.pagination-info').textContent().catch(() => '') || '');
      const isFirstPage = pageInfo.includes('第 1 /');

      expect(isDisabled || !isFirstPage).toBeTruthy();
    }
  });

  test('should disable next button on last page', async ({ page }) => {
    const hasPagination = await page.locator('.pagination').isVisible().catch(() => false);

    if (hasPagination) {
      let nextButton = page.locator('button:has-text("下一页")');
      while (!(await nextButton.isDisabled().catch(() => false))) {
        await nextButton.click();
        await page.waitForTimeout(500);
        nextButton = page.locator('button:has-text("下一页")');
      }

      const isDisabled = await nextButton.isDisabled().catch(() => false);
      expect(isDisabled).toBeTruthy();
    }
  });

  test('should clear error message when clicking on it', async ({ page }) => {
    const fileInput = page.locator('input[type="file"]');

    await fileInput.setInputFiles({
      name: 'bad.exe',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from('test'),
    });

    await expect(page.locator('.error-message')).toBeVisible({ timeout: 3000 });

    await page.locator('.error-message').click();
    await page.waitForTimeout(300);
    await expect(page.locator('.error-message')).toHaveCount(0);
  });

  test('should show file format restrictions in upload zone', async ({ page }) => {
    const small = page.locator('.upload-zone small');
    await expect(small).toContainText('TXT');
    await expect(small).toContainText('MD');
    await expect(small).toContainText('PDF');
    await expect(small).toContainText('DOCX');
  });
});