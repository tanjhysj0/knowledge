import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #54: 管理端独立路由。
 * /admin 小说列表页；/admin/novels/new 与 /admin/novels/:id 共用编辑页
 * （编辑按 id 拉取详情预填，刷新不丢）；/admin/settings LLM 设置独立路由。
 * 覆盖：左侧菜单（高亮随路由）、3 字段表单、上传、编辑（改名/换封面）、
 * 编辑页刷新恢复、删除、LLM 设置路由。
 */

interface DocumentSummary {
  id: number;
  filename: string;
  title: string | null;
  cover_image_path: string | null;
}

async function listDocumentsViaApi(): Promise<DocumentSummary[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  // #63：管理端全量视图（含 pending/processing/failed）。
  const res = await ctx.get('/api/documents?page=1&page_size=100&all_statuses=true');
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

type Page = import('@playwright/test').Page;

const titleInput = (page: Page) => page.locator('[data-testid="novel-title-input"]');
const textFileInput = (page: Page) => page.locator('[data-testid="novel-text-file-input"]');
const coverFileInput = (page: Page) => page.locator('[data-testid="admin-cover-file-input"]');
const submitBtn = (page: Page) => page.locator('[data-testid="novel-submit-btn"]');
const preview = (page: Page) => page.locator('[data-testid="novel-form-cover-preview"]');
const menuItem = (page: Page, text: string) =>
  page.locator('.admin-menu-item').filter({ hasText: text });

cleanupTest.describe('Admin Page（管理端独立路由）- E2E (#54)', () => {
  cleanupTest.beforeEach(async ({ page }) => {
    // 上传用例含后端 embedding 推理（CPU），首次冷启动可能超过默认 5s。
    cleanupTest.setTimeout(30_000);
    await page.goto('/admin');
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  cleanupTest('应展示左侧菜单与小说列表，新建按钮跳转编辑页', async ({ page }) => {
    await expect(page.locator('.admin-brand')).toContainText('管理端');
    await expect(menuItem(page, '小说管理')).toBeVisible();
    await expect(menuItem(page, 'LLM 设置')).toBeVisible();
    // 默认路由 /admin：小说管理菜单高亮，列表页标题可见
    await expect(menuItem(page, '小说管理')).toHaveClass(/active/);
    await expect(page.locator('h1')).toContainText('小说管理');

    // 新建入口跳转独立编辑页
    await page.locator('[data-testid="novel-create-btn"]').click();
    await expect(page).toHaveURL(/\/admin\/novels\/new$/);
  });

  cleanupTest('新建页展示 3 字段表单与默认封面预览', async ({ page }) => {
    await page.goto('/admin/novels/new');

    // 3 个主要字段：小说名（必填）、封面（可选）、文本文件（必填）
    await expect(titleInput(page)).toBeVisible();
    await expect(page.locator('button:has-text("选择封面")')).toBeVisible();
    await expect(textFileInput(page)).toBeVisible();
    await expect(submitBtn(page)).toContainText('上传小说');

    // 未选封面时预览显示默认封面图
    await expect(preview(page).locator('svg[aria-label="默认封面"]')).toBeVisible();
    await expect(preview(page).locator('img')).toHaveCount(0);
  });

  cleanupTest('小说名与文本文件必填校验', async ({ page }) => {
    await page.goto('/admin/novels/new');

    // 空表单提交：小说名必填
    await submitBtn(page).click();
    await expect(page.locator('text=请输入小说名')).toBeVisible();

    // 填了小说名但未选文件：文本文件必填
    await titleInput(page).fill('必填校验小说');
    await submitBtn(page).click();
    await expect(page.locator('text=请选择小说文本文件')).toBeVisible();
  });

  cleanupTest('填写表单上传后返回列表并显示小说名', async ({ page, uploadedDocs }) => {
    const title = `测试小说-${Date.now()}`;
    const filename = `admin-${Date.now()}.txt`;

    await page.goto('/admin/novels/new');
    await titleInput(page).fill(title);
    await textFileInput(page).setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('admin upload e2e content'),
    });
    await submitBtn(page).click();

    // 保存成功后返回列表页。上传含 embedding 推理，高负载下可能超过默认
    // expect 超时（5s），显式放宽。
    await expect(page).toHaveURL(/\/admin$/, { timeout: 30_000 });
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

    await page.goto('/admin/novels/new');
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
    await page.goto('/admin/novels/new');
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

    // 编辑按钮跳转独立编辑页 /admin/novels/:id
    await row.locator('[data-testid="novel-edit-btn"]').click();
    await expect(page).toHaveURL(/\/admin\/novels\/\d+$/);
    await expect(page.locator('h1')).toContainText('编辑小说');
    // 正文不可换：文本文件字段隐藏
    await expect(textFileInput(page)).toHaveCount(0);
    await expect(submitBtn(page)).toContainText('保存修改');
    // 小说名按 id 从后端拉取预填
    await expect(titleInput(page)).toHaveValue(title);

    // 改名保存
    await titleInput(page).fill(newTitle);
    await submitBtn(page).click();

    // 保存后返回列表页并显示新名
    await expect(page).toHaveURL(/\/admin$/);
    await expect(
      page.locator('.doc-item').filter({ hasText: newTitle })
    ).toBeVisible({ timeout: 15_000 });

    const items = await pollUntil(page, (list) =>
      list.some((item) => item.title === newTitle)
    );
    expect(items.some((item) => item.title === newTitle)).toBeTruthy();
  });

  cleanupTest('编辑页刷新后数据不丢', async ({ page, uploadedDocs }) => {
    const title = `刷新测试-${Date.now()}`;
    const filename = `admin-reload-${Date.now()}.txt`;

    await page.goto('/admin/novels/new');
    await titleInput(page).fill(title);
    await textFileInput(page).setInputFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('reload e2e content'),
    });
    await submitBtn(page).click();
    await expect(
      page.locator('.doc-item').filter({ hasText: title })
    ).toBeVisible({ timeout: 15_000 });
    await uploadedDocs.track(filename);

    const items = await pollUntil(page, (list) =>
      list.some((item) => item.title === title)
    );
    const docId = items.find((item) => item.title === title)!.id;

    // 直达编辑页并刷新，预填数据仍在
    await page.goto(`/admin/novels/${docId}`);
    await expect(page.locator('h1')).toContainText('编辑小说');
    await expect(titleInput(page)).toHaveValue(title);
    await page.reload();
    await expect(page.locator('h1')).toContainText('编辑小说');
    await expect(titleInput(page)).toHaveValue(title);
  });

  cleanupTest('编辑页直达不存在的 id 显示错误提示', async ({ page }) => {
    await page.goto('/admin/novels/99999999');
    await expect(page.locator('text=小说不存在或已被删除')).toBeVisible({
      timeout: 5_000,
    });
    // 取消可返回列表
    await page.locator('[data-testid="novel-cancel-btn"]').click();
    await expect(page).toHaveURL(/\/admin$/);
  });

  cleanupTest('编辑时更换封面并保存', async ({ page, uploadedDocs }) => {
    const title = `换封面-${Date.now()}`;
    const filename = `admin-recover-${Date.now()}.txt`;

    await page.goto('/admin/novels/new');
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
    await expect(page.locator('h1')).toContainText('编辑小说');
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

    await page.goto('/admin/novels/new');
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

  cleanupTest('LLM 设置独立路由展示模型列表页，菜单高亮随路由', async ({ page }) => {
    await menuItem(page, 'LLM 设置').click();

    await expect(page).toHaveURL(/\/admin\/settings$/);
    await expect(menuItem(page, 'LLM 设置')).toHaveClass(/active/);
    await expect(menuItem(page, '小说管理')).not.toHaveClass(/active/);
    // #69：设置页已改为模型列表页（表格 + 新增表单）。
    await expect(page.locator('h1')).toContainText('模型管理', { timeout: 5_000 });
    await expect(page.locator('table')).toBeVisible();
    await expect(page.locator('button:has-text("拉取模型列表")')).toBeVisible();
    await expect(page.locator('button:has-text("新增")')).toBeVisible();
  });
});
