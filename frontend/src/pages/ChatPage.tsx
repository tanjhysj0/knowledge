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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thinkingOpen, setThinkingOpen] = useState<Record<number, boolean>>({});
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 加载可用文档与历史对话，使刷新后可恢复
  useEffect(() => {
    let cancelled = false;
    documentApi.list(1, 100)
      .then((res) => {
        if (!cancelled) setDocuments(res.items);
      })
      .catch((err) => {
        console.error('加载文档列表失败:', err);
      });
    chatApi.history()
      .then((history) => {
        if (cancelled) return;
        // 后端 ChatMessage 字段对齐前端 ChatMessage
        setMessages(history.map((m) => ({ id: m.id, role: m.role, content: m.content })));
      })
      .catch((err) => {
        console.error('加载对话历史失败:', err);
      });
    return () => {
      cancelled = true;
    };
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

    const userMessage = { id: Date.now(), role: 'user', content: input };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setError(null);
    setIsLoading(true);

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: input, document_ids: documents.map((d) => d.id) }),
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

  const handleClear = async () => {
    try {
      await chatApi.clear();
      setMessages([]);
      setThinkingOpen({});
    } catch (err) {
      console.error('Clear failed:', err);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <>
      <header>
        <h1>DocQA - 文档问答助手</h1>
        {messages.length > 0 && (
          <button onClick={handleClear} title="清除对话历史">
            清除历史
          </button>
        )}
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
            <div className="content typing-indicator">
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
        {documents.length > 0 && (
          <div className="context-indicator">
            基于 {documents.length} 个文档回答
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
