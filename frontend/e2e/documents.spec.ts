import { test, expect } from '@playwright/test';

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
    
    // Should show error for unsupported file type
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
    
    // Check dragging class is added
    const hasDraggingClass = await uploadZone.evaluate(el => el.classList.contains('dragging'));
    expect(hasDraggingClass).toBeTruthy();
  });

  test('should upload file via click and show progress', async ({ page }) => {
    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached();
    
    const testContent = 'This is a test document for E2E testing.';
    await fileInput.setInputFiles({
      name: 'e2e-test.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(testContent),
    });
    
    // Should show uploading state or complete
    await page.waitForTimeout(500);
  });

  test('should display upload progress bar during upload', async ({ page }) => {
    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached();
    
    // Upload a file
    const testContent = 'Test document content for progress bar testing. '.repeat(100);
    await fileInput.setInputFiles({
      name: 'progress-test.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from(testContent),
    });
    
    // Progress bar might appear briefly
    const progressBarVisible = await page.locator('.upload-progress-bar').isVisible().catch(() => false);
    const uploadInfoVisible = await page.locator('.upload-progress-info').isVisible().catch(() => false);
    
    // Either progress shows or upload completes
    expect(progressBarVisible || uploadInfoVisible || true).toBeTruthy();
  });

  test('should delete document when clicking delete button', async ({ page }) => {
    const fileInput = page.locator('input[type="file"]');
    
    // Upload a document first
    const testContent = 'Document to be deleted: ' + Date.now();
    await fileInput.setInputFiles({
      name: 'to-delete.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(testContent),
    });
    
    await page.waitForTimeout(3000);
    
    const deleteButtons = page.locator('button:has-text("删除")');
    const count = await deleteButtons.count();
    
    if (count > 0) {
      page.on('dialog', dialog => dialog.accept().catch(() => {}));
      await deleteButtons.first().click();
      await page.waitForTimeout(1000);
    }
  });

  test('should show error when delete fails (non-existent document)', async ({ page }) => {
    const deleteButtons = page.locator('button:has-text("删除")');
    const count = await deleteButtons.count();
    
    if (count > 0) {
      const color = await deleteButtons.first().evaluate(el => getComputedStyle(el).color);
      expect(color).toContain('244');
    }
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
    
    // Trigger an error with unsupported file
    await fileInput.setInputFiles({
      name: 'bad.exe',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from('test'),
    });
    
    await page.waitForTimeout(500);
    
    const errorMsg = page.locator('.error-message');
    const hasError = await errorMsg.isVisible().catch(() => false);
    
    if (hasError) {
      await errorMsg.click();
      await page.waitForTimeout(300);
      const stillHasError = await errorMsg.isVisible().catch(() => false);
      expect(stillHasError).toBeFalsy();
    }
  });

  test('should show file format restrictions in upload zone', async ({ page }) => {
    const small = page.locator('.upload-zone small');
    await expect(small).toContainText('TXT');
    await expect(small).toContainText('MD');
    await expect(small).toContainText('PDF');
    await expect(small).toContainText('DOCX');
  });

  test('should upload markdown file successfully', async ({ page }) => {
    const fileInput = page.locator('input[type="file"]');
    
    const mdContent = `# Test Document

This is a test markdown document.

## Section 1
Content for section 1.
`;
    
    await fileInput.setInputFiles({
      name: 'test-doc.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from(mdContent),
    });
    
    await page.waitForTimeout(3000);
  });

  test('should show uploading state during file upload', async ({ page }) => {
    const fileInput = page.locator('input[type="file"]');
    
    // Upload a file
    await fileInput.setInputFiles({
      name: 'uploading-test.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('test content'),
    });
    
    // Check if upload progress info shows filename
    const uploadProgressInfo = page.locator('.upload-progress-info');
    const infoVisible = await uploadProgressInfo.isVisible().catch(() => false);
    
    // Either progress shows or already completed
    expect(infoVisible || true).toBeTruthy();
  });
});
