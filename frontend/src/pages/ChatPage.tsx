import { useState, useRef, useEffect, useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  chatApi,
  documentApi,
  llmStatusApi,
  LLMUnavailableError,
} from '../services/api';
import type { Document } from '../types';
import { SSEParser } from '../utils/sseParser';
import { getDisplayTitle } from '../utils/format';
import { parseDocId, parseSources } from '../utils/chat';
import { useConversations } from '../hooks/useConversations';
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
  const [input, setInput] = useState('');
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

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;
    if (activeConvId === null) return;

    const userMessage = { id: Date.now(), role: 'user', content: input };
    setMessages((prev) => [...prev, userMessage]);
    const sentText = input;
    setInput('');
    setError(null);
    setIsLoading(true);

    const convIdAtSend = activeConvId;
    // 会话上下文有且只有其绑定的一本小说（#52）；未绑定（存量会话）不携带文档。
    const currentConv = conversations.find((c) => c.id === convIdAtSend);
    const documentIds =
      currentConv?.document_id != null ? [currentConv.document_id] : [];

    const controller = new AbortController();
    abortRef.current = controller;
    // #45 catch 块需要能清掉本轮助手占位消息，所以提到 try 块外。
    let assistantId: number | null = null;

    try {
      // #58：流式请求统一走服务层；503 在服务层解析为 LLMUnavailableError。
      const response = await chatApi.stream(
        {
          message: sentText,
          document_ids: documentIds,
          conversation_id: convIdAtSend,
        },
        controller.signal
      );

      if (!response.ok) {
        throw new Error('请求失败');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('无法读取响应');

      const decoder = new TextDecoder();
      const parser = new SSEParser();
      // #45 赋值给外层 let，catch 块才能清掉本轮助手占位（不可用 const 重声明）。
      const newAssistantId = Date.now() + 1;
      assistantId = newAssistantId;
      setMessages((prev) => [...prev, { id: newAssistantId, role: 'assistant', content: '', sources: [] }]);
      // 累积本轮的 sources（SSE done 事件一次性下发），用 ref 避免闭包陈旧
      const sourcesRef: { current: string[] } = { current: [] };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        const events = parser.feed(text);
        // #45 先在 setMessages 回调外捕获 error 事件，throw 才能跳到外层 catch。
        const errorEvent = events.find((e) => e.event === 'error');
        if (errorEvent) {
          const errPayload = errorEvent.data as
            | { reason?: string; error?: string; content?: string }
            | null;
          const reason =
            errPayload?.reason ||
            errPayload?.error ||
            errPayload?.content?.toString() ||
            '模型返回错误';
          throw new LLMUnavailableError(reason, false);
        }
        if (events.length > 0) {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              let { content, thinking } = m;
              for (const ev of events) {
                const payload = ev.data as
                  | { content?: string; sources?: string[] }
                  | null;
                if (ev.event === 'thinking' || ev.event === 'message') {
                  const piece = payload?.content;
                  if (typeof piece !== 'string' || piece.length === 0) continue;
                  if (ev.event === 'thinking') {
                    thinking = (thinking || '') + piece;
                  } else {
                    content += piece;
                  }
                } else if (ev.event === 'done') {
                  // done 事件携带 sources（#33）；保留后端顺序（#56 收敛到 parseSources）
                  const incoming = parseSources(payload);
                  if (incoming.length > 0) {
                    sourcesRef.current = incoming;
                  }
                }
              }
              return { ...m, content, thinking, sources: sourcesRef.current };
            })
          );
        }
      }

      for (const ev of parser.end()) {
        const payload = ev.data as
          | { content?: string; sources?: string[] }
          | null;
        if (ev.event === 'thinking' || ev.event === 'message') {
          const piece = payload?.content;
          if (typeof piece !== 'string' || piece.length === 0) continue;
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              if (ev.event === 'thinking') {
                return { ...m, thinking: (m.thinking || '') + piece };
              }
              return { ...m, content: m.content + piece };
            })
          );
        } else if (ev.event === 'done') {
          const incoming = parseSources(payload);
          if (incoming.length > 0) {
            sourcesRef.current = incoming;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, sources: sourcesRef.current } : m
              )
            );
          }
        }
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        // 主动取消：忽略错误（典型场景：切换会话时）
      } else if (err instanceof LLMUnavailableError) {
        // #45 LLM 不可用：显示红字 banner 并清掉本轮的用户/助手占位消息。
        setLlmBanner({ message: err.message, showSettingsLink: err.showSettingsLink });
        setLlmBannerDismissed(false);
        setMessages((prev) =>
          prev.filter((m) => m.id !== userMessage.id && m.id !== assistantId)
        );
      } else {
        setError(err.message || '发送消息失败');
        // Remove the user message if the request failed
        setMessages((prev) => prev.filter((m) => m.id !== userMessage.id));
      }
    } finally {
      abortRef.current = null;
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
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
              onClick={handleSend}
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
