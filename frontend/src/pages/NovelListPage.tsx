/**
 * 首页纯展示书架（#50）：无上传/删除等管理能力（已迁至 /admin），
 * 只保留平铺卡片网格——每张卡片 = 封面大图（竖版 2:3，无封面显示
 * 默认封面图）+ 底部居中书名（超长省略）。hover 微放大 + 阴影。
 *
 * 整卡可点击（role=button + cursor），点击链路由 #51 实现（跳转
 * /chat?doc=<id> 并聚焦单小说）。
 */
import { useState, useEffect, useCallback } from 'react';
import { documentApi } from '../services/api';
import type { Document } from '../types';
import DefaultCover from '../components/DefaultCover';
import { getCoverFileName, getDisplayTitle } from '../utils/format';

/** 书架一次拉取的小说数量（纯展示，无分页）。 */
const SHELF_PAGE_SIZE = 1000;

/** 单张小说卡片：封面大图（2:3）+ 底部居中书名。 */
function NovelCard({ doc }: { doc: Document }) {
  return (
    <div
      className="novel-card"
      data-testid="novel-card"
      role="button"
      tabIndex={0}
      title={getDisplayTitle(doc)}
    >
      <div className="novel-card-cover">
        {doc.cover_image_path ? (
          <img
            className="novel-card-cover-img"
            src={`/api/covers/${getCoverFileName(doc.cover_image_path)}`}
            alt={`${getDisplayTitle(doc)} 封面`}
            loading="lazy"
          />
        ) : (
          <DefaultCover className="novel-card-cover-default" />
        )}
      </div>
      <div className="novel-card-title">{getDisplayTitle(doc)}</div>
    </div>
  );
}

export default function NovelListPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [error, setError] = useState<string | null>(null);

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
            <NovelCard key={doc.id} doc={doc} />
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
