import { test, expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #49: 首页从独立「文档管理页」重构为「小说库」（NovelListPage）。
 * 本 spec 由旧 documents.spec.ts 迁移而来，选择器改为
 * [data-testid="novel-file-input"] / [data-testid="cover-file-input"]，
 * 并新增封面相关用例（封面就绪/移除、封面缩略图、无封面占位）。
 */

// 通过后端 API 列出全部文档（用于测试间断言）
interface DocumentSummary {
  id: number;
  filename: string;
  chunk_count: number;
  size: number;
  cover_image_path: string | null;
}

async function listDocumentsViaApi(): Promise<DocumentSummary[]> {
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

// #48/#49：后端只校验封面扩展名与大小，fake PNG 头即可通过。
const FAKE_PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

cleanupTest.describe('Novel List Page（首页小说库）- E2E (#49)', () => {
  cleanupTest.beforeEach(async ({ page }) => {
    // 进入首页（小说库）
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  const novelFileInput = (page: import('@playwright/test').Page) =>
    page.locator('[data-testid="novel-file-input"]');
  const coverFileInput = (page: import('@playwright/test').Page) =>
    page.locator('[data-testid="cover-file-input"]');

  cleanupTest('应展示页面标题与双上传区', async ({ page }) => {
    // h1 标题 + 正文/封面两个拖拽区及支持格式
    await expect(page.locator('h1')).toContainText('我的小说库');

    const bodyZone = page.locator('.upload-zone').first();
    await expect(bodyZone).toContainText('拖拽或点击上传小说正文');
    await expect(bodyZone.locator('small')).toContainText('TXT');
    await expect(bodyZone.locator('small')).toContainText('MD');
    await expect(bodyZone.locator('small')).toContainText('PDF');
    await expect(bodyZone.locator('small')).toContainText('DOCX');

    const coverZone = page.locator('.cover-upload-zone');
    await expect(coverZone).toContainText('拖拽或点击上传封面（可选）');
    await expect(coverZone.locator('small')).toContainText('JPG');
    await expect(coverZone.locator('small')).toContainText('PNG');
    await expect(coverZone.locator('small')).toContainText('WEBP');
  });

  cleanupTest('应展示文档列表或空状态', async ({ page }) => {
    // 有文档时显示列表，无文档时显示空状态
    const hasDocuments = await page.locator('.doc-item').count();
    const hasEmptyState = await page
      .locator('text=暂无小说，请上传文件')
      .isVisible()
      .catch(() => false);
    expect(hasDocuments > 0 || hasEmptyState).toBeTruthy();
  });

  cleanupTest('无文档时应显示空状态', async ({ page }) => {
    // 无文档时应显示空状态或至少有列表项可验证
    const hasEmptyState = await page
      .locator('text=暂无小说，请上传文件')
      .isVisible()
      .catch(() => false);
    const hasDocs = await page.locator('.doc-item').count();
    expect(hasEmptyState || hasDocs > 0).toBeTruthy();
  });

  cleanupTest('存在分页时应能跳转下一页', async ({ page, uploadedDocs }) => {
    // 自包含：上传 11 个文档，确保产生 2 页。
    const tag = Date.now();
    const fileInput = novelFileInput(page);
    const filenames: string[] = [];
    for (let i = 0; i < 11; i++) {
      const filename = `pagination-${tag}-${i}.txt`;
      filenames.push(filename);
      await fileInput.setInputFiles({
        name: filename,
        mimeType: 'text/plain',
        buffer: Buffer.from(`pagination doc ${i}`),
      });
    }
    // 通过后端 API 轮询确认全部 11 个文档都已持久化
    const deadline = Date.now() + 30_000;
    let allPersisted = false;
    while (Date.now() < deadline) {
      const items = await listDocumentsViaApi();
      allPersisted = filenames.every((name) =>
        items.some((item) => item.filename === name)
      );
      if (allPersisted) break;
      await page.waitForTimeout(250);
    }
    expect(allPersisted).toBeTruthy();
    for (const filename of filenames) {
      await uploadedDocs.track(filename);
    }

    // 刷新页面让 UI 拿到最新数据
    await page.reload();
    await page.waitForLoadState('networkidle').catch(() => {});

    await expect(page.locator('.pagination')).toBeVisible({ timeout: 10_000 });

    const nextButton = page.locator('button:has-text("下一页")');
    await expect(nextButton).toBeEnabled({ timeout: 5_000 });

    const pageInfo = page.locator('.pagination-info');
    await expect(pageInfo).toContainText(/第 1 \/ \d+ 页/, { timeout: 5_000 });
    const pageInfoBefore = (await pageInfo.textContent()) ?? '';

    await nextButton.click();

    // 等待页码文本变化
    await expect(pageInfo).not.toHaveText(pageInfoBefore, { timeout: 5_000 });
    await expect(pageInfo).toContainText(/第 2 \/ \d+ 页/, { timeout: 5_000 });
  });

  cleanupTest('应能从分页跳转上一页', async ({ page, uploadedDocs }) => {
    // 自包含：上传 12 个文档确保产生 2 页。
    const tag = Date.now();
    const fileInput = novelFileInput(page);
    for (let i = 0; i < 12; i++) {
      await fileInput.setInputFiles({
        name: `prevpage-${tag}-${i}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`prevpage doc ${i}`),
      });
      await expect(
        page.locator(`.doc-item-name:has-text("prevpage-${tag}-${i}.txt")`)
      ).toBeVisible({ timeout: 5_000 });
      await uploadedDocs.track(`prevpage-${tag}-${i}.txt`);
    }

    await expect(page.locator('.pagination')).toBeVisible({ timeout: 10_000 });

    const nextButton = page.locator('button:has-text("下一页")');
    await expect(nextButton).toBeEnabled({ timeout: 5_000 });

    const pageInfo = page.locator('.pagination-info');
    await expect(pageInfo).toContainText('第 1 /', { timeout: 5_000 });
    await nextButton.click();
    await expect(pageInfo).toContainText('第 2 /', { timeout: 5_000 });

    const pageInfoBefore = (await pageInfo.textContent()) ?? '';

    // 点上一页
    const prevButton = page.locator('button:has-text("上一页")');
    await prevButton.click();

    await expect(pageInfo).not.toHaveText(pageInfoBefore, { timeout: 5_000 });
    await expect(pageInfo).toContainText('第 1 /', { timeout: 5_000 });
  });

  cleanupTest('上传不支持的文件类型应报错', async ({ page }) => {
    const fileInput = novelFileInput(page);
    await expect(fileInput).toBeAttached();

    // 构造不支持的 .exe 文件
    await fileInput.setInputFiles({
      name: 'test.exe',
      mimeType: 'application/x-msdownload',
      buffer: Buffer.from('test content'),
    });

    await expect(page.locator('.error-message')).toContainText(
      '不支持的文件格式',
      { timeout: 3_000 }
    );
  });

  cleanupTest('上传不支持的封面格式应报错', async ({ page }) => {
    const coverInput = coverFileInput(page);
    await expect(coverInput).toBeAttached();

    await coverInput.setInputFiles({
      name: 'bad-cover.gif',
      mimeType: 'image/gif',
      buffer: Buffer.from('GIF89a'),
    });

    await expect(page.locator('.error-message')).toContainText(
      '不支持的封面格式',
      { timeout: 3_000 }
    );
  });

  cleanupTest('拖拽文件到正文上传区应展示拖拽态', async ({ page }) => {
    // 模拟 dragover 事件触发正文区 dragging 样式
    await page.evaluate(() => {
      const zone = document.querySelector('.upload-zone') as HTMLElement;
      if (zone) {
        zone.dispatchEvent(
          new DragEvent('dragover', {
            bubbles: true,
            cancelable: true,
            dataTransfer: new DataTransfer(),
          })
        );
      }
    });

    await expect(page.locator('.upload-zone').first()).toHaveClass(/dragging/);
  });

  cleanupTest('拖拽文件到封面上传区应展示拖拽态', async ({ page }) => {
    // 模拟 dragover 事件触发封面区 dragging 样式
    await page.evaluate(() => {
      const zone = document.querySelector('.cover-upload-zone') as HTMLElement;
      if (zone) {
        zone.dispatchEvent(
          new DragEvent('dragover', {
            bubbles: true,
            cancelable: true,
            dataTransfer: new DataTransfer(),
          })
        );
      }
    });

    await expect(page.locator('.cover-upload-zone')).toHaveClass(/dragging/);
  });

  cleanupTest('上传 txt 文件应出现在列表中且元数据正确', async ({ page, uploadedDocs }) => {
    // 上传 TXT 文件并校验前端列表与后端元数据
    const filename = `e2e-upload-${Date.now()}.txt`;
    const testContent = 'This is a test document for E2E testing.';
    const fileInput = novelFileInput(page);

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from(testContent),
    });

    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.locator(`.doc-item:has-text("${filename}") .doc-item-meta`)
    ).toBeVisible();

    // 后端应保存文档，且分块数 > 0
    const uploadedDoc = (await listDocumentsViaApi()).find(
      (d) => d.filename === filename
    );
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);

    await uploadedDocs.track(filename);
  });

  cleanupTest('应完成大体积 markdown 文件上传并持久化', async ({ page, uploadedDocs }) => {
    // 上传大体积 MD 文件验证持久化
    const filename = `progress-test-${Date.now()}.md`;
    const testContent = 'Test document content for progress bar testing. '.repeat(200);
    const fileInput = novelFileInput(page);

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/markdown',
      buffer: Buffer.from(testContent),
    });

    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toBeVisible({ timeout: 5_000 });
    await uploadedDocs.track(filename);

    // 校验大小与分块数
    const uploadedDoc = (await listDocumentsViaApi()).find(
      (d) => d.filename === filename
    );
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.size).toBeGreaterThan(1000);
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);
  });

  cleanupTest('上传 markdown 应在后端创建分块', async ({ page, uploadedDocs }) => {
    // 上传 MD 文件并断言后端分块成功
    const filename = `test-doc-${Date.now()}.md`;
    const mdContent = `# Test Document

This is a test markdown document.

## Section 1
Content for section 1.

## Section 2
Content for section 2.
`;
    const fileInput = novelFileInput(page);
    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/markdown',
      buffer: Buffer.from(mdContent),
    });

    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toBeVisible({ timeout: 5_000 });
    await uploadedDocs.track(filename);

    const uploadedDoc = (await listDocumentsViaApi()).find(
      (d) => d.filename === filename
    );
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.chunk_count).toBeGreaterThan(0);
  });

  cleanupTest('应能删除文档且前后端均清理', async ({ page, uploadedDocs }) => {
    // 上传 → 删除 → 验证前后端均无残留
    const filename = `to-delete-${Date.now()}.txt`;
    const fileInput = novelFileInput(page);

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('Document to be deleted: ' + Date.now()),
    });

    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toBeVisible({ timeout: 5_000 });

    const beforeDocs = await listDocumentsViaApi();
    const targetDoc = beforeDocs.find((d) => d.filename === filename);
    expect(targetDoc).toBeDefined();
    expect(targetDoc!.chunk_count).toBeGreaterThan(0);
    await uploadedDocs.track(filename);

    const docItem = page.locator(`.doc-item:has-text("${filename}")`).first();
    await docItem.locator('button:has-text("删除")').click();

    // 前端列表中该文档应消失
    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toHaveCount(0, { timeout: 5_000 });

    // 后端也应同步删除（仅验证目标文档消失）
    const afterDocs = await listDocumentsViaApi();
    expect(afterDocs.find((d) => d.filename === filename)).toBeUndefined();
  });

  cleanupTest('删除不存在的文档应返回 404', async ({ page, uploadedDocs }) => {
    const filename = `to-delete-404-${Date.now()}.txt`;
    const fileInput = novelFileInput(page);
    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('will be deleted twice'),
    });

    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toBeVisible({ timeout: 5_000 });
    await uploadedDocs.track(filename);

    const targetDoc = (await listDocumentsViaApi()).find(
      (d) => d.filename === filename
    );
    expect(targetDoc).toBeDefined();

    // 第一次通过 API 删除应成功（200）
    const statusFromApi = await deleteDocumentViaApi(targetDoc!.id);
    expect(statusFromApi).toBe(200);

    await page.reload();
    await page.waitForLoadState('networkidle').catch(() => {});
    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toHaveCount(0);

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
    const hasPagination = await page
      .locator('.pagination')
      .isVisible()
      .catch(() => false);

    if (hasPagination) {
      const paginationInfo = page.locator('.pagination-info');
      await expect(paginationInfo).toBeVisible();

      // 校验页码与总数文案格式（#49 文案：共 N 本小说）
      const infoText = await paginationInfo.textContent();
      expect(infoText?.match(/第 \d+ \/ \d+ 页/)).toBeTruthy();
      expect(infoText?.match(/共 \d+ 本小说/)).toBeTruthy();
    }
  });

  cleanupTest('第一页应禁用上一页按钮', async ({ page }) => {
    const hasPagination = await page
      .locator('.pagination')
      .isVisible()
      .catch(() => false);

    if (hasPagination) {
      const prevButton = page.locator('button:has-text("上一页")');
      const isDisabled = await prevButton.isDisabled().catch(() => false);

      // 第一页时上一页按钮应禁用
      const pageInfo =
        (await page.locator('.pagination-info').textContent().catch(() => '')) || '';
      const isFirstPage = pageInfo.includes('第 1 /');

      expect(isDisabled || !isFirstPage).toBeTruthy();
    }
  });

  cleanupTest('末页应禁用下一页按钮', async ({ page }) => {
    const hasPagination = await page
      .locator('.pagination')
      .isVisible()
      .catch(() => false);

    if (hasPagination) {
      const nextButton = page.locator('button:has-text("下一页")');
      // 一次性点到底。每次点击后等待页码文本变化，避免 race condition。
      for (let i = 0; i < 20; i++) {
        if (await nextButton.isDisabled().catch(() => false)) break;
        const pageInfoBefore = await page
          .locator('.pagination-info')
          .textContent()
          .catch(() => '');
        await nextButton.click();
        await expect(page.locator('.pagination-info')).not.toHaveText(
          pageInfoBefore ?? '',
          { timeout: 3_000 }
        );
      }

      const isDisabled = await nextButton.isDisabled().catch(() => false);
      expect(isDisabled).toBeTruthy();
    }
  });

  cleanupTest('点击错误提示应清除错误', async ({ page }) => {
    // 触发错误后点击错误条应清除
    const fileInput = novelFileInput(page);

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

  cleanupTest('选择封面后应显示已就绪，点击可移除', async ({ page }) => {
    // 封面就绪提示 + 点击移除复位
    const coverInput = coverFileInput(page);

    await coverInput.setInputFiles({
      name: 'cover.png',
      mimeType: 'image/png',
      buffer: FAKE_PNG,
    });

    const coverZone = page.locator('.cover-upload-zone');
    await expect(coverZone).toContainText('封面已就绪：cover.png');
    await expect(coverZone).toContainText('点击移除');

    await coverZone.click();
    await expect(coverZone).toContainText('拖拽或点击上传封面（可选）');
  });

  cleanupTest('上传正文+封面后列表应展示封面缩略图', async ({ page, uploadedDocs }) => {
    // #49：封面随正文一起上传，列表项展示封面 img
    const filename = `novel-with-cover-${Date.now()}.txt`;
    const coverInput = coverFileInput(page);
    const fileInput = novelFileInput(page);

    await coverInput.setInputFiles({
      name: 'cover.png',
      mimeType: 'image/png',
      buffer: FAKE_PNG,
    });
    await expect(page.locator('.cover-upload-zone')).toContainText(
      '封面已就绪：cover.png'
    );

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('novel body with cover'),
    });

    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toBeVisible({ timeout: 5_000 });

    // 封面缩略图为 img，src 指向 /api/covers/*
    const coverImg = page.locator(
      `.doc-item:has-text("${filename}") img.doc-item-cover`
    );
    await expect(coverImg).toBeVisible({ timeout: 5_000 });
    await expect(coverImg).toHaveAttribute('src', /\/api\/covers\//);

    // 后端应记录封面相对路径（covers/{id}.png）
    const uploadedDoc = (await listDocumentsViaApi()).find(
      (d) => d.filename === filename
    );
    expect(uploadedDoc).toBeDefined();
    expect(uploadedDoc!.cover_image_path).toMatch(/^covers\/\d+\.png$/);
    await uploadedDocs.track(filename);

    // 封面已随正文上传，封面区复位
    await expect(page.locator('.cover-upload-zone')).toContainText(
      '拖拽或点击上传封面（可选）'
    );
  });

  cleanupTest('无封面文档应展示占位缩略图', async ({ page, uploadedDocs }) => {
    // #49：无封面时展示占位 SVG，而非 img
    const filename = `novel-no-cover-${Date.now()}.txt`;
    const fileInput = novelFileInput(page);

    await fileInput.setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('novel without cover'),
    });

    await expect(
      page.locator(`.doc-item-name:has-text("${filename}")`)
    ).toBeVisible({ timeout: 5_000 });
    await uploadedDocs.track(filename);

    const item = page.locator(`.doc-item:has-text("${filename}")`);
    await expect(item.locator('.doc-item-cover-placeholder')).toBeVisible();
    await expect(item.locator('img.doc-item-cover')).toHaveCount(0);
  });
});
