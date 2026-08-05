# ADR-0004: 升级 anthropic SDK 修复 httpx 0.28 兼容性问题

## 状态
已批准（#26）

## 背景
`make test` 跑后端单元测试时，`tests/test_llm.py` 中 12 个测试在 setup 阶段抛 `TypeError: AsyncClient.__init__() got an unexpected keyword argument 'proxies'`，全部 ERROR。

环境：
- `anthropic==0.26.0`
- `httpx==0.28.1`

httpx 0.28 移除了 `proxies` kwarg（改用 `proxy` / `transport`），但 anthropic 0.26 仍向 httpx 透传 `proxies=None`，导致 `AsyncAnthropic(...)` 初始化失败。

更换依赖的同时，发现 `tests/test_llm.py::TestResetProviders` 有 2 个 FAIL 一直被上面的 12 个 ERROR 遮蔽：
- `reset_providers()` 的实现只调用 `rebuild()` 重建 client，并不清空 singleton
- `RAGService` 在 `__init__` 缓存 `self._llm = LLMService()`，settings 切换后旧 client 仍被持有，但测试期望 reset 后立即拿到新实例

## 决策

### 1. 升级 anthropic 依赖
`requirements.txt`：`anthropic==0.26.0` → `anthropic>=0.39.0`，配套注释解释原因。

选择升级而非降级 httpx 的原因：
- httpx 0.28 是 pyproject 中其它依赖的传导版本，pin `<0.28` 会形成解析冲突
- anthropic 0.39+ 长期维护，安全性与稳定性更好

### 2. `reset_providers()` 真正清空 singleton
`app/services/llm.py` 移除 `rebuild()` 方法，`reset_providers()` 直接将 `_instance` 置为 `None`：

```python
def reset_providers():
    """Clear LLM provider singletons so the next access rebuilds them with current settings."""
    OpenAIProvider._instance = None
    AnthropicProvider._instance = None
```

`__init__` 增加幂等保护：

```python
def __init__(self):
    if getattr(self, "_initialized", False):
        return
    self._initialized = True
    ...
```

### 3. `RAGService` 改为按调用解析 LLM
`app/services/rag.py` 移除 `self._llm = LLMService()` 缓存，改为方法 `_llm()` 返回 `get_llm_provider()`：

```python
def _llm(self):
    """Resolve the current LLM provider on each call so settings changes take effect."""
    return get_llm_provider()
```

`answer()` / `answer_stream()` 改为 `await self._llm().chat(...)` / `async for chunk in self._llm().stream_chat(...)`。这样 settings 切换后下一次请求总会拿到新实例，避免旧 client 残留。

## 后果

**正面**
- 12 个 Anthropic ERROR 全部恢复为 PASSED
- 2 个 reset_providers FAIL 恢复为 PASSED
- 全套 77 个后端单元测试稳定通过
- `reset_providers` 语义与测试预期一致（"clears singletons"）
- settings 切换的真实端到端行为被测试覆盖

**遗留**
- `make test` 仍包含 4 个 E2E flake（`chat.spec.ts:346`、`api.spec.ts:53`、`documents.spec.ts:54`、`chat.spec.ts:159`），均为已有的测试隔离 / 流式响应时序问题，与本次 LLM 依赖变更无关，需另开 issue 跟进
