import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #53: /admin 管理端。
 * 覆盖：左侧菜单、3 字段表单（小说名必填/封面可选默认图/文本文件必填）、
 * 上传、编辑（改名/换封面）、删除、LLM 设置 tab。
 */

interface DocumentSummary {
  id: number;
  filename: string;
  title: string | null;
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

// 后端只校验封面扩展名与大小，fake PNG 头即可通过（#48）。
const FAKE_PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** 轮询后端 API 直到满足条件，返回最终列表。 */
async function pollUntil(
  page: import('@playwright/test').Page,
  predicate: (items: DocumentSummary[]) => boolean,
  timeoutMs = 30_000
): Promise<DocumentSummary[]> {
  const deadline = Date.now() + timeoutMs;
  let items: DocumentSummary[] = [];
  while (Date.now() < deadline) {
    items = await listDocumentsViaApi();
    if (predicate(items)) return items;
    await page.waitForTimeout(250);
  }
  return items;
}

cleanupTest.describe('Admin Page（/admin 管理端）- E2E (#53)', () => {
  cleanupTest.beforeEach(async ({ page }) => {
    await page.goto('/admin');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  const titleInput = (page: import('@playwright/test').Page) =>
    page.locator('[data-testid="novel-title-input"]');
  const textFileInput = (page: import('@playwright/test').Page) =>
    page.locator('[data-testid="novel-text-file-input"]');
  const coverFileInput = (page: import('@playwright/test').Page) =>
    page.locator('[data-testid="admin-cover-file-input"]');
  const submitBtn = (page: import('@playwright/test').Page) =>
    page.locator('[data-testid="novel-submit-btn"]');
  const preview = (page: import('@playwright/test').Page) =>
    page.locator('[data-testid="novel-form-cover-preview"]');

  cleanupTest('应展示左侧菜单与 3 字段表单', async ({ page }) => {
    await expect(page.locator('.admin-brand')).toContainText('管理端');

    const menuItems = page.locator('.admin-menu-item');
    await expect(menuItems.nth(0)).toContainText('小说管理');
    await expect(menuItems.nth(1)).toContainText('LLM 设置');

    // 3 个主要字段：小说名（必填）、封面（可选）、文本文件（必填）
    await expect(titleInput(page)).toBeVisible();
    await expect(page.locator('button:has-text("选择封面")')).toBeVisible();
    await expect(textFileInput(page)).toBeVisible();
    await expect(submitBtn(page)).toContainText('上传小说');
  });

  cleanupTest('未选封面时表单预览显示默认封面图', async ({ page }) => {
    await expect(preview(page).locator('svg[aria-label="默认封面"]')).toBeVisible();
    await expect(preview(page).locator('img')).toHaveCount(0);
  });

  cleanupTest('小说名与文本文件必填校验', async ({ page }) => {
    // 空表单提交：小说名必填
    await submitBtn(page).click();
    await expect(page.locator('text=请输入小说名')).toBeVisible();

    // 填了小说名但未选文件：文本文件必填
    await titleInput(page).fill('必填校验小说');
    await submitBtn(page).click();
    await expect(page.locator('text=请选择小说文本文件')).toBeVisible();
  });

  cleanupTest('填写表单上传后列表显示小说名并持久化', async ({ page, uploadedDocs }) => {
    const title = `测试小说-${Date.now()}`;
    const filename = `admin-${Date.now()}.txt`;

    await titleInput(page).fill(title);
    await textFileInput(page).setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('admin upload e2e content'),
    });
    await submitBtn(page).click();

    await expect(
      page.locator('.doc-item').filter({ hasText: title })
    ).toBeVisible({ timeout: 15_000 });

    const items = await pollUntil(page, (list) =>
      list.some((item) => item.title === title)
    );
    const persisted = items.find((item) => item.title === title);
    expect(persisted).toBeTruthy();
    expect(persisted!.filename).toBe(filename);
    expect(persisted!.cover_image_path).toBeFalsy();

    await uploadedDocs.track(filename);
  });

  cleanupTest('带封面上传后列表显示封面缩略图', async ({ page, uploadedDocs }) => {
    const title = `封面小说-${Date.now()}`;
    const filename = `admin-cover-${Date.now()}.txt`;

    await titleInput(page).fill(title);
    await coverFileInput(page).setInputFiles({
      name: 'cover.png',
      mimeType: 'image/png',
      buffer: FAKE_PNG,
    });
    // 选中封面后预览切换为图片（默认封面图消失）
    await expect(preview(page).locator('img')).toHaveCount(1);
    await expect(preview(page).locator('svg')).toHaveCount(0);

    await textFileInput(page).setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('cover e2e content'),
    });
    await submitBtn(page).click();

    const row = page.locator('.doc-item').filter({ hasText: title });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row.locator('img.doc-item-cover')).toHaveAttribute(
      'src',
      /\/api\/covers\//
    );

    const items = await pollUntil(page, (list) =>
      list.some((item) => item.title === title && item.cover_image_path)
    );
    const persisted = items.find((item) => item.title === title);
    expect(persisted!.cover_image_path).toMatch(/^covers\/\d+\.png$/);

    await uploadedDocs.track(filename);
  });

  cleanupTest('编辑小说可改名并保存，正文字段隐藏', async ({ page, uploadedDocs }) => {
    const title = `编辑测试-${Date.now()}`;
    const newTitle = `${title}-新名`;
    const filename = `admin-edit-${Date.now()}.txt`;

    // 先上传一本
    await titleInput(page).fill(title);
    await textFileInput(page).setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('edit e2e content'),
    });
    await submitBtn(page).click();
    const row = page.locator('.doc-item').filter({ hasText: title });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await uploadedDocs.track(filename);

    // 进入编辑模式
    await row.locator('[data-testid="novel-edit-btn"]').click();
    await expect(page.locator('h1')).toContainText('编辑小说');
    // 正文不可换：文本文件字段隐藏
    await expect(textFileInput(page)).toHaveCount(0);
    await expect(submitBtn(page)).toContainText('保存修改');
    // 小说名预填当前值
    await expect(titleInput(page)).toHaveValue(title);

    // 改名保存
    await titleInput(page).fill(newTitle);
    await submitBtn(page).click();

    await expect(
      page.locator('.doc-item').filter({ hasText: newTitle })
    ).toBeVisible({ timeout: 15_000 });
    // 保存后表单回到新建态
    await expect(page.locator('h1')).toContainText('新建小说');
    await expect(submitBtn(page)).toContainText('上传小说');

    const items = await pollUntil(page, (list) =>
      list.some((item) => item.title === newTitle)
    );
    expect(items.some((item) => item.title === newTitle)).toBeTruthy();
  });

  cleanupTest('编辑时更换封面并保存', async ({ page, uploadedDocs }) => {
    const title = `换封面-${Date.now()}`;
    const filename = `admin-recover-${Date.now()}.txt`;

    await titleInput(page).fill(title);
    await textFileInput(page).setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('recover e2e content'),
    });
    await submitBtn(page).click();
    await expect(
      page.locator('.doc-item').filter({ hasText: title })
    ).toBeVisible({ timeout: 15_000 });
    await uploadedDocs.track(filename);

    // 编辑并更换封面
    await page
      .locator('.doc-item')
      .filter({ hasText: title })
      .locator('[data-testid="novel-edit-btn"]')
      .click();
    await coverFileInput(page).setInputFiles({
      name: 'new-cover.png',
      mimeType: 'image/png',
      buffer: FAKE_PNG,
    });
    await expect(preview(page).locator('img')).toHaveCount(1);
    await submitBtn(page).click();

    const row = page.locator('.doc-item').filter({ hasText: title });
    await expect(row.locator('img.doc-item-cover')).toHaveAttribute(
      'src',
      /\/api\/covers\//
    );
  });

  cleanupTest('删除小说后列表移除', async ({ page, uploadedDocs }) => {
    const title = `删除测试-${Date.now()}`;
    const filename = `admin-del-${Date.now()}.txt`;

    await titleInput(page).fill(title);
    await textFileInput(page).setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('delete e2e content'),
    });
    await submitBtn(page).click();
    const row = page.locator('.doc-item').filter({ hasText: title });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await uploadedDocs.track(filename);

    await row.locator('button:has-text("删除")').click();
    await expect(
      page.locator('.doc-item').filter({ hasText: title })
    ).toHaveCount(0, { timeout: 15_000 });
  });

  cleanupTest('切换 LLM 设置 tab 显示配置表单', async ({ page }) => {
    await page
      .locator('.admin-menu-item')
      .filter({ hasText: 'LLM 设置' })
      .click();

    await expect(page.locator('text=LLM 配置')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('button:has-text("保存配置")')).toBeVisible();
    await expect(page.locator('button:has-text("重置")')).toBeVisible();
  });
});
