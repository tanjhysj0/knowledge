/**
 * #59：会话侧栏子组件——纯展示 + 回调。data-testid 与样式类
 * 与拆分前完全一致（conversation-sidebar / conversation-list /
 * conversation-item-* / conversation-delete-* / data-active）。
 */
import type { Conversation } from '../types';

interface ConversationSidebarProps {
  conversations: Conversation[];
  activeConvId: number | null;
  sidebarBusy: boolean;
  onSwitch: (id: number) => void;
  onDelete: (id: number) => void;
}

export default function ConversationSidebar({
  conversations,
  activeConvId,
  sidebarBusy,
  onSwitch,
  onDelete,
}: ConversationSidebarProps) {
  return (
    <aside className="conversation-sidebar" data-testid="conversation-sidebar">
      <div className="conversation-sidebar-header">
        <span className="conversation-sidebar-title">会话</span>
      </div>
      <ul className="conversation-list" data-testid="conversation-list">
        {conversations.length === 0 && (
          <li className="conversation-empty">暂无会话</li>
        )}
        {conversations.map((c) => {
          const isActive = c.id === activeConvId;
          return (
            <li
              key={c.id}
              className={`conversation-item ${isActive ? 'active' : ''}`}
              data-testid={`conversation-item-${c.id}`}
              data-active={isActive ? 'true' : 'false'}
            >
              <button
                type="button"
                className="conversation-item-main"
                onClick={() => onSwitch(c.id)}
                disabled={sidebarBusy}
                title={c.title ?? '新对话'}
              >
                <span className="conversation-item-title">
                  {c.title ?? '新对话'}
                </span>
                <span className="conversation-item-meta">
                  {c.message_count} 条消息
                </span>
              </button>
              <button
                type="button"
                className="conversation-item-delete"
                data-testid={`conversation-delete-${c.id}`}
                onClick={() => onDelete(c.id)}
                disabled={sidebarBusy}
                title="删除会话"
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
