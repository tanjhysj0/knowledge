/**
 * #53：默认封面图（无封面小说统一使用，管理端表单/列表与首页书架共用）。
 * 内置 SVG 静态资源：竖版书形 + 书名占位，无需网络请求。
 */
interface DefaultCoverProps {
  className?: string;
}

export default function DefaultCover({ className }: DefaultCoverProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 32"
      width="48"
      height="64"
      role="img"
      aria-label="默认封面"
    >
      <rect width="24" height="32" rx="2" fill="#e3e8ef" />
      {/* 书脊装饰 */}
      <rect x="3" y="4" width="2" height="24" rx="1" fill="#c3cbd6" />
      <rect x="7" y="8" width="12" height="1.5" rx="0.75" fill="#c3cbd6" />
      <rect x="7" y="12" width="9" height="1.5" rx="0.75" fill="#c3cbd6" />
      <rect x="7" y="16" width="11" height="1.5" rx="0.75" fill="#c3cbd6" />
      <text x="12" y="27" textAnchor="middle" fontSize="6" fill="#9aa4b2">
        小说
      </text>
    </svg>
  );
}
