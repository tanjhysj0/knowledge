import { test, expect } from '@playwright/test';

test.describe('Layout + HomePage - E2E', () => {
  test.describe('Layout navigation', () => {
    test('should display all 4 nav links and DocQA logo on any page', async ({ page }) => {
      await page.goto('/');

      // DocQA logo
      const logo = page.locator('nav a:has-text("DocQA")').first();
      await expect(logo).toBeVisible();
      await expect(logo).toHaveAttribute('href', '/');

      // 4 navigation links
      await expect(page.locator('nav a:has-text("首页")')).toBeVisible();
      await expect(page.locator('nav a:has-text("文档管理")')).toBeVisible();
      await expect(page.locator('nav a:has-text("问答")')).toBeVisible();
      await expect(page.locator('nav a:has-text("设置")')).toBeVisible();

      // Verify nav also renders on /documents
      await page.goto('/documents');
      await expect(page.locator('nav a:has-text("DocQA")')).toBeVisible();
      await expect(page.locator('nav a:has-text("首页")')).toBeVisible();

      // Verify nav also renders on /chat
      await page.goto('/chat');
      await expect(page.locator('nav a:has-text("DocQA")')).toBeVisible();

      // Verify nav also renders on /settings
      await page.goto('/settings');
      await expect(page.locator('nav a:has-text("DocQA")')).toBeVisible();
    });

    test('should navigate to /documents when clicking 文档管理 and highlight it', async ({ page }) => {
      await page.goto('/');
      const docsLink = page.locator('nav a:has-text("文档管理")');
      await docsLink.click();

      await expect(page).toHaveURL(/\/documents$/);
      await expect(docsLink).toHaveClass(/bg-blue-50/);
    });

    test('should navigate to /chat when clicking 问答 and highlight it', async ({ page }) => {
      await page.goto('/');
      const chatLink = page.locator('nav a:has-text("问答")');
      await chatLink.click();

      await expect(page).toHaveURL(/\/chat$/);
      await expect(chatLink).toHaveClass(/bg-blue-50/);
    });

    test('should navigate to /settings when clicking 设置 and highlight it', async ({ page }) => {
      await page.goto('/');
      const settingsLink = page.locator('nav a:has-text("设置")');
      await settingsLink.click();

      await expect(page).toHaveURL(/\/settings$/);
      await expect(settingsLink).toHaveClass(/bg-blue-50/);
    });

    test('should navigate to / when clicking DocQA logo', async ({ page }) => {
      await page.goto('/settings');
      const logo = page.locator('nav a:has-text("DocQA")').first();
      await logo.click();

      await expect(page).toHaveURL(/\/$/);
      await expect(page.locator('nav a:has-text("首页")')).toHaveClass(/bg-blue-50/);
    });

    test('should only highlight the link matching current path', async ({ page }) => {
      await page.goto('/documents');

      // 文档管理 should be highlighted
      await expect(page.locator('nav a:has-text("文档管理")')).toHaveClass(/bg-blue-50/);

      // Others should not be highlighted
      await expect(page.locator('nav a:has-text("首页")')).not.toHaveClass(/bg-blue-50/);
      await expect(page.locator('nav a:has-text("问答")')).not.toHaveClass(/bg-blue-50/);
      await expect(page.locator('nav a:has-text("设置")')).not.toHaveClass(/bg-blue-50/);
    });

    test('should render Layout main area on unmatched paths (SPA fallback)', async ({ page }) => {
      await page.goto('/non-existent-route');

      // Layout nav should still be visible
      await expect(page.locator('nav')).toBeVisible();
      await expect(page.locator('nav a:has-text("DocQA")')).toBeVisible();
      await expect(page.locator('nav a:has-text("首页")')).toBeVisible();

      // Main element should still render (SPA fallback)
      await expect(page.locator('main')).toBeVisible();
    });
  });

  test.describe('HomePage', () => {
    test('should render h1 title and subtitle', async ({ page }) => {
      await page.goto('/');

      const h1 = page.locator('h1');
      await expect(h1).toContainText('DocQA');
      await expect(h1).toContainText('文档问答助手');

      await expect(page.locator('text=上传文档，开始智能问答')).toBeVisible();
    });
  });
});
