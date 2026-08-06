import { useState, useRef, useEffect, useCallback } from 'react';
import { chatApi, documentApi } from '../services/api';
import type { Document } from '../types';
import { SSEParser } from '../utils/sseParser';
import '../App.css';

interface ChatMessage {
  id: number;
  role: string;
  content: string;
  thinking?: string;
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
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 加载可用文档与历史对话，使刷新后可恢复。
  // 不使用 cancelled 守卫：React StrictMode 在 dev 下会双调用 effect，若
  // 第一次调用被 cancelled 丢弃会拿到空列表。组件卸载后 setDocuments 仍是
  // noop（React 18 静默忽略），不会引发可见 bug。
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
    chatApi.history()
      .then((history) => {
        // 后端 ChatMessage 字段对齐前端 ChatMessage
        setMessages(history.map((m) => ({ id: m.id, role: m.role, content: m.content })));
      })
      .catch((err) => {
        console.error('加载对话历史失败:', err);
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

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage = { id: Date.now(), role: 'user', content: input };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setError(null);
    setIsLoading(true);

    const documentIds = Array.from(selectedIds);

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: input, document_ids: documentIds }),
      });

      if (!response.ok) {
        throw new Error('请求失败');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('无法读取响应');

      const decoder = new TextDecoder();
      const parser = new SSEParser();
      const assistantId = Date.now() + 1;
      setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '' }]);

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
                const payload = ev.data as { content?: string } | null;
                const piece = payload?.content;
                if (typeof piece !== 'string' || piece.length === 0) continue;
                if (ev.event === 'thinking') {
                  thinking = (thinking || '') + piece;
                } else if (ev.event === 'message') {
                  content += piece;
                } else if (ev.event === 'error') {
                  throw new Error(payload?.content?.toString() || '模型返回错误');
                }
              }
              return { ...m, content, thinking };
            })
          );
        }
      }

      for (const ev of parser.end()) {
        const payload = ev.data as { content?: string } | null;
        const piece = payload?.content;
        if (typeof piece !== 'string' || piece.length === 0) continue;
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== assistantId) return m;
            if (ev.event === 'thinking') {
              return { ...m, thinking: (m.thinking || '') + piece };
            }
            if (ev.event === 'message') {
              return { ...m, content: m.content + piece };
            }
            return m;
          })
        );
      }
    } catch (err: any) {
      setError(err.message || '发送消息失败');
      // Remove the user message if the request failed
      setMessages((prev) => prev.filter((m) => m.id !== userMessage.id));
    } finally {
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
    <>
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
    </>
  );
}
