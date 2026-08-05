import { useState, useRef, useCallback } from 'react';
import { documentApi } from './services/api';
import type { Document } from './types';
import ChatPage from './pages/ChatPage';
import './App.css';

function App() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = async (file: File) => {
    try {
      const doc = await documentApi.upload(file);
      setDocuments((prev) => [doc, ...prev]);
    } catch (error) {
      console.error('Upload failed:', error);
    }
  };

  const handleDeleteDoc = async (id: number) => {
    try {
      await documentApi.delete(id);
      setDocuments((prev) => prev.filter((d) => d.id !== id));
    } catch (error) {
      console.error('Delete failed:', error);
    }
  };

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFileUpload(file);
    },
    []
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <h2>文档列表</h2>
        <div
          className={`upload-zone ${isDragging ? 'dragging' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragging(true);
          }}
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
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFileUpload(file);
            e.target.value = '';
          }}
          hidden
        />
        <ul className="doc-list">
          {documents.map((doc) => (
            <li key={doc.id}>
              <span>{doc.filename}</span>
              <button onClick={() => handleDeleteDoc(doc.id)}>删除</button>
            </li>
          ))}
        </ul>
      </aside>

      <main className="chat-area">
        <ChatPage documents={documents} />
      </main>
    </div>
  );
}

export default App;
