import { useState, useRef } from 'react';
import { documentApi, chatApi } from './services/api';
import type { Document, ChatMessage } from './types';
import './App.css';

function App() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = async (file: File) => {
    try {
      const doc = await documentApi.upload(file);
      setDocuments(prev => [doc, ...prev]);
    } catch (error) {
      console.error('Upload failed:', error);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  };

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content: input,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: input, document_ids: documents.map(d => d.id) }),
      });

      const reader = response.body?.getReader();
      if (!reader) return;

      let assistantContent = '';
      const assistantMessage: ChatMessage = {
        id: Date.now() + 1,
        role: 'assistant',
        content: '',
        created_at: new Date().toISOString(),
      };
      setMessages(prev => [...prev, assistantMessage]);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = new TextDecoder().decode(value);
        assistantContent += text;
        setMessages(prev =>
          prev.map(m => m.id === assistantMessage.id ? { ...m, content: assistantContent } : m)
        );
      }
    } catch (error) {
      console.error('Chat error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteDoc = async (id: number) => {
    try {
      await documentApi.delete(id);
      setDocuments(prev => prev.filter(d => d.id !== id));
    } catch (error) {
      console.error('Delete failed:', error);
    }
  };

  const handleClearHistory = async () => {
    try {
      await chatApi.clear();
      setMessages([]);
    } catch (error) {
      console.error('Clear failed:', error);
    }
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <h2>文档列表</h2>
        <div
          className={`upload-zone ${isDragging ? 'dragging' : ''}`}
          onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <p>拖拽或点击上传</p>
          <small>支持 TXT, MD, PDF, DOCX</small>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.md,.pdf,.docx"
          onChange={e => e.target.files?.[0] && handleFileUpload(e.target.files[0])}
          hidden
        />
        <ul className="doc-list">
          {documents.map(doc => (
            <li key={doc.id}>
              <span>{doc.filename}</span>
              <button onClick={() => handleDeleteDoc(doc.id)}>删除</button>
            </li>
          ))}
        </ul>
      </aside>

      <main className="chat-area">
        <header>
          <h1>DocQA - 文档问答助手</h1>
          {messages.length > 0 && <button onClick={handleClearHistory}>清除历史</button>}
        </header>

        <div className="messages">
          {messages.map(msg => (
            <div key={msg.id} className={`message ${msg.role}`}>
              <div className="role">{msg.role === 'user' ? '用户' : 'AI'}</div>
              <div className="content">{msg.content}</div>
            </div>
          ))}
          {isLoading && <div className="message assistant"><div className="content">思考中...</div></div>}
        </div>

        <div className="input-area">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), handleSend())}
            placeholder="输入问题..."
            rows={3}
          />
          <button onClick={handleSend} disabled={isLoading}>发送</button>
        </div>
      </main>
    </div>
  );
}

export default App;
