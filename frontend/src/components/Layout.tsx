/**
 * 全局前台布局（#50）：顶部导航已删除（首页/问答/设置入口全站移除，
 * 管理能力迁至 /admin），仅保留主内容容器。
 */
interface LayoutProps {
  children: React.ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  return (
    <div className="min-h-screen flex flex-col">
      <main className="flex-1 bg-gray-50">
        {children}
      </main>
    </div>
  );
}
