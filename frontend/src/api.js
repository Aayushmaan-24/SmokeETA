// Typed-ish helpers for the Smoke ETA API. All endpoints return the exact
// JSON files produced by fetch_data.py / simulate.py.

const API_TIMEOUT_MS = 120_000 // agent answers can take 30–40s; keep a wide margin

async function fetchJsonImpl(path, options = {}) {
  const res = await fetch(path, options)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.json()
}


export async function fetchJson(path, options = {}) {
  return fetchJsonImpl(path, options)
}

export async function fetchJsonWithTimeout(path, options = {}, timeoutMs = API_TIMEOUT_MS) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetchJsonImpl(path, { ...options, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

export const getSim = (mode = 'live') => fetchJson('/api/sim?mode=' + encodeURIComponent(mode))
export const getFires = () => fetchJson('/api/fires')
// AQI is requested per-mode: Replay must never be served the Live payload.
export const getAqi = (mode = 'live') => fetchJson('/api/aqi?mode=' + encodeURIComponent(mode))
export const getZones = () => fetchJson('/api/zones')
export const getHealth = () => fetchJson('/api/health')

export function regenerate() {
  return fetchJson('/api/regenerate', { method: 'POST' })
}

const AGENT_DEFAULTS = {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
}

export async function askAgent(body, timeoutMs = API_TIMEOUT_MS) {
  return fetchJsonWithTimeout('/api/ask', {
    ...AGENT_DEFAULTS,
    body: JSON.stringify(body),
  }, timeoutMs)
}
