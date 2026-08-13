/**
 * 全局前台布局（#54）：顶部导航条恢复，仅含「书籍列表」「会话列表」
 * 两个入口，当前路由高亮对应导航项。管理端 /admin 使用独立
 * AdminLayout，不经过此布局。
 */
import { Link, useLocation } from 'react-router-dom';
import '../App.css';

interface LayoutProps {
  children: React.ReactNode;
}

function navItemClass(active: boolean): string {
  return `top-nav-item ${active ? 'active' : ''}`;
}

export default function Layout({ children }: LayoutProps) {
  const { pathname } = useLocation();

  // 高亮规则：/ 高亮书籍列表；/chat 高亮会话列表；/settings 无高亮项
  const booksActive = pathname === '/';
  const chatActive = pathname.startsWith('/chat');

  return (
    <div className="min-h-screen flex flex-col">
      <nav className="top-nav" aria-label="前台主导航">
        <div className="top-nav-inner">
          <Link
            to="/"
            className={navItemClass(booksActive)}
            data-testid="nav-books"
          >
            书籍列表
          </Link>
          <Link
            to="/chat"
            className={navItemClass(chatActive)}
            data-testid="nav-chats"
          >
            会话列表
          </Link>
        </div>
      </nav>
      <main className="flex-1 bg-gray-50">
        {children}
      </main>
    </div>
  );
}
