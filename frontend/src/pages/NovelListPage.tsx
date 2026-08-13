import { useState, useRef, useEffect, useCallback } from 'react';
import { documentApi } from '../services/api';
import type { Document, UploadProgress } from '../types';

const ALLOWED_TYPES = ['txt', 'md', 'pdf', 'docx'];
const ALLOWED_COVER_TYPES = ['jpg', 'jpeg', 'png', 'webp'];
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

/** 封面缩略图：有封面显示 img，无封面显示占位 SVG（#49）。 */
function CoverThumb({ doc }: { doc: Document }) {
  if (!doc.cover_image_path) {
    return (
      <div className="doc-item-cover doc-item-cover-placeholder">
        <svg viewBox="0 0 24 32" width="24" height="32" aria-hidden="true">
          <rect width="24" height="32" rx="2" fill="#e3e8ef" />
          <text x="12" y="20" textAnchor="middle" fontSize="10" fill="#9aa4b2">
            封
          </text>
        </svg>
      </div>
    );
  }
  const coverFile = doc.cover_image_path.split('/').pop() ?? doc.cover_image_path;
  return (
    <img
      className="doc-item-cover"
      src={`/api/covers/${coverFile}`}
      alt={`${doc.filename} 封面`}
      loading="lazy"
    />
  );
}

export default function NovelListPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [isDragging, setIsDragging] = useState(false);
  const [isCoverDragging, setIsCoverDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [uploadFileName, setUploadFileName] = useState('');
  const [pendingCover, setPendingCover] = useState<File | null>(null);
  const [pendingCoverName, setPendingCoverName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const coverInputRef = useRef<HTMLInputElement>(null);

  const fetchDocuments = useCallback(async (page: number) => {
    try {
      setError(null);
      const response = await documentApi.list(page, PAGE_SIZE);
      setDocuments(response.items);
      setTotal(response.total);
      setTotalPages(response.total_pages);
      setCurrentPage(response.page);
    } catch (err) {
      setError('加载小说列表失败');
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
      const doc = await documentApi.upload(file, pendingCover, undefined, (progress) => {
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
      // 封面已随正文一起上传（#49）
      setPendingCover(null);
      setPendingCoverName('');
    } catch (err: any) {
      setError(err.response?.data?.detail || '上传失败');
      console.error('Upload failed:', err);
    } finally {
      setIsUploading(false);
      setUploadProgress(null);
    }
  };

  const handleCoverSelect = (file: File) => {
    const ext = getFileExtension(file.name);
    if (!ALLOWED_COVER_TYPES.includes(ext)) {
      setError(`不支持的封面格式: .${ext}，仅支持 ${ALLOWED_COVER_TYPES.join(', ')}`);
      return;
    }
    setError(null);
    setPendingCover(file);
    setPendingCoverName(file.name);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  };

  const handleCoverDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsCoverDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleCoverSelect(file);
  };

  const handleCoverZoneClick = () => {
    if (isUploading) return;
    if (pendingCover) {
      // 已就绪封面：点击移除
      setPendingCover(null);
      setPendingCoverName('');
      return;
    }
    coverInputRef.current?.click();
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
      <h1 className="text-2xl font-bold mb-4">我的小说库</h1>

      {error && (
        <div className="error-message" onClick={() => setError(null)}>
          {error}
        </div>
      )}

      <div className="upload-grid">
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
          <p>{isUploading ? '上传中...' : '拖拽或点击上传小说正文'}</p>
          <small>支持 {ALLOWED_TYPES.join(', ').toUpperCase()} 格式</small>
        </div>

        <div
          className={`upload-zone cover-upload-zone ${isCoverDragging ? 'dragging' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setIsCoverDragging(true);
          }}
          onDragLeave={() => setIsCoverDragging(false)}
          onDrop={handleCoverDrop}
          onClick={handleCoverZoneClick}
        >
          {pendingCover ? (
            <>
              <p className="cover-ready">封面已就绪：{pendingCoverName}</p>
              <small>点击移除，选择正文后将一起上传</small>
            </>
          ) : (
            <>
              <p>拖拽或点击上传封面（可选）</p>
              <small>支持 {ALLOWED_COVER_TYPES.join(', ').toUpperCase()} 格式</small>
            </>
          )}
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        data-testid="novel-file-input"
        className="novel-file-input"
        accept=".txt,.md,.pdf,.docx"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFileUpload(file);
          e.target.value = '';
        }}
        hidden
      />

      <input
        ref={coverInputRef}
        type="file"
        data-testid="cover-file-input"
        className="cover-file-input"
        accept=".jpg,.jpeg,.png,.webp"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleCoverSelect(file);
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
                <CoverThumb doc={doc} />
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
                  第 {currentPage} / {totalPages} 页，共 {total} 本小说
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
          <div className="empty-state">暂无小说，请上传文件</div>
        )}
      </div>
    </div>
  );
}
