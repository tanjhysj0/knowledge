/**
 * #61：输入区子组件——textarea 自适应高度、Enter 发送 / Shift+Enter
 * 换行、发送按钮与上下文指示。data-testid 与样式类与拆分前完全一致。
 */
import { useEffect, useRef } from 'react';
import type { KeyboardEvent } from 'react';

interface InputAreaProps {
  input: string;
  onChange: (value: string) => void;
  onSend: () => void;
  isLoading: boolean;
  activeConvId: number | null;
  contextLabel: string | null;
}

export default function InputArea({
  input,
  onChange,
  onSend,
  isLoading,
  activeConvId,
  contextLabel,
}: InputAreaProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px';
    }
  }, [input]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div className="input-area">
      {contextLabel && (
        <div className="context-indicator" data-testid="context-indicator">
          <span className="context-label">{contextLabel}</span>
        </div>
      )}

      <div className="input-row">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            activeConvId === null
              ? '从首页选择一本小说开始讨论'
              : '输入问题... (Enter 发送，Shift+Enter 换行)'
          }
          rows={1}
          disabled={isLoading || activeConvId === null}
        />
        <button
          onClick={onSend}
          disabled={isLoading || !input.trim() || activeConvId === null}
        >
          {isLoading ? '发送中...' : '发送'}
        </button>
      </div>
    </div>
  );
}
