/**
 * 首页纯展示书架（#50）：无上传/删除等管理能力（已迁至 /admin），
 * 只保留平铺卡片网格——每张卡片 = 封面大图（竖版 2:3，无封面显示
 * 默认封面图）+ 底部居中书名（超长省略）。hover 微放大 + 阴影。
 *
 * 整卡可点击（role=button + cursor）：点击跳转 /chat?doc=<id>（#51），
 * 聊天页聚焦该小说并新建会话。
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { documentApi } from '../services/api';
import type { Document } from '../types';
import CoverImage from '../components/CoverImage';
import { getDisplayTitle } from '../utils/format';

/** 书架一次拉取的小说数量（纯展示，无分页）。 */
const SHELF_PAGE_SIZE = 1000;

/** 单张小说卡片：封面大图（2:3）+ 底部居中书名；点击开始讨论（#51）。 */
function NovelCard({ doc, onOpen }: { doc: Document; onOpen: (id: number) => void }) {
  return (
    <div
      className="novel-card"
      data-testid="novel-card"
      role="button"
      tabIndex={0}
      title={getDisplayTitle(doc)}
      onClick={() => onOpen(doc.id)}
      onKeyDown={(e) => {
        // role=button 语义：Enter / 空格键触发跳转
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen(doc.id);
        }
      }}
    >
      <div className="novel-card-cover">
        <CoverImage
          path={doc.cover_image_path}
          imgClassName="novel-card-cover-img"
          defaultClassName="novel-card-cover-default"
          alt={`${getDisplayTitle(doc)} 封面`}
        />
      </div>
      <div className="novel-card-title">{getDisplayTitle(doc)}</div>
    </div>
  );
}

export default function NovelListPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  /** #51：点击卡片跳转聊天页并携带小说标识（/chat?doc=<id>）。 */
  const openNovelChat = useCallback(
    (id: number) => navigate(`/chat?doc=${id}`),
    [navigate]
  );

  const fetchDocuments = useCallback(async () => {
    try {
      setError(null);
      const response = await documentApi.list(1, SHELF_PAGE_SIZE);
      setDocuments(response.items);
    } catch (err) {
      setError('加载小说失败');
      console.error('Failed to load documents:', err);
    }
  }, []);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  return (
    <div className="shelf-page">
      {error && <div className="error-message">{error}</div>}
      {documents.length > 0 ? (
        <div className="shelf-grid" data-testid="shelf-grid">
          {documents.map((doc) => (
            <NovelCard key={doc.id} doc={doc} onOpen={openNovelChat} />
          ))}
        </div>
      ) : (
        !error && (
          <div className="shelf-empty" data-testid="shelf-empty">
            暂无小说
          </div>
        )
      )}
    </div>
  );
}
