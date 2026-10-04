// Actual Chrome page zoom, independently of device scale or CSS transforms.
import { mkdir, writeFile, mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright')
const executablePath = process.env.CHROMIUM_EXECUTABLE || process.argv[3]
const output = process.env.KVIST_QA_OUTPUT || '/tmp/kvist-web-qa'
const base = process.env.KVIST_WEB_URL || 'http://127.0.0.1:9672'
const root = await mkdtemp(join(tmpdir(), 'kvist-zoom-'))
const extension = join(root, 'extension')
await mkdir(extension)
await mkdir(join(output, 'screenshots'), { recursive: true })
await writeFile(join(extension, 'manifest.json'), JSON.stringify({ manifest_version: 3, name: 'Local zoom QA', version: '1.0', permissions: ['tabs'], background: { service_worker: 'worker.js' } }))
await writeFile(join(extension, 'worker.js'), 'chrome.runtime.onInstalled.addListener(() => {});')
const context = await chromium.launchPersistentContext(join(root, 'profile'), {
  headless: true, ...(executablePath ? { executablePath } : {}), viewport: null,
  ignoreDefaultArgs: ['--disable-extensions'], args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
})
try {
  const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker')
  const page = await context.newPage()
  await page.goto(base)
  await page.locator('#app').waitFor()
  const report = []
  for (const [width, height] of [[1280, 720], [1440, 900], [1920, 1080], [2560, 1440]]) {
    await page.setViewportSize({ width, height })
    for (const zoom of [1, 1.1, 1.25]) {
      const actual = await worker.evaluate(async ({ base, zoom }) => {
        const tabs = await chrome.tabs.query({ url: base + '/*' })
        await chrome.tabs.setZoom(tabs.at(-1).id, zoom)
        return chrome.tabs.getZoom(tabs.at(-1).id)
      }, { base, zoom })
      await page.waitForTimeout(200)
      const measured = await page.evaluate(() => {
        const rect = document.querySelector('#composer').getBoundingClientRect()
        return { width: innerWidth, height: innerHeight, dpr: devicePixelRatio, overflow: document.documentElement.scrollWidth > innerWidth, composer: rect.right <= innerWidth && rect.bottom <= innerHeight }
      })
      assert(Math.abs(actual - zoom) < .001)
      assert(Math.abs(measured.width - width / zoom) < 2)
      assert(measured.composer && !measured.overflow)
      report.push({ viewport: [width, height], zoom: actual, ...measured })
      await page.screenshot({ path: join(output, `screenshots/browser-zoom-${width}x${height}-${Math.round(zoom * 100)}.png`) })
    }
  }
  await writeFile(join(output, 'zoom.json'), JSON.stringify(report, null, 2))
  console.log('PASS 12 actual browser zoom / viewport combinations')
} finally { await context.close() }
