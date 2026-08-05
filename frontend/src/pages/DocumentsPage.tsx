import { useState, useRef } from 'react';
import { documentApi } from '../services/api';
import type { Document } from '../types';

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
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

  const handleDelete = async (id: number) => {
    try {
      await documentApi.delete(id);
      setDocuments(prev => prev.filter(d => d.id !== id));
    } catch (error) {
      console.error('Delete failed:', error);
    }
  };

  return (
    <div className="p-6">
      <h2 className="text-2xl font-bold mb-4">文档管理</h2>
      
      <div
        className={`border-2 border-dashed rounded-lg p-8 text-center mb-6 transition-colors cursor-pointer ${
          isDragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-gray-400'
        }`}
        onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        <p className="text-gray-600">拖拽或点击上传</p>
        <small className="text-gray-400">支持 TXT, MD, PDF, DOCX</small>
      </div>
      
      <input
        ref={fileInputRef}
        type="file"
        accept=".txt,.md,.pdf,.docx"
        onChange={e => e.target.files?.[0] && handleFileUpload(e.target.files[0])}
        hidden
      />

      <div className="bg-white rounded-lg shadow">
        {documents.map(doc => (
          <div key={doc.id} className="flex items-center justify-between p-4 border-b last:border-b-0">
            <span className="text-gray-800">{doc.filename}</span>
            <button
              onClick={() => handleDelete(doc.id)}
              className="text-red-500 hover:text-red-700"
            >
              删除
            </button>
          </div>
        ))}
        {documents.length === 0 && (
          <p className="p-4 text-gray-500 text-center">暂无文档</p>
        )}
      </div>
    </div>
  );
}
