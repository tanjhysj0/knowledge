/**
 * Playwright global setup: inject the ``X-E2E-Test`` header so every browser
 * context built during the E2E run carries the marker. The backend reads it
 * in ``app.services.llm.get_llm_provider`` and swaps in ``MockLLMProvider``,
 * so no test ever invokes the real LLM.
 *
 * This runs once per `npx playwright test` invocation. The header is set on
 * each context by `playwright.config.ts` via `use.extraHTTPHeaders`, so this
 * file is intentionally a no-op placeholder kept for future setup needs.
 */
export default async function globalSetup(): Promise<void> {
  // Reserved for future pre-run setup (e.g. resetting test data).
  // The X-E2E-Test header is injected via `use.extraHTTPHeaders` in
  // `playwright.config.ts`, which every context inherits automatically.
}