import { useState, useRef, useEffect, useCallback } from 'react';
import { documentApi } from '../services/api';
import type { Document, UploadProgress } from '../types';

const ALLOWED_TYPES = ['txt', 'md', 'pdf', 'docx'];
const PAGE_SIZE = 10;

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDate(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getFileExtension(filename: string): string {
  return filename.split('.').pop()?.toLowerCase() || '';
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [uploadFileName, setUploadFileName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchDocuments = useCallback(async (page: number) => {
    try {
      setError(null);
      const response = await documentApi.list(page, PAGE_SIZE);
      setDocuments(response.items);
      setTotal(response.total);
      setTotalPages(response.total_pages);
      setCurrentPage(response.page);
    } catch (err) {
      setError('加载文档列表失败');
      console.error('Failed to load documents:', err);
    }
  }, []);

  useEffect(() => {
    fetchDocuments(1);
  }, [fetchDocuments]);

  const validateFile = (file: File): string | null => {
    const ext = getFileExtension(file.name);
    if (!ALLOWED_TYPES.includes(ext)) {
      return `不支持的文件格式: .${ext}，仅支持 ${ALLOWED_TYPES.join(', ')}`;
    }
    return null;
  };

  const handleFileUpload = async (file: File) => {
    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }

    setError(null);
    setIsUploading(true);
    setUploadFileName(file.name);
    setUploadProgress(null);

    try {
      const doc = await documentApi.upload(file, (progress) => {
        setUploadProgress(progress);
      });
      setDocuments((prev) => [doc, ...prev]);
      setTotal((prev) => prev + 1);
      // Refresh to correct page
      const newTotalPages = Math.ceil((total + 1) / PAGE_SIZE);
      setTotalPages(newTotalPages);
      if (currentPage > newTotalPages) {
        setCurrentPage(newTotalPages);
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || '上传失败');
      console.error('Upload failed:', err);
    } finally {
      setIsUploading(false);
      setUploadProgress(null);
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
      setDocuments((prev) => prev.filter((d) => d.id !== id));
      setTotal((prev) => prev - 1);
      // Adjust pagination if needed
      if (documents.length === 1 && currentPage > 1) {
        setCurrentPage((p) => p - 1);
        fetchDocuments(currentPage - 1);
      } else {
        fetchDocuments(currentPage);
      }
    } catch (err) {
      setError('删除失败');
      console.error('Delete failed:', err);
    }
  };

  const handlePageChange = (page: number) => {
    if (page < 1 || page > totalPages) return;
    fetchDocuments(page);
  };

  return (
    <div className="p-6">
      <h2 className="text-2xl font-bold mb-4">文档管理</h2>

      {error && (
        <div className="error-message" onClick={() => setError(null)}>
          {error}
        </div>
      )}

      <div
        className={`upload-zone ${isDragging ? 'dragging' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        onClick={() => !isUploading && fileInputRef.current?.click()}
      >
        <p>{isUploading ? '上传中...' : '拖拽或点击上传文件'}</p>
        <small>支持 {ALLOWED_TYPES.join(', ').toUpperCase()} 格式</small>
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

      {isUploading && uploadProgress && (
        <div className="upload-progress">
          <div className="upload-progress-info">
            <span>{uploadFileName}</span>
            <span>{uploadProgress.percentage}%</span>
          </div>
          <div className="upload-progress-bar">
            <div
              className="upload-progress-fill"
              style={{ width: `${uploadProgress.percentage}%` }}
            />
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow" style={{ marginTop: '16px' }}>
        {documents.length > 0 ? (
          <>
            {documents.map((doc) => (
              <div key={doc.id} className="doc-item">
                <div className="doc-item-info">
                  <div className="doc-item-name">{doc.filename}</div>
                  <div className="doc-item-meta">
                    <span>{formatFileSize(doc.size)}</span>
                    <span>{formatDate(doc.created_at)}</span>
                    <span>.{doc.file_type.toUpperCase()}</span>
                  </div>
                </div>
                <div className="doc-item-actions">
                  <button
                    onClick={() => handleDelete(doc.id)}
                    className="pagination-btn"
                    style={{ color: '#f44336', borderColor: '#f44336' }}
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
            {totalPages > 1 && (
              <div className="pagination">
                <button
                  className="pagination-btn"
                  onClick={() => handlePageChange(currentPage - 1)}
                  disabled={currentPage <= 1}
                >
                  上一页
                </button>
                <span className="pagination-info">
                  第 {currentPage} / {totalPages} 页，共 {total} 个文档
                </span>
                <button
                  className="pagination-btn"
                  onClick={() => handlePageChange(currentPage + 1)}
                  disabled={currentPage >= totalPages}
                >
                  下一页
                </button>
              </div>
            )}
          </>
        ) : (
          <div className="empty-state">暂无文档，请上传文件</div>
        )}
      </div>
    </div>
  );
}
