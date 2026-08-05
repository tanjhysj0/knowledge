import { useState, useRef, useEffect, useCallback } from 'react';
import { chatApi } from '../services/api';
import type { Document } from '../types';
import '../App.css';

interface ChatMessage {
  id: number;
  role: string;
  content: string;
}

interface ChatPageProps {
  documents: Document[];
}

export default function ChatPage({ documents }: ChatPageProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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

      let assistantContent = '';
      const assistantMessage = { id: Date.now() + 1, role: 'assistant', content: '' };
      setMessages((prev) => [...prev, assistantMessage]);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = new TextDecoder().decode(value);
        assistantContent += text;
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantMessage.id ? { ...m, content: assistantContent } : m))
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
            <div className="content">
              {msg.content}
              {msg.role === 'assistant' && msg.content === messages.find(m => m.id === msg.id)?.content && isLoading && msg.id === messages[messages.length - 1]?.id && (
                <span className="typing-cursor">|</span>
              )}
            </div>
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
