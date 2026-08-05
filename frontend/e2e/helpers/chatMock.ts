/**
 * Streamed LLM mock for E2E tests.
 *
 * The DocQA backend's POST /api/chat/stream endpoint emits a Server-Sent Events
 * stream with three event kinds: `thinking`, `message`, `done`. Each event is
 * serialized as:
 *
 *     event: <name>\r\n
 *     data: <json>\r\n
 *     \r\n
 *
 * (real responses use CRLF; \n\n is also accepted by the SSE parser.)
 *
 * Tests must mock this endpoint to avoid hitting the real LLM provider. This
 * helper installs a Playwright route that produces the same wire format and
 * delivers events with realistic per-chunk delays so the React UI mirrors
 * real streaming behavior.
 *
 * Nothing else is mocked. /api/chat/history, /api/documents and /api/settings
 * continue to hit the real backend, matching how the production app behaves.
 */
import type { Page, Route } from '@playwright/test';

export interface ChatMockOptions {
  /** Plain-text answer returned as `message` events. Required. */
  answer: string;
  /** Optional reasoning text returned as `thinking` events. */
  thinking?: string;
  /** Number of characters per streamed chunk. Smaller ⇒ more events. */
  chunkSize?: number;
  /** Delay (ms) inserted between `message` chunks to mimic LLM latency. */
  chunkDelayMs?: number;
  /** Initial latency (ms) before the response stream starts. */
  initialDelayMs?: number;
  /** If set, the mock returns a non-200 status with this JSON payload. */
  failureStatus?: number;
  failureBody?: unknown;
  /**
   * Per-request answer overrides keyed by the presence of `document_ids`.
   * When the request body contains at least one document id, `withDocs` is used;
   * otherwise `withoutDocs` is used. If a key is absent, `answer` is the fallback.
   * Useful for verifying that the frontend forwards the right document_ids to
   * the backend (e.g. Issue #25 cross-page RAG tracer bullet).
   */
  answersByDocs?: {
    withDocs?: string;
    withoutDocs?: string;
  };
}

export const DEFAULT_CHAT_ANSWER =
  'Hello! I am a mocked DocQA assistant. (no real LLM was called)';

/**
 * Install a route handler that satisfies POST /api/chat/stream with a
 * deterministic, stream-shaped SSE response. Calling this replaces any
 * previously registered handler for the same endpoint.
 */
export async function installChatStreamMock(
  page: Page,
  opts: ChatMockOptions
): Promise<void> {
  await uninstallChatStreamMock(page);
  const chunkSize = Math.max(1, opts.chunkSize ?? 12);
  const chunkDelayMs = Math.max(0, opts.chunkDelayMs ?? 8);
  const initialDelayMs = Math.max(0, opts.initialDelayMs ?? 30);

  await page.route('**/api/chat/stream', (route: Route) =>
    handleChatStream(route, opts, chunkSize, chunkDelayMs, initialDelayMs)
  );
}

/**
 * Install a route handler that satisfies POST /api/chat (non-streaming) with a
 * deterministic ChatResponse payload. Mirrors the real backend's JSON shape
 * so contract tests pass without invoking the real LLM.
 */
export async function installNonStreamChatMock(
  page: Page,
  opts: { answer?: string; sources?: string[] } = {}
): Promise<void> {
  await uninstallNonStreamChatMock(page);
  const answer = opts.answer ?? DEFAULT_CHAT_ANSWER;
  const sources = opts.sources ?? [];
  const body = JSON.stringify({ message: answer, sources });
  await page.route('**/api/chat', (route: Route) => {
    if (route.request().method() !== 'POST') {
      return route.continue();
    }
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      body,
    });
  });
}

/**
 * Remove any previously installed mock for /api/chat/stream. Safe to call
 * when no handler is registered.
 */
export async function uninstallChatStreamMock(page: Page): Promise<void> {
  await page.unroute('**/api/chat/stream').catch(() => {});
}

/**
 * Remove any previously installed mock for /api/chat (non-streaming). Safe
 * to call when no handler is registered.
 */
export async function uninstallNonStreamChatMock(page: Page): Promise<void> {
  await page.unroute('**/api/chat').catch(() => {});
}

async function handleChatStream(
  route: Route,
  opts: ChatMockOptions,
  chunkSize: number,
  chunkDelayMs: number,
  initialDelayMs: number
): Promise<void> {
  if (opts.failureStatus) {
    await new Promise((resolve) => setTimeout(resolve, initialDelayMs));
    await route.fulfill({
      status: opts.failureStatus,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts.failureBody ?? { error: 'mock failure' }),
    });
    return;
  }

  // Pick the answer based on document_ids forwarded by the frontend.
  const answer = pickAnswerByDocumentIds(route, opts);

  // Build the full SSE response into a single buffer; the browser's SSE parser
  // understands the \n\n record boundaries regardless of how it is delivered.
  const parts: string[] = [];
  if (opts.thinking) {
    for (const piece of sliceText(opts.thinking, chunkSize)) {
      parts.push(sseRecord('thinking', { content: piece }));
    }
  }
  for (const piece of sliceText(answer, chunkSize)) {
    parts.push(sseRecord('message', { content: piece }));
    if (chunkDelayMs > 0) {
      // small artificial delay per chunk keeps the typing indicator visible
      await new Promise((resolve) => setTimeout(resolve, chunkDelayMs));
    }
  }
  parts.push(sseRecord('done', { sources: [] }));

  if (initialDelayMs > 0) {
    await new Promise((resolve) => setTimeout(resolve, initialDelayMs));
  }

  await route.fulfill({
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'X-Accel-Buffering': 'no',
    },
    body: parts.join(''),
  });
}

function pickAnswerByDocumentIds(route: Route, opts: ChatMockOptions): string {
  if (!opts.answersByDocs) return opts.answer;
  let body: { document_ids?: unknown } | null = null;
  try {
    const raw = route.request().postData();
    if (raw) body = JSON.parse(raw);
  } catch {
    body = null;
  }
  const ids = Array.isArray(body?.document_ids) ? body!.document_ids : [];
  if (ids.length > 0) {
    return opts.answersByDocs.withDocs ?? opts.answer;
  }
  return opts.answersByDocs.withoutDocs ?? opts.answer;
}

function sseRecord(event: string, data: unknown): string {
  // Use LF (the backend emits CRLF; both forms are accepted by the parser).
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function* sliceText(text: string, size: number): Generator<string> {
  if (!text) return;
  for (let i = 0; i < text.length; i += size) {
    yield text.slice(i, i + size);
  }
}
