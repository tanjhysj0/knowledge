/**
 * Playwright global setup: inject the ``X-E2E-Test`` header so every browser
 * context built during the E2E run carries the marker. The backend reads it
 * in ``app.services.llm.get_llm_provider`` and swaps in ``MockLLMProvider``,
 * so no test ever invokes the real LLM.
 *
 * #66 后续：preflight 无条件检查默认模型 api_key（chat.py 不再豁免 mock
 * 请求），因此测试开始前先确保默认模型有 key（用仓库根 ``llm.config``
 * 的真实配置，不写入 dummy 模型），否则无 key 环境（CI / 新机器）所有
 * 聊天 E2E 会被 preflight 拒绝。llm.config 缺失或后端未就绪时静默跳过——
 * 前置 project（preflight-unconfigured.spec）收尾也会配回 key。
 */
import { ensureDefaultModelKey } from './cleanup';

export default async function globalSetup(): Promise<void> {
  await ensureDefaultModelKey();
}
