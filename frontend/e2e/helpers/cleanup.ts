/**
 * Shared cleanup utilities for E2E tests.
 * Provides fixtures to:
 * - Track documents uploaded during a test/spec and delete them on teardown
 * - Snapshot/restore the llm_models list so provider/key changes don't leak between specs
 */
import { test as base, request as apiRequest, expect } from '@playwright/test';

export const BACKEND_BASE = process.env.E2E_BACKEND_URL || 'http://localhost:8000';

// #66 后续：preflight 无条件检查默认模型 api_key（chat.py 不再豁免 mock 请求），
// E2E 必须保证默认模型 key 恒非空——用固定 dummy key 占位，mock 请求永远用不到它。
const E2E_DUMMY_API_KEY = 'e2e-test-key';

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
 * 确保默认模型 api_key 非空：无默认模型则创建（dummy key），
 * key 为空则 PUT 补 dummy key。幂等；后端不可达时静默跳过
 * （globalSetup 阶段后端可能尚未就绪）。
 */
export async function ensureDefaultModelKey(): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.get('/api/models');
    if (!res.ok()) return;
    const models: ModelSummary[] = await res.json();
    if (models.length === 0) {
      await ctx
        .post('/api/models', {
          data: {
            provider_type: 'openai',
            base_url: '',
            model_name: 'e2e-model',
            api_key: E2E_DUMMY_API_KEY,
            is_default: true,
          },
        })
        .catch(() => {});
      return;
    }
    if (defaultModelLacksKey(models)) {
      const def = models.find((m) => m.is_default) ?? models[0];
      await ctx
        .put(`/api/models/${def.id}`, { data: { api_key: E2E_DUMMY_API_KEY } })
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
    // 再按快照重建（api_key 用 dummy key 占位：不能也不应恢复真实 key，
    // 且必须保持非空——preflight 无条件检查配置，空 key 会打挂并行聊天测试）。
    for (const model of snapshot) {
      await ctx
        .post('/api/models', {
          data: {
            provider_type: model.provider_type,
            base_url: model.base_url,
            model_name: model.model_name,
            api_key: E2E_DUMMY_API_KEY,
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
