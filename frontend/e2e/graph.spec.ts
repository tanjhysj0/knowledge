import { expect, request as apiRequest } from '@playwright/test';
import { test as cleanupTest, BACKEND_BASE } from './helpers/cleanup';

/**
 * Issue #80: GraphRAG 图谱构建管线。
 *
 * 覆盖验收：
 * - 上传并处理文档后，图数据表出现该文档的三元组（MockLLM 下确定性可断言）；
 * - 查询接口按实体名返回一跳邻居及源文本块引用；
 * - 删除文档后其图数据被清理；
 * - 写入三元组接口（供测试与后续检索器使用）。
 *
 * 关键机制：上传请求显式携带 ``X-E2E-Test: true`` 头（Playwright 的
 * extraHTTPHeaders 只作用于浏览器 context，API request 需显式设置），
 * 后端路由层解析该头并把 ``e2e_mock`` 透传后台任务，图谱抽取因此走
 * MockLLMProvider 的固定三元组（张三-是-主角 / 李四-击败-张三）。
 */

interface DocStatus {
  id: number;
  filename: string;
  status: string;
  progress: number;
}

interface Triple {
  id: number;
  document_id: number;
  subject: string;
  relation: string;
  object: string;
  chunk_index: number;
  content: string;
}

/** 携带 X-E2E-Test 头的 API context（图抽取走 MockLLMProvider）。 */
function e2eApiContext() {
  return apiRequest.newContext({
    baseURL: BACKEND_BASE,
    extraHTTPHeaders: { 'X-E2E-Test': 'true' },
  });
}

async function uploadViaApi(filename: string, content: string): Promise<DocStatus> {
  const ctx = await e2eApiContext();
  try {
    const res = await ctx.post('/api/documents/upload', {
      multipart: {
        file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from(content) },
      },
    });
    expect(res.ok()).toBeTruthy();
    return (await res.json()) as DocStatus;
  } finally {
    await ctx.dispose();
  }
}

async function listViaApi(allStatuses: boolean): Promise<DocStatus[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const query = allStatuses
      ? '?page=1&page_size=100&all_statuses=true'
      : '?page=1&page_size=100';
    const res = await ctx.get(`/api/documents${query}`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    return (body.items || []) as DocStatus[];
  } finally {
    await ctx.dispose();
  }
}

/** 轮询全量列表直到目标文档 ready。 */
async function pollReady(filename: string, timeoutMs = 60_000): Promise<DocStatus> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const items = await listViaApi(true);
    const doc = items.find((d) => d.filename === filename);
    if (doc && doc.status === 'ready' && doc.progress === 100) return doc;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`文档 ${filename} 在 ${timeoutMs}ms 内未达到 ready`);
}

async function fetchTriples(documentId: number): Promise<Triple[]> {
  const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
  try {
    const res = await ctx.get(`/api/graph/triples?document_id=${documentId}`);
    expect(res.ok()).toBeTruthy();
    return (await res.json()) as Triple[];
  } finally {
    await ctx.dispose();
  }
}

cleanupTest.describe('GraphRAG 图谱构建 - E2E (#80)', () => {
  cleanupTest.beforeEach(async () => {
    // 后台索引含真实 embedding 推理（HTTP 服务），放宽单个用例超时。
    cleanupTest.setTimeout(90_000);
  });

  cleanupTest('上传后图数据出现固定三元组，邻居查询可回溯源文本块', async ({ uploadedDocs }) => {
    const filename = `graph-${Date.now()}.txt`;
    await uploadViaApi(filename, '张三与李四在星海相遇，张三成为主角。');
    const doc = await pollReady(filename);
    await uploadedDocs.track(filename);

    // MockLLM 抽取输出确定：张三-是-主角 / 李四-击败-张三（chunk 0）。
    const triples = await fetchTriples(doc.id);
    const pairs = triples.map((t) => `${t.subject}-${t.relation}-${t.object}`);
    expect(pairs).toContain('张三-是-主角');
    expect(pairs).toContain('李四-击败-张三');

    // 源文本块引用随三元组返回（证据回溯）。
    const found = triples.find((t) => t.subject === '张三');
    expect(found).toBeTruthy();
    expect(found!.chunk_index).toBe(0);
    expect(found!.content).toContain('张三');

    // 一跳邻居：张三 → 主角（出边）/ 李四（入边）。
    const ctx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
    try {
      const res = await ctx.get(
        `/api/graph/neighbors?document_id=${doc.id}&name=${encodeURIComponent('张三')}`
      );
      expect(res.ok()).toBeTruthy();
      const body = await res.json();
      const out = body.neighbors.find(
        (n: { name: string; direction: string }) => n.name === '主角' && n.direction === 'out'
      );
      const inbound = body.neighbors.find(
        (n: { name: string; direction: string }) => n.name === '李四' && n.direction === 'in'
      );
      expect(out).toBeTruthy();
      expect(out!.relation).toBe('是');
      expect(inbound).toBeTruthy();
      expect(inbound!.relation).toBe('击败');
      // 邻居同样带源文本块引用。
      expect(out!.content).toContain('张三');
    } finally {
      await ctx.dispose();
    }
  });

  cleanupTest('写入三元组接口可用，删除文档后图数据被清理', async ({ uploadedDocs }) => {
    const filename = `graph-write-${Date.now()}.txt`;
    await uploadViaApi(filename, '王五登场。');
    const doc = await pollReady(filename);
    await uploadedDocs.track(filename);

    // 写入三元组（供测试与后续检索器使用），同步实体表。
    const ctx = await e2eApiContext();
    try {
      const res = await ctx.post('/api/graph/triples', {
        data: {
          document_id: doc.id,
          subject: '王五',
          relation: '挑战',
          object: '张三',
          chunk_index: 0,
          content: '王五挑战张三',
        },
      });
      expect(res.ok()).toBeTruthy();
      const created = (await res.json()) as Triple;
      expect(created.subject).toBe('王五');
      expect(created.object).toBe('张三');

      // 按文档查包含写入的三元组。
      const triples = await fetchTriples(doc.id);
      expect(triples.some((t) => t.subject === '王五' && t.relation === '挑战')).toBeTruthy();
    } finally {
      await ctx.dispose();
    }

    // 删除文档后图数据被清理（三元组为空）。
    const delCtx = await apiRequest.newContext({ baseURL: BACKEND_BASE });
    try {
      const del = await delCtx.delete(`/api/documents/${doc.id}`);
      expect(del.ok()).toBeTruthy();
    } finally {
      await delCtx.dispose();
    }
    await new Promise((r) => setTimeout(r, 300));
    expect(await fetchTriples(doc.id)).toEqual([]);
  });
});
