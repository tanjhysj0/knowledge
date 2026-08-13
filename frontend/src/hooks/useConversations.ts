/**
 * #59：会话域逻辑收敛为 hook——会话列表加载、聚焦新建、删除、切换、
 * 消息历史加载与 StrictMode 双执行守卫全部内聚于此，页面只做编排。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  Dispatch,
  MutableRefObject,
  SetStateAction,
} from 'react';
import { conversationApi, documentApi } from '../services/api';
import type { ChatMessage as ApiChatMessage, Conversation } from '../types';
import { getDisplayTitle } from '../utils/format';

/** 聊天页内的消息视图模型（#59 随会话域一并迁移）。 */
export interface ChatMessage {
  id: number;
  role: string;
  content: string;
  thinking?: string;
  /** RAG 检索命中的文档来源列表（#33），如 ``["doc_1", "doc_3"]``。空数组 / undefined = 未命中。 */
  sources?: string[];
  /** 发送该消息时选中的文档（后端逗号分隔字符串）；仅 user 消息用于恢复会话文档上下文。 */
  documentIds?: string | null;
}

interface UseConversationsParams {
  /** #51 路由聚焦的小说 id；null = 无参访问，激活会话列表首条。 */
  focusedDocId: number | null;
  /** 共享 AbortController ref：切换会话时取消 in-flight SSE 流（流 hook 写入）。 */
  abortRef: MutableRefObject<AbortController | null>;
  /** 切换会话时同步复位流式 loading（#60 归流 hook 所有，此处仅复位）。 */
  setIsLoading: Dispatch<SetStateAction<boolean>>;
  /** 会话域错误提示（删除失败 / 聚焦新建失败）。 */
  setError: Dispatch<SetStateAction<string | null>>;
}

export function useConversations({
  focusedDocId,
  abortRef,
  setIsLoading,
  setError,
}: UseConversationsParams) {
  // StrictMode 下挂载 effect 会执行两次：聚焦建会话路径用 ref 防止
  // 重复创建（两次 list 都在首条 create 落地前发出，都会看到空列表）。
  const focusedConvCreatedRef = useRef(false);
  // 会话（#35）：左侧栏列表 + 当前激活 id。会话只能由首页小说卡片进入
  // 时创建（#52 绑定小说），无会话时不自动创建。
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<number | null>(null);
  // 防止快速连续点击同一会话的删除 / 切换按钮。
  const [sidebarBusy, setSidebarBusy] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [thinkingOpen, setThinkingOpen] = useState<Record<number, boolean>>({});

  /** #51/#52：按小说 id 新建绑定会话——标题默认取小说名；取不到时回退后端默认。
   *
   * 后端 POST 带 ``document_id`` 时按 (client_id, document_id) 幂等：
   * 该客户端下已有绑定会话则直接返回既有（本方法调用前已先行查列表，
   * 此幂等主要防多个标签页竞态）。
   */
  const createFocusedConversation = async (docId: number) => {
    let title: string | undefined;
    try {
      const doc = await documentApi.get(docId);
      title = getDisplayTitle(doc);
    } catch (err) {
      console.warn('获取小说信息失败，会话标题回退默认:', err);
    }
    return conversationApi.create(
      title ? { title, document_id: docId } : { document_id: docId }
    );
  };

  // #51 聚焦参数只在挂载时确定一次——首页卡片跳转 /chat?doc=<id>
  // 必然重新挂载，无需响应同路由参数变化。
  useEffect(() => {
    conversationApi
      .list()
      .then(async (list) => {
        if (focusedDocId !== null) {
          // #51 路由聚焦 + #52 会话绑定：先找该小说在本地已有绑定会话，
          // 有则直接激活恢复历史（重复点击同一卡片不另开新会话）；
          // 没有才新建并绑定。ref 防止 StrictMode 双执行重复创建。
          if (focusedConvCreatedRef.current) return;
          focusedConvCreatedRef.current = true;
          const bound = list.find((c) => c.document_id === focusedDocId) ?? null;
          if (bound) {
            setConversations(list);
            setActiveConvId(bound.id);
            return;
          }
          try {
            const created = await createFocusedConversation(focusedDocId);
            setConversations([created, ...list]);
            setActiveConvId(created.id);
            setMessages([]);
          } catch (err) {
            // 创建失败时重置守卫，允许 StrictMode 第二次执行重试
            focusedConvCreatedRef.current = false;
            setError('新建会话失败，请稍后重试');
            console.error('聚焦新建会话失败:', err);
          }
        } else if (list.length > 0) {
          setConversations(list);
          setActiveConvId(list[0].id);
        }
      })
      .catch((err) => {
        console.error('加载会话列表失败:', err);
      });
  }, []);

  // 激活会话变化时：拉取该会话的消息历史（#35）。
  useEffect(() => {
    if (activeConvId === null) return;
    let cancelled = false;
    conversationApi
      .messages(activeConvId)
      .then((items: ApiChatMessage[]) => {
        if (cancelled) return;
        setMessages(
          items.map((m) => ({ id: m.id, role: m.role, content: m.content }))
        );
        setThinkingOpen({});
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('加载会话消息失败:', err);
      });
    return () => {
      cancelled = true;
    };
  }, [activeConvId]);

  /** 删除一个会话：若删的是激活会话则切到下一条或置空。 */
  const handleDeleteConversation = useCallback(
    async (id: number) => {
      if (sidebarBusy) return;
      if (!confirm('确认删除该会话及其全部消息？')) return;
      setSidebarBusy(true);
      try {
        await conversationApi.remove(id);
        const remaining = conversations.filter((c) => c.id !== id);
        setConversations(remaining);
        if (activeConvId === id) {
          // 删完后空了不再自动建会话（会话只能从首页小说卡片创建）
          setActiveConvId(remaining.length > 0 ? remaining[0].id : null);
          setMessages([]);
          setThinkingOpen({});
        }
      } catch (err) {
        console.error('删除会话失败:', err);
        setError('删除会话失败，请稍后重试');
      } finally {
        setSidebarBusy(false);
      }
    },
    [sidebarBusy, conversations, activeConvId, setError]
  );

  /** 切换会话：取消 in-flight 流 → 清空消息 → 触发 effect 拉取新历史。 */
  const handleSwitchConversation = useCallback(
    (id: number) => {
      if (sidebarBusy) return;
      if (id === activeConvId) return;
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      setIsLoading(false);
      setActiveConvId(id);
      setMessages([]);
      setThinkingOpen({});
      setError(null);
    },
    [sidebarBusy, activeConvId, abortRef, setIsLoading, setError]
  );

  /** 思考过程 details 展开 / 折叠（逐消息独立记忆）。 */
  const handleToggleThinking = useCallback((id: number, open: boolean) => {
    setThinkingOpen((prev) => ({ ...prev, [id]: open }));
  }, []);

  return {
    conversations,
    activeConvId,
    sidebarBusy,
    messages,
    setMessages,
    thinkingOpen,
    handleDeleteConversation,
    handleSwitchConversation,
    handleToggleThinking,
  };
}
