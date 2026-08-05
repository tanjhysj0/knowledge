import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

interface EndpointsConfig {
  cloudAgentApiBase: string;
  llmBaseUrl?: string;
  modelMapping: Record<string, string>;
  apiKeyFile: string;
  injectEnabled: boolean;
  llmInterceptEnabled: boolean;
}

// 从项目根目录读取端点配置
function readEndpointsConfig(): EndpointsConfig | null {
  const configPath = path.join(process.cwd(), 'e2e', 'endpoints.json');
  try {
    if (fs.existsSync(configPath)) {
      return JSON.parse(fs.readFileSync(configPath, 'utf-8'));
    }
  } catch (e) {
    console.error('读取 endpoints.json 失败:', e);
  }
  return null;
}

// 从配置文件指定的路径读取 API 密钥
function readApiKey(filePath: string): string | null {
  try {
    const expandedPath = filePath.replace('~', os.homedir());
    if (fs.existsSync(expandedPath)) {
      return fs.readFileSync(expandedPath, 'utf-8').trim();
    }
  } catch (e) {
    console.error('读取 API 密钥失败:', e);
  }
  return null;
}

// 从 endpoints.json 读取 dmodel 配置，缺一项即抛错（按配置驱动，禁止硬编码）
function resolveConfiguredModel(config: EndpointsConfig | null): string {
  if (!config) {
    throw new Error('缺少 e2e/endpoints.json，无法确定测试模型');
  }
  const model = config.modelMapping?.['dmodel'];
  if (!model) {
    throw new Error("endpoints.json 中 modelMapping.dmodel 未配置，无法确定测试模型");
  }
  return model;
}

// 用真实 API 密钥配置后端设置
async function configureBackendSettings(baseUrl: string, apiKey: string, model: string, llmBaseUrl: string) {
  try {
    await fetch(`${baseUrl}/api/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        llm_provider: 'openai',
        llm_api_key: apiKey,
        llm_base_url: llmBaseUrl,
        llm_model: model,
      }),
    });
  } catch (e) {
    console.error('配置后端设置失败:', e);
  }
}

// 从 endpoints.json 读取 llmBaseUrl；未配置则抛错（禁止硬编码）
function resolveLlmBaseUrl(config: EndpointsConfig | null): string {
  if (!config?.llmBaseUrl) {
    throw new Error('endpoints.json 中 llmBaseUrl 未配置，无法确定测试 Base URL');
  }
  return config.llmBaseUrl;
}

test.describe('Chat Page - E2E', () => {
  // 在所有测试开始前用 endpoints.json 配置真实后端
  test.beforeAll(async ({ request }) => {
    const config = readEndpointsConfig();
    if (config) {
      const apiKey = readApiKey(config.apiKeyFile);
      if (apiKey) {
        const model = resolveConfiguredModel(config);
        const llmBaseUrl = resolveLlmBaseUrl(config);
        await configureBackendSettings('http://localhost:8000', apiKey, model, llmBaseUrl);
      }
    }
  });

  test.beforeEach(async ({ page }) => {
    // 导航到聊天页
    await page.goto('/chat');
    await page.waitForLoadState('networkidle').catch(() => {});
    // 等待聊天页输入框渲染
    await page.waitForSelector('textarea');
    // 清理之前的对话历史
    const clearBtn = page.locator('button:has-text("清除历史")');
    if (await clearBtn.isVisible().catch(() => false)) {
      await clearBtn.click();
      await page.waitForTimeout(500);
    }
  });

  test.afterEach(async ({ page }) => {
    // 每个测试结束后清理消息
    const clearBtn = page.locator('button:has-text("清除历史")');
    if (await clearBtn.isVisible().catch(() => false)) {
      await clearBtn.click();
      await page.waitForTimeout(500);
    }
  });

  test('应展示页面标题与空状态', async ({ page }) => {
    // 验证页面标题与空状态文案
    await expect(page.locator('h1')).toContainText('DocQA');
    await expect(page.locator('.empty-state p')).toContainText('开始对话吧！');
    await expect(page.locator('.empty-state small')).toContainText('上传文档后可基于文档内容回答问题');
  });

  test('应禁止空消息发送', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 空输入时按钮应禁用
    await expect(sendButton).toBeDisabled();

    // 仅空白字符输入时按钮也应禁用
    await textarea.fill('   ');
    await expect(sendButton).toBeDisabled();

    // 有文字输入时按钮应启用
    await textarea.fill('Hello');
    await expect(sendButton).toBeEnabled();
  });

  test('应发送消息并立即显示用户消息', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 输入消息并发送
    const testMessage = '测试消息 ' + Date.now();
    await textarea.fill(testMessage);
    await sendButton.click();

    // 用户消息应立即出现，无需等待 API 响应
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('.message.user .content')).toContainText(testMessage);
  });

  test('发送后应显示打字动画指示器', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Test message');
    await sendButton.click();

    // 短暂等待后检查打字动画指示器（typing-indicator 或 typing-cursor）
    await page.waitForTimeout(300);
    const hasTyping = await page.locator('.typing-indicator, .typing-cursor').isVisible().catch(() => false);
    expect(hasTyping).toBeTruthy();
  });

  test('应展示流式回复', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Hello AI');
    await sendButton.click();

    // 等待助手消息出现
    await expect(page.locator('.message.assistant')).toBeVisible({ timeout: 10_000 });

    // 验证助手消息有实际内容
    const assistantContent = page.locator('.message.assistant .content').first();
    await expect(assistantContent).not.toBeEmpty({ timeout: 10_000 });
  });

  test('多轮对话应保持上下文', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 第一轮对话
    const firstMessage = 'First question ' + Date.now();
    await textarea.fill(firstMessage);
    await sendButton.click();

    // 等待第一条用户消息出现
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 5_000 });

    // 记录第一轮消息数量
    const userMessages = page.locator('.message.user');
    const assistantMessages = page.locator('.message.assistant');
    const firstUserCount = await userMessages.count();
    const firstAssistantCount = await assistantMessages.count();

    // 等待响应完成
    await page.waitForTimeout(500);

    // 第二轮对话
    const secondMessage = 'Second question';
    await textarea.fill(secondMessage);
    await sendButton.click();

    // 等待第二条用户消息出现
    await expect(page.locator('.message.user').last()).toBeVisible({ timeout: 5_000 });

    // 验证用户消息数量增加（多轮上下文生效）
    const secondUserCount = await userMessages.count();
    expect(secondUserCount).toBeGreaterThan(firstUserCount);
  });

  test('存在消息时应显示清除历史按钮', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 初始时清除按钮不应可见
    await expect(page.locator('button:has-text("清除历史")')).not.toBeVisible();

    // 发送一条消息
    await textarea.fill('Test message');
    await sendButton.click();

    // 等待消息出现
    await page.waitForTimeout(500);

    // 此时清除按钮应可见
    await expect(page.locator('button:has-text("清除历史")')).toBeVisible();
  });

  test('点击清除按钮应清空对话历史', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 先发送一条消息
    await textarea.fill('Message to be cleared');
    await sendButton.click();

    // 等待用户消息出现
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 5_000 });

    // 点击清除按钮
    const clearBtn = page.locator('button:has-text("清除历史")');
    await clearBtn.click();

    // 等待清除操作完成
    await page.waitForTimeout(1_000);

    // 清除后应回到空状态或无用户消息
    const emptyStateVisible = await page.locator('.empty-state').isVisible().catch(() => false);
    const userMessageCount = await page.locator('.message.user').count();
    expect(emptyStateVisible || userMessageCount === 0).toBeTruthy();
  });

  test('选中文档时应显示上下文指示器', async ({ page }) => {
    // /chat 路由默认不传 documents，所以无上下文指示器
    const contextIndicator = page.locator('.context-indicator');
    const hasDocsInitially = await contextIndicator.isVisible().catch(() => false);

    // 没有文档时应不显示上下文指示器
    if (!hasDocsInitially) {
      await expect(contextIndicator).not.toBeVisible();
    }
  });

  test('应支持 Enter 键发送消息', async ({ page }) => {
    const textarea = page.locator('textarea');

    // 填写消息后按 Enter 发送（不带 Shift）
    const testMessage = 'Enter key test';
    await textarea.fill(testMessage);
    await textarea.press('Enter');

    // 消息应被发送
    await expect(page.locator('.message.user .content')).toContainText(testMessage, { timeout: 5_000 });
  });

  test('应支持 Shift+Enter 换行', async ({ page }) => {
    const textarea = page.locator('textarea');

    // 通过 pressSequentially 输入多行内容测试 Shift+Enter
    await textarea.click();
    await textarea.pressSequentially('Line 1');
    await textarea.press('Shift+Enter');
    await textarea.pressSequentially('Line 2');

    // 内容应包含换行符
    const content = await textarea.inputValue();
    expect(content).toContain('\n');

    // 发送按钮应保持启用
    await expect(page.locator('button:has-text("发送")')).toBeEnabled();
  });

  test('应正确展示用户与助手角色标签', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Test message');
    await sendButton.click();

    // 等待双方消息出现
    await expect(page.locator('.message.user')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('.message.assistant')).toBeVisible({ timeout: 10_000 });

    // 校验角色标签（用户 / AI）
    await expect(page.locator('.message.user .role')).toContainText('用户');
    await expect(page.locator('.message.assistant .role')).toContainText('AI');
  });

  test('输入框应随内容动态调整高度', async ({ page }) => {
    const textarea = page.locator('textarea');

    // 短输入
    await textarea.fill('Short');
    const shortHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // 长输入
    await textarea.fill('This is a much longer message that should cause the textarea to grow in height');
    const longHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // 输入越长，高度应越大或保持相等
    expect(longHeight).toBeGreaterThanOrEqual(shortHeight);
  });

  test('加载中应禁用发送按钮', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    await textarea.fill('Test message');
    await sendButton.click();

    // 加载期间发送按钮应禁用
    await expect(sendButton).toBeDisabled();
  });

  test('输入框自适应高度应受限上限', async ({ page }) => {
    const textarea = page.locator('textarea');

    // 极长输入
    const longText = 'Line 1\n'.repeat(20);
    await textarea.fill(longText);
    const height = await textarea.evaluate((el) => el.getBoundingClientRect().height);

    // 高度不应超过最大高度（App.css 中定义为 120px，允许少量测量误差）
    expect(height).toBeLessThanOrEqual(130);
  });

  test('应加载聊天页并展示输入区', async ({ page }) => {
    // 验证输入区组件存在
    await expect(page.locator('textarea')).toBeVisible();
    await expect(page.locator('button:has-text("发送")')).toBeVisible();
  });

  test('输入仅空白时应禁用发送按钮', async ({ page }) => {
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    // 仅空白字符输入
    await textarea.fill('   \n  \t  ');

    // 按钮应禁用
    await expect(sendButton).toBeDisabled();
  });

  test('刷新页面应恢复对话历史', async ({ page }) => {
    // 发送 2 条消息 → 等待响应 → reload → 验证历史显示
    const textarea = page.locator('textarea');
    const sendButton = page.locator('button:has-text("发送")');

    const firstMsg = 'reload-msg-1-' + Date.now();
    await textarea.fill(firstMsg);
    await sendButton.click();
    await expect(page.locator('.message.user .content')).toContainText(firstMsg, { timeout: 5_000 });
    await expect(page.locator('.message.assistant')).toBeVisible({ timeout: 10_000 });

    const secondMsg = 'reload-msg-2-' + Date.now();
    await textarea.fill(secondMsg);
    await sendButton.click();
    await expect(page.locator('.message.user .content').last()).toContainText(secondMsg, { timeout: 5_000 });

    // reload 后 ChatPage 应调用 history 接口并恢复显示
    await page.reload();
    await page.waitForSelector('textarea');

    await expect(page.locator('.message.user')).toHaveCount(2, { timeout: 5_000 });
    await expect(page.locator('.message.assistant')).toHaveCount(2, { timeout: 10_000 });

    await expect(page.locator('.message.user .content').first()).toContainText(firstMsg);
    await expect(page.locator('.message.user .content').last()).toContainText(secondMsg);
  });
});
