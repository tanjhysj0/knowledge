import { expect } from '@playwright/test';
import { test as cleanupTest } from './helpers/cleanup';

// 测试内模型名带时间戳前缀，避免与快照恢复的记录撞名。
const SUFFIX = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
// hasText 是子串匹配（openai 名会是 anthropic 名的子串），
// 用 `:has(td:has-text())` 定位模型名所在的精确行。
const rowWithModel = (page: import('@playwright/test').Page, name: string) =>
  page.locator(`tbody tr:has(td:has-text("${name}"))`);

cleanupTest.describe.configure({ mode: 'serial' });
cleanupTest.describe('模型列表页（#69）', () => {
  cleanupTest.beforeEach(async ({ modelsGuard }) => {
    // modelsGuard 在 spec 结束时清空测试新增记录并恢复快照；
    // 注入即可，即使本用例不直接使用。
    void modelsGuard;
    // 每个用例前把列表恢复为干净基线：仅保留快照中已有的记录。
    await modelsGuard.restore();
  });

  cleanupTest('应展示模型管理页标题、列表表格与新增表单', async ({ page }) => {
    await page.goto('/settings');
    await page.waitForSelector('h1');

    await expect(page.locator('h1')).toContainText('模型管理');
    await expect(page.locator('table')).toBeVisible();
    // 新增表单：两个下拉（接口类型 / 模型名称）、API Key、Base URL
    await expect(page.locator('select').first()).toBeVisible();
    await expect(page.locator('select').nth(1)).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('input[type="text"]')).toBeVisible();
    await expect(page.locator('button:has-text("拉取模型列表")')).toBeVisible();
  });

  cleanupTest('拦截拉取接口后应能通过下拉选择模型并新增', async ({ page }) => {
    const modelName = `e2e-openai-${SUFFIX}`;
    // mock 拉取端点：返回一个可选项，覆盖"模型名称单选下拉=拉取端点返回的选项"
    await page.route('**/api/models/fetch', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models: [modelName, 'gpt-4o-mini'] }),
      });
    });

    await page.goto('/settings');
    await page.waitForSelector('h1');

    await page.locator('button:has-text("拉取模型列表")').click();
    const modelSelect = page.locator('select').nth(1);
    // option 在 Playwright 中不视为 visible，选择成功后断言 select 的值。
    await modelSelect.selectOption(modelName);
    await expect(modelSelect).toHaveValue(modelName);
    await page.locator('input[type="text"]').fill('https://api.openai.com/v1');
    await page.locator('button:has-text("新增")').click();

    await expect(page.locator('text=模型已新增')).toBeVisible({ timeout: 5_000 });
    const row = page.locator('tr', { hasText: modelName });
    await expect(row).toBeVisible();
    await expect(row.locator('text=默认')).toBeVisible();
  });

  cleanupTest('编辑既有模型应更新列表', async ({ page }) => {
    const modelName = `e2e-openai-${SUFFIX}`;
    await page.route('**/api/models/fetch', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models: [modelName] }),
      });
    });

    await page.goto('/settings');
    await page.waitForSelector('h1');

    await page.locator('button:has-text("拉取模型列表")').click();
    await page.locator('select').nth(1).selectOption(modelName);
    await page.locator('button:has-text("新增")').click();
    await expect(page.locator('text=模型已新增')).toBeVisible({ timeout: 5_000 });

    // 编辑：改模型名并保存
    const editedName = `${modelName}-edited`;
    await page.route('**/api/models/fetch', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models: [editedName] }),
      });
    });
    const row = page.locator('tr', { hasText: modelName });
    await row.locator('button:has-text("编辑")').click();
    await page.locator('button:has-text("拉取模型列表")').click();
    await page.locator('select').nth(1).selectOption(editedName);
    await page.locator('button:has-text("保存修改")').click();

    await expect(page.locator('text=模型已更新')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('tr', { hasText: editedName })).toBeVisible();
  });

  cleanupTest('设默认应移动默认标记', async ({ page }) => {
    const first = `e2e-openai-${SUFFIX}`;
    const second = `e2e-anthropic-${SUFFIX}`;
    await page.route('**/api/models/fetch', async (route, request) => {
      const body = JSON.parse(request.postData() || '{}');
      const models = body.provider_type === 'anthropic' ? [second] : [first];
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models }),
      });
    });

    await page.goto('/settings');
    await page.waitForSelector('h1');

    // 第一条（openai，自动默认）
    await page.locator('button:has-text("拉取模型列表")').click();
    await page.locator('select').nth(1).selectOption(first);
    await page.locator('button:has-text("新增")').click();
    await expect(page.locator('text=模型已新增')).toBeVisible({ timeout: 5_000 });

    // 第二条（anthropic）
    await page.locator('select').first().selectOption('anthropic');
    await page.locator('button:has-text("拉取模型列表")').click();
    await page.locator('select').nth(1).selectOption(second);
    await page.locator('button:has-text("新增")').click();
    await expect(page.locator('text=模型已新增')).toBeVisible({ timeout: 5_000 });

    // 对第二条设默认
    const row = rowWithModel(page, second);
    await row.locator('button:has-text("设默认")').click();
    await expect(page.locator('text=已将').first()).toBeVisible({ timeout: 5_000 });

    // 默认标记应移到第二条
    const secondRow = rowWithModel(page, second);
    await expect(secondRow.locator('text=默认')).toBeVisible();
    const firstRow = rowWithModel(page, first);
    await expect(firstRow.locator('button:has-text("设默认")')).toBeVisible();
  });

  cleanupTest('删除非默认模型应从列表移除', async ({ page }) => {
    const first = `e2e-openai-${SUFFIX}`;
    const second = `e2e-anthropic-${SUFFIX}`;
    await page.route('**/api/models/fetch', async (route, request) => {
      const body = JSON.parse(request.postData() || '{}');
      const models = body.provider_type === 'anthropic' ? [second] : [first];
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models }),
      });
    });

    await page.goto('/settings');
    await page.waitForSelector('h1');

    await page.locator('button:has-text("拉取模型列表")').click();
    await page.locator('select').nth(1).selectOption(first);
    await page.locator('button:has-text("新增")').click();
    await expect(page.locator('text=模型已新增')).toBeVisible({ timeout: 5_000 });

    await page.locator('select').first().selectOption('anthropic');
    await page.locator('button:has-text("拉取模型列表")').click();
    await page.locator('select').nth(1).selectOption(second);
    await page.locator('button:has-text("新增")').click();
    await expect(page.locator('text=模型已新增')).toBeVisible({ timeout: 5_000 });

    // 删除非默认记录（接受 confirm）
    page.once('dialog', (dialog) => void dialog.accept());
    const row = rowWithModel(page, second);
    await row.locator('button:has-text("删除")').click();

    await expect(page.locator('text=模型已删除')).toBeVisible({ timeout: 5_000 });
    await expect(rowWithModel(page, second)).toHaveCount(0);
  });

  cleanupTest('列表为空时展示空态提示', async ({ page, request }) => {
    // 清空全部模型记录（非默认先行，最后删默认），回到未配置状态。
    const listRes = await request.get('/api/models');
    const current = await listRes.json();
    const nonDefaultFirst = [
      ...current.filter((m: { is_default: boolean }) => !m.is_default),
      ...current.filter((m: { is_default: boolean }) => m.is_default),
    ];
    for (const model of nonDefaultFirst) {
      await request.delete(`/api/models/${model.id}`);
    }

    await page.goto('/settings');
    await page.waitForSelector('h1');

    await expect(page.locator('text=暂无模型，请新增第一条')).toBeVisible();
  });
});
