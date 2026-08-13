/**
 * #61：消息列表子组件——空态引导、消息循环（MessageItem）、typing
 * 指示器与错误提示。data-testid 与样式类与拆分前完全一致。
 */
import type { RefObject } from 'react';
import { Link } from 'react-router-dom';
import type { ChatMessage } from '../hooks/useConversations';
import type { Document } from '../types';
import MessageItem from './MessageItem';

interface MessageListProps {
  messages: ChatMessage[];
  thinkingOpen: Record<number, boolean>;
  documents: Document[];
  isLoading: boolean;
  activeConvId: number | null;
  error: string | null;
  onDismissError: () => void;
  onToggleThinking: (id: number, open: boolean) => void;
  /** 列表末尾滚动锚点（父级 scrollToBottom 使用）。 */
  endRef: RefObject<HTMLDivElement>;
}

export default function MessageList({
  messages,
  thinkingOpen,
  documents,
  isLoading,
  activeConvId,
  error,
  onDismissError,
  onToggleThinking,
  endRef,
}: MessageListProps) {
  return (
    <div className="messages">
      {messages.length === 0 && (
        <div className="empty-state">
          {activeConvId === null ? (
            <>
              <p>还没有会话</p>
              <small>从首页选择一本小说开始讨论，会话将自动创建并绑定该小说</small>
              <Link to="/" className="empty-state-link" data-testid="empty-shelf-link">
                去书架选小说
              </Link>
            </>
          ) : (
            <>
              <p>开始对话吧！</p>
              <small>基于这本小说提问，AI 会结合原文内容回答</small>
            </>
          )}
        </div>
      )}

      {messages.map((msg) => (
        <MessageItem
          key={msg.id}
          msg={msg}
          thinkingOpen={!!thinkingOpen[msg.id]}
          onToggleThinking={(open) => onToggleThinking(msg.id, open)}
          documents={documents}
        />
      ))}

      {isLoading && (
        <div className="message assistant">
          <div className="role">AI</div>
          {/* typing-indicator 不使用 .content 类，避免与 .message.assistant .content
              selector 在 SSE 流期间产生歧义；保留 .typing-indicator 类以供 observer 检测 */}
          <div className="typing-indicator">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
      )}

      {error && (
        <div className="error-message" onClick={onDismissError}>
          {error}
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
}
