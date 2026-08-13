/**
 * 管理端独立布局（不套用全局 Layout）：左侧菜单 + 右侧 Outlet。
 *
 * 菜单两项对应独立路由：「小说管理」匹配 /admin 及 /admin/novels/*，
 * 「LLM 设置」匹配 /admin/settings。高亮随当前路由变化。
 */
import { Link, Outlet, useLocation } from 'react-router-dom';

function menuClass(active: boolean): string {
  return `admin-menu-item ${active ? 'active' : ''}`;
}

export default function AdminLayout() {
  const { pathname } = useLocation();
  const novelsActive = pathname === '/admin' || pathname.startsWith('/admin/novels');
  const settingsActive = pathname.startsWith('/admin/settings');

  return (
    <div className="admin-layout">
      <aside className="admin-sidebar">
        <div className="admin-brand">DocQA 管理端</div>
        <nav className="admin-menu">
          <Link to="/admin" className={menuClass(novelsActive)}>
            小说管理
          </Link>
          <Link to="/admin/settings" className={menuClass(settingsActive)}>
            LLM 设置
          </Link>
        </nav>
      </aside>
      <main className="admin-content">
        <Outlet />
      </main>
    </div>
  );
}
