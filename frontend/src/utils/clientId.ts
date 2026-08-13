/**
 * 会话空间客户端标识（#52）。
 *
 * 首次访问生成随机 client key 并持久化到 localStorage；此后所有会话
 * 相关请求经 axios 拦截器统一携带 ``X-Client-Id`` 头，后端据此把会话
 * 空间按浏览器（客户端）隔离——不同浏览器互不可见对方会话。
 */

const CLIENT_ID_STORAGE_KEY = 'docqa_client_id';

/** 返回当前客户端的 client key；不存在则生成并持久化。 */
export function getClientId(): string {
  let clientId = localStorage.getItem(CLIENT_ID_STORAGE_KEY);
  if (!clientId) {
    clientId = `client-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 10)}`;
    localStorage.setItem(CLIENT_ID_STORAGE_KEY, clientId);
  }
  return clientId;
}
