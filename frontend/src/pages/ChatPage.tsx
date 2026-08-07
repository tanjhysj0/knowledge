import { useState, useRef, useEffect, useCallback } from 'react';
import { conversationApi, documentApi } from '../services/api';
import type { ChatMessage as ApiChatMessage, Conversation, Document } from '../types';
import { SSEParser } from '../utils/sseParser';
import '../App.css';

interface ChatMessage {
  id: number;
  role: string;
  content: string;
  thinking?: string;
  /** RAG 检索命中的文档来源列表（#33），如 ``["doc_1", "doc_3"]``。空数组 / undefined = 未命中。 */
  sources?: string[];
}

/** 把 ``doc_<id>`` 解析为 ``<id>`` 整数（无效 token 返回 null）。 */
function parseDocId(token: string): number | null {
  if (!token.startsWith('doc_')) return null;
  const id = Number(token.slice(4));
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** 从首条用户消息截取前 20 字作为会话标题摘要。 */
function summarizeTitle(text: string): string {
  const trimmed = text.trim();
  if (trimmed.length <= 20) return trimmed;
  return trimmed.slice(0, 20) + '…';
}

export default function ChatPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set());
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thinkingOpen, setThinkingOpen] = useState<Record<number, boolean>>({});
  // 会话（#35）：左侧栏列表 + 当前激活 id；首次加载为空数组时自动建一个。
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<number | null>(null);
  // 防止快速连续点击同一会话的删除 / 切换按钮。
  const [sidebarBusy, setSidebarBusy] = useState(false);
  // 切换会话时取消 in-flight SSE 流。
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 加载可用文档与会话列表（#35）。
  useEffect(() => {
    documentApi.list(1, 100)
      .then((res) => {
        setDocuments(res.items);
        // 默认全选新加载的文档
        setSelectedIds((prev) => {
          const next = new Set(prev);
          for (const d of res.items) next.add(d.id);
          return next;
        });
      })
      .catch((err) => {
        console.error('加载文档列表失败:', err);
      });

    conversationApi
      .list()
      .then(async (list) => {
        if (list.length === 0) {
          // 首次进入若无会话则自动建一个，激活到该 id 并空消息列表。
          const created = await conversationApi.create({});
          setConversations([created]);
          setActiveConvId(created.id);
          setMessages([]);
        } else {
          setConversations(list);
          setActiveConvId(list[0].id);
        }
      })
      .catch((err) => {
        console.error('加载会话列表失败:', err);
      });
  }, []);

  // 激活会话变化时：拉取该会话的消息历史（#35）。
  useEffect(() => {
    if (activeConvId === null) return;
    let cancelled = false;
    conversationApi
      .messages(activeConvId)
      .then((items: ApiChatMessage[]) => {
        if (cancelled) return;
        setMessages(
          items.map((m) => ({ id: m.id, role: m.role, content: m.content }))
        );
        setThinkingOpen({});
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('加载会话消息失败:', err);
      });
    return () => {
      cancelled = true;
    };
  }, [activeConvId]);

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

  const toggleDocument = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedIds((prev) => {
      if (prev.size === documents.length) {
        return new Set();
      }
      return new Set(documents.map((d) => d.id));
    });
  };

  /** 终止当前正在进行的 SSE 流（#35 切换会话时使用）。 */
  const abortCurrentStream = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  };

  /** 新建一个会话：建好后激活、消息清空。 */
  const handleNewConversation = async () => {
    if (sidebarBusy) return;
    setSidebarBusy(true);
    try {
      const created = await conversationApi.create({});
      setConversations((prev) => [created, ...prev]);
      setActiveConvId(created.id);
      setMessages([]);
      setThinkingOpen({});
      setError(null);
    } catch (err) {
      console.error('新建会话失败:', err);
      setError('新建会话失败，请稍后重试');
    } finally {
      setSidebarBusy(false);
    }
  };

  /** 删除一个会话：若删的是激活会话则切到下一条或置空。 */
  const handleDeleteConversation = async (id: number) => {
    if (sidebarBusy) return;
    if (!confirm('确认删除该会话及其全部消息？')) return;
    setSidebarBusy(true);
    try {
      await conversationApi.remove(id);
      const remaining = conversations.filter((c) => c.id !== id);
      setConversations(remaining);
      if (activeConvId === id) {
        if (remaining.length === 0) {
          // 删完后空了，再建一条
          const created = await conversationApi.create({});
          setConversations([created]);
          setActiveConvId(created.id);
        } else {
          setActiveConvId(remaining[0].id);
        }
        setMessages([]);
        setThinkingOpen({});
      }
    } catch (err) {
      console.error('删除会话失败:', err);
      setError('删除会话失败，请稍后重试');
    } finally {
      setSidebarBusy(false);
    }
  };

  /** 切换会话：取消 in-flight 流 → 清空消息 → 触发 effect 拉取新历史。 */
  const handleSwitchConversation = (id: number) => {
    if (sidebarBusy) return;
    if (id === activeConvId) return;
    abortCurrentStream();
    setIsLoading(false);
    setActiveConvId(id);
    setMessages([]);
    setThinkingOpen({});
    setError(null);
  };

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;
    if (activeConvId === null) return;

    const userMessage = { id: Date.now(), role: 'user', content: input };
    setMessages((prev) => [...prev, userMessage]);
    const sentText = input;
    setInput('');
    setError(null);
    setIsLoading(true);

    const documentIds = Array.from(selectedIds);
    const convIdAtSend = activeConvId;

    // 首条用户消息发送成功后，把会话标题改成消息前 20 字摘要（#35）。
    // 不在标题已是用户摘要形式时再次修改，避免重复触发。
    const currentConv = conversations.find((c) => c.id === convIdAtSend);
    const isFirstMessage =
      currentConv && (currentConv.message_count === 0 || currentConv.title === '新对话');
    if (isFirstMessage) {
      const newTitle = summarizeTitle(sentText);
      // 乐观更新本地标题（失败不回滚，便于用户继续对话）
      setConversations((prev) =>
        prev.map((c) => (c.id === convIdAtSend ? { ...c, title: newTitle } : c))
      );
      conversationApi
        .update(convIdAtSend, { title: newTitle })
        .catch((err) => console.error('更新会话标题失败:', err));
    }

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: sentText,
          document_ids: documentIds,
          conversation_id: convIdAtSend,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error('请求失败');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('无法读取响应');

      const decoder = new TextDecoder();
      const parser = new SSEParser();
      const assistantId = Date.now() + 1;
      setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '', sources: [] }]);

      // 累积本轮的 sources（SSE done 事件一次性下发），用 ref 避免闭包陈旧
      const sourcesRef: { current: string[] } = { current: [] };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        const events = parser.feed(text);
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
                  // done 事件携带 sources（#33）；保留后端顺序
                  const incoming = Array.isArray(payload?.sources)
                    ? Array.from(
                        new Set(
                          (payload!.sources as unknown[]).filter(
                            (s): s is string => typeof s === 'string'
                          )
                        )
                      )
                    : [];
                  if (incoming.length > 0) {
                    sourcesRef.current = incoming;
                  }
                } else if (ev.event === 'error') {
                  throw new Error(payload?.content?.toString() || '模型返回错误');
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
          const incoming = Array.isArray(payload?.sources)
            ? Array.from(
                new Set(
                  (payload!.sources as unknown[]).filter(
                    (s): s is string => typeof s === 'string'
                  )
                )
              )
            : [];
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

  const selectedCount = selectedIds.size;
  const totalCount = documents.length;
  // 三态文案：有文档未选 / 全选 / 部分选；无文档时不渲染
  const contextLabel = (() => {
    if (totalCount === 0) return null;
    if (selectedCount === 0) return '未选择文档，将基于通用知识回答';
    if (selectedCount === totalCount) return `基于全部 ${totalCount} 个文档回答`;
    return `基于 ${selectedCount} / ${totalCount} 个文档回答`;
  })();

  return (
    <div className="chat-layout">
      {/* 左侧会话栏（#35） */}
      <aside className="conversation-sidebar" data-testid="conversation-sidebar">
        <div className="conversation-sidebar-header">
          <span className="conversation-sidebar-title">会话</span>
          <button
            type="button"
            className="conversation-new-btn"
            data-testid="conversation-new"
            onClick={handleNewConversation}
            disabled={sidebarBusy}
          >
            ＋ 新建
          </button>
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
                  onClick={() => handleSwitchConversation(c.id)}
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
                  onClick={() => handleDeleteConversation(c.id)}
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

      {/* 右侧对话主体 */}
      <div className="chat-area">
        <header>
          <h1>DocQA - 文档问答助手</h1>
        </header>

        <div className="messages">
          {messages.length === 0 && (
            <div className="empty-state">
              <p>开始对话吧！</p>
              <small>上传文档后可基于文档内容回答问题</small>
            </div>
          )}

          {messages.map((msg) => (
            <div key={msg.id} className={`message ${msg.role}`}>
              <div className="role">{msg.role === 'user' ? '用户' : 'AI'}</div>
              {msg.role === 'assistant' && msg.thinking && msg.thinking.trim().length > 0 && (
                <details
                  className="thinking-section"
                  open={!!thinkingOpen[msg.id]}
                  onToggle={(e) => setThinkingOpen((prev) => ({ ...prev, [msg.id]: (e.target as HTMLDetailsElement).open }))}
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

        <div className="input-area">
          {contextLabel && (
            <div
              className={`context-indicator ${selectedCount === 0 ? 'context-empty' : ''}`}
              data-testid="context-indicator"
            >
              <span className="context-label">{contextLabel}</span>
              {totalCount > 0 && (
                <button
                  type="button"
                  className="context-toggle"
                  data-testid="context-toggle"
                  onClick={() => setSelectorOpen((v) => !v)}
                  aria-expanded={selectorOpen}
                >
                  {selectorOpen ? '收起' : '选择文档'}
                </button>
              )}
            </div>
          )}

          {selectorOpen && totalCount > 0 && (
            <div className="document-selector" data-testid="document-selector">
              <div className="document-selector-header">
                <span>文档选择</span>
                <button
                  type="button"
                  className="document-selector-all"
                  data-testid="document-selector-all"
                  onClick={toggleSelectAll}
                >
                  {selectedCount === totalCount ? '全部取消' : '全部选择'}
                </button>
              </div>
              <ul className="document-selector-list">
                {documents.map((doc) => (
                  <li key={doc.id} className="document-selector-item">
                    <label>
                      <input
                        type="checkbox"
                        data-testid={`document-checkbox-${doc.id}`}
                        checked={selectedIds.has(doc.id)}
                        onChange={() => toggleDocument(doc.id)}
                      />
                      <span className="document-selector-name" title={doc.filename}>
                        {doc.filename}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="input-row">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入问题... (Enter 发送，Shift+Enter 换行)"
              rows={1}
              disabled={isLoading}
            />
            <button onClick={handleSend} disabled={isLoading || !input.trim()}>
              {isLoading ? '发送中...' : '发送'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
