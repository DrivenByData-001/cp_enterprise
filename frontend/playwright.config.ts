import { defineConfig, devices } from '@playwright/test'

// Real-browser regression coverage for the Vocabulary Map's semantic zoom
// (docs/25-vocabulary-map.md §16/§17). Unlike VocabularyMapView.test.tsx
// (vitest + jsdom, which deliberately mocks the whole VocabularyGraph
// component — see that file's own top comment), these tests exercise the
// actual @xyflow/react graph in a real browser: node presence/absence per
// zoom band, close-zoom detail text, and viewport/API-call behaviour that
// only a real DOM + real CSS transform can prove.
//
// The dev server proxies /api to a FastAPI backend (vite.config.ts) that
// these tests never start — every network call the page makes is
// intercepted and served from a fixture (see e2e/fixtures.ts), so the
// suite needs neither a running backend nor a database and stays fast/
// deterministic in CI.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5183',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --port 5183 --strictPort',
    url: 'http://127.0.0.1:5183',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // This sandbox's installed @playwright/test (1.63) expects a newer
        // Chromium revision than the one pre-provisioned on this box —
        // point at the pre-installed binary directly rather than
        // downloading a matching revision (network policy blocks the
        // Playwright CDN here; see /root/.ccr/README.md).
        launchOptions: {
          executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
        },
      },
    },
  ],
})
