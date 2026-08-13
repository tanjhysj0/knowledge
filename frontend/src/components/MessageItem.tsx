/**
 * #61：单条消息子组件——角色标签、thinking details 展开、正文与
 * RAG 来源区。data-testid 与样式类与拆分前完全一致。
 */
import type { ChatMessage } from '../hooks/useConversations';
import type { Document } from '../types';
import { parseDocId } from '../utils/chat';

interface MessageItemProps {
  msg: ChatMessage;
  /** 该消息 thinking 是否展开（父级持有逐消息展开状态）。 */
  thinkingOpen: boolean;
  onToggleThinking: (open: boolean) => void;
  /** 文档列表：sources 中的 doc_<id> token 解析为文件名与分段数。 */
  documents: Document[];
}

export default function MessageItem({
  msg,
  thinkingOpen,
  onToggleThinking,
  documents,
}: MessageItemProps) {
  return (
    <div className={`message ${msg.role}`}>
      <div className="role">{msg.role === 'user' ? '用户' : 'AI'}</div>
      {msg.role === 'assistant' && msg.thinking && msg.thinking.trim().length > 0 && (
        <details
          className="thinking-section"
          open={thinkingOpen}
          onToggle={(e) => onToggleThinking((e.target as HTMLDetailsElement).open)}
        >
          <summary className="thinking-summary">思考过程</summary>
          <div className="thinking-content">{msg.thinking}</div>
        </details>
      )}
      <div className="content">{msg.content}</div>
      {msg.role === 'assistant' &&
        Array.isArray(msg.sources) &&
        msg.sources.length > 0 && (
          <div
            className="sources"
            data-testid={`message-sources-${msg.id}`}
            aria-label="参考文档来源"
          >
            <div className="sources-label">参考来源：</div>
            <ul className="sources-list">
              {msg.sources.map((token, idx) => {
                const docId = parseDocId(token);
                const doc = docId !== null
                  ? documents.find((d) => d.id === docId)
                  : undefined;
                const label = doc ? doc.filename : token;
                const chunks = doc?.chunk_count;
                return (
                  <li
                    key={`${token}-${idx}`}
                    className="sources-item"
                    data-testid={`source-item-${token}`}
                    title={label}
                  >
                    <span className="sources-name">{label}</span>
                    {chunks !== undefined && (
                      <span className="sources-chunks"> · {chunks} 段</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
    </div>
  );
}
