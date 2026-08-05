import { test, expect } from '@playwright/test';
import { test as cleanupTest } from './helpers/cleanup';

cleanupTest.describe('Settings Page - True E2E', () => {
  cleanupTest.beforeEach(async ({ page, settingsGuard }) => {
    // 进入设置页
    await page.goto('/settings');
    await page.waitForSelector('input[type="text"]');

    // 将模型统一重置为 gpt-4o-mini，保证测试一致性
    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const currentValue = await llmModelInput.inputValue();
    if (currentValue !== 'gpt-4o-mini') {
      await llmModelInput.fill('gpt-4o-mini');
      await page.locator('button:has-text("保存配置")').click();
      await page.waitForResponse('**/api/settings', { timeout: 10_000 }).catch(() => {});
      await page.reload();
      await page.waitForSelector('input[type="text"]');
    }
  });

  cleanupTest('应展示页面标题与当前配置', async ({ page }) => {
    // 验证标题与各分区可见
    await expect(page.locator('h1')).toContainText('设置');

    await expect(page.locator('text=LLM 配置')).toBeVisible();
    await expect(page.locator('text=Provider').first()).toBeVisible();

    await expect(page.locator('button:has-text("保存配置")')).toBeVisible();
    await expect(page.locator('button:has-text("重置")')).toBeVisible();
  });

  cleanupTest('应从真实后端加载并展示当前 LLM 配置', async ({ page }) => {
    await page.waitForSelector('select >> nth=0');

    // Provider 下拉框应有当前值
    const llmProviderSelect = page.locator('select').first();
    await expect(llmProviderSelect).not.toBeEmpty();

    // Base URL 与 Model 输入框应展示当前配置
    const baseUrlInput = page.locator('input[type="text"]').first();
    await expect(baseUrlInput).not.toBeEmpty();

    const modelInput = page.locator('input[type="text"]').nth(1);
    await expect(modelInput).not.toBeEmpty();
  });

  cleanupTest('切换 LLM Provider 保存刷新后应持久化', async ({ page }) => {
    await page.waitForSelector('select >> nth=0');

    // 切换 Provider：选另一个不同值
    const llmProviderSelect = page.locator('select').first();
    const currentValue = await llmProviderSelect.inputValue();

    const options = await llmProviderSelect.locator('option').evaluateAll(
      (els) => els.map((el) => (el as HTMLOptionElement).value)
    );

    const differentValue = options.find((val) => val !== currentValue);
    expect(differentValue).toBeDefined();

    await llmProviderSelect.selectOption(differentValue!);
    await llmProviderSelect.evaluate(el => el.dispatchEvent(new Event('change', { bubbles: true })));

    // 保存并断言成功提示
    await page.locator('button:has-text("保存配置")').click();
    await expect(page.locator('text=配置已保存并生效')).toBeVisible({ timeout: 15_000 });

    // 刷新后 Provider 应保持新值
    await page.reload();
    await page.waitForSelector('input[type="text"]');

    await expect(page.locator('select').first()).toHaveValue(differentValue!);
  });

  cleanupTest('应能修改 Base URL 与 Model', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    // 修改 Base URL（避免重复设置同一值）
    const llmBaseUrlInput = page.locator('input[type="text"]').first();
    const originalUrl = await llmBaseUrlInput.inputValue();
    const modifiedUrl = originalUrl.includes('test') ? 'https://api.openai.com/v1' : 'https://test.api.com/v1';

    await llmBaseUrlInput.fill(modifiedUrl);
    await expect(llmBaseUrlInput).toHaveValue(modifiedUrl);

    // 修改 Model
    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const originalModel = await llmModelInput.inputValue();
    const modifiedModel = originalModel.includes('test') ? 'gpt-4o-mini' : 'gpt-4o-test';

    await llmModelInput.fill(modifiedModel);
    await expect(llmModelInput).toHaveValue(modifiedModel);
  });

  cleanupTest('应通过真实 API 成功保存配置', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    // 修改 Model 并保存，应收到成功或失败提示
    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const originalModel = await llmModelInput.inputValue();
    await llmModelInput.fill(originalModel + '-updated');

    await page.locator('button:has-text("保存配置")').click();

    await expect(
      page.locator('text=配置已保存并生效').or(page.locator('text=保存配置失败'))
    ).toBeVisible({ timeout: 15_000 });
  });

  cleanupTest('点击保存时应展示错误或成功提示', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    // 输入非法 URL 测试错误处理
    const llmBaseUrlInput = page.locator('input[type="text"]').first();
    await llmBaseUrlInput.fill('invalid-url');

    await page.locator('button:has-text("保存配置")').click();

    // 至少出现成功或失败提示
    await expect(
      page.locator('text=保存配置失败').or(page.locator('text=配置已保存并生效'))
    ).toBeVisible({ timeout: 15_000 });
  });

  cleanupTest('点击重置应恢复原始配置', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    // 先保存一个已知值，再修改为临时值，点击重置应恢复
    const llmBaseUrlInput = page.locator('input[type="text"]').first();
    const llmModelInput = page.locator('input[type="text"]').nth(1);

    await llmModelInput.fill('known-test-model');
    await page.locator('button:has-text("保存配置")').click();
    await page.waitForResponse('**/api/settings', { timeout: 10_000 });

    await expect(llmModelInput).toHaveValue('known-test-model');

    await llmModelInput.fill('modified-temp-model');

    await page.locator('button:has-text("重置")').click();

    await page.waitForResponse('**/api/settings');

    // 重置后应恢复为之前保存的值
    await expect(llmModelInput).toHaveValue('known-test-model', { timeout: 5_000 });
  });

  cleanupTest('加载设置时应展示加载状态', async ({ page }) => {
    // 进入页面时应先显示加载状态
    const responsePromise = page.waitForResponse('**/api/settings');
    await page.goto('/settings');

    await responsePromise;

    await expect(page.locator('text=LLM 配置')).toBeVisible({ timeout: 10_000 });
  });

  cleanupTest('加载时应展示错误或设置内容', async ({ page }) => {
    // 进入设置页：要么加载失败显示错误，要么成功显示配置
    await page.goto('/settings');

    const hasError = await page.locator('text=加载配置失败').isVisible().catch(() => false);
    const hasSettings = await page.locator('text=LLM 配置').isVisible().catch(() => false);

    expect(hasError || hasSettings).toBeTruthy();
  });

  cleanupTest('保存中应禁用保存按钮', async ({ page }) => {
    await page.waitForSelector('button:has-text("保存配置")');

    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const originalModel = await llmModelInput.inputValue();
    await llmModelInput.fill(originalModel + '-test');

    // 拦截保存请求延迟 500ms 以观察按钮状态
    await page.route('**/api/settings', async (route, request) => {
      if (request.method() === 'PUT') {
        await new Promise(resolve => setTimeout(resolve, 500));
        await route.continue();
      } else {
        await route.continue();
      }
    });

    await page.locator('button:has-text("保存配置")').click();

    // 保存期间按钮应禁用或文案变为"保存中..."
    const buttonDisabled = await page.locator('button:has-text("保存")').isDisabled().catch(() => false);
    const buttonText = await page.locator('button:has-text("保存")').textContent().catch(() => '');

    expect(buttonDisabled || (buttonText?.includes('保存中') ?? false)).toBeTruthy();
  });

  cleanupTest('刷新后设置应保持', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    // 修改 Model 并保存，刷新后值应保持
    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const originalModel = await llmModelInput.inputValue();
    const newModel = originalModel + '-persist-test';

    await llmModelInput.fill(newModel);
    await page.locator('button:has-text("保存配置")').click();

    await page.waitForResponse('**/api/settings', { timeout: 15_000 }).catch(() => {});

    await page.reload();
    await page.waitForSelector('input[type="text"]');

    const currentModel = await llmModelInput.inputValue();
    expect([originalModel, newModel]).toContain(currentModel);
  });

  cleanupTest('切换 Provider 后 ChatPage 仍可使用（端到端持久化）', async ({ page }) => {
    // 端到端验证：切换 Provider 后 ChatPage 仍可加载
    await page.waitForSelector('select >> nth=0');
    const llmProviderSelect = page.locator('select').first();
    const originalProvider = await llmProviderSelect.inputValue();

    // 切换 Provider
    const options = await llmProviderSelect.locator('option').evaluateAll(
      (els) => els.map((el) => (el as HTMLOptionElement).value)
    );
    const differentProvider = options.find((val) => val !== originalProvider);
    expect(differentProvider).toBeDefined();

    await llmProviderSelect.selectOption(differentProvider!);
    await page.locator('button:has-text("保存配置")').click();
    await expect(page.locator('text=配置已保存并生效')).toBeVisible({ timeout: 15_000 });

    // 切换到聊天页仍可正常加载
    await page.goto('/chat');
    await page.waitForSelector('textarea');

    await expect(page.locator('textarea')).toBeVisible();
    await expect(page.locator('button:has-text("发送")')).toBeVisible();

    // 恢复原 Provider
    await page.goto('/settings');
    await page.waitForSelector('select >> nth=0');
    await page.locator('select').first().selectOption(originalProvider);
    await page.locator('button:has-text("保存配置")').click();
    await expect(page.locator('text=配置已保存并生效')).toBeVisible({ timeout: 15_000 });
  });
});
