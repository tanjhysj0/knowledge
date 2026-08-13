/**
 * #56：共享封面组件——「有封面显示 img / 无封面显示默认封面图」分支
 * 只维护一份。首页书架卡片、管理列表缩略图、编辑表单预览三处共用。
 */
import DefaultCover from './DefaultCover';
import { getCoverUrl } from '../utils/format';

interface CoverImageProps {
  /** 后端封面相对路径（如 ``covers/123.png``）；空 / null / undefined 显示默认封面图。 */
  path?: string | null;
  /** img 分支的 className（宽高与 object-fit 由容器场景决定）。 */
  imgClassName: string;
  /** 默认封面分支的 className。 */
  defaultClassName: string;
  alt: string;
}

export default function CoverImage({
  path,
  imgClassName,
  defaultClassName,
  alt,
}: CoverImageProps) {
  if (!path) {
    return <DefaultCover className={defaultClassName} />;
  }
  return (
    <img
      className={imgClassName}
      src={getCoverUrl(path)}
      alt={alt}
      loading="lazy"
    />
  );
}
