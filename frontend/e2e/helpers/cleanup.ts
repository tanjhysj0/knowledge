/**
 * Shared cleanup utilities for E2E tests.
 * Provides fixtures to:
 * - Track documents uploaded during a test/spec and delete them on teardown
 * - Snapshot/restore the llm_models list so provider/key changes don't leak between specs
 */
import { test as base, request as apiRequest, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';

export const BACKEND_BASE = process.env.E2E_BACKEND_URL || 'http://localhost:8000';

// #66 后续：preflight 无条件检查默认模型 api_key（chat.py 不再豁免 mock 请求），
// E2E 必须保证默认模型 key 恒非空。E2E 向 DB 提交的默认模型一律来自仓库根
// ``llm.config`` 的真实配置（不写入 e2e-model 之类的假记录）；llm.config
// 缺失/解析失败时宁可跳过也不写入 dummy 模型。
const REPO_ROOT = path.resolve(process.cwd(), '..');
const LLM_CONFIG_PATH = path.join(REPO_ROOT, 'llm.config');

interface LlmConfig {
  providerType: string;
  baseUrl: string;
  modelName: string;
  apiKey: string;
}

/**
 * 读取仓库根 ``llm.config`` 的真实 LLM 配置（api-key / base_url / model）。
 * 文件缺失或字段不完整时返回 null（调用方不得回退到 dummy 模型）。
 */
function loadLlmConfig(): LlmConfig | null {
  let raw: string;
  try {
    raw = fs.readFileSync(LLM_CONFIG_PATH, 'utf-8');
  } catch {
    console.warn(`[e2e] llm.config 不存在：${LLM_CONFIG_PATH}，跳过默认模型配置`);
    return null;
  }
  const line = (label: string): string => {
    const match = raw.match(new RegExp(`^${label}\\s*[:\\t]\\s*(.+)$`, 'm'));
    return match ? match[1].trim() : '';
  };
  const apiKey = line('api-key');
  const baseUrl = line('base_url \\(OpenAI\\)');
  const modelName = line('model \\(OpenAI\\)');
  if (!apiKey || !baseUrl || !modelName) {
    console.warn('[e2e] llm.config 字段不完整（api-key / base_url / model），跳过默认模型配置');
    return null;
  }
  return { providerType: 'openai', baseUrl, modelName, apiKey };
}

interface DocumentSummary {
  id: number;
  filename: string;
}

async function listDocuments(): Promise<DocumentSummary[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    // #63：默认列表只返回 ready 小说，track 需全量视图才能找到刚上传
    // 仍处于 pending/processing 的文档。
    const res = await ctx.get('/api/documents?page=1&page_size=100&all_statuses=true');
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    return body.items || [];
  } finally {
    await ctx.dispose();
  }
}

async function deleteDocument(id: number): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    await ctx.delete(`/api/documents/${id}`);
  } catch {
    /* ignore */
  } finally {
    await ctx.dispose();
  }
}

// #69：模型列表快照（列表 API 只回传脱敏 key，真实 api_key 不可恢复，
// 恢复时用空串占位，避免把测试 key 泄漏进快照）。
interface ModelSummary {
  id: number;
  provider_type: string;
  base_url: string;
  model_name: string;
  is_default: boolean;
  api_key_masked: string;
}

/** 默认模型 key 是否为空（列表 API 回传脱敏 key，空串 = 未配置）。 */
function defaultModelLacksKey(models: ModelSummary[]): boolean {
  const def = models.find((m) => m.is_default) ?? models[0];
  return !def || !def.api_key_masked;
}

/**
 * 确保默认模型 api_key 非空：无默认模型则创建（llm.config 真实配置），
 * key 为空则 PUT 补 llm.config 的 key。幂等；后端不可达或 llm.config
 * 不可用时静默跳过（不写入 dummy 模型）。
 */
export async function ensureDefaultModelKey(): Promise<void> {
  const llm = loadLlmConfig();
  if (!llm) return;
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.get('/api/models');
    if (!res.ok()) return;
    const models: ModelSummary[] = await res.json();
    if (models.length === 0) {
      await ctx
        .post('/api/models', {
          data: {
            provider_type: llm.providerType,
            base_url: llm.baseUrl,
            model_name: llm.modelName,
            api_key: llm.apiKey,
            is_default: true,
          },
        })
        .catch(() => {});
      return;
    }
    if (defaultModelLacksKey(models)) {
      const def = models.find((m) => m.is_default) ?? models[0];
      await ctx
        .put(`/api/models/${def.id}`, { data: { api_key: llm.apiKey } })
        .catch(() => {});
    }
  } finally {
    await ctx.dispose();
  }
}

/**
 * 清空默认模型 api_key（错误路径测试专用）：PUT 留空 = 保持原值，
 * 故删除后以空串重建。仅在 preflight-unconfigured.spec（前置 project）
 * 中调用，主套件并行期间任何测试不得清 key。
 */
export async function clearDefaultApiKey(): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.get('/api/models');
    if (!res.ok()) return;
    const models: ModelSummary[] = await res.json();
    const def = models.find((m) => m.is_default) ?? models[0];
    if (!def) return;
    await ctx.delete(`/api/models/${def.id}`).catch(() => {});
    await ctx
      .post('/api/models', {
        data: {
          provider_type: def.provider_type,
          base_url: def.base_url,
          model_name: def.model_name,
          api_key: '',
          is_default: true,
        },
      })
      .catch(() => {});
  } finally {
    await ctx.dispose();
  }
}

async function getModelsSnapshot(): Promise<ModelSummary[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.get('/api/models');
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    return (body || []).map((m: ModelSummary) => ({
      provider_type: m.provider_type,
      base_url: m.base_url,
      model_name: m.model_name,
      is_default: m.is_default,
    }));
  } finally {
    await ctx.dispose();
  }
}

async function restoreModels(snapshot: ModelSummary[]): Promise<void> {
  // 重建快照用的 key：优先 llm.config 真实 key（DB 里不留 dummy 假数据）；
  // llm.config 不可用时回退 dummy key 占位（无法恢复真实 key，且必须保持
  // 非空——preflight 无条件检查配置，空 key 会打挂并行聊天测试）。
  const llm = loadLlmConfig();
  const restoreKey = llm?.apiKey || 'e2e-test-key';
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    // 先清空当前列表（删除全部，包括测试新增的记录）。
    // 默认模型在列表中仍有其它记录时不可删（400），故先删非默认、最后删默认。
    const listRes = await ctx.get('/api/models');
    if (listRes.ok()) {
      const current = await listRes.json();
      const nonDefaultFirst = [
        ...current.filter((m: ModelSummary) => !m.is_default),
        ...current.filter((m: ModelSummary) => m.is_default),
      ];
      for (const model of nonDefaultFirst) {
        await ctx.delete(`/api/models/${model.id}`).catch(() => {});
      }
    }
    // 再按快照重建（api_key 用 llm.config 真实 key；不能恢复脱敏 key）。
    for (const model of snapshot) {
      await ctx
        .post('/api/models', {
          data: {
            provider_type: model.provider_type,
            base_url: model.base_url,
            model_name: model.model_name,
            api_key: restoreKey,
            is_default: model.is_default,
          },
        })
        .catch(() => {});
    }
  } catch {
    /* ignore */
  } finally {
    await ctx.dispose();
  }
}

interface E2EFixtures {
  /**
   * Documents uploaded during a spec are deleted automatically after the spec.
   * Use upload() inside the test to register a document for cleanup.
   */
  uploadedDocs: {
    track: (filename: string) => Promise<DocumentSummary | null>;
    cleanupAll: () => Promise<void>;
  };
  /** #69：快照/恢复 llm_models 列表，测试间不泄漏模型记录。 */
  modelsGuard: {
    snapshot: ModelSummary[];
    restore: () => Promise<void>;
  };
}

export const test = base.extend<E2EFixtures>({
  uploadedDocs: async ({}, use) => {
    const tracked: DocumentSummary[] = [];

    const track = async (filename: string): Promise<DocumentSummary | null> => {
      const docs = await listDocuments();
      const doc = docs.find((d) => d.filename === filename);
      if (doc) {
        tracked.push(doc);
      }
      return doc || null;
    };

    const cleanupAll = async (): Promise<void> => {
      // 仅删除当前测试显式追踪的文档，避免并行测试互相清理数据。
      for (const doc of tracked) {
        await deleteDocument(doc.id);
      }
      tracked.length = 0;
    };

    await use({ track, cleanupAll });

    // after the spec
    await cleanupAll();
  },

  modelsGuard: async ({}, use) => {
    const snapshot = await getModelsSnapshot();
    const restore = async () => restoreModels(snapshot);

    await use({ snapshot, restore });

    // after the spec, restore the models list
    await restore();
  },
});

export { expect };
