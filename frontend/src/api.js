// Typed-ish helpers for the Smoke ETA API. All endpoints return the exact
// JSON files produced by fetch_data.py / simulate.py.

export async function fetchJson(path) {
  const res = await fetch(path)
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

export const getSim = () => fetchJson('/api/sim')
export const getFires = () => fetchJson('/api/fires')
export const getAqi = () => fetchJson('/api/aqi')
export const getZones = () => fetchJson('/api/zones')
export const getHealth = () => fetchJson('/api/health')

export function regenerate() {
  return fetchJson('/api/regenerate', { method: 'POST' })
}
