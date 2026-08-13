/**
 * 聊天域共享纯函数（#56）：把聊天页内多处重复的解析逻辑收敛到一处，
 * 供流式处理与消息渲染共用。
 */

/** 把 ``doc_<id>`` 解析为 ``<id>`` 整数（无效 token 返回 null）。 */
export function parseDocId(token: string): number | null {
  if (!token.startsWith('doc_')) return null;
  const id = Number(token.slice(4));
  return Number.isInteger(id) && id > 0 ? id : null;
}

/**
 * 把 SSE done 事件的 sources 载荷解析为去重后的字符串列表：
 * 过滤非字符串项、按后端首次出现顺序去重（#33）。
 */
export function parseSources(payload: { sources?: string[] } | null): string[] {
  if (!Array.isArray(payload?.sources)) return [];
  return Array.from(
    new Set(payload!.sources.filter((s): s is string => typeof s === 'string'))
  );
}
