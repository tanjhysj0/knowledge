/**
 * #53：/admin 管理端。
 *
 * 独立布局（不套用全局 Layout）：左侧菜单两项——小说管理与 LLM 设置。
 * 小说管理以「小说」为单元，3 个主要字段：小说名（必填）、封面（可选，
 * 无封面显示默认封面图）、文本文件（必填）。编辑仅支持改名/换封面，
 * 正文不可换。LLM 设置整体复用 SettingsPage。
 */
import { useState, useRef, useEffect, useCallback } from 'react';
import { documentApi } from '../services/api';
import type { Document, UploadProgress } from '../types';
import DefaultCover from '../components/DefaultCover';
import SettingsPage from './SettingsPage';

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

/** 展示用小说名：优先 title，缺省回退文件名去扩展名。 */
function getDisplayTitle(doc: Document): string {
  return doc.title || doc.filename.replace(/\.[^.]+$/, '');
}

function getCoverFileName(path: string): string {
  return path.split('/').pop() ?? path;
}

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

/** 表单封面预览：新选封面 > 当前封面 > 默认封面图。 */
function FormCoverPreview({
  previewUrl,
  currentPath,
}: {
  previewUrl: string;
  currentPath?: string | null;
}) {
  if (previewUrl) {
    return <img className="novel-form-cover-img" src={previewUrl} alt="封面预览" />;
  }
  if (currentPath) {
    return (
      <img
        className="novel-form-cover-img"
        src={`/api/covers/${getCoverFileName(currentPath)}`}
        alt="当前封面"
      />
    );
  }
  return <DefaultCover className="novel-form-cover-img" />;
}

interface NovelFormProps {
  editingDoc: Document | null;
  onCancelEdit: () => void;
  onSaved: () => void;
}

/** 新建/编辑小说表单：小说名（必填）、封面（可选）、文本文件（必填）。 */
function NovelForm({ editingDoc, onCancelEdit, onSaved }: NovelFormProps) {
  const [title, setTitle] = useState('');
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [coverPreviewUrl, setCoverPreviewUrl] = useState('');
  const [textFile, setTextFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const coverInputRef = useRef<HTMLInputElement>(null);

  // 进入/退出编辑模式时同步表单；提交成功后清空（新建态）。
  useEffect(() => {
    setTitle(editingDoc ? getDisplayTitle(editingDoc) : '');
    setCoverFile(null);
    setCoverPreviewUrl('');
    setTextFile(null);
    setError(null);
  }, [editingDoc]);

  // 释放已失效的 objectURL。
  useEffect(() => {
    return () => {
      if (coverPreviewUrl) URL.revokeObjectURL(coverPreviewUrl);
    };
  }, [coverPreviewUrl]);

  const handleCoverSelect = (file: File) => {
    const ext = getFileExtension(file.name);
    if (!ALLOWED_COVER_TYPES.includes(ext)) {
      setError(`不支持的封面格式: .${ext}，仅支持 ${ALLOWED_COVER_TYPES.join(', ')}`);
      return;
    }
    setError(null);
    if (coverPreviewUrl) URL.revokeObjectURL(coverPreviewUrl);
    setCoverFile(file);
    setCoverPreviewUrl(URL.createObjectURL(file));
  };

  const handleRemoveCover = () => {
    if (coverPreviewUrl) URL.revokeObjectURL(coverPreviewUrl);
    setCoverFile(null);
    setCoverPreviewUrl('');
  };

  const handleSubmit = async () => {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      setError('请输入小说名');
      return;
    }
    if (!editingDoc && !textFile) {
      setError('请选择小说文本文件');
      return;
    }

    setError(null);
    setIsSubmitting(true);
    setUploadProgress(null);
    try {
      if (editingDoc) {
        await documentApi.update(editingDoc.id, {
          title: trimmedTitle,
          cover: coverFile,
        });
      } else {
        await documentApi.upload(textFile!, coverFile, trimmedTitle, (progress) => {
          setUploadProgress(progress);
        });
      }
      onSaved();
    } catch (err: any) {
      setError(err.response?.data?.detail || (editingDoc ? '保存失败' : '上传失败'));
      console.error('Submit failed:', err);
    } finally {
      setIsSubmitting(false);
      setUploadProgress(null);
    }
  };

  return (
    <div className="novel-form">
      <div className="novel-form-row">
        <label htmlFor="novel-title">
          小说名 <span className="novel-form-required">*</span>
        </label>
        <input
          id="novel-title"
          type="text"
          data-testid="novel-title-input"
          placeholder="如：十日终焉"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={isSubmitting}
        />
      </div>

      <div className="novel-form-row">
        <span className="novel-form-label">封面</span>
        <div className="novel-form-cover">
          <div className="novel-form-cover-preview" data-testid="novel-form-cover-preview">
            <FormCoverPreview
              previewUrl={coverPreviewUrl}
              currentPath={editingDoc?.cover_image_path}
            />
          </div>
          <div className="novel-form-cover-actions">
            <input
              ref={coverInputRef}
              type="file"
              data-testid="admin-cover-file-input"
              className="novel-form-file-input"
              accept=".jpg,.jpeg,.png,.webp"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleCoverSelect(file);
                e.target.value = '';
              }}
              hidden
            />
            <button
              type="button"
              className="pagination-btn"
              disabled={isSubmitting}
              onClick={() => coverInputRef.current?.click()}
            >
              {coverFile ? '更换封面' : '选择封面'}
            </button>
            {coverFile && (
              <button
                type="button"
                className="pagination-btn"
                disabled={isSubmitting}
                onClick={handleRemoveCover}
              >
                移除
              </button>
            )}
            <small>jpg / png / webp，可选，无封面显示默认封面图</small>
          </div>
        </div>
      </div>

      {!editingDoc && (
        <div className="novel-form-row">
          <label htmlFor="novel-text">
            文本文件 <span className="novel-form-required">*</span>
          </label>
          <div className="novel-form-file">
            <input
              id="novel-text"
              type="file"
              data-testid="novel-text-file-input"
              accept=".txt,.md,.pdf,.docx"
              disabled={isSubmitting}
              onChange={(e) => {
                const file = e.target.files?.[0];
                setTextFile(file ?? null);
              }}
            />
            {textFile && <small>已选择：{textFile.name}</small>}
            <small>支持 {ALLOWED_TYPES.join(', ').toUpperCase()} 格式</small>
          </div>
        </div>
      )}

      {error && (
        <div className="error-message" onClick={() => setError(null)}>
          {error}
        </div>
      )}

      {isSubmitting && uploadProgress && (
        <div className="upload-progress">
          <div className="upload-progress-info">
            <span>上传中</span>
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

      <div className="novel-form-actions">
        <button
          type="button"
          className="novel-submit-btn"
          data-testid="novel-submit-btn"
          disabled={isSubmitting}
          onClick={handleSubmit}
        >
          {isSubmitting ? '提交中...' : editingDoc ? '保存修改' : '上传小说'}
        </button>
        {editingDoc && (
          <button
            type="button"
            className="pagination-btn"
            disabled={isSubmitting}
            onClick={onCancelEdit}
          >
            取消编辑
          </button>
        )}
      </div>
    </div>
  );
}

interface NovelListProps {
  onEdit: (doc: Document) => void;
}

/** 小说分页列表：封面缩略图 | 小说名 | 大小 | 上传时间 | 编辑/删除。 */
function NovelList({ onEdit }: NovelListProps) {
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

interface NovelManagerProps {
  editingDoc: Document | null;
  listVersion: number;
  onStartEdit: (doc: Document) => void;
  onSaved: () => void;
  onCancelEdit: () => void;
}

/** 小说管理面板：表单 + 列表。 */
function NovelManager({
  editingDoc,
  listVersion,
  onStartEdit,
  onSaved,
  onCancelEdit,
}: NovelManagerProps) {
  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">
        {editingDoc ? '编辑小说' : '新建小说'}
      </h1>
      <NovelForm editingDoc={editingDoc} onCancelEdit={onCancelEdit} onSaved={onSaved} />
      <h2 className="admin-list-title">小说列表</h2>
      <NovelList key={listVersion} onEdit={onStartEdit} />
    </div>
  );
}

type AdminTab = 'novels' | 'llm';

export default function AdminPage() {
  const [tab, setTab] = useState<AdminTab>('novels');
  const [editingDoc, setEditingDoc] = useState<Document | null>(null);
  const [listVersion, setListVersion] = useState(0);

  const handleSaved = () => {
    setEditingDoc(null);
    setListVersion((v) => v + 1);
  };

  return (
    <div className="admin-layout">
      <aside className="admin-sidebar">
        <div className="admin-brand">DocQA 管理端</div>
        <nav className="admin-menu">
          <button
            className={`admin-menu-item ${tab === 'novels' ? 'active' : ''}`}
            onClick={() => setTab('novels')}
          >
            小说管理
          </button>
          <button
            className={`admin-menu-item ${tab === 'llm' ? 'active' : ''}`}
            onClick={() => setTab('llm')}
          >
            LLM 设置
          </button>
        </nav>
      </aside>
      <main className="admin-content">
        {tab === 'novels' ? (
          <NovelManager
            editingDoc={editingDoc}
            listVersion={listVersion}
            onStartEdit={setEditingDoc}
            onSaved={handleSaved}
            onCancelEdit={() => setEditingDoc(null)}
          />
        ) : (
          <SettingsPage />
        )}
      </main>
    </div>
  );
}
