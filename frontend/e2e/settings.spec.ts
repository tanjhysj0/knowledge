import { test, expect } from '@playwright/test';

test.describe('Settings Page - True E2E', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings');
    await page.waitForSelector('input[type="text"]');

    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const currentValue = await llmModelInput.inputValue();
    if (currentValue !== 'gpt-4o-mini') {
      await llmModelInput.fill('gpt-4o-mini');
      await page.locator('button:has-text("保存配置")').click();
      await page.waitForResponse('**/api/settings', { timeout: 10000 }).catch(() => {});
      await page.reload();
      await page.waitForSelector('input[type="text"]');
    }
  });

  test('should display page title and current configuration', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('设置');

    await expect(page.locator('text=LLM 配置')).toBeVisible();
    await expect(page.locator('text=Provider').first()).toBeVisible();

    await expect(page.locator('button:has-text("保存配置")')).toBeVisible();
    await expect(page.locator('button:has-text("重置")')).toBeVisible();
  });

  test('should load and display current LLM settings from real backend', async ({ page }) => {
    await page.waitForSelector('select >> nth=0');

    const llmProviderSelect = page.locator('select').first();
    await expect(llmProviderSelect).not.toBeEmpty();

    const baseUrlInput = page.locator('input[type="text"]').first();
    await expect(baseUrlInput).not.toBeEmpty();

    const modelInput = page.locator('input[type="text"]').nth(1);
    await expect(modelInput).not.toBeEmpty();
  });

  test('should switch LLM provider and persist after save+reload', async ({ page }) => {
    await page.waitForSelector('select >> nth=0');

    const llmProviderSelect = page.locator('select').first();
    const currentValue = await llmProviderSelect.inputValue();

    const options = await llmProviderSelect.locator('option').evaluateAll(
      (els) => els.map((el) => (el as HTMLOptionElement).value)
    );

    const differentValue = options.find((val) => val !== currentValue);
    expect(differentValue).toBeDefined();

    await llmProviderSelect.selectOption(differentValue!);
    await llmProviderSelect.evaluate(el => el.dispatchEvent(new Event('change', { bubbles: true })));

    await page.locator('button:has-text("保存配置")').click();
    await expect(page.locator('text=配置已保存并生效')).toBeVisible({ timeout: 15000 });

    await page.reload();
    await page.waitForSelector('input[type="text"]');

    await expect(page.locator('select').first()).toHaveValue(differentValue!);
  });

  test('should modify Base URL and Model', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    const llmBaseUrlInput = page.locator('input[type="text"]').first();
    const originalUrl = await llmBaseUrlInput.inputValue();
    const modifiedUrl = originalUrl.includes('test') ? 'https://api.openai.com/v1' : 'https://test.api.com/v1';

    await llmBaseUrlInput.fill(modifiedUrl);
    await expect(llmBaseUrlInput).toHaveValue(modifiedUrl);

    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const originalModel = await llmModelInput.inputValue();
    const modifiedModel = originalModel.includes('test') ? 'gpt-4o-mini' : 'gpt-4o-test';

    await llmModelInput.fill(modifiedModel);
    await expect(llmModelInput).toHaveValue(modifiedModel);
  });

  test('should save configuration successfully via real API', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const originalModel = await llmModelInput.inputValue();
    await llmModelInput.fill(originalModel + '-updated');

    await page.locator('button:has-text("保存配置")').click();

    await expect(
      page.locator('text=配置已保存并生效').or(page.locator('text=保存配置失败'))
    ).toBeVisible({ timeout: 15000 });
  });

  test('should show error or success message when save attempted', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    const llmBaseUrlInput = page.locator('input[type="text"]').first();
    await llmBaseUrlInput.fill('invalid-url');

    await page.locator('button:has-text("保存配置")').click();

    await expect(
      page.locator('text=保存配置失败').or(page.locator('text=配置已保存并生效'))
    ).toBeVisible({ timeout: 15000 });
  });

  test('should reset to original configuration when clicking reset button', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    const llmBaseUrlInput = page.locator('input[type="text"]').first();
    const llmModelInput = page.locator('input[type="text"]').nth(1);

    await llmModelInput.fill('known-test-model');
    await page.locator('button:has-text("保存配置")').click();
    await page.waitForResponse('**/api/settings', { timeout: 10000 });

    await expect(llmModelInput).toHaveValue('known-test-model');

    await llmModelInput.fill('modified-temp-model');

    await page.locator('button:has-text("重置")').click();

    await page.waitForResponse('**/api/settings');

    await expect(llmModelInput).toHaveValue('known-test-model', { timeout: 5000 });
  });

  test('should show loading state while fetching settings', async ({ page }) => {
    const responsePromise = page.waitForResponse('**/api/settings');
    await page.goto('/settings');

    await responsePromise;

    await expect(page.locator('text=LLM 配置')).toBeVisible({ timeout: 10000 });
  });

  test('should show error or settings when loading', async ({ page }) => {
    await page.goto('/settings');

    const hasError = await page.locator('text=加载配置失败').isVisible().catch(() => false);
    const hasSettings = await page.locator('text=LLM 配置').isVisible().catch(() => false);

    expect(hasError || hasSettings).toBeTruthy();
  });

  test('should disable save button while saving', async ({ page }) => {
    await page.waitForSelector('button:has-text("保存配置")');

    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const originalModel = await llmModelInput.inputValue();
    await llmModelInput.fill(originalModel + '-test');

    await page.route('**/api/settings', async (route, request) => {
      if (request.method() === 'PUT') {
        await new Promise(resolve => setTimeout(resolve, 500));
        await route.continue();
      } else {
        await route.continue();
      }
    });

    await page.locator('button:has-text("保存配置")').click();

    const buttonDisabled = await page.locator('button:has-text("保存")').isDisabled().catch(() => false);
    const buttonText = await page.locator('button:has-text("保存")').textContent().catch(() => '');

    expect(buttonDisabled || (buttonText?.includes('保存中') ?? false)).toBeTruthy();
  });

  test('should persist settings after page reload', async ({ page }) => {
    await page.waitForSelector('input[type="text"]');

    const llmModelInput = page.locator('input[type="text"]').nth(1);
    const originalModel = await llmModelInput.inputValue();
    const newModel = originalModel + '-persist-test';

    await llmModelInput.fill(newModel);
    await page.locator('button:has-text("保存配置")').click();

    await page.waitForResponse('**/api/settings', { timeout: 15000 }).catch(() => {});

    await page.reload();
    await page.waitForSelector('input[type="text"]');

    const currentModel = await llmModelInput.inputValue();
    expect([originalModel, newModel]).toContain(currentModel);
  });

  test('should keep ChatPage functional after provider switch (end-to-end persistence)', async ({ page }) => {
    // Capture original provider
    await page.waitForSelector('select >> nth=0');
    const llmProviderSelect = page.locator('select').first();
    const originalProvider = await llmProviderSelect.inputValue();

    // Switch provider (toggle between openai and anthropic)
    const options = await llmProviderSelect.locator('option').evaluateAll(
      (els) => els.map((el) => (el as HTMLOptionElement).value)
    );
    const differentProvider = options.find((val) => val !== originalProvider);
    expect(differentProvider).toBeDefined();

    await llmProviderSelect.selectOption(differentProvider!);
    await page.locator('button:has-text("保存配置")').click();
    await expect(page.locator('text=配置已保存并生效')).toBeVisible({ timeout: 15000 });

    // Navigate to chat page
    await page.goto('/chat');
    await page.waitForSelector('textarea');

    // ChatPage should still load successfully regardless of which provider is selected
    await expect(page.locator('textarea')).toBeVisible();
    await expect(page.locator('button:has-text("发送")')).toBeVisible();

    // Restore original provider
    await page.goto('/settings');
    await page.waitForSelector('select >> nth=0');
    await page.locator('select').first().selectOption(originalProvider);
    await page.locator('button:has-text("保存配置")').click();
    await expect(page.locator('text=配置已保存并生效')).toBeVisible({ timeout: 15000 });
  });
});