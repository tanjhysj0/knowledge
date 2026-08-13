import type { Document } from '../types';

/** 文件大小 → 人类可读字符串（B / KB / MB / GB）。 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

/** ISO 日期 → 本地化的中文日期时间字符串。 */
export function formatDate(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** 文件名 → 小写扩展名（不含点）。 */
export function getFileExtension(filename: string): string {
  return filename.split('.').pop()?.toLowerCase() || '';
}

/** 展示用小说名：优先 title，缺省回退文件名去扩展名。 */
export function getDisplayTitle(doc: Document): string {
  return doc.title || doc.filename.replace(/\.[^.]+$/, '');
}

/** 封面存储路径 → 文件名（用于拼静态资源 URL）。 */
export function getCoverFileName(path: string): string {
  return path.split('/').pop() ?? path;
}
