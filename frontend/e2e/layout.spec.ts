import { test, expect } from '@playwright/test';

test.describe('Layout + NovelListPage - E2E (#50)', () => {
  test.describe('前台无导航', () => {
    test('首页/问答/设置页均不再渲染顶部导航', async ({ page }) => {
      // #50：顶部导航（首页/问答/设置链接）全站移除，前台不再渲染 nav
      for (const path of ['/', '/chat', '/settings']) {
        await page.goto(path);
        await expect(page.locator('nav')).toHaveCount(0);
      }
    });

    test('未匹配路径下前台主区应仍渲染（SPA fallback）', async ({ page }) => {
      // 未匹配路径仍应渲染前台主区（SPA fallback），且无导航
      await page.goto('/non-existent-route');

      await expect(page.locator('nav')).toHaveCount(0);
      await expect(page.locator('main')).toBeVisible();
    });
  });

  test.describe('NovelListPage（首页纯展示书架）', () => {
    test('首页无上传区、删除按钮等管理能力', async ({ page }) => {
      // #50：上传/删除等管理能力已迁至 /admin，首页仅剩书架
      await page.goto('/');

      await expect(page.locator('.upload-zone')).toHaveCount(0);
      await expect(page.locator('[data-testid="novel-file-input"]')).toHaveCount(0);
      await expect(page.locator('[data-testid="cover-file-input"]')).toHaveCount(0);
      await expect(page.locator('button:has-text("删除")')).toHaveCount(0);
    });

    test('首页渲染书架：卡片网格或空状态二选一', async ({ page }) => {
      // #50：有小说时展示卡片网格，无小说时展示居中空状态。
      // 组合选择器等待二者之一出现，避免数据加载与两次检查间的竞态。
      await page.goto('/');

      await expect(
        page.locator('.novel-card, [data-testid="shelf-empty"]').first()
      ).toBeVisible({ timeout: 5_000 });
    });
  });
});
