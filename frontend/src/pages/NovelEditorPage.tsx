/**
 * 小说编辑页：新建与编辑共用同一页面组件。
 *
 * 路由驱动模式：/admin/novels/new 为新建，/admin/novels/:id 为编辑。
 * 编辑模式按 id 从后端拉取详情预填（刷新页面数据不丢），404 时提示
 * 并给出返回列表入口。保存成功后返回 /admin 列表页。
 */
import { useState, useRef, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { documentApi } from '../services/api';
import type { Document, UploadProgress } from '../types';
import CoverImage from '../components/CoverImage';
import { getDisplayTitle, getFileExtension } from '../utils/format';

const ALLOWED_TYPES = ['txt', 'md', 'pdf', 'docx'];
const ALLOWED_COVER_TYPES = ['jpg', 'jpeg', 'png', 'webp'];

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
  return (
    <CoverImage
      path={currentPath}
      imgClassName="novel-form-cover-img"
      defaultClassName="novel-form-cover-img"
      alt="当前封面"
    />
  );
}

export default function NovelEditorPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isEditing = id !== undefined;
  const editingId = isEditing ? Number(id) : null;
  const invalidId = isEditing && (editingId === null || Number.isNaN(editingId));

  const [doc, setDoc] = useState<Document | null>(null);
  const [title, setTitle] = useState('');
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [coverPreviewUrl, setCoverPreviewUrl] = useState('');
  const [textFile, setTextFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [loading, setLoading] = useState(isEditing);
  const [error, setError] = useState<string | null>(null);
  const coverInputRef = useRef<HTMLInputElement>(null);

  // 编辑模式：按 id 拉取详情预填，刷新页面数据不丢。
  useEffect(() => {
    if (!isEditing || editingId === null) return;
    if (invalidId) {
      setError('小说不存在或已被删除');
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    // 切换编辑目标（如浏览器前进/后退直接改 :id）时清空上次表单残留，
    // 避免把上一本书所选封面提交到当前书。
    setDoc(null);
    setTitle('');
    setCoverFile(null);
    setCoverPreviewUrl('');
    setTextFile(null);
    setUploadProgress(null);
    (async () => {
      try {
        const fetched = await documentApi.get(editingId);
        if (cancelled) return;
        setDoc(fetched);
        setTitle(getDisplayTitle(fetched));
        setError(null);
      } catch (err: any) {
        if (cancelled) return;
        setError(err.response?.status === 404 ? '小说不存在或已被删除' : '加载小说信息失败');
        console.error('Failed to load document:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isEditing, editingId, invalidId]);

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
    if (!isEditing && !textFile) {
      setError('请选择小说文本文件');
      return;
    }

    setError(null);
    setIsSubmitting(true);
    setUploadProgress(null);
    try {
      if (isEditing && editingId !== null) {
        await documentApi.update(editingId, {
          title: trimmedTitle,
          cover: coverFile,
        });
      } else {
        await documentApi.upload(textFile!, coverFile, trimmedTitle, (progress) => {
          setUploadProgress(progress);
        });
      }
      navigate('/admin');
    } catch (err: any) {
      setError(err.response?.data?.detail || (isEditing ? '保存失败' : '上传失败'));
      console.error('Submit failed:', err);
    } finally {
      setIsSubmitting(false);
      setUploadProgress(null);
    }
  };

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">{isEditing ? '编辑小说' : '新建小说'}</h1>

      {loading ? (
        <div className="empty-state">加载中...</div>
      ) : (
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
                  currentPath={doc?.cover_image_path}
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

          {!isEditing && (
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
              {isSubmitting ? '提交中...' : isEditing ? '保存修改' : '上传小说'}
            </button>
            <button
              type="button"
              className="pagination-btn"
              data-testid="novel-cancel-btn"
              disabled={isSubmitting}
              onClick={() => navigate('/admin')}
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
