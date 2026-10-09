/**
 * End-to-end interaction test for the Smoke ETA dashboard.
 * Drives real Chrome via puppeteer-core against the dev servers.
 * Run from frontend/: node e2e.mjs
 */
import puppeteer from 'puppeteer-core'

const CHROME =
  'C:/Program Files/Google/Chrome/Application/chrome.exe'
const BASE = 'http://localhost:5173'

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1400,900'],
})
const page = await browser.newPage()
await page.setViewport({ width: 1400, height: 900 })

const errors = []
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(`console: ${m.text()}`)
})

const results = []
const check = (name, ok) => {
  results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`)
  if (!ok) process.exitCode = 1
}

await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 45000 })
await page.waitForSelector('.map', { timeout: 30000 })
await new Promise((r) => setTimeout(r, 1500))

// 1. Dashboard loads real data
check('map rendered', (await page.$$('.leaflet-container')).length === 1)
check('zone list has 11 zones', (await page.$$('.zone-row')).length === 11)
const canvasCount = await page.$$eval(
  'canvas.smoke-particle-canvas',
  (els) => els.length,
)
check('particle canvas present', canvasCount === 1)

// 2. Timeline scrubbing updates the hour display
const hourBefore = await page.$eval('.hour-display', (el) => el.textContent)
await page.$eval('.slider', (el) => {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  ).set
  setter.call(el, '30')
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
})
await new Promise((r) => setTimeout(r, 800))
const hourAfter = await page.$eval('.hour-display', (el) => el.textContent)
check(
  `scrub changes hour (${hourBefore.trim()} -> ${hourAfter.trim()})`,
  hourBefore !== hourAfter && hourAfter.includes('30'),
)

// 3. Play advances time; pause stops it
await page.evaluate(() => {
  const buttons = [...document.querySelectorAll('.timeline-controls button')]
  buttons.find((b) => b.textContent.includes('Play')).click()
})
await new Promise((r) => setTimeout(r, 2500))
const hourPlaying = await page.$eval('.hour-display', (el) => el.textContent)
check(`play advances time (${hourPlaying.trim()})`, !hourPlaying.includes('30.0'))
await page.evaluate(() => {
  const buttons = [...document.querySelectorAll('.timeline-controls button')]
  buttons.find((b) => b.textContent.includes('Pause')).click()
})
await new Promise((r) => setTimeout(r, 400))
const h1 = await page.$eval('.hour-display', (el) => el.textContent)
await new Promise((r) => setTimeout(r, 1200))
const h2 = await page.$eval('.hour-display', (el) => el.textContent)
check('pause stops time', h1 === h2)

// 4. Reset button
await page.evaluate(() => {
  const buttons = [...document.querySelectorAll('.timeline-controls button')]
  buttons[0].click() // reset is the first control
})
await new Promise((r) => setTimeout(r, 400))
const hourReset = await page.$eval('.hour-display', (el) => el.textContent)
check(`reset returns to hour 0 (${hourReset.trim()})`, hourReset.includes('0.0'))

// 5. Zone selection via list -> details panel shows sim fields
await page.evaluate(() => {
  ;[...document.querySelectorAll('.zone-row')]
    .find((b) => b.textContent.includes('Dwarka'))
    .click()
})
await new Promise((r) => setTimeout(r, 500))
const details = await page.$('.zone-details')
check('zone details panel opens', !!details)
const detailText = await page.$eval('.zone-details', (el) => el.textContent)
check(
  'details show arrival/peak/influence',
  detailText.includes('Arrival') &&
    detailText.includes('Peak') &&
    detailText.includes('Relative influence'),
)
check(
  'details show current AQI context',
  detailText.includes('Current PM2.5') || detailText.includes('—'),
)

// 6. Marker click also selects zone
await page.evaluate(() => {
  ;[...document.querySelectorAll('.zone-row')][0].click() // deselect via list
})
await page.evaluate(() => {
  // click first zone circle marker on the map (path element)
  const paths = [...document.querySelectorAll('path.leaflet-interactive')]
  // fire markers come first; zones after. Click a marker and check panel.
  paths[paths.length - 1]?.dispatchEvent(
    new MouseEvent('click', { bubbles: true }),
  )
})
await new Promise((r) => setTimeout(r, 500))
check('marker click opens details', !!(await page.$('.zone-details')))

// 7. Particle canvas actually drew pixels (frame data reached the layer)
const drewPixels = await page.evaluate(() => {
  const c = document.querySelector('canvas.smoke-particle-canvas')
  if (!c) return false
  const ctx = c.getContext('2d')
  const data = ctx.getImageData(0, 0, c.width, c.height).data
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] > 0) return true
  }
  return false
})
check('particles drawn on canvas at hour 0', drewPixels)

// 8. Error state: point the app at a dead API via reload of a bad hash isn't
// possible without mocking; instead verify the backend 404 UI path by
// intercepting the API call.
await page.setRequestInterception(true)
page.on('request', (req) => {
  if (req.url().includes('/api/sim')) {
    req.respond({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'sim.json not found (test)' }),
    })
  } else {
    req.continue()
  }
})
await page.reload({ waitUntil: 'networkidle2' })
await new Promise((r) => setTimeout(r, 1000))
const errText = await page.evaluate(() => document.body.textContent)
check(
  'error state shown when sim unavailable',
  errText.includes('Data unavailable') && errText.includes('sim.json not found'),
)
check('retry button present', !!(await page.$('.error-card button')))

console.log(results.join('\n'))
if (errors.length) {
  console.log('\nBrowser errors:')
  console.log(errors.slice(0, 10).join('\n'))
  process.exitCode = 1
}
await browser.close()
