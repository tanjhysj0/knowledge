import { test, expect } from '@playwright/test';

test.describe('Layout + NovelListPage - E2E (#50/#54)', () => {
  test.describe('前台顶部导航条（#54）', () => {
    test('前台三页均渲染导航条，仅含书籍列表/会话列表两个入口', async ({ page }) => {
      for (const path of ['/', '/chat', '/settings']) {
        await page.goto(path);

        const nav = page.locator('nav.top-nav');
        await expect(nav).toBeVisible();
        await expect(nav.locator('a')).toHaveCount(2);
        await expect(nav.locator('[data-testid="nav-books"]')).toHaveText('书籍列表');
        await expect(nav.locator('[data-testid="nav-chats"]')).toHaveText('会话列表');
      }
    });

    test('当前路由高亮对应导航项（/ 书籍列表，/chat 会话列表）', async ({ page }) => {
      await page.goto('/');
      await expect(page.locator('[data-testid="nav-books"]')).toHaveClass(/active/);
      await expect(page.locator('[data-testid="nav-chats"]')).not.toHaveClass(/active/);

      await page.goto('/chat');
      await expect(page.locator('[data-testid="nav-chats"]')).toHaveClass(/active/);
      await expect(page.locator('[data-testid="nav-books"]')).not.toHaveClass(/active/);

      // /settings 不属于两个入口，无高亮项
      await page.goto('/settings');
      await expect(page.locator('[data-testid="nav-books"]')).not.toHaveClass(/active/);
      await expect(page.locator('[data-testid="nav-chats"]')).not.toHaveClass(/active/);
    });

    test('书籍列表跳 /（书架可见）、会话列表跳 /chat（会话侧栏可见）', async ({ page }) => {
      await page.goto('/settings');

      await page.locator('[data-testid="nav-books"]').click();
      await expect(page).toHaveURL(/\/$/, { timeout: 5_000 });
      await expect(
        page
          .locator('[data-testid="shelf-grid"], [data-testid="shelf-empty"]')
          .first()
      ).toBeVisible({ timeout: 5_000 });

      await page.locator('[data-testid="nav-chats"]').click();
      await expect(page).toHaveURL(/\/chat$/, { timeout: 5_000 });
      await expect(page.locator('[data-testid="conversation-sidebar"]')).toBeVisible();
    });

    test('/admin 不渲染前台导航条，管理端布局不变', async ({ page }) => {
      await page.goto('/admin');

      await expect(page.locator('nav.top-nav')).toHaveCount(0);
      await expect(page.locator('.admin-sidebar')).toBeVisible();
      await expect(page.locator('.admin-menu-item').first()).toBeVisible();
    });

    test('未匹配路径下前台主区应仍渲染（SPA fallback）', async ({ page }) => {
      // 未匹配路径仍应渲染前台主区（SPA fallback），导航条随前台布局渲染
      await page.goto('/non-existent-route');

      await expect(page.locator('nav.top-nav')).toBeVisible();
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
