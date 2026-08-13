/**
 * /admin 小说列表页：分页列表 + 「新建小说」入口。
 *
 * 编辑跳转独立编辑页 /admin/novels/:id（与新建共用同一页面组件），
 * 新建跳转 /admin/novels/new。上传/删除等管理能力保留在本页。
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { documentApi } from '../services/api';
import type { Document } from '../types';
import DefaultCover from '../components/DefaultCover';
import { formatDate, formatFileSize, getCoverFileName, getDisplayTitle } from '../utils/format';

const PAGE_SIZE = 10;

/** 列表行封面缩略图：有封面显示 img，无封面显示默认封面图（#53）。 */
function CoverThumb({ doc }: { doc: Document }) {
  if (!doc.cover_image_path) {
    return <DefaultCover className="doc-item-cover" />;
  }
  return (
    <img
      className="doc-item-cover"
      src={`/api/covers/${getCoverFileName(doc.cover_image_path)}`}
      alt={`${getDisplayTitle(doc)} 封面`}
      loading="lazy"
    />
  );
}

/** 小说分页列表：封面缩略图 | 小说名 | 大小 | 上传时间 | 编辑/删除。 */
function NovelList({ onEdit }: { onEdit: (doc: Document) => void }) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const handleDelete = async (id: number) => {
    setDeletingId(id);
    try {
      await documentApi.delete(id);
      // 当前页删除后仅剩 0 条时回退一页，否则刷新当前页。
      if (documents.length === 1 && currentPage > 1) {
        fetchDocuments(currentPage - 1);
      } else {
        fetchDocuments(currentPage);
      }
    } catch (err) {
      setError('删除失败');
      console.error('Delete failed:', err);
    } finally {
      setDeletingId(null);
    }
  };

  const handlePageChange = (page: number) => {
    if (page < 1 || page > totalPages) return;
    fetchDocuments(page);
  };

  return (
    <div className="bg-white rounded-lg shadow">
      {error && (
        <div className="error-message" onClick={() => setError(null)}>
          {error}
        </div>
      )}
      {documents.length > 0 ? (
        <>
          {documents.map((doc) => (
            <div key={doc.id} className="doc-item" data-testid="novel-list-item">
              <CoverThumb doc={doc} />
              <div className="doc-item-info">
                <div className="doc-item-name">{getDisplayTitle(doc)}</div>
                <div className="doc-item-meta">
                  <span>{formatFileSize(doc.size)}</span>
                  <span>{formatDate(doc.created_at)}</span>
                  <span>.{doc.file_type.toUpperCase()}</span>
                </div>
              </div>
              <div className="doc-item-actions">
                <button
                  className="pagination-btn"
                  data-testid="novel-edit-btn"
                  onClick={() => onEdit(doc)}
                >
                  编辑
                </button>
                <button
                  className="pagination-btn"
                  style={{ color: '#f44336', borderColor: '#f44336' }}
                  disabled={deletingId === doc.id}
                  onClick={() => handleDelete(doc.id)}
                >
                  {deletingId === doc.id ? '删除中...' : '删除'}
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
        <div className="empty-state">暂无小说，请先上传</div>
      )}
    </div>
  );
}

export default function AdminNovelListPage() {
  const navigate = useNavigate();

  return (
    <div className="p-6">
      <div className="admin-list-header">
        <h1 className="text-2xl font-bold">小说管理</h1>
        <button
          className="novel-submit-btn"
          data-testid="novel-create-btn"
          onClick={() => navigate('/admin/novels/new')}
        >
          新建小说
        </button>
      </div>
      <NovelList onEdit={(doc) => navigate(`/admin/novels/${doc.id}`)} />
    </div>
  );
}
