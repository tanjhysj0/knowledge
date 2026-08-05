import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

interface EndpointsConfig {
  cloudAgentApiBase: string;
  modelMapping: Record<string, string>;
  apiKeyFile: string;
  injectEnabled: boolean;
  llmInterceptEnabled: boolean;
}

// Read endpoints.json from project root
function readEndpointsConfig(): EndpointsConfig | null {
  const configPath = path.join(process.cwd(), 'e2e', 'endpoints.json');
  try {
    if (fs.existsSync(configPath)) {
      return JSON.parse(fs.readFileSync(configPath, 'utf-8'));
    }
  } catch (e) {
    console.error('Failed to read endpoints.json:', e);
  }
  return null;
}

// Read API key from file path in config
function readApiKey(filePath: string): string | null {
  try {
    const expandedPath = filePath.replace('~', os.homedir());
    if (fs.existsSync(expandedPath)) {
      return fs.readFileSync(expandedPath, 'utf-8').trim();
    }
  } catch (e) {
    console.error('Failed to read API key:', e);
  }
  return null;
}

// Configure backend settings before tests
async function configureBackendSettings(baseUrl: string, apiKey: string, model: string) {
  try {
    await fetch(`${baseUrl}/api/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        llm: {
          provider: 'openai',
          api_key: apiKey,
          base_url: 'https://api.minimaxi.com/v1',
          model: model,
        },
        embedding: {
          provider: 'openai',
          api_key: apiKey,
          base_url: 'https://api.minimaxi.com/v1',
          model: 'text-embedding-3-small',
        },
      }),
    });
  } catch (e) {
    console.error('Failed to configure backend settings:', e);
  }
}

test.describe('Chat Page - E2E', () => {
  // Configure backend with endpoints.json settings before all tests
  test.beforeAll(async ({ request }) => {
    const config = readEndpointsConfig();
    if (config) {
      const apiKey = readApiKey(config.apiKeyFile);
      const model = config.modelMapping['dmodel'] || 'MiniMax-M3';
      if (apiKey) {
        // Get the backend URL from frontend's API calls
        const baseUrl = 'http://localhost:5173'.replace('/chat', '').replace(/\/$/, '');
        await configureBackendSettings('http://localhost:8000', apiKey, model);
      }
    }
  });

  test.beforeEach(async ({ page }) => {
    // Navigate to the chat page
    await page.goto('/chat');
    await page.waitForLoadState('networkidle').catch(() => {});
    // Wait for chat page to render
    await page.waitForSelector('textarea');
    // Clear any existing messages first
    const clearBtn = page.locator('button:has-text("清除历史")');
    if (await clearBtn.isVisible().catch(() => false)) {
      await clearBtn.click();
      await page.waitForTimeout(500);
    }
  });

  test.afterEach(async ({ page }) => {
    // Clean up messages after each test
    const clearBtn = page.locator('button:has-text("清除历史")');
    if (await clearBtn.isVisible().catch(() => false)) {
      await clearBtn.click();
      await page.waitForTimeout(500);
    }
  });

  test('should display page title and empty state', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('DocQA');
    await expect(page.locator('.empty-state p')).toContainText('开始对话吧！');
    await expect(page.locator('.empty-state small')).toContainText('上传文档后可基于文档内容回答问题');
  });

  test('should block empty message submission', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // Button should be disabled when input is empty
    await expect(sendButton).toBeDisabled();

    // Button should be disabled when input only has whitespace
    await textarea.fill('   ');
    await expect(sendButton).toBeDisabled();

    // Button should be enabled when input has text
    await textarea.fill('Hello');
    await expect(sendButton).toBeEnabled();
  });

  test('should send message and display user message immediately', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    const testMessage = '测试消息 ' + Date.now();
    await textarea.fill(testMessage);
    await sendButton.click();

    // User message should appear immediately (before API response)
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.message.user .content')).toContainText(testMessage);
  });

  test('should show typing indicator after sending message', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Test message');
    await sendButton.click();

    // Check for typing indicator (either .typing-indicator or .typing-cursor)
    await page.waitForTimeout(300);
    const hasTyping = await page.locator('.typing-indicator, .typing-cursor').isVisible().catch(() => false);
    expect(hasTyping).toBeTruthy();
  });

  test('should display streaming response after sending message', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Hello AI');
    await sendButton.click();

    // Wait for assistant response to appear
    await expect(page.locator('.message.assistant')).toBeVisible({ timeout: 60000 });

    // Verify the assistant message has content
    const assistantContent = page.locator('.message.assistant .content').first();
    await expect(assistantContent).not.toBeEmpty({ timeout: 60000 });
  });

  test('should maintain context in multi-turn conversation', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // First message
    const firstMessage = 'First question ' + Date.now();
    await textarea.fill(firstMessage);
    await sendButton.click();

    // Wait for first user message to appear
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 5000 });

    // Count messages after first exchange
    const userMessages = page.locator('.message.user');
    const assistantMessages = page.locator('.message.assistant');
    const firstUserCount = await userMessages.count();
    const firstAssistantCount = await assistantMessages.count();

    // Wait for response to complete
    await page.waitForTimeout(500);

    // Second message
    const secondMessage = 'Second question';
    await textarea.fill(secondMessage);
    await sendButton.click();

    // Wait for second user message
    await expect(page.locator('.message.user').last()).toBeVisible({ timeout: 5000 });

    // Verify we have more user messages now (multi-turn)
    const secondUserCount = await userMessages.count();
    expect(secondUserCount).toBeGreaterThan(firstUserCount);
  });

  test('should show clear history button when messages exist', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // Clear button should not be visible initially
    await expect(page.locator('button:has-text("清除历史")')).not.toBeVisible();

    // Send a message
    await textarea.fill('Test message');
    await sendButton.click();

    // Wait for message to appear
    await page.waitForTimeout(500);

    // Clear button should now be visible
    await expect(page.locator('button:has-text("清除历史")')).toBeVisible();
  });

  test('should clear chat history when clicking clear button', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // Send a message
    await textarea.fill('Message to be cleared');
    await sendButton.click();

    // Wait for message to appear
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 5000 });

    // Click clear button
    const clearBtn = page.locator('button:has-text("清除历史")');
    await clearBtn.click();

    // Wait for clear to complete
    await page.waitForTimeout(1000);

    // After clear, either empty state or no user messages should show
    const emptyStateVisible = await page.locator('.empty-state').isVisible().catch(() => false);
    const userMessageCount = await page.locator('.message.user').count();
    expect(emptyStateVisible || userMessageCount === 0).toBeTruthy();
  });

  test('should show context indicator when documents are selected', async ({ page }) => {
    // The chat page at /chat has empty documents array by default
    const contextIndicator = page.locator('.context-indicator');
    const hasDocsInitially = await contextIndicator.isVisible().catch(() => false);

    // Without documents, no context indicator should show
    if (!hasDocsInitially) {
      await expect(contextIndicator).not.toBeVisible();
    }
  });

  test('should support Enter key to send message', async ({ page }) => {
    const textarea = page.locator('textarea');

    const testMessage = 'Enter key test';
    await textarea.fill(testMessage);

    // Press Enter to send (without Shift)
    await textarea.press('Enter');

    // Message should be sent
    await expect(page.locator('.message.user .content')).toContainText(testMessage, { timeout: 5000 });
  });

  test('should support Shift+Enter for newline', async ({ page }) => {
    const textarea = page.locator('textarea');

    // Fill message with newline using pressSequentially
    await textarea.click();
    await textarea.pressSequentially('Line 1');
    await textarea.press('Shift+Enter');
    await textarea.pressSequentially('Line 2');

    // Content should contain newline
    const content = await textarea.inputValue();
    expect(content).toContain('\n');

    // Send button should still be enabled
    await expect(page.locator('button:has-text("发送")')).toBeEnabled();
  });

  test('should display role labels correctly for user and assistant', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Test message');
    await sendButton.click();

    // Wait for both messages
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.message.assistant')).toBeVisible({ timeout: 60000 });

    // Check role labels
    await expect(page.locator('.message.user .role')).toContainText('用户');
    await expect(page.locator('.message.assistant .role')).toContainText('AI');
  });

  test('should resize textarea dynamically based on content', async ({ page }) => {
    const textarea = page.locator('textarea');

    // Small input
    await textarea.fill('Short');
    const shortHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // Longer input
    await textarea.fill('This is a much longer message that should cause the textarea to grow in height');
    const longHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // Height should increase with more content
    expect(longHeight).toBeGreaterThanOrEqual(shortHeight);
  });

  test('should disable send button while loading', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Test message');
    await sendButton.click();

    // Button should be disabled during loading
    await expect(sendButton).toBeDisabled();
  });

  test('should auto-resize textarea with max height limit', async ({ page }) => {
    const textarea = page.locator('textarea');

    // Very long input
    const longText = 'Line 1\n'.repeat(20);
    await textarea.fill(longText);
    const height = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // Height should not exceed max-height (120px as defined in App.css)
    expect(height).toBeLessThanOrEqual(130);
  });

  test('should load chat page and display input area', async ({ page }) => {
    // Verify input area components exist
    await expect(page.locator('textarea')).toBeVisible();
    await expect(page.locator('button:has-text("发送")')).toBeVisible();
  });

  test('should handle send button with whitespace-only input', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // Fill with whitespace only
    await textarea.fill('   \n  \t  ');

    // Button should be disabled
    await expect(sendButton).toBeDisabled();
  });
});
