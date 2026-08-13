import { useState, useRef, useEffect, useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  documentApi,
  llmStatusApi,
} from '../services/api';
import type { Document } from '../types';
import { getDisplayTitle } from '../utils/format';
import { parseDocId } from '../utils/chat';
import { useConversations } from '../hooks/useConversations';
import { useChatStream } from '../hooks/useChatStream';
import ConversationSidebar from '../components/ConversationSidebar';
import '../App.css';

/** #45 聊天页输入区上方的 LLM 异常 banner。 */
interface LLMBannerState {
  message: string;
  showSettingsLink: boolean;
}

/** 把 ``doc`` 路由参数解析为合法小说 id（无参 / 非法返回 null）。 */
function parseFocusedDocId(param: string | null): number | null {
  if (param === null) return null;
  const id = Number(param);
  return Number.isInteger(id) && id > 0 ? id : null;
}

export default function ChatPage() {
  // #51：路由聚焦单小说（/chat?doc=<id>）。null = 无参访问，激活会话列表首条。
  const [searchParams] = useSearchParams();
  const focusedDocId = parseFocusedDocId(searchParams.get('doc'));
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 切换会话时取消 in-flight SSE 流（会话 hook 与发送流程共用）。
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // #45：preflight 检出的 LLM 状态 + 用户主动 dismiss 标志。null = 正常无 banner。
  const [llmBanner, setLlmBanner] = useState<LLMBannerState | null>(null);
  const [llmBannerDismissed, setLlmBannerDismissed] = useState(false);

  // #59：会话域逻辑（列表加载 / 聚焦新建 / 删除 / 切换 / 消息历史）收敛到 hook。
  const {
    conversations,
    activeConvId,
    sidebarBusy,
    messages,
    setMessages,
    thinkingOpen,
    handleDeleteConversation,
    handleSwitchConversation,
    handleToggleThinking,
  } = useConversations({ focusedDocId, abortRef, setIsLoading, setError });

  // #60：流式发送流程收敛到 hook；showLLMBanner 以回调注入——
  // #61 由 useLLMBanner 提供实现，当前由页面内联过渡实现。
  const showLLMBanner = useCallback(
    (message: string, showSettingsLink: boolean) => {
      setLlmBanner({ message, showSettingsLink });
      setLlmBannerDismissed(false);
    },
    []
  );

  const { input, setInput, send } = useChatStream({
    activeConvId,
    conversations,
    abortRef,
    setMessages,
    isLoading,
    setIsLoading,
    setError,
    showLLMBanner,
  });

  // #45：进入聊天页时拉取 LLM 可用性；未配置立刻显示红字 banner（带"去设置"链接）。
  useEffect(() => {
    let cancelled = false;
    llmStatusApi
      .get()
      .then((status) => {
        if (cancelled) return;
        if (!status.configured) {
          setLlmBanner({ message: status.reason, showSettingsLink: true });
          setLlmBannerDismissed(false);
        }
      })
      .catch((err) => {
        // 拉取状态失败时不强阻塞对话；用户在 send 时仍会被后端拒绝。
        console.warn('LLM 状态查询失败：', err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 加载可用文档列表（#35）。#51 聚焦参数只在挂载时确定一次——
  // 首页卡片跳转 /chat?doc=<id> 必然重新挂载，无需响应同路由参数变化。
  useEffect(() => {
    documentApi.list(1, 100)
      .then((res) => {
        setDocuments(res.items);
      })
      .catch((err) => {
        console.error('加载文档列表失败:', err);
      });
  }, []);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px';
    }
  }, [input]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const activeConv = conversations.find((c) => c.id === activeConvId) ?? null;
  // 上下文 = 当前会话绑定的小说（有且只有一本）；未绑定时不显示提示。
  const boundDoc =
    activeConv?.document_id != null
      ? documents.find((d) => d.id === activeConv.document_id) ?? null
      : null;
  const contextLabel = boundDoc ? `基于《${getDisplayTitle(boundDoc)}》回答` : null;

  return (
    <div className="chat-layout">
      {/* 左侧会话栏（#35，#59 抽为子组件） */}
      <ConversationSidebar
        conversations={conversations}
        activeConvId={activeConvId}
        sidebarBusy={sidebarBusy}
        onSwitch={handleSwitchConversation}
        onDelete={handleDeleteConversation}
      />

      {/* 右侧对话主体 */}
      <div className="chat-area">
        <header>
          {/* #51 顶部左侧 DocQA Logo：点击返回首页书架（#50 删导航后的返回入口） */}
          <Link
            to="/"
            className="chat-header-logo"
            data-testid="chat-logo"
            aria-label="DocQA 返回首页"
          >
            DocQA
          </Link>
          <h1>小说问答助手</h1>
        </header>

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
            <div key={msg.id} className={`message ${msg.role}`}>
              <div className="role">{msg.role === 'user' ? '用户' : 'AI'}</div>
              {msg.role === 'assistant' && msg.thinking && msg.thinking.trim().length > 0 && (
                <details
                  className="thinking-section"
                  open={!!thinkingOpen[msg.id]}
                  onToggle={(e) => handleToggleThinking(msg.id, (e.target as HTMLDetailsElement).open)}
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
            <div className="error-message" onClick={() => setError(null)}>
              {error}
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* #45 LLM 不可用 / 运行时失败的顶部红字 banner */}
        {llmBanner && !llmBannerDismissed && (
          <div className="llm-error-banner" data-testid="llm-error-banner" role="alert">
            <span className="llm-error-banner-text">{llmBanner.message}</span>
            {llmBanner.showSettingsLink && (
              <Link
                to="/settings"
                className="llm-error-banner-link"
                data-testid="llm-error-banner-link"
              >
                去设置
              </Link>
            )}
            <button
              type="button"
              className="llm-error-banner-close"
              data-testid="llm-error-banner-close"
              aria-label="关闭"
              onClick={() => setLlmBannerDismissed(true)}
            >
              ×
            </button>
          </div>
        )}

        <div className="input-area">
          {contextLabel && (
            <div className="context-indicator" data-testid="context-indicator">
              <span className="context-label">{contextLabel}</span>
            </div>
          )}

          <div className="input-row">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                activeConvId === null
                  ? '从首页选择一本小说开始讨论'
                  : '输入问题... (Enter 发送，Shift+Enter 换行)'
              }
              rows={1}
              disabled={isLoading || activeConvId === null}
            />
            <button
              onClick={send}
              disabled={isLoading || !input.trim() || activeConvId === null}
            >
              {isLoading ? '发送中...' : '发送'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
