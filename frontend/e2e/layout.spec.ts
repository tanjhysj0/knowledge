import { test, expect } from '@playwright/test';

test.describe('Layout + NovelListPage - E2E', () => {
  test.describe('Layout navigation', () => {
    test('应在所有页面展示 DocQA Logo 与 3 个导航链接', async ({ page }) => {
      await page.goto('/');

      // DocQA logo 指向首页
      const logo = page.locator('nav a:has-text("DocQA")').first();
      await expect(logo).toBeVisible();
      await expect(logo).toHaveAttribute('href', '/');

      // #49：导航精简为 3 个链接（文档管理已并入首页小说库）
      await expect(page.locator('nav a:has-text("首页")')).toBeVisible();
      await expect(page.locator('nav a:has-text("问答")')).toBeVisible();
      await expect(page.locator('nav a:has-text("设置")')).toBeVisible();

      // 验证 /chat /settings 页面也都渲染顶部导航
      await page.goto('/chat');
      await expect(page.locator('nav a:has-text("DocQA")')).toBeVisible();
      await expect(page.locator('nav a:has-text("首页")')).toBeVisible();

      await page.goto('/settings');
      await expect(page.locator('nav a:has-text("DocQA")')).toBeVisible();
    });

    test('点击“首页”应跳转并高亮', async ({ page }) => {
      // 点击"首页"应跳转且当前链接高亮
      await page.goto('/chat');
      const homeLink = page.locator('nav a:has-text("首页")');
      await homeLink.click();

      await expect(page).toHaveURL(/\/$/);
      await expect(homeLink).toHaveClass(/bg-blue-50/);
    });

    test('点击“问答”应跳转并高亮', async ({ page }) => {
      // 点击"问答"应跳转且当前链接高亮
      await page.goto('/');
      const chatLink = page.locator('nav a:has-text("问答")');
      await chatLink.click();

      await expect(page).toHaveURL(/\/chat$/);
      await expect(chatLink).toHaveClass(/bg-blue-50/);
    });

    test('点击“设置”应跳转并高亮', async ({ page }) => {
      // 点击"设置"应跳转且当前链接高亮
      await page.goto('/');
      const settingsLink = page.locator('nav a:has-text("设置")');
      await settingsLink.click();

      await expect(page).toHaveURL(/\/settings$/);
      await expect(settingsLink).toHaveClass(/bg-blue-50/);
    });

    test('点击 DocQA Logo 应回到首页', async ({ page }) => {
      // 点击 Logo 应回到首页
      await page.goto('/settings');
      const logo = page.locator('nav a:has-text("DocQA")').first();
      await logo.click();

      await expect(page).toHaveURL(/\/$/);
      await expect(page.locator('nav a:has-text("首页")')).toHaveClass(/bg-blue-50/);
    });

    test('只有匹配当前路径的链接高亮', async ({ page }) => {
      // 当前路径下的链接高亮，其它链接不高亮
      await page.goto('/chat');

      await expect(page.locator('nav a:has-text("问答")')).toHaveClass(/bg-blue-50/);

      await expect(page.locator('nav a:has-text("首页")')).not.toHaveClass(/bg-blue-50/);
      await expect(page.locator('nav a:has-text("设置")')).not.toHaveClass(/bg-blue-50/);
    });

    test('未匹配路径下 Layout 主区应仍渲染（SPA fallback）', async ({ page }) => {
      // 未匹配路径仍应渲染 Layout 主区（SPA fallback）
      await page.goto('/non-existent-route');

      await expect(page.locator('nav')).toBeVisible();
      await expect(page.locator('nav a:has-text("DocQA")')).toBeVisible();
      await expect(page.locator('nav a:has-text("首页")')).toBeVisible();

      await expect(page.locator('main')).toBeVisible();
    });
  });

  test.describe('NovelListPage（首页）', () => {
    test('首页应展示 h1 标题与双上传区', async ({ page }) => {
      // #49：首页即小说库，h1 为"我的小说库"，正文 + 封面两个拖拽区并排
      await page.goto('/');

      const h1 = page.locator('h1');
      await expect(h1).toContainText('我的小说库');

      await expect(page.locator('.upload-zone').first()).toBeVisible();
      await expect(page.locator('.upload-zone').nth(1)).toBeVisible();
    });
  });
});
