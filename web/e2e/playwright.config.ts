import { defineConfig } from '@playwright/test'

// 브라우저 스모크 (compose e2e 프로파일) — 컨테이너 안 127.0.0.1:8800 → api:8000 프록시로 PUBLIC_BASE_URL 과 같은 origin 사용
export default defineConfig({
  testDir: '.',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'results/report.json' }]],
  globalSetup: './proxy.ts',
  outputDir: 'results/artifacts',
  use: { baseURL: `http://127.0.0.1:${process.env.E2E_PORT || 8800}`, viewport: { width: 1440, height: 900 }, locale: 'ko-KR', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
})
