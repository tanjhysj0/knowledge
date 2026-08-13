import { useState, useRef, useEffect, useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { documentApi } from '../services/api';
import type { Document } from '../types';
import { getDisplayTitle } from '../utils/format';
import { useConversations } from '../hooks/useConversations';
import { useChatStream } from '../hooks/useChatStream';
import { useLLMBanner } from '../hooks/useLLMBanner';
import ConversationSidebar from '../components/ConversationSidebar';
import MessageList from '../components/MessageList';
import InputArea from '../components/InputArea';
import '../App.css';

/** 把 ``doc`` 路由参数解析为合法小说 id（无参 / 非法返回 null）。 */
function parseFocusedDocId(param: string | null): number | null {
  if (param === null) return null;
  const id = Number(param);
  return Number.isInteger(id) && id > 0 ? id : null;
}

export default function ChatPage() {
  // #51：路由聚焦单小说（/chat?doc=<id>）。null = 无参访问，激活会话列表首条。
  const [searchParams] = useSearchParams();
  const focusedDocId = parseFocusedDocId(searchParams.get('doc'));
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 切换会话时取消 in-flight SSE 流（会话 hook 与发送流程共用）。
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // #59：会话域逻辑（列表加载 / 聚焦新建 / 删除 / 切换 / 消息历史）收敛到 hook。
  const {
    conversations,
    activeConvId,
    sidebarBusy,
    messages,
    setMessages,
    thinkingOpen,
    handleDeleteConversation,
    handleSwitchConversation,
    handleToggleThinking,
  } = useConversations({ focusedDocId, abortRef, setIsLoading, setError });

  // #61：LLM 可用性 preflight 与异常 banner 状态收敛到 hook；
  // showBanner 即 #60 useChatStream 的注入回调。
  const { llmBanner, llmBannerDismissed, showBanner, dismissBanner } = useLLMBanner();

  // #60：流式发送流程收敛到 hook。
  const { input, setInput, send } = useChatStream({
    activeConvId,
    conversations,
    abortRef,
    setMessages,
    isLoading,
    setIsLoading,
    setError,
    showLLMBanner: showBanner,
  });

  // 加载可用文档列表（#35）。#51 聚焦参数只在挂载时确定一次——
  // 首页卡片跳转 /chat?doc=<id> 必然重新挂载，无需响应同路由参数变化。
  useEffect(() => {
    documentApi.list(1, 100)
      .then((res) => {
        setDocuments(res.items);
      })
      .catch((err) => {
        console.error('加载文档列表失败:', err);
      });
  }, []);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const activeConv = conversations.find((c) => c.id === activeConvId) ?? null;
  // 上下文 = 当前会话绑定的小说（有且只有一本）；未绑定时不显示提示。
  const boundDoc =
    activeConv?.document_id != null
      ? documents.find((d) => d.id === activeConv.document_id) ?? null
      : null;
  const contextLabel = boundDoc ? `基于《${getDisplayTitle(boundDoc)}》回答` : null;

  return (
    <div className="chat-layout">
      {/* 左侧会话栏（#35，#59 抽为子组件） */}
      <ConversationSidebar
        conversations={conversations}
        activeConvId={activeConvId}
        sidebarBusy={sidebarBusy}
        onSwitch={handleSwitchConversation}
        onDelete={handleDeleteConversation}
      />

      {/* 右侧对话主体 */}
      <div className="chat-area">
        <header>
          {/* #51 顶部左侧 DocQA Logo：点击返回首页书架（#50 删导航后的返回入口） */}
          <Link
            to="/"
            className="chat-header-logo"
            data-testid="chat-logo"
            aria-label="DocQA 返回首页"
          >
            DocQA
          </Link>
          <h1>小说问答助手</h1>
        </header>

        {/* #61：消息区抽为子组件（空态 / 消息循环 / typing / 错误提示） */}
        <MessageList
          messages={messages}
          thinkingOpen={thinkingOpen}
          documents={documents}
          isLoading={isLoading}
          activeConvId={activeConvId}
          error={error}
          onDismissError={() => setError(null)}
          onToggleThinking={handleToggleThinking}
          endRef={messagesEndRef}
        />

        {/* #45 LLM 不可用 / 运行时失败的顶部红字 banner */}
        {llmBanner && !llmBannerDismissed && (
          <div className="llm-error-banner" data-testid="llm-error-banner" role="alert">
            <span className="llm-error-banner-text">{llmBanner.message}</span>
            {llmBanner.showSettingsLink && (
              <Link
                to="/settings"
                className="llm-error-banner-link"
                data-testid="llm-error-banner-link"
              >
                去设置
              </Link>
            )}
            <button
              type="button"
              className="llm-error-banner-close"
              data-testid="llm-error-banner-close"
              aria-label="关闭"
              onClick={dismissBanner}
            >
              ×
            </button>
          </div>
        )}

        {/* #61：输入区抽为子组件（自适应高度 / 快捷键 / 上下文指示） */}
        <InputArea
          input={input}
          onChange={setInput}
          onSend={send}
          isLoading={isLoading}
          activeConvId={activeConvId}
          contextLabel={contextLabel}
        />
      </div>
    </div>
  );
}
