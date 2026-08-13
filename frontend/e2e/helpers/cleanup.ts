/**
 * Shared cleanup utilities for E2E tests.
 * Provides fixtures to:
 * - Track documents uploaded during a test/spec and delete them on teardown
 * - Snapshot/restore settings so provider/key changes don't leak between specs
 */
import { test as base, request as apiRequest, expect } from '@playwright/test';

export const BACKEND_BASE = process.env.E2E_BACKEND_URL || 'http://localhost:8000';

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

interface LLMSettingsSnapshot {
  llm_provider: string;
  llm_api_key: string | null;
  llm_base_url: string;
  llm_model: string;
}

async function getSettingsSnapshot(): Promise<LLMSettingsSnapshot> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.get('/api/settings');
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    const llm = body.llm || {};
    return {
      llm_provider: llm.provider,
      llm_api_key: null, // never restore the key (only the masked value is readable)
      llm_base_url: llm.base_url,
      llm_model: llm.model,
    };
  } finally {
    await ctx.dispose();
  }
}

async function restoreSettings(snapshot: LLMSettingsSnapshot): Promise<void> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    await ctx.put('/api/settings', {
      data: {
        llm_provider: snapshot.llm_provider,
        llm_base_url: snapshot.llm_base_url,
        llm_model: snapshot.llm_model,
        // do NOT include llm_api_key: preserve whatever key was active
      },
    });
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
  /** Snapshot/restore LLM settings across a spec. */
  settingsGuard: {
    snapshot: LLMSettingsSnapshot;
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

  settingsGuard: async ({}, use) => {
    const snapshot = await getSettingsSnapshot();
    const restore = async () => restoreSettings(snapshot);

    await use({ snapshot, restore });

    // after the spec, restore settings
    await restore();
  },
});

export { expect };
