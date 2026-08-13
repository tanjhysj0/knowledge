/**
 * #60：流式问答发送流程收敛为 hook——用户消息追加、按会话绑定算文档、
 * AbortController 登记、SSE 循环解析（含 parser.end() 收尾）与三类失败
 * 回滚全部内聚于此，页面只做编排。
 *
 * isLoading / error 状态由页面编排层持有并注入（#59 的 useConversations
 * 同样需要这两个 setter，状态放在任一侧都会形成 hook 间循环依赖）。
 */
import { useState } from 'react';
import type {
  Dispatch,
  MutableRefObject,
  SetStateAction,
} from 'react';
import { chatApi, LLMUnavailableError } from '../services/api';
import type { Conversation } from '../types';
import { SSEParser } from '../utils/sseParser';
import { parseSources } from '../utils/chat';
import type { ChatMessage } from './useConversations';

interface UseChatStreamParams {
  activeConvId: number | null;
  conversations: Conversation[];
  /** 共享 AbortController ref：登记本轮请求；切换会话时由会话 hook 取消。 */
  abortRef: MutableRefObject<AbortController | null>;
  /** 追加用户消息 / 流式更新助手消息 / 回滚（消息状态由会话 hook 拥有）。 */
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  /** 流式 loading 状态（页面编排层持有，发送与切换会话共用）。 */
  isLoading: boolean;
  setIsLoading: Dispatch<SetStateAction<boolean>>;
  /** 发送失败错误 setter（页面编排层持有，发送与会话操作共用）。 */
  setError: Dispatch<SetStateAction<string | null>>;
  /** LLM 不可用时显示 banner（#61 由 useLLMBanner 提供实现，此处注入回调）。 */
  showLLMBanner: (message: string, showSettingsLink: boolean) => void;
}

export function useChatStream({
  activeConvId,
  conversations,
  abortRef,
  setMessages,
  isLoading,
  setIsLoading,
  setError,
  showLLMBanner,
}: UseChatStreamParams) {
  const [input, setInput] = useState('');

  const send = async () => {
    if (!input.trim() || isLoading) return;
    if (activeConvId === null) return;

    const userMessage = { id: Date.now(), role: 'user', content: input };
    setMessages((prev) => [...prev, userMessage]);
    const sentText = input;
    setInput('');
    setError(null);
    setIsLoading(true);

    const convIdAtSend = activeConvId;
    // 会话上下文有且只有其绑定的一本小说（#52）；未绑定（存量会话）不携带文档。
    const currentConv = conversations.find((c) => c.id === convIdAtSend);
    const documentIds =
      currentConv?.document_id != null ? [currentConv.document_id] : [];

    const controller = new AbortController();
    abortRef.current = controller;
    // #45 catch 块需要能清掉本轮助手占位消息，所以提到 try 块外。
    let assistantId: number | null = null;

    try {
      // #58：流式请求统一走服务层；503 在服务层解析为 LLMUnavailableError。
      const response = await chatApi.stream(
        {
          message: sentText,
          document_ids: documentIds,
          conversation_id: convIdAtSend,
        },
        controller.signal
      );

      if (!response.ok) {
        throw new Error('请求失败');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('无法读取响应');

      const decoder = new TextDecoder();
      const parser = new SSEParser();
      // #45 赋值给外层 let，catch 块才能清掉本轮助手占位（不可用 const 重声明）。
      const newAssistantId = Date.now() + 1;
      assistantId = newAssistantId;
      setMessages((prev) => [...prev, { id: newAssistantId, role: 'assistant', content: '', sources: [] }]);
      // 累积本轮的 sources（SSE done 事件一次性下发），用 ref 避免闭包陈旧
      const sourcesRef: { current: string[] } = { current: [] };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        const events = parser.feed(text);
        // #45 先在 setMessages 回调外捕获 error 事件，throw 才能跳到外层 catch。
        const errorEvent = events.find((e) => e.event === 'error');
        if (errorEvent) {
          const errPayload = errorEvent.data as
            | { reason?: string; error?: string; content?: string }
            | null;
          const reason =
            errPayload?.reason ||
            errPayload?.error ||
            errPayload?.content?.toString() ||
            '模型返回错误';
          throw new LLMUnavailableError(reason, false);
        }
        if (events.length > 0) {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              let { content, thinking } = m;
              for (const ev of events) {
                const payload = ev.data as
                  | { content?: string; sources?: string[] }
                  | null;
                if (ev.event === 'thinking' || ev.event === 'message') {
                  const piece = payload?.content;
                  if (typeof piece !== 'string' || piece.length === 0) continue;
                  if (ev.event === 'thinking') {
                    thinking = (thinking || '') + piece;
                  } else {
                    content += piece;
                  }
                } else if (ev.event === 'done') {
                  // done 事件携带 sources（#33）；保留后端顺序（#56 收敛到 parseSources）
                  const incoming = parseSources(payload);
                  if (incoming.length > 0) {
                    sourcesRef.current = incoming;
                  }
                }
              }
              return { ...m, content, thinking, sources: sourcesRef.current };
            })
          );
        }
      }

      for (const ev of parser.end()) {
        const payload = ev.data as
          | { content?: string; sources?: string[] }
          | null;
        if (ev.event === 'thinking' || ev.event === 'message') {
          const piece = payload?.content;
          if (typeof piece !== 'string' || piece.length === 0) continue;
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              if (ev.event === 'thinking') {
                return { ...m, thinking: (m.thinking || '') + piece };
              }
              return { ...m, content: m.content + piece };
            })
          );
        } else if (ev.event === 'done') {
          const incoming = parseSources(payload);
          if (incoming.length > 0) {
            sourcesRef.current = incoming;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, sources: sourcesRef.current } : m
              )
            );
          }
        }
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        // 主动取消：忽略错误（典型场景：切换会话时）
      } else if (err instanceof LLMUnavailableError) {
        // #45 LLM 不可用：显示红字 banner 并清掉本轮的用户/助手占位消息。
        showLLMBanner(err.message, err.showSettingsLink);
        setMessages((prev) =>
          prev.filter((m) => m.id !== userMessage.id && m.id !== assistantId)
        );
      } else {
        setError(err.message || '发送消息失败');
        // Remove the user message if the request failed
        setMessages((prev) => prev.filter((m) => m.id !== userMessage.id));
      }
    } finally {
      abortRef.current = null;
      setIsLoading(false);
    }
  };

  return { input, setInput, send };
}
