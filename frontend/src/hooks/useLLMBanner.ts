/**
 * #61：LLM 可用性 preflight 与异常 banner 状态收敛为 hook——
 * 进入聊天页拉取状态、未配置显示带"去设置"的 banner、用户 dismiss、
 * 以及运行时失败展示（showBanner 作为 #60 useChatStream 的注入回调）。
 */
import { useCallback, useEffect, useState } from 'react';
import { llmStatusApi } from '../services/api';

/** #45 聊天页输入区上方的 LLM 异常 banner。null = 正常无 banner。 */
export interface LLMBannerState {
  message: string;
  showSettingsLink: boolean;
}

export function useLLMBanner() {
  // #45：preflight 检出的 LLM 状态 + 用户主动 dismiss 标志。
  const [llmBanner, setLlmBanner] = useState<LLMBannerState | null>(null);
  const [llmBannerDismissed, setLlmBannerDismissed] = useState(false);

  /** 运行时 LLM 失败（#60 注入回调）：显示 banner 并重置 dismiss。 */
  const showBanner = useCallback(
    (message: string, showSettingsLink: boolean) => {
      setLlmBanner({ message, showSettingsLink });
      setLlmBannerDismissed(false);
    },
    []
  );

  const dismissBanner = useCallback(() => {
    setLlmBannerDismissed(true);
  }, []);

  // #45：进入聊天页时拉取 LLM 可用性；未配置立刻显示红字 banner（带"去设置"链接）。
  useEffect(() => {
    let cancelled = false;
    llmStatusApi
      .get()
      .then((status) => {
        if (cancelled) return;
        if (!status.configured) {
          setLlmBanner({ message: status.reason, showSettingsLink: true });
          setLlmBannerDismissed(false);
        }
      })
      .catch((err) => {
        // 拉取状态失败时不强阻塞对话；用户在 send 时仍会被后端拒绝。
        console.warn('LLM 状态查询失败：', err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { llmBanner, llmBannerDismissed, showBanner, dismissBanner };
}
