import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: '.', testMatch: '**/*.spec.js', fullyParallel: false, workers: 1, retries: 0,
  timeout: 90000, expect: { timeout: 15000 }, outputDir: 'test-results/artifacts',
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
  use: { baseURL: process.env.GRAPH_VIEWER_QA_URL, trace: 'off', screenshot: 'only-on-failure', video: 'off', viewport: { width: 1440, height: 960 } },
  projects: [
    { name: 'desktop-chrome', use: { browserName: 'chromium', channel: 'chrome' } },
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'firefox', use: { browserName: 'firefox' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
    { name: 'mobile-chromium-emulation', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
    { name: 'mobile-webkit-emulation', use: { ...devices['iPhone 15'], browserName: 'webkit' } },
  ],
});
